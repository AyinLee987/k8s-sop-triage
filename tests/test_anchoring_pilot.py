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


def _patch_run(monkeypatch, tmp_path, asked, reviewer):
    def fake_holmes(cmd, **kw):
        asked.append(cmd[2])
        with open(cmd[cmd.index("--json-output-file") + 1], "w", encoding="utf-8") as fh:
            json.dump({"result": f"第 {len(asked)} 次\n```json\n" + json.dumps({"root_causes": [{"object": f"svc{len(asked)}", "cause": "c"}]}) + "\n```",
                       "total_tokens": 1000, "num_llm_calls": 3}, fh)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    observe = remediate.observe
    monkeypatch.setattr(remediate, "observe", lambda lc, since, **kw: observe(lc, since, **{**kw, "sleep": lambda s: None, "min_seconds": 0}))
    slept = []
    monkeypatch.setattr(ap.time, "sleep", slept.append)
    monkeypatch.setattr(ap.subprocess, "run", fake_holmes)
    monkeypatch.setattr(ap, "ask_reviewer", reviewer)
    monkeypatch.setattr(ap, "REPO", tmp_path)
    return slept


def test_second_look_control_has_no_review(tmp_path, monkeypatch):
    asked = []

    def no_reviewer(*a):
        raise AssertionError("the re arm must not call a reviewer")

    _patch_run(monkeypatch, tmp_path, asked, no_reviewer)
    out = tmp_path / "BH01-re-r1.json"
    rec = ap.run_one(SC["BH01"], "re", out, types.SimpleNamespace(max_steps=5, metrics=False), {}, "m", Sim())
    assert len(asked) == 2 and [s["stage"] for s in rec["stages"]] == ["agent", "agent2"]
    assert '"object": "svc1"' in asked[1] and "质疑" not in asked[1] and "资深" not in asked[1] and asked[1].endswith(holmes_pilot.FORMAT)
    final = json.loads(out.read_text(encoding="utf-8"))
    assert final["review"] is None and "第 2 次" in final["result"] and rec["root_causes"] == [{"object": "svc2", "cause": "c"}]


def test_a_review_without_text_fails_the_run(tmp_path, monkeypatch):
    asked = []

    def silent(system, user, model):
        raise RuntimeError("the reviewer returned no text twice (finish_reason=length)")

    _patch_run(monkeypatch, tmp_path, asked, silent)
    rec = ap.run_one(SC["BH01"], "m5", tmp_path / "BH01-m5-r1.json", types.SimpleNamespace(max_steps=5, metrics=False), {}, "m", Sim())
    assert len(asked) == 1 and "no text" in rec["error"] and rec["stages"][-1]["verdict"] == "error"   # not counted as a result


def test_reviewer_retries_an_empty_reply_and_is_told_the_limits(monkeypatch):
    replies = []

    class FakeClient:
        def __init__(self, **kw):
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

        def create(self, **kw):
            assert kw["max_tokens"] >= 20000
            text = replies.pop(0)
            return types.SimpleNamespace(usage=types.SimpleNamespace(total_tokens=7),
                                         choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text), finish_reason="length" if not text else "stop")])

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeClient))
    monkeypatch.setattr(ap, "cfg", lambda k, d=None: d or "x")
    replies[:] = ["", "有内容"]
    assert ap.ask_reviewer("s", "u", "m") == ("有内容", 14)
    replies[:] = ["", None]
    with pytest.raises(RuntimeError, match="no text"):
        ap.ask_reviewer("s", "u", "m")
    for prompt in (ap.DEVIL, ap.SENIOR):   # reviewers must only ask for checks a read-only agent can make
        assert "只读权限" in prompt and "不能进容器执行命令" in prompt and "没有链路追踪" in prompt


def test_settle_gives_healthy_traffic_before_the_fault(tmp_path, monkeypatch):
    asked = []
    slept = _patch_run(monkeypatch, tmp_path, asked, None)
    sim = Sim()
    ap.run_one(SC["BH01"], "base", tmp_path / "BH01-base-r1.json", types.SimpleNamespace(max_steps=5, metrics=False, settle=120), {}, "m", sim)
    wipes = [i for i, c in enumerate(sim.calls) if "delete pods --all" in c]
    faults = [i for i, c in enumerate(sim.calls) if "managedFields" not in c and (" apply " in c or " set env " in c or " set resources " in c or " scale " in c or "patch service" in c)]
    assert len(wipes) == 1 and faults and wipes[0] < faults[0] and 120 in slept      # fresh pods once, the wait, and the fault after them
    sim2, n = Sim(), len(slept)
    ap.run_one(SC["BH01"], "base", tmp_path / "BH01-base-r2.json", types.SimpleNamespace(max_steps=5, metrics=False), {}, "m", sim2)
    assert sum("delete pods --all" in c for c in sim2.calls) == 1 and 120 not in slept[n:]
