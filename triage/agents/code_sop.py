"""Arm 3 — code SOP + LLM: code executes the deterministic SOP steps (same collection as the rule
baseline, in parallel); the model is consulted only at three judgment points:

  J1  interpret branch evidence for an abnormal workload        -> diagnosis
  J2  interpret application-log errors / slow processing        -> diagnosis
  J3  nothing matched: accept "no fault" or investigate further -> decision
When a client shows dependency errors, code follows the SOP jump rule (client overrides, then 30-redis)
and adds that evidence before the judgment, so the model judges with both sides in view.
If a judgment returns `undetermined`, or J3 says investigate, a *bounded* free investigation runs
(the text-SOP loop on a child budget), so the code-SOP arm can still handle faults the SOP doesn't cover.
"""
from __future__ import annotations

import json

from triage.agents import sop_steps
from triage.agents.base import Agent, AgentResult, short
from triage.agents.text_sop import initial_messages, run_tool_loop
from triage.schema import Diagnosis

JUDGE_SYSTEM = """你是 Kubernetes 排查助手。程序已经按 SOP 执行了确定的步骤并收集了证据。
你的任务只是根据证据做判断，不能调用工具。只输出一个 JSON 对象，不要输出其他文字。

{sop}"""

DIAGNOSIS_INSTRUCTION = """请根据以上证据输出诊断 JSON，字段：status, cause_category, cause, object, evidence, unknowns。
evidence 必须原样引用上面证据里的内容。证据不足时 status 写 undetermined，并在 unknowns 里写明还缺什么。"""

DECISION_INSTRUCTION = """程序按 SOP 没有发现异常的工作负载，各工作负载日志里也没有已知错误。
请结合用户反馈的症状判断：输出 {"decision": "no_fault"} 表示接受"没有故障"的结论；
输出 {"decision": "investigate", "reason": "..."} 表示症状提示还有 SOP 没覆盖的问题，需要进一步排查。"""


class CodeSopAgent(Agent):
    name = "code"
    uses_llm = True

    def __init__(self, llm=None, sop_extra: str | None = None, free_tool_calls: int = 8, free_model_calls: int = 6):
        super().__init__(llm, sop_extra)
        self.free_tool_calls = free_tool_calls
        self.free_model_calls = free_model_calls

    def _diagnose(self, symptom: str, namespace: str, tools) -> AgentResult:
        trace = tools.trace
        overview = tools.overview()  # SOP step 1
        if overview.get("error") == "forbidden":
            return AgentResult(Diagnosis("undetermined", "other", "无权限读取命名空间", None,
                                         [short(overview)], [f"缺少命名空间 {namespace} 的读权限"]),
                               stop_reason="forbidden")

        abnormal = sop_steps.find_abnormal(overview)  # 01-pod-common step 2
        if abnormal:
            ab = abnormal[0]
            evidence = sop_steps.collect_branch_evidence(tools, ab)  # step 3, parallel
            trace.add(type="sop_step", name="branch", branch=ab.branch, workload=f"{ab.kind}/{ab.name}")
            bundle = {"总览中的异常": [r for a in abnormal for r in a.reasons][:12], "分支证据": evidence}
            logs = "\n".join((evidence.get("previous_logs") or evidence.get("logs") or {}).get("lines", []))
            if ab.name != sop_steps.DEPENDENCY and sop_steps.has_dependency_error(logs):
                bundle.update(self._dependency_bundle(tools, overview, evidence.get("workload") or {}))
            diag = self._judge_diagnosis(symptom, namespace, bundle, tools, label="J1_branch")
            return self._maybe_free(diag, symptom, namespace, tools, "分支证据不足")

        app = sop_steps.collect_app_logs(tools, overview)  # step 4, parallel
        if app["error_lines"]:
            ref = next(iter(app["error_lines"]))
            kind, _, name = ref.partition("/")
            workload = tools.get_workload(kind.lower(), name)
            trace.add(type="sop_step", name="app_log_errors", workload=ref)
            bundle = {"日志中的错误": app["error_lines"], "相关工作负载": workload}
            if any(sop_steps.has_dependency_error("\n".join(v)) for v in app["error_lines"].values()):
                bundle.update(self._dependency_bundle(tools, overview, workload))
            diag = self._judge_diagnosis(symptom, namespace, bundle, tools, label="J2_app_logs")
            return self._maybe_free(diag, symptom, namespace, tools, "日志错误无法归类")
        if app["slow_lines"]:  # 20-worker step 4
            ref = next(iter(app["slow_lines"]))
            kind, _, name = ref.partition("/")
            trace.add(type="sop_step", name="slow_processing", workload=ref)
            diag = self._judge_diagnosis(symptom, namespace,
                                         {"耗时异常的日志": app["slow_lines"], "相关工作负载": tools.get_workload(kind.lower(), name)},
                                         tools, label="J2_slow")
            return self._maybe_free(diag, symptom, namespace, tools, "处理耗时异常但无法归类")

        summary = {
            "工作负载": overview.get("workloads"),
            "Pod": [{k: p.get(k) for k in ("name", "phase", "ready", "restarts")} for p in overview.get("pods", [])],
            "日志": "各工作负载最近 60 行日志没有错误或重试记录",
        }
        decision = self._judge_json(symptom, namespace, summary, DECISION_INSTRUCTION, tools, label="J3_decide")
        if decision.get("decision") == "investigate":
            trace.add(type="sop_step", name="free_investigation", reason=str(decision.get("reason", ""))[:200])
            return self._free(symptom, namespace, tools,
                              f"程序已完成 SOP 第 1、2、4 步，结果：{short(summary, 1500)}\n"
                              f"没有匹配的 SOP 分支。判断理由：{decision.get('reason', '')}\n请自行排查（只读）。")
        checked = [f"{w['kind']}/{w['name']} {w.get('ready')}/{w.get('desired')}" for w in overview.get("workloads", [])]
        return AgentResult(Diagnosis("no_fault_found", "no_fault", "按 SOP 检查未发现故障", None,
                                     ["工作负载就绪情况：" + "; ".join(checked), "各工作负载最近日志无错误"], []))

    def _dependency_bundle(self, tools, overview, client_workload: dict) -> dict:
        """Jump rule from 00-index / step 5: client overrides first, then the dependency (30-redis) — collected by code."""
        tools.trace.add(type="sop_step", name="follow_dependency")
        return {"客户端是否单独覆盖了 REDIS_* 配置（00-index 跳转原则第 2 条）": sop_steps.client_redis_overrides(client_workload) or "没有覆盖",
                "依赖检查（按 30-redis.md 收集）": sop_steps.follow_dependency(tools, overview)}

    # ---- judgment points ----
    def _judge_diagnosis(self, symptom, namespace, evidence, tools, label) -> Diagnosis:
        data = self._judge_json(symptom, namespace, evidence, DIAGNOSIS_INSTRUCTION, tools, label)
        return Diagnosis.from_any(data)

    def _judge_json(self, symptom, namespace, evidence, instruction, tools, label) -> dict:
        messages = [
            {"role": "system", "content": JUDGE_SYSTEM.format(sop=sop_steps.sop_text(self.sop_extra))},
            {"role": "user", "content": (f"默认命名空间：{namespace}\n用户反馈的症状：{symptom}\n\n"
                                         f"程序收集的证据：\n{json.dumps(evidence, ensure_ascii=False)[:12000]}\n\n{instruction}")},
        ]
        result = self.llm.chat(messages, tools.budget, tools=None, trace=tools.trace, label=label)
        text = result.content or ""
        start, end = text.find("{"), text.rfind("}")
        try:
            return json.loads(text[start:end + 1]) if start >= 0 else {}
        except json.JSONDecodeError:
            return {}

    # ---- bounded free investigation ----
    def _maybe_free(self, diag: Diagnosis, symptom, namespace, tools, why: str) -> AgentResult:
        if diag.status != "undetermined":
            return AgentResult(diag)
        context = (f"程序按 SOP 收集证据后的初步判断是 undetermined（{why}）。"
                   f"初步诊断：{json.dumps(diag.to_dict(), ensure_ascii=False)[:1500]}\n请补充排查（只读）。")
        return self._free(symptom, namespace, tools, context)

    def _free(self, symptom, namespace, tools, context: str) -> AgentResult:
        parent = tools.budget
        child = parent.child(self.free_tool_calls, self.free_model_calls)
        tools.budget = child
        try:
            messages = initial_messages(symptom, namespace, extra_context=context, sop_extra=self.sop_extra)
            result = run_tool_loop(self.llm, tools, messages, child, tools.trace, label="free_investigation")
            result.notes.append("free_investigation")
            return result
        finally:
            parent.absorb(child)
            tools.budget = parent
