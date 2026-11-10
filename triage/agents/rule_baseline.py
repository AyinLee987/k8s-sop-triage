"""Arm 1 — rule baseline: deterministic collection + fixed rules from the SOP. No LLM.
Answers: "do these faults even need a model?" """
from __future__ import annotations

import re

from triage.agents import sop_steps
from triage.agents.base import Agent, AgentResult, short
from triage.schema import Diagnosis

CONFIG_ERR = re.compile(r"must be|ValueError|invalid value|out of range", re.IGNORECASE)
COMMAND_ERR = re.compile(r"No module named|not found|executable file|exec format", re.IGNORECASE)
PROBE_ERR = re.compile(r"(Liveness|Readiness|Startup) probe failed", re.IGNORECASE)
UNREACHABLE = re.compile(r"ConnectionError|Name or service not known|connection refused|Temporary failure in name resolution", re.IGNORECASE)
AUTH = re.compile(r"AuthenticationError|WRONGPASS|invalid password|NOAUTH|auth", re.IGNORECASE)


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
            return AgentResult(self._decide_branch(ab, ev, namespace))
        app = sop_steps.collect_app_logs(tools, overview)
        for ref, lines in app["error_lines"].items():
            kind, _, name = ref.partition("/")
            text = "\n".join(lines)
            obj = {"kind": kind, "name": name, "namespace": namespace}
            if AUTH.search(text) and not UNREACHABLE.search(text):
                return AgentResult(Diagnosis("root_cause_found", "dependency_auth_failed",
                                             f"{ref} 连接依赖时认证失败", obj, lines[-3:]))
            if UNREACHABLE.search(text):
                return AgentResult(Diagnosis("root_cause_found", "dependency_unreachable",
                                             f"{ref} 无法连接依赖服务", obj, lines[-3:]))
        checked = [f"{w['kind']}/{w['name']} {w.get('ready')}/{w.get('desired')}" for w in overview.get("workloads", [])]
        unknowns = [f"日志中有未归类的错误：{list(app['error_lines'])}"] if app["error_lines"] else []
        return AgentResult(Diagnosis("no_fault_found", "no_fault", "按 SOP 检查未发现故障", None,
                                     ["工作负载就绪情况：" + "; ".join(checked), "各工作负载最近日志无已知错误"],
                                     unknowns))

    def _decide_branch(self, ab, ev: dict, namespace: str) -> Diagnosis:
        obj = {"kind": ab.kind, "name": ab.name, "namespace": namespace}
        containers = (ev.get("workload") or {}).get("containers") or [{}]
        spec = containers[0]
        pod_c = (ev["pod"].get("containers") or [{}])[0]
        events_text = "\n".join(e.get("message", "") for e in (ev.get("events") or {}).get("events", []))
        logs = "\n".join((ev.get("previous_logs") or ev.get("logs") or {}).get("lines", []))

        if ab.branch == "image":
            return Diagnosis("root_cause_found", "image_unavailable", f"镜像 {spec.get('image')} 不可用",
                             obj, [f"image={spec.get('image')}", f"waiting={pod_c.get('waiting_reason')}"])
        if ab.branch == "not_ready":  # SOP 3c: dependency errors in logs win over the probe symptom
            if AUTH.search(logs) and not UNREACHABLE.search(logs):
                line = next(ln for ln in logs.splitlines() if AUTH.search(ln))
                return Diagnosis("root_cause_found", "dependency_auth_failed", "依赖认证失败导致就绪探针失败", obj, [line[:300]])
            if UNREACHABLE.search(logs):
                line = next(ln for ln in logs.splitlines() if UNREACHABLE.search(ln))
                return Diagnosis("root_cause_found", "dependency_unreachable", "依赖不可达导致就绪探针失败", obj, [line[:300]])
        if pod_c.get("last_terminated_reason") == "OOMKilled":
            limit = ((spec.get("resources") or {}).get("limits") or {}).get("memory")
            return Diagnosis("root_cause_found", "memory_limit_too_low", f"内存限制 {limit} 过低，容器被 OOMKilled",
                             obj, ["lastState.terminated.reason=OOMKilled", f"limits.memory={limit}"])
        if CONFIG_ERR.search(logs):
            line = next(ln for ln in logs.splitlines() if CONFIG_ERR.search(ln))
            return Diagnosis("root_cause_found", "invalid_config_value", "启动时配置校验失败", obj,
                             [line[:300]], ["需要对照环境变量确认具体配置项"])
        start_text = "\n".join([logs, pod_c.get("waiting_message") or "", events_text])
        if COMMAND_ERR.search(start_text):  # SOP 3b-2/3b-3: logs, or the waiting message/events when it never started
            line = next(ln for ln in start_text.splitlines() if COMMAND_ERR.search(ln))
            return Diagnosis("root_cause_found", "wrong_command", f"启动命令错误：{spec.get('command')}",
                             obj, [line[:300], f"command={spec.get('command')}"])
        if PROBE_ERR.search(events_text):
            line = next(ln for ln in events_text.splitlines() if PROBE_ERR.search(ln))
            return Diagnosis("root_cause_found", "probe_misconfigured", "健康检查探针失败导致重启或不就绪",
                             obj, [line[:300]])
        return Diagnosis("undetermined", "other", f"{ab.kind}/{ab.name} 异常但规则无法归类", obj,
                         ab.reasons[:5], ["规则未覆盖该异常"])
