"""Arm 1 — rule baseline: deterministic collection + fixed rules from the SOPs. No LLM.
Answers: "do these faults even need a model?" """
from __future__ import annotations

import re

from triage.agents import sop_steps
from triage.agents.base import Agent, AgentResult, short
from triage.schema import Diagnosis

CONFIG_ERR = re.compile(r"must be|ValueError|invalid value|out of range", re.IGNORECASE)
COMMAND_ERR = re.compile(r"No module named|not found|executable file|exec format", re.IGNORECASE)
PROBE_ERR = re.compile(r"(Liveness|Readiness|Startup) probe failed", re.IGNORECASE)
UNREACHABLE = re.compile(r"ConnectionError|TimeoutError|Name or service not known|connection refused|Temporary failure in name resolution", re.IGNORECASE)
AUTH = re.compile(r"AuthenticationError|WRONGPASS|invalid password|NOAUTH", re.IGNORECASE)
OOM = re.compile(r"OutOfMemoryError|OOM command", re.IGNORECASE)


def _size_bytes(value: str | None) -> float | None:
    if not value:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(b|kb|mb|gb)?", value.strip().lower())
    if not m:
        return None
    return float(m.group(1)) * {"b": 1, None: 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3}[m.group(2)]


class RuleBaselineAgent(Agent):
    name = "rule"
    uses_llm = False

    def _diagnose(self, symptom: str, namespace: str, tools) -> AgentResult:
        overview = tools.overview()
        if overview.get("error") == "forbidden":
            return AgentResult(Diagnosis(status="undetermined", unknowns=[f"没有命名空间 {namespace} 的读权限"],
                                         evidence=[short(overview)]))
        abnormal = sop_steps.find_abnormal(overview)
        if abnormal:
            ab = abnormal[0]
            ev = sop_steps.collect_branch_evidence(tools, ab)
            return AgentResult(self._decide_branch(ab, ev, namespace, tools, overview))
        app = sop_steps.collect_app_logs(tools, overview)
        for ref, lines in app["error_lines"].items():
            text = "\n".join(lines)
            if sop_steps.has_dependency_error(text):
                kind, _, name = ref.partition("/")
                workload = tools.get_workload(kind.lower(), name)
                return AgentResult(self._dependency(kind, name, workload, text, lines, namespace, tools, overview))
        for ref, lines in app["slow_lines"].items():  # 20-worker step 4
            kind, _, name = ref.partition("/")
            workload = tools.get_workload(kind.lower(), name)
            env = [e for c in workload.get("containers", []) for e in c.get("env", []) if "DELAY" in e or "_MS=" in e]
            return AgentResult(Diagnosis("root_cause_found", "processing_too_slow", f"{ref} 处理耗时过长",
                                         {"kind": kind, "name": name, "namespace": namespace}, [lines[-1][:300], *env[:3]]))
        checked = [f"{w['kind']}/{w['name']} {w.get('ready')}/{w.get('desired')}" for w in overview.get("workloads", [])]
        unknowns = [f"日志中有未归类的错误：{list(app['error_lines'])}"] if app["error_lines"] else []
        return AgentResult(Diagnosis("no_fault_found", "no_fault", "按 SOP 检查未发现故障", None,
                                     ["工作负载就绪情况：" + "; ".join(checked), "各工作负载最近日志无已知错误"],
                                     unknowns))

    # ---- dependency errors: 10-api / 20-worker step 5, then 30-redis ----
    def _dependency(self, kind, name, workload, text, lines, namespace, tools, overview) -> Diagnosis:
        client = {"kind": kind, "name": name, "namespace": namespace}
        overrides = sop_steps.client_redis_overrides(workload)
        host_port = [o for o in overrides if o.startswith(("REDIS_HOST", "REDIS_PORT"))]
        password = [o for o in overrides if o.startswith("REDIS_PASSWORD")]
        if host_port and UNREACHABLE.search(text):
            return Diagnosis("root_cause_found", "dependency_unreachable", f"{kind}/{name} 的 Redis 地址被覆盖：{host_port[0]}",
                             client, [lines[-1][:300], *host_port])
        if password and AUTH.search(text):
            return Diagnosis("root_cause_found", "dependency_auth_failed", f"{kind}/{name} 的 Redis 密码被覆盖为字面值",
                             client, [lines[-1][:300], *password])
        dep = sop_steps.follow_dependency(tools, overview)
        return self._decide_dependency(dep, lines, namespace) or Diagnosis(
            "undetermined", "other", f"{kind}/{name} 报依赖错误，但客户端配置和 Redis 都没找到明确原因", client,
            [lines[-1][:300]], ["依赖错误的根因未确定"])

    def _decide_dependency(self, dep: dict, client_lines: list[str], namespace: str) -> Diagnosis | None:
        redis = {"kind": "StatefulSet", "name": sop_steps.DEPENDENCY, "namespace": namespace}
        client_ev = client_lines[-1][:300] if client_lines else ""
        if dep.get("desired_replicas") == 0:
            return Diagnosis("root_cause_found", "dependency_down", "Redis 的副本数为 0，没有在运行", redis,
                             [f"statefulset/redis desired=0 ready={dep.get('ready_replicas')}", client_ev])
        diff = dep.get("config_diff") or {}
        if diff.get("auth_override"):
            return Diagnosis("root_cause_found", "dependency_config_error", "redis-config 里多了认证配置，覆盖了 Secret 中的密码",
                             redis, [f"redis-config: {diff['auth_override'][0]}", client_ev])
        limit = _size_bytes(diff.get("maxmemory"))
        if limit is not None and limit < 192 * 1024 ** 2 and OOM.search("\n".join(client_lines)):
            return Diagnosis("root_cause_found", "dependency_memory_full", f"redis-config 的 maxmemory 被改成 {diff['maxmemory']}",
                             redis, [f"redis-config: maxmemory {diff['maxmemory']}", client_ev])
        return None

    # ---- abnormal workload branches: 01-pod-common step 3 ----
    def _decide_branch(self, ab, ev: dict, namespace: str, tools, overview) -> Diagnosis:
        obj = {"kind": ab.kind, "name": ab.name, "namespace": namespace}
        containers = (ev.get("workload") or {}).get("containers") or [{}]
        spec = containers[0]
        pod_c = (ev["pod"].get("containers") or [{}])[0]
        events_text = "\n".join(e.get("message", "") for e in (ev.get("events") or {}).get("events", []))
        logs_lines = (ev.get("previous_logs") or ev.get("logs") or {}).get("lines", [])
        logs = "\n".join(logs_lines)

        if ab.branch == "image":
            return Diagnosis("root_cause_found", "image_unavailable", f"镜像 {spec.get('image')} 不可用",
                             obj, [f"image={spec.get('image')}", f"waiting={pod_c.get('waiting_reason')}"])
        if ab.name == sop_steps.DEPENDENCY:  # the dependency itself is abnormal (30-redis step 1-2)
            dep = {"desired_replicas": (ev.get("workload") or {}).get("desired"), "config_diff": ev.get("config_diff")}
            decided = self._decide_dependency(dep, logs_lines, namespace)
            if decided:
                decided.evidence.append(events_text.splitlines()[0][:300] if events_text else "")
                return decided
        if ab.branch == "not_ready" and sop_steps.has_dependency_error(logs):  # 01-pod-common 3c -> component SOP step 5
            lines = [ln for ln in logs_lines if sop_steps.has_dependency_error(ln)]
            return self._dependency(ab.kind, ab.name, ev.get("workload") or {}, logs, lines, namespace, tools, overview)
        if pod_c.get("last_terminated_reason") == "OOMKilled":
            limit = ((spec.get("resources") or {}).get("limits") or {}).get("memory")
            return Diagnosis("root_cause_found", "memory_limit_too_low", f"内存限制 {limit} 过低，容器被 OOMKilled",
                             obj, ["lastState.terminated.reason=OOMKilled", f"limits.memory={limit}"])
        start_text = "\n".join([logs, pod_c.get("waiting_message") or "", events_text])
        if CONFIG_ERR.search(logs):
            line = next(ln for ln in logs.splitlines() if CONFIG_ERR.search(ln))
            return Diagnosis("root_cause_found", "invalid_config_value", "启动时配置校验失败", obj,
                             [line[:300]], ["需要对照环境变量确认具体配置项"])
        if COMMAND_ERR.search(start_text):  # 3b-2/3b-3: logs, or the waiting message/events when it never started
            line = next(ln for ln in start_text.splitlines() if COMMAND_ERR.search(ln))
            return Diagnosis("root_cause_found", "wrong_command", f"启动命令错误：{spec.get('command')}",
                             obj, [line[:300], f"command={spec.get('command')}"])
        if PROBE_ERR.search(events_text):
            line = next(ln for ln in events_text.splitlines() if PROBE_ERR.search(ln))
            return Diagnosis("root_cause_found", "probe_misconfigured", "健康检查探针失败导致重启或不就绪",
                             obj, [line[:300]])
        return Diagnosis("undetermined", "other", f"{ab.kind}/{ab.name} 异常但规则无法归类", obj,
                         ab.reasons[:5], ["规则未覆盖该异常"])
