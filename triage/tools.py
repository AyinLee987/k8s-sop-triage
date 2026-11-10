"""Read-only tool layer shared by all three arms.

Read-only is enforced twice: this module only ever issues `get` / `logs`, and
the runner it is given uses the RBAC-restricted kubeconfig (k8s/rbac-diag.yaml).
Every call is charged to the budget, time-bounded, de-duplicated and traced.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field

from triage.budget import Budget, BudgetExceeded

MAX_CHARS = 6000


@dataclass
class Trace:
    events: list[dict] = field(default_factory=list)

    def add(self, **event) -> None:
        event.setdefault("t", round(time.time(), 3))
        self.events.append(event)


# ---------- summarizers (pure functions on kubectl JSON; also used by the rule/code arms) ----------

def summarize_pod(pod: dict) -> dict:
    meta, spec, status = pod.get("metadata", {}), pod.get("spec", {}), pod.get("status", {})
    containers = []
    for cs in status.get("containerStatuses", []) or []:
        state, last = cs.get("state", {}), cs.get("lastState", {})
        entry = {"name": cs.get("name"), "ready": cs.get("ready"), "restarts": cs.get("restartCount", 0)}
        if "waiting" in state:
            entry["waiting_reason"] = state["waiting"].get("reason")
            entry["waiting_message"] = (state["waiting"].get("message") or "")[:300]
        if "running" in state:
            entry["running_since"] = state["running"].get("startedAt")
        if "terminated" in last:
            entry["last_terminated_reason"] = last["terminated"].get("reason")
            entry["last_exit_code"] = last["terminated"].get("exitCode")
        containers.append(entry)
    ready = all(c.get("ready") for c in containers) if containers else False
    return {
        "name": meta.get("name"),
        "app": (meta.get("labels") or {}).get("app"),
        "owner": _owner(meta),
        "phase": status.get("phase"),
        "ready": ready,
        "restarts": sum(c["restarts"] for c in containers),
        "containers": containers,
        "node": spec.get("nodeName"),
    }


def _owner(meta: dict) -> str | None:
    refs = meta.get("ownerReferences") or []
    return f"{refs[0].get('kind')}/{refs[0].get('name')}" if refs else None


def summarize_container_spec(c: dict) -> dict:
    env = []
    for e in c.get("env", []) or []:
        if "value" in e:
            env.append(f"{e['name']}={e['value']}")
        elif "valueFrom" in e:
            src = e["valueFrom"]
            ref = src.get("secretKeyRef") or src.get("configMapKeyRef") or src.get("fieldRef") or {}
            kind = next(iter(src.keys()), "valueFrom")
            env.append(f"{e['name']} <- {kind}:{ref.get('name', ref.get('fieldPath', ''))}/{ref.get('key', '')}".rstrip("/"))
    out = {
        "name": c.get("name"),
        "image": c.get("image"),
        "imagePullPolicy": c.get("imagePullPolicy"),
        "command": c.get("command"),
        "args": c.get("args"),
        "env": env,
        "envFrom": [next(iter(x.values())).get("name") for x in c.get("envFrom", []) or [] if x],
        "resources": c.get("resources"),
    }
    for probe in ("livenessProbe", "readinessProbe", "startupProbe"):
        if c.get(probe):
            p = c[probe]
            out[probe] = {k: p[k] for k in ("httpGet", "exec", "tcpSocket", "periodSeconds", "failureThreshold") if k in p}
    return {k: v for k, v in out.items() if v not in (None, [], {})}


def summarize_workload(obj: dict) -> dict:
    meta, spec, status = obj.get("metadata", {}), obj.get("spec", {}), obj.get("status", {})
    template = spec.get("template", {}).get("spec", {})
    return {
        "kind": obj.get("kind"),
        "name": meta.get("name"),
        "namespace": meta.get("namespace"),
        "desired": spec.get("replicas"),
        "ready": status.get("readyReplicas", 0),
        "available": status.get("availableReplicas", 0),
        "updated": status.get("updatedReplicas", 0),
        "selector": (spec.get("selector") or {}).get("matchLabels"),
        "containers": [summarize_container_spec(c) for c in template.get("containers", [])],
        "conditions": [
            {k: c.get(k) for k in ("type", "status", "reason", "message")}
            for c in status.get("conditions", []) or []
        ],
    }


def summarize_service(svc: dict) -> dict:
    spec = svc.get("spec", {})
    return {
        "name": svc.get("metadata", {}).get("name"),
        "type": spec.get("type"),
        "selector": spec.get("selector"),
        "ports": [{k: p.get(k) for k in ("name", "port", "targetPort")} for p in spec.get("ports", []) or []],
    }


def summarize_events(items: list[dict], since: str | None = None, object_name: str | None = None) -> list[dict]:
    out = []
    for e in items:
        ts = e.get("lastTimestamp") or e.get("eventTime") or e.get("firstTimestamp") or ""
        if since and ts and ts < since:
            continue
        involved = e.get("involvedObject", {})
        name = involved.get("name", "")
        if object_name and not name.startswith(object_name):
            continue
        out.append({
            "time": ts,
            "type": e.get("type"),
            "reason": e.get("reason"),
            "object": f"{involved.get('kind')}/{name}",
            "message": (e.get("message") or "")[:300],
            "count": e.get("count") or 1,
        })
    out.sort(key=lambda x: x["time"] or "", reverse=True)
    return out[:40]


# ---------- the tool layer ----------

class ReadOnlyTools:
    """Tool schemas exposed to the LLM arms live in TOOL_SPECS; method names match."""

    def __init__(self, runner, namespace: str, budget: Budget, trace: Trace | None = None,
                 events_since: str | None = None, max_chars: int = MAX_CHARS):
        self.runner = runner
        self.namespace = namespace
        self.budget = budget
        self.trace = trace or Trace()
        self.events_since = events_since
        self.max_chars = max_chars
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()

    # Public tools ------------------------------------------------------------
    def overview(self, namespace: str | None = None) -> dict:
        ns = namespace or self.namespace
        return self._call("overview", {"namespace": ns}, lambda t: self._overview(ns, t))

    def get_workload(self, kind: str, name: str, namespace: str | None = None) -> dict:
        ns = namespace or self.namespace
        kind = kind.lower()
        return self._call("get_workload", {"kind": kind, "name": name, "namespace": ns},
                          lambda t: self._get_json([kind, name], ns, t, summarize_workload))

    def get_pod(self, name: str, namespace: str | None = None) -> dict:
        ns = namespace or self.namespace
        return self._call("get_pod", {"name": name, "namespace": ns},
                          lambda t: self._get_json(["pod", name], ns, t, summarize_pod))

    def get_logs(self, pod: str, previous: bool = False, tail: int = 60, namespace: str | None = None) -> dict:
        ns = namespace or self.namespace
        tail = max(1, min(int(tail), 200))
        args = {"pod": pod, "previous": bool(previous), "tail": tail, "namespace": ns}
        return self._call("get_logs", args, lambda t: self._logs(pod, bool(previous), tail, ns, t))

    def get_events(self, namespace: str | None = None, object_name: str | None = None) -> dict:
        ns = namespace or self.namespace
        return self._call("get_events", {"namespace": ns, "object_name": object_name},
                          lambda t: self._events(ns, object_name, t))

    def get_service(self, name: str, namespace: str | None = None) -> dict:
        ns = namespace or self.namespace
        return self._call("get_service", {"name": name, "namespace": ns}, lambda t: self._service(name, ns, t))

    def dispatch(self, name: str, args: dict) -> dict:
        allowed = {spec["function"]["name"] for spec in TOOL_SPECS}
        if name not in allowed:
            return {"error": "unknown_tool", "message": f"no tool named {name}"}
        try:
            return getattr(self, name)(**(args or {}))
        except TypeError as exc:
            return {"error": "bad_arguments", "message": str(exc)[:200]}

    # Internals ---------------------------------------------------------------
    def _call(self, name: str, args: dict, fn) -> dict:
        key = json.dumps([name, args], sort_keys=True, ensure_ascii=False)
        with self._lock:  # the code-SOP arm collects evidence in parallel threads
            if key in self._cache:
                self.budget.note_duplicate()
                self.trace.add(type="tool", name=name, args=args, duplicate=True)
                return {**self._cache[key], "note": "重复调用：参数与之前完全相同，返回缓存结果"}
            timeout = self.budget.charge_tool()
        started = time.monotonic()
        result = fn(timeout)
        text = json.dumps(result, ensure_ascii=False)
        if len(text) > self.max_chars:
            result = {"truncated": True, "content": text[: self.max_chars]}
        with self._lock:
            self._cache[key] = result
            self.trace.add(type="tool", name=name, args=args, ms=round((time.monotonic() - started) * 1000),
                           ok="error" not in result, chars=min(len(text), self.max_chars))
        return result

    def _run_json(self, args: list[str], ns: str, timeout: float):
        res = self.runner.run(["-n", ns, *args, "-o", "json"], timeout=timeout)
        if not res.ok:
            return None, _error(res)
        try:
            return json.loads(res.stdout), None
        except json.JSONDecodeError:
            return None, {"error": "bad_output", "message": res.stdout[:200]}

    def _get_json(self, args, ns, timeout, summarize):
        data, err = self._run_json(["get", *args], ns, timeout)
        return err or summarize(data)

    def _overview(self, ns: str, timeout: float) -> dict:
        data, err = self._run_json(["get", "deployments,statefulsets,pods,services"], ns, timeout)
        if err:
            return err
        out = {"namespace": ns, "workloads": [], "pods": [], "services": []}
        for item in data.get("items", []):
            kind = item.get("kind")
            if kind in ("Deployment", "StatefulSet"):
                w = summarize_workload(item)
                out["workloads"].append({k: w[k] for k in ("kind", "name", "desired", "ready", "available", "updated")})
            elif kind == "Pod":
                out["pods"].append(summarize_pod(item))
            elif kind == "Service":
                out["services"].append(summarize_service(item))
        return out

    def _logs(self, pod: str, previous: bool, tail: int, ns: str, timeout: float) -> dict:
        args = ["-n", ns, "logs", pod, f"--tail={tail}"]
        if previous:
            args.append("--previous")
        res = self.runner.run(args, timeout=timeout)
        if not res.ok:
            return _error(res)
        return {"pod": pod, "previous": previous, "lines": res.stdout.splitlines()[-tail:]}

    def _events(self, ns: str, object_name: str | None, timeout: float) -> dict:
        data, err = self._run_json(["get", "events"], ns, timeout)
        if err:
            return err
        return {"namespace": ns, "events": summarize_events(data.get("items", []), self.events_since, object_name)}

    def _service(self, name: str, ns: str, timeout: float) -> dict:
        svc, err = self._run_json(["get", "service", name], ns, timeout)
        if err:
            return err
        out = summarize_service(svc)
        slices, err = self._run_json(["get", "endpointslices", "-l", f"kubernetes.io/service-name={name}"], ns, timeout)
        if err:
            out["endpoints_error"] = err
            return out
        ready = []
        for s in slices.get("items", []):
            for ep in s.get("endpoints", []) or []:
                if (ep.get("conditions") or {}).get("ready"):
                    ready.append((ep.get("targetRef") or {}).get("name") or ",".join(ep.get("addresses", [])))
        out["ready_endpoints"] = ready
        return out


def _error(res) -> dict:
    if res.timed_out:
        return {"error": "timeout", "message": "kubectl call timed out"}
    if res.forbidden:
        return {"error": "forbidden", "message": res.stderr.strip()[:300]}
    return {"error": "kubectl_failed", "message": res.stderr.strip()[:300]}


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }}


_NS = {"namespace": {"type": "string", "description": "可选；默认是本次排查的命名空间"}}
TOOL_SPECS = [
    _fn("overview", "列出命名空间内的 Deployment、StatefulSet、Pod（含状态、重启、等待原因、上次终止原因）和 Service。", dict(_NS), []),
    _fn("get_workload", "查看一个 Deployment 或 StatefulSet 的规格（镜像、命令、环境变量、资源、探针）和状态。",
        {"kind": {"type": "string", "enum": ["deployment", "statefulset"]}, "name": {"type": "string"}, **_NS}, ["kind", "name"]),
    _fn("get_pod", "查看一个 Pod 的状态摘要。", {"name": {"type": "string"}, **_NS}, ["name"]),
    _fn("get_logs", "读取 Pod 日志。previous=true 读上一次运行（已退出容器）的日志。",
        {"pod": {"type": "string"}, "previous": {"type": "boolean"}, "tail": {"type": "integer"}, **_NS}, ["pod"]),
    _fn("get_events", "列出命名空间最近的事件，可按对象名前缀过滤。", {"object_name": {"type": "string"}, **_NS}, []),
    _fn("get_service", "查看 Service 的 selector、端口和当前就绪的 endpoints。", {"name": {"type": "string"}, **_NS}, ["name"]),
]

__all__ = ["ReadOnlyTools", "TOOL_SPECS", "Trace", "BudgetExceeded", "summarize_pod", "summarize_workload",
           "summarize_events", "summarize_service"]
