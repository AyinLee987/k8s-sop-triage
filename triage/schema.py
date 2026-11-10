"""Diagnosis output shared by all arms (five fields from the SOP, plus status)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

CAUSE_CATEGORIES = (
    "image_unavailable",
    "invalid_config_value",
    "wrong_command",
    "memory_limit_too_low",
    "probe_misconfigured",
    "dependency_unreachable",
    "dependency_auth_failed",
    "no_fault",
    "other",
)
STATUSES = ("root_cause_found", "no_fault_found", "undetermined", "budget_exhausted")


@dataclass
class Diagnosis:
    status: str
    cause_category: str = "other"
    cause: str = ""
    object: dict | None = None
    evidence: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_any(cls, data) -> "Diagnosis":
        """Parse model output leniently; anything malformed becomes `undetermined`, never an exception."""
        if isinstance(data, str):
            text = data.strip()
            if text.startswith("```"):
                text = text.strip("`")
                text = text[text.find("{"):]
            start, end = text.find("{"), text.rfind("}")
            try:
                data = json.loads(text[start:end + 1]) if start >= 0 else {}
            except json.JSONDecodeError:
                return cls(status="undetermined", unknowns=["诊断输出不是合法 JSON"], cause=data[:300])
        if not isinstance(data, dict):
            return cls(status="undetermined", unknowns=["诊断输出格式错误"])
        status = data.get("status") if data.get("status") in STATUSES else "undetermined"
        category = data.get("cause_category") if data.get("cause_category") in CAUSE_CATEGORIES else "other"
        obj = data.get("object")
        if isinstance(obj, str):
            kind, _, name = obj.partition("/")
            obj = {"kind": kind if name else "", "name": name or kind}
        if not isinstance(obj, dict) or not obj.get("name"):
            obj = None
        return cls(
            status=status,
            cause_category=category,
            cause=str(data.get("cause", ""))[:1000],
            object=obj,
            evidence=[str(x)[:500] for x in _as_list(data.get("evidence"))][:20],
            unknowns=[str(x)[:500] for x in _as_list(data.get("unknowns"))][:20],
        )


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


DIAGNOSIS_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": list(STATUSES[:3])},
        "cause_category": {"type": "string", "enum": list(CAUSE_CATEGORIES)},
        "cause": {"type": "string"},
        "object": {
            "type": "object",
            "description": "故障对象；没有故障或无法确定时给空对象 {}",
            "properties": {"kind": {"type": "string"}, "name": {"type": "string"}, "namespace": {"type": "string"}},
        },
        "evidence": {"type": "array", "items": {"type": "string"}},
        "unknowns": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "cause_category", "cause", "object", "evidence", "unknowns"],
}
