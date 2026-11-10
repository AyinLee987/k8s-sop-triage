"""Common agent interface. Agents receive only: the symptom text, the default namespace and a ReadOnlyTools.
They never see scenario ids, inject steps or answers."""
from __future__ import annotations

from dataclasses import dataclass, field

from triage.budget import BudgetExceeded
from triage.schema import Diagnosis


@dataclass
class AgentResult:
    diagnosis: Diagnosis
    stop_reason: str = "submitted"
    notes: list[str] = field(default_factory=list)


class Agent:
    name = "base"
    uses_llm = False

    def __init__(self, llm=None):
        self.llm = llm

    def diagnose(self, symptom: str, namespace: str, tools) -> AgentResult:
        try:
            return self._diagnose(symptom, namespace, tools)
        except BudgetExceeded as exc:
            return AgentResult(
                Diagnosis(status="budget_exhausted", unknowns=[f"预算耗尽或调用失败：{exc.reason}"]),
                stop_reason=exc.reason,
            )

    def _diagnose(self, symptom: str, namespace: str, tools) -> AgentResult:  # pragma: no cover
        raise NotImplementedError

    def describe(self) -> dict:
        return {"arm": self.name, "llm": self.llm.describe() if self.llm else None}


def short(obj, limit: int = 400) -> str:
    import json
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"
