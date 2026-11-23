"""The anchoring experiment's prompts and its review flow, without a cluster or a model."""
import json
import sys
import types

import pytest

from triage import remediate
from triage.lifecycle import REPO, load_scenarios

sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tests"))
import anchoring_pilot as ap  # noqa: E402
import holmes_pilot  # noqa: E402
from test_loop_e2e import Sim  # noqa: E402

SC = {s["id"]: s for s in load_scenarios(split="all", source="all")}
OBS = {"since": "x", "seconds": 45.0, "requests": 100, "slow_180ms": 70, "share_slow": 0.7, "p50_ms": 400, "p90_ms": 900, "p99_ms": 1200,
       "http_500": 0, "checkout_attempts": 2, "checkout_failed": 0, "card_rejected": 0, "resolved": False}


def test_prompts_of_the_four_arms():
    sc = SC["CV05"]
    base = holmes_pilot.build_prompt(sc, "boutique", "prod", OBS)
    assert ap.build_prompt("base", sc, "boutique", OBS) == base == ap.build_prompt("m5", sc, "boutique", OBS)
    m2, m6 = ap.build_prompt("m2", sc, "boutique", OBS), ap.build_prompt("m6", sc, "boutique", OBS)
    # m2 changes the procedure and keeps the answer format; m6 keeps the procedure and changes the answer format
    assert "第一步：只整理发现" in m2 and "未解释" in m2 and m2.endswith(holmes_pilot.FORMAT)
    assert "symptoms_explained" in m6 and "independent_cause" in m6 and holmes_pilot.FORMAT not in m6 and "第一步" not in m6
    for p in (m2, m6):   # the same incident description as the base prompt, and nothing about the answer
        assert p.startswith(base.split("请排查根因")[0]) and sc["answer"]["object"]["name"] not in p.split("请排查根因")[1]
    with pytest.raises(ValueError):
        ap.build_prompt("m9", sc, "boutique", OBS)


def test_senior_verdict_and_second_prompt():
    assert ap.verdict_of('好的。\n```json\n{"verdict": "accept", "biases": [], "unexplained": [], "checks": []}\n```') == "accept"
    assert ap.verdict_of('```json\n{"verdict": "reinvestigate", "checks": ["x"]}\n```') == "reinvestigate"
    assert ap.verdict_of("没有代码块") == "reinvestigate"          # an unreadable review does not clear the diagnosis
    first = '分析……\n```json\n{"root_causes": [{"object": "frontend", "cause": "cpu"}], "explanation": "e"}\n```'
    p = ap.second_prompt(SC["CV05"], "boutique", OBS, first, "质疑内容", "资深意见")
    assert '"object": "frontend"' in p and "分析……" not in p      # the conclusion, not the first run's reasoning
    assert "质疑内容" in p and "资深意见" in p and p.endswith(holmes_pilot.FORMAT)


def test_test_scenarios_are_refused(monkeypatch):
    monkeypatch.setattr(ap, "load_dotenv", lambda: None)
    monkeypatch.setattr(sys, "argv", ["anchoring_pilot.py", "--scenarios", "CV05,CT08"])
    with pytest.raises(SystemExit) as e:
        ap.main()
    assert "CT08" in str(e.value) and "CV05" not in str(e.value)


@pytest.mark.parametrize("verdict,calls", [("accept", 1), ("reinvestigate", 2)])
def test_review_flow_on_a_simulated_cluster(tmp_path, monkeypatch, verdict, calls):
    asked, reviews = [], []

    def fake_holmes(cmd, **kw):
        asked.append(cmd[2])
        with open(cmd[cmd.index("--json-output-file") + 1], "w", encoding="utf-8") as fh:
            json.dump({"result": f"第 {len(asked)} 次\n```json\n" + json.dumps({"root_causes": [{"object": f"svc{len(asked)}", "cause": "c"}]}) + "\n```",
                       "total_tokens": 1000, "num_llm_calls": 3}, fh)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_reviewer(system, user, model):
        reviews.append((system, user))
        return ("质疑" if system == ap.DEVIL else '```json\n{"verdict": "%s", "checks": []}\n```' % verdict), 50

    observe = remediate.observe
    monkeypatch.setattr(remediate, "observe", lambda lc, since, **kw: observe(lc, since, **{**kw, "sleep": lambda s: None, "min_seconds": 0}))
    monkeypatch.setattr(ap.time, "sleep", lambda s: None)
    monkeypatch.setattr(ap.subprocess, "run", fake_holmes)
    monkeypatch.setattr(ap, "ask_reviewer", fake_reviewer)
    monkeypatch.setattr(ap, "REPO", tmp_path)
    out = tmp_path / "BH01-m5-r1.json"
    rec = ap.run_one(SC["BH01"], "m5", out, types.SimpleNamespace(max_steps=5, metrics=False), {}, "m", Sim())
    assert len(asked) == calls and [s["stage"] for s in rec["stages"]] == ["agent", "review", "agent2"][:calls + 1]
    assert len(reviews) == 2 and "第 1 次" in reviews[0][1] and "质疑" in reviews[1][1]      # devil sees the report, senior sees both
    final = json.loads(out.read_text(encoding="utf-8"))
    assert final["review"]["verdict"] == verdict and f"第 {calls} 次" in final["result"] and "第 1 次" in final["first_result"]
    assert rec["root_causes"] == [{"object": f"svc{calls}", "cause": "c"}] and rec["total_tokens"] == 1000 * calls + 100
    if calls == 2:
        assert "质疑" in asked[1] and '"object": "svc1"' in asked[1] and asked[0] == holmes_pilot.build_prompt(SC["BH01"], "boutique", "prod", rec["before"])
