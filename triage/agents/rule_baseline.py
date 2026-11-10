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
TIMEOUT = re.compile(r"TimeoutError|timed? ?out", re.IGNORECASE)
CONSUMER_EVENTS = ('"claimed"', '"committed"', '"acked"')


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
        for ref, lines in app["slow_lines"].items():  # 20-worker step 5
            kind, _, name = ref.partition("/")
            workload = tools.get_workload(kind.lower(), name)
            obj = {"kind": kind, "name": name, "namespace": namespace}
            facts = sop_steps.workload_spec_facts(workload)
            if facts["cpu_low"]:
                return AgentResult(Diagnosis("root_cause_found", "cpu_limit_too_low", f"{ref} 的 CPU limit {facts['cpu_limit']} 过低",
                                             obj, [lines[-1][:300], f"limits.cpu={facts['cpu_limit']}"]))
            env = [e for c in workload.get("containers", []) for e in c.get("env", []) if "DELAY" in e or "_MS=" in e]
            return AgentResult(Diagnosis("root_cause_found", "processing_too_slow", f"{ref} 处理耗时过长",
                                         obj, [lines[-1][:300], *env[:3]]))
        decided = self._baseline(tools, overview, app, namespace)  # 01-pod-common step 4
        if decided:
            return AgentResult(decided)
        checked = [f"{w['kind']}/{w['name']} {w.get('ready')}/{w.get('desired')}" for w in overview.get("workloads", [])]
        unknowns = [f"日志中有未归类的错误：{list(app['error_lines'])}"] if app["error_lines"] else []
        return AgentResult(Diagnosis("no_fault_found", "no_fault", "按 SOP 检查未发现故障", None,
                                     ["工作负载就绪情况：" + "; ".join(checked), "各工作负载最近日志无已知错误",
                                      "app-config、启动命令、Service 与基线一致"], unknowns))

    # ---- step 4: everything looks healthy; compare with the baseline ----
    def _baseline(self, tools, overview, app, namespace) -> Diagnosis | None:
        b = sop_steps.baseline_checks(tools, overview)
        worker_lines = next((r.get("lines", []) for ref, r in app["logs"].items() if ref.endswith("/worker")), [])
        consuming = any(any(e in ln for e in CONSUMER_EVENTS) for ln in worker_lines)
        if b["worker_command_differs"] and not consuming:  # 20-worker step 4.1
            return Diagnosis("root_cause_found", "wrong_command", f"worker 的启动命令不是消费者：{b['worker_command']}",
                             {"kind": "Deployment", "name": "worker", "namespace": namespace},
                             [f"command={b['worker_command']}（基线 {sop_steps.WORKER_COMMAND}）", *(worker_lines[-2:])])
        if b["service_issues"]:
            issue = b["service_issues"][0]
            name = issue.split("/", 1)[1].split()[0]
            return Diagnosis("root_cause_found", "service_misconfigured", issue,
                             {"kind": "Service", "name": name, "namespace": namespace}, b["service_issues"][:3])
        diff = b["app_config_diff"].get("changed") if isinstance(b["app_config_diff"], dict) else None
        if diff:  # 20-worker step 4.2
            key, change = next(iter(diff.items()))
            return Diagnosis("root_cause_found", "config_drift", f"app-config 的 {key} 被改过（{change}），只有部分 Pod 用上了新值",
                             {"kind": "ConfigMap", "name": "app-config", "namespace": namespace},
                             [f"app-config {key}: {change}", f"last_updated={b['app_config_diff'].get('last_updated')}",
                              f"pods={b['pod_created']}"[:300]])
        return None

    # ---- dependency errors: 10-api / 20-worker, then 30-redis ----
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
        """30-redis.md steps 1-8, in order."""
        redis = {"kind": "StatefulSet", "name": sop_steps.DEPENDENCY, "namespace": namespace}
        client_text = "\n".join(client_lines)
        client_ev = client_lines[-1][:300] if client_lines else ""
        if dep.get("desired_replicas") == 0:
            return Diagnosis("root_cause_found", "dependency_down", "Redis 的副本数为 0，没有在运行", redis,
                             [f"statefulset/redis desired=0 ready={dep.get('ready_replicas')}", client_ev])
        if dep.get("mount_failure"):
            msg = dep["mount_failures"][0]
            _key, kind, obj = dep["mount_failure"]
            return Diagnosis("root_cause_found", "dependency_config_error", f"redis 引用的 {kind} {obj} 里缺少键，卷挂载失败",
                             {"kind": kind, "name": obj, "namespace": namespace}, [f"FailedMount: {msg[:250]}"])
        if dep.get("service_issues"):
            return Diagnosis("root_cause_found", "service_misconfigured", dep["service_issues"][0],
                             {"kind": "Service", "name": sop_steps.DEPENDENCY, "namespace": namespace},
                             [*dep["service_issues"][:2], client_ev])
        diff = dep.get("config_diff") or {}
        limit = _size_bytes(diff.get("maxmemory"))
        if limit is not None and limit < 192 * 1024 ** 2 and OOM.search(client_text):
            return Diagnosis("root_cause_found", "dependency_memory_full", f"redis-config 的 maxmemory 被改成 {diff['maxmemory']}",
                             redis, [f"redis-config: maxmemory {diff['maxmemory']}", client_ev])
        if diff.get("auth_override"):
            return Diagnosis("root_cause_found", "dependency_config_error", "redis-config 里多了认证配置，覆盖了 Secret 中的密码",
                             redis, [f"redis-config: {diff['auth_override'][0]}", client_ev])
        if dep.get("probe_auth_failures"):  # step 5: config file matches baseline, but Redis's own probe cannot auth
            return Diagnosis("root_cause_found", "dependency_config_error", "Redis 的密码在运行时被改过（配置文件与基线一致）",
                             redis, [dep["probe_auth_failures"][0][:300], "redis-config 与基线一致", client_ev])
        if AUTH.search(client_text) and (dep.get("ready_replicas") or 0) >= 1:  # step 6: only some clients fail
            return Diagnosis("root_cause_found", "dependency_auth_failed",
                             "Secret redis-auth 的密码被轮换，新起的 Pod 用新密码，Redis 仍用旧密码",
                             {"kind": "Secret", "name": "redis-auth", "namespace": namespace},
                             [client_ev, f"redis ready={dep.get('ready_replicas')}", f"client pods={dep.get('client_pods')}"[:300]])
        spec = dep.get("spec") or {}
        if spec.get("cpu_low") and TIMEOUT.search(client_text):  # step 7
            return Diagnosis("root_cause_found", "cpu_limit_too_low", f"redis 的 CPU limit {spec.get('cpu_limit')} 过低，请求超时",
                             redis, [f"limits.cpu={spec.get('cpu_limit')}", client_ev])
        if OOM.search(client_text):  # step 8: maxmemory unchanged, data grew to the limit
            return Diagnosis("root_cause_found", "dependency_memory_full", "Redis 已用内存涨到 maxmemory（上限没变），写入被拒",
                             redis, [client_ev, f"redis-config: maxmemory {diff.get('maxmemory')}"])
        return None

    # ---- abnormal workload branches: 01-pod-common step 3 ----
    def _decide_branch(self, ab, ev: dict, namespace: str, tools, overview) -> Diagnosis:
        obj = {"kind": ab.kind, "name": ab.name, "namespace": namespace}
        containers = (ev.get("workload") or {}).get("containers") or [{}]
        spec = containers[0]
        pod_c = ((ev.get("pod") or {}).get("containers") or [{}])[0]
        events_text = "\n".join(e.get("message", "") for e in (ev.get("events") or {}).get("events", []))
        logs_lines = (ev.get("previous_logs") or ev.get("logs") or {}).get("lines", [])
        logs = "\n".join(logs_lines)

        if ab.branch == "image":
            return Diagnosis("root_cause_found", "image_unavailable", f"镜像 {spec.get('image')} 不可用",
                             obj, [f"image={spec.get('image')}", f"waiting={pod_c.get('waiting_reason')}"])
        if ab.name == sop_steps.DEPENDENCY:  # the dependency itself is abnormal: 30-redis in order
            decided = self._decide_dependency(sop_steps.follow_dependency(tools, overview), logs_lines, namespace)
            if decided:
                return decided
        if ab.branch == "scaled_zero":
            return Diagnosis("root_cause_found", "scaled_to_zero", f"{ab.kind}/{ab.name} 的副本数被缩成 0",
                             obj, [f"{ab.kind.lower()}/{ab.name} desired=0"])
        if ab.branch == "mount":
            found = sop_steps.mount_failure(events_text, ev.get("workload") or {})
            if found:
                key, kind, name = found
                return Diagnosis("root_cause_found", "invalid_config_value", f"{ab.name} 引用的 {kind} {name} 缺少键 {key}",
                                 {"kind": kind, "name": name, "namespace": namespace},
                                 [next(ln for ln in events_text.splitlines() if sop_steps.MOUNT_FAIL.search(ln))[:300]])
        if ab.branch == "not_ready" and sop_steps.has_dependency_error(logs):  # 3d -> component SOP, dependency step
            lines = [ln for ln in logs_lines if sop_steps.has_dependency_error(ln)]
            return self._dependency(ab.kind, ab.name, ev.get("workload") or {}, logs, lines, namespace, tools, overview)
        if pod_c.get("last_terminated_reason") == "OOMKilled":
            limit = ((spec.get("resources") or {}).get("limits") or {}).get("memory")
            return Diagnosis("root_cause_found", "memory_limit_too_low", f"内存限制 {limit} 过低，容器被 OOMKilled",
                             obj, ["lastState.terminated.reason=OOMKilled", f"limits.memory={limit}"])
        start_text = "\n".join([logs, pod_c.get("waiting_message") or "", events_text])
        if COMMAND_ERR.search("\n".join([pod_c.get("waiting_message") or "", events_text])):  # never started: 3c-2
            line = next(ln for ln in start_text.splitlines() if COMMAND_ERR.search(ln))
            return Diagnosis("root_cause_found", "wrong_command", f"启动命令错误：{spec.get('command')}",
                             obj, [line[:300], f"command={spec.get('command')}"])
        if PROBE_ERR.search(events_text):  # 3c-3
            line = next(ln for ln in events_text.splitlines() if PROBE_ERR.search(ln))
            probe = spec.get("livenessProbe") if "Liveness" in line else spec.get("readinessProbe")
            return Diagnosis("root_cause_found", "probe_misconfigured", "健康检查探针失败导致重启或不就绪",
                             obj, [line[:300], f"probe={probe}"])
        if CONFIG_ERR.search(logs):  # 3c-4
            line = next(ln for ln in logs.splitlines() if CONFIG_ERR.search(ln))
            return Diagnosis("root_cause_found", "invalid_config_value", "启动时配置校验失败", obj,
                             [line[:300]], ["需要对照环境变量确认具体配置项"])
        if COMMAND_ERR.search(logs):
            line = next(ln for ln in logs.splitlines() if COMMAND_ERR.search(ln))
            return Diagnosis("root_cause_found", "wrong_command", f"启动命令错误：{spec.get('command')}",
                             obj, [line[:300], f"command={spec.get('command')}"])
        return Diagnosis("undetermined", "other", f"{ab.kind}/{ab.name} 异常但规则无法归类", obj,
                         ab.reasons[:5], ["规则未覆盖该异常"])
