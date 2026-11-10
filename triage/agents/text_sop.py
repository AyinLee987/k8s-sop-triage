"""Arm 2 — text SOP + LLM: the model reads the same SOP and chooses/executes every step via tools."""
from __future__ import annotations

import json

from triage.agents import sop_steps
from triage.agents.base import Agent, AgentResult
from triage.schema import DIAGNOSIS_JSON_SCHEMA, Diagnosis
from triage.tools import TOOL_SPECS

SUBMIT_TOOL = {"type": "function", "function": {
    "name": "submit_diagnosis",
    "description": "提交最终诊断。调用后排查结束。",
    "parameters": DIAGNOSIS_JSON_SCHEMA,
}}

SYSTEM_TEMPLATE = """你是 Kubernetes 排查助手，只能使用提供的只读工具，不能修改集群。
请按下面的 SOP 排查，完成后调用 submit_diagnosis 提交诊断。不要猜测：证据不足时把状态写成 undetermined。
工具调用次数和时间都有限，不要重复调用参数完全相同的工具。

{sop}"""


def initial_messages(symptom: str, namespace: str, extra_context: str | None = None,
                     sop_extra: str | None = None) -> list[dict]:
    user = f"默认命名空间：{namespace}\n用户反馈的症状：{symptom}"
    if extra_context:
        user += f"\n\n{extra_context}"
    return [
        {"role": "system", "content": SYSTEM_TEMPLATE.format(sop=sop_steps.sop_text(sop_extra))},
        {"role": "user", "content": user},
    ]


def run_tool_loop(llm, tools, messages: list[dict], budget, trace, label: str) -> AgentResult:
    """Shared ReAct-style loop; also reused by the code-SOP arm for bounded free investigation."""
    nudged = False
    while True:
        result = llm.chat(messages, budget, tools=TOOL_SPECS + [SUBMIT_TOOL], trace=trace, label=label)
        if not result.tool_calls:
            if not nudged:
                nudged = True
                messages.append({"role": "assistant", "content": result.content or ""})
                messages.append({"role": "user", "content": "请调用 submit_diagnosis 提交诊断，或继续调用工具。"})
                continue
            return AgentResult(Diagnosis.from_any(result.content), stop_reason="answered_without_tool")
        messages.append({"role": "assistant", "content": result.content or "",
                         "tool_calls": [{"id": c["id"], "type": "function",
                                         "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                                        for c in result.tool_calls]})
        for call in result.tool_calls:
            if call["name"] == "submit_diagnosis":
                return AgentResult(Diagnosis.from_any(call["arguments"]), stop_reason="submitted")
        for call in result.tool_calls:
            output = tools.dispatch(call["name"], call["arguments"])
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(output, ensure_ascii=False)})


class TextSopAgent(Agent):
    name = "text"
    uses_llm = True

    def _diagnose(self, symptom: str, namespace: str, tools) -> AgentResult:
        messages = initial_messages(symptom, namespace, sop_extra=self.sop_extra)
        return run_tool_loop(self.llm, tools, messages, tools.budget, tools.trace, label="text_sop")
