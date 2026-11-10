"""Deterministic grader. Object localisation and cause are scored separately.
Anything ambiguous is flagged `needs_review` for a human instead of being auto-scored generously."""
from __future__ import annotations

from triage.schema import Diagnosis


def _norm(text: str | None) -> str:
    return (text or "").strip().lower()


def _contains_any(text: str, keywords: list[str]) -> bool:
    low = text.lower()
    return any(k.lower() in low for k in keywords)


def grade(diag: Diagnosis, answer: dict) -> dict:
    expected_status = answer["status"]
    status_ok = diag.status == expected_status
    evidence_text = " ".join(diag.evidence)
    out = {"status_ok": status_ok, "object_ok": None, "cause_ok": None, "evidence_supported": None,
           "correct": False, "needs_review": False, "stopped_at_symptom": False, "notes": []}

    if expected_status == "no_fault_found":
        out["correct"] = status_ok and diag.cause_category in ("no_fault", "other")
        if not status_ok:
            out["notes"].append(f"expected no_fault_found, got {diag.status}")
        return out

    if expected_status == "undetermined":
        unknowns = " ".join(diag.unknowns) + " " + diag.cause + " " + evidence_text
        mentions_gap = _contains_any(unknowns, answer.get("accept_unknowns_keywords", []))
        out["correct"] = status_ok and mentions_gap
        out["evidence_supported"] = mentions_gap
        if status_ok and not mentions_gap:
            out["needs_review"] = True
            out["notes"].append("status undetermined but did not name the missing access/data")
        return out

    # root_cause_found scenarios
    want = answer["object"]
    got = diag.object or {}
    got_name = _norm(got.get("name"))
    accepted = [_norm(n) for n in answer.get("accept_objects") or [want["name"]]]
    name_ok = any(got_name == n or got_name.startswith(n + "-") for n in accepted)
    kind_ok = (not got.get("kind") or _norm(got.get("kind")) in (_norm(want["kind"]), "pod")
               or (len(accepted) > 1 and got_name in accepted))  # e.g. ConfigMap redis-config accepted for a redis root cause
    out["object_ok"] = bool(name_ok and kind_ok)
    # cross-component scenarios: did the diagnosis stop at the component where the symptom showed up?
    symptom_objects = [_norm(n) for n in answer.get("symptom_objects", [])]
    out["stopped_at_symptom"] = bool(symptom_objects) and not out["object_ok"] and any(
        got_name == n or got_name.startswith(n + "-") for n in symptom_objects)
    if out["stopped_at_symptom"]:
        out["notes"].append(f"stopped at symptom component {got.get('name')} instead of {want['name']}")

    if answer["cause_category"] == "other":
        cause_ok = _contains_any(diag.cause + " " + evidence_text, answer.get("accept_cause_keywords", []))
        if cause_ok and diag.cause_category != "other":
            out["needs_review"] = True
            out["notes"].append(f"keywords matched but category={diag.cause_category}")
    else:
        cause_ok = diag.cause_category == answer["cause_category"]
        if not cause_ok and _contains_any(diag.cause, answer.get("accept_cause_keywords", [])):
            out["needs_review"] = True
            out["notes"].append(f"category {diag.cause_category} != {answer['cause_category']} but cause text matches")
    out["cause_ok"] = bool(cause_ok)
    out["evidence_supported"] = _contains_any(evidence_text, answer.get("key_evidence", [])) if answer.get("key_evidence") else None
    if out["cause_ok"] and out["evidence_supported"] is False:
        out["needs_review"] = True
        out["notes"].append("cause right but evidence does not quote any key evidence")
    out["correct"] = bool(status_ok and out["object_ok"] and out["cause_ok"])
    return out
