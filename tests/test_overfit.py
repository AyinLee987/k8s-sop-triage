"""Bad-case dose experiment: note ordering, injection into the prompt, end-to-end on the fake backend,
and regression detection in the report."""
import argparse
import json

from triage.experiments import overfit
from triage.lifecycle import load_scenarios
from triage.llm import ChatResult

SC = {s["id"]: s for s in load_scenarios(split="all", source="all")}


def test_note_library_is_balanced_and_never_names_holdout_scenarios():
    notes = overfit.load_notes()
    assert len(notes) == 40 and len({n["id"] for n in notes}) == 40
    holdout = {k for k, s in SC.items() if s["split"] == "holdout"}
    assert not {n["source"] for n in notes} & holdout
    assert {n["kind"] for n in notes} == set(overfit.KIND_ORDER)
    dev = {k for k, s in SC.items() if s["split"] == "dev"}
    assert {n["source"] for n in notes} <= dev | {"general"}


def test_noise_notes_do_not_hint_at_holdout_mechanisms():
    """A 'noise' note that happens to describe a holdout mechanism correctly would quietly help on holdout."""
    holdout_terms = ["envFrom", "不会自动更新", "liveness", "存活探针", "timeoutSeconds", "KEY_PREFIX", "mountPath",
                     "CPU limit", "限流", "轮换", "uvicorn"]
    for n in overfit.load_notes():
        if n["kind"] == "noise":
            assert not any(t.lower() in n["text"].lower() for t in holdout_terms), n["id"]


def test_doses_are_nested_and_mixed():
    extras = overfit.dose_extras([0, 5, 10, 40])
    assert extras[0] == (None, [])
    assert extras[10][1][:5] == extras[5][1]
    order = overfit.ordered_notes(overfit.load_notes())
    assert len({n["kind"] for n in order[:6]}) == 6  # first six cover every kind
    assert extras[40][0].count("复盘：") == 40


def test_kind_filter_for_ablations():
    extras = overfit.dose_extras([6], kinds=["specific"])
    assert all(i in {"N01", "N02", "N03", "N04", "N05", "N06"} for i in extras[6][1])


class DoseSensitiveLLM:
    """Fake model that behaves well without notes, but follows the 'stay on the first failing service' note."""

    def __init__(self):
        from triage.llm import Usage
        self.usage = Usage()
        self.prompts = []

    def describe(self):
        return {"model": "dose-sensitive-fake"}

    def chat(self, messages, budget, tools=None, trace=None, label="chat"):
        budget.charge_model()
        self.usage.add({"prompt_tokens": len(messages[0]["content"]), "completion_tokens": 50})
        system = messages[0]["content"]
        self.prompts.append(system)
        anchored = "历史排障经验" in system and "结论要落在最先报错的服务上" in system
        obj = {"kind": "Deployment", "name": "api"} if anchored else {"kind": "StatefulSet", "name": "redis"}
        cat = "dependency_unreachable" if anchored else "dependency_down"
        return ChatResult(json.dumps({"status": "root_cause_found", "cause_category": cat, "cause": "x",
                                      "object": obj, "evidence": ["redis desired=0", "ConnectionError"], "unknowns": []}))


def test_fake_backend_end_to_end_detects_regression(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(overfit, "_scenarios", lambda backend, sets: [SC["R08"]])
    llm = DoseSensitiveLLM()
    out = tmp_path / "o.jsonl"
    anchor_dose = next(i for i, n in enumerate(overfit.ordered_notes(overfit.load_notes()), 1) if n["id"] == "N13")
    args = argparse.Namespace(backend="fake", doses=f"0,{anchor_dose}", arms="code", reps=1, sets="dev", kinds=None,
                              seed=7, max_cost=5.0, out=str(out), finalize=False, llm_factory=lambda: llm)
    assert overfit.run(args) == 0
    recs = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert [r["dose"] for r in recs] == [0, anchor_dose]
    assert recs[0]["grade"]["correct"] and not recs[1]["grade"]["correct"]
    assert recs[1]["grade"]["stopped_at_symptom"]
    assert "历史排障经验" not in llm.prompts[0] and "历史排障经验" in llm.prompts[1]
    report = overfit.render_dose_report(recs)
    assert f"code / R08（dev）/ 剂量 {anchor_dose}" in report and "停在症状层" in report
    assert recs[1]["usage"]["prompt_tokens"] > recs[0]["usage"]["prompt_tokens"]  # notes cost tokens


def test_report_without_regressions_says_so():
    rec = {"experiment": "overfit", "dose": 0, "arm": "text", "scenario": "R06", "split": "dev",
           "grade": {"correct": True}, "usage": {"prompt_tokens": 10, "completion_tokens": 1}, "cost_usd": 0}
    text = overfit.render_dose_report([rec, dict(rec, dose=10)])
    assert "没有发现回退" in text and "| text | 10 | 100%（1/1）" in text


def test_fake_backend_covers_every_listed_scenario():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from fake_cluster import GENERATED_STATE, SCENARIO_STATE
    states = {**SCENARIO_STATE, **GENERATED_STATE}
    for sid in overfit.FAKE_DEV + overfit.FAKE_HOLDOUT:
        assert sid in states and sid in SC
        assert SC[sid]["split"] == ("dev" if sid in overfit.FAKE_DEV else "holdout")
