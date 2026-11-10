"""End-to-end test of the experiment loop (runner.run_matrix) with a fake environment lifecycle,
a fake cluster whose state follows the injected scenario, a scripted model, and the real grader/report."""
import json

from fake_cluster import NS, SCENARIO_STATE, FakeCluster

from triage.budget import Budget
from triage.lifecycle import EnvironmentError_, load_scenarios
from triage.llm import ChatResult, FakeLLM
from triage.report import render
from triage.runner import run_matrix

SC = {s["id"]: s for s in load_scenarios(split="all")}


class Switch:
    """Diagnoser-side kubectl: answers from the FakeCluster of whatever scenario is currently injected."""

    def __init__(self):
        self.cluster = FakeCluster("healthy")

    def run(self, args, timeout=30.0):
        return self.cluster.handle(list(args))


class FakeLifecycle:
    def __init__(self, switch, unhealthy=False, confirm_fails=(), reset_fails=()):
        self.switch, self.unhealthy = switch, unhealthy
        self.confirm_fails, self.reset_fails = set(confirm_fails), set(reset_fails)
        self.calls = []

    def verify_healthy(self, log, phase="verify_healthy", wait_s=0):
        self.calls.append("verify")
        log.add(phase, not self.unhealthy)
        if self.unhealthy:
            raise EnvironmentError_("verify_healthy: api 1/2")

    def precheck(self, sc, log):
        self.calls.append("precheck")

    def inject(self, sc, log):
        self.calls.append(f"inject:{sc['id']}")
        self.switch.cluster = FakeCluster(SCENARIO_STATE[sc["id"]])
        return "2026-11-20T10:01:00Z"

    def confirm(self, sc, log):
        self.calls.append("confirm")
        if sc["id"] in self.confirm_fails:
            raise EnvironmentError_("fault did not take effect")

    def reset(self, sc, log):
        self.calls.append(f"reset:{sc['id']}")
        self.switch.cluster = FakeCluster("healthy")
        if sc["id"] in self.reset_fails:
            raise EnvironmentError_("verify_recovered: worker 0/1")


def oom_judge(messages):
    assert "OOMKilled" in messages[-1]["content"]
    return ChatResult(json.dumps({"status": "root_cause_found", "cause_category": "memory_limit_too_low",
                                  "cause": "内存限制过低", "object": {"kind": "Deployment", "name": "worker"},
                                  "evidence": ["OOMKilled", "limits.memory=16Mi"], "unknowns": []}))


def read(out):
    return [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]


def test_full_matrix_writes_graded_records_and_report(tmp_path):
    sw = Switch()
    out = tmp_path / "r.jsonl"
    llm = FakeLLM([oom_judge, ChatResult('{"decision": "no_fault"}')])
    code = run_matrix(FakeLifecycle(sw), [SC["S04"], SC["S07"]], ["rule", "code"], 1, llm, sw, NS, out,
                      budget_factory=Budget)
    assert code == 0
    recs = read(out)
    assert [(r["scenario"], r["arm"]) for r in recs] == [("S04", "rule"), ("S04", "code"), ("S07", "rule"), ("S07", "code")]
    assert all(r["grade"]["correct"] for r in recs), [r["grade"] for r in recs]
    assert recs[1]["usage"]["prompt_tokens"] == 1000 and recs[0]["usage"]["prompt_tokens"] == 0
    assert all(r["versions"]["sop/triage_sop.md"] for r in recs)
    text = render(recs)
    assert "| S04 |" in text and "环境失败 0 次" in text


def test_fault_not_taking_effect_is_recorded_reset_and_the_run_continues(tmp_path):
    sw = Switch()
    lc = FakeLifecycle(sw, confirm_fails={"S01"})
    out = tmp_path / "r.jsonl"
    assert run_matrix(lc, [SC["S01"], SC["S03"]], ["rule"], 1, None, sw, NS, out, budget_factory=Budget) == 0
    recs = read(out)
    assert recs[0]["env_failure"] and "grade" not in recs[0]
    assert recs[1]["grade"]["correct"]
    assert "reset:S01" in lc.calls  # injected, so it must be reset even though confirmation failed


def test_reset_failure_stops_the_batch(tmp_path):
    sw = Switch()
    out = tmp_path / "r.jsonl"
    code = run_matrix(FakeLifecycle(sw, reset_fails={"S01"}), [SC["S01"], SC["S03"]], ["rule"], 1, None, sw, NS, out,
                      budget_factory=Budget)
    assert code == 2
    recs = read(out)
    assert len(recs) == 1 and recs[0]["reset_failure"]


def test_unhealthy_environment_stops_before_injecting(tmp_path):
    sw = Switch()
    lc = FakeLifecycle(sw, unhealthy=True)
    out = tmp_path / "r.jsonl"
    assert run_matrix(lc, [SC["S01"]], ["rule"], 1, None, sw, NS, out, budget_factory=Budget) == 2
    assert not any(c.startswith(("inject", "reset")) for c in lc.calls)
    assert read(out)[0]["env_failure"].startswith("verify_healthy")
