"""End-to-end offline tests: fake cluster + (for LLM arms) scripted fake model + real grader and real answers."""
import json

import pytest
from fake_cluster import NS, SCENARIO_STATE, T1, FakeCluster

from triage.agents import CodeSopAgent, RuleBaselineAgent, TextSopAgent
from triage.budget import Budget
from triage.grader import grade
from triage.kube import FakeRunner
from triage.lifecycle import load_scenarios
from triage.llm import ChatResult, FakeLLM
from triage.tools import ReadOnlyTools, Trace

SCENARIOS = {s["id"]: s for s in load_scenarios(split="all")}


def make_tools(state, budget=None):
    cluster = FakeCluster(state)
    runner = FakeRunner(cluster.handle)
    return ReadOnlyTools(runner, NS, budget or Budget(), Trace(), events_since="2026-11-20T10:01:00Z"), runner


def call(_tool, **args):
    return {"id": f"call_{_tool}_{len(json.dumps(args))}", "name": _tool, "arguments": args}


# ---------------- rule baseline ----------------
RULE_EXPECTED_CORRECT = {"S01", "S02", "S03", "S04", "S05", "S06", "S07", "S10"}


@pytest.mark.parametrize("sid", sorted(SCENARIO_STATE))
def test_rule_baseline_on_every_scenario(sid):
    tools, _ = make_tools(SCENARIO_STATE[sid])
    result = RuleBaselineAgent().diagnose(SCENARIOS[sid]["symptom"], NS, tools)
    g = grade(result.diagnosis, SCENARIOS[sid]["answer"])
    assert g["correct"] == (sid in RULE_EXPECTED_CORRECT), (sid, result.diagnosis, g)


def test_rule_baseline_misses_sop_gaps_as_expected():
    """S08 (no permission, other namespace) and S09 (Service selector) are outside the rules: the baseline
    reports no fault. That is the point of these scenarios, not a bug."""
    for sid in ("S08", "S09"):
        tools, _ = make_tools(SCENARIO_STATE[sid])
        assert RuleBaselineAgent().diagnose(SCENARIOS[sid]["symptom"], NS, tools).diagnosis.status == "no_fault_found"


def test_stale_events_are_filtered_by_injection_time():
    tools, _ = make_tools("healthy")
    events = tools.get_events()["events"]
    assert all(e["time"] >= "2026-11-20T10:01:00Z" for e in events)
    assert not any("old event" in e["message"] for e in events)


# ---------------- text SOP + LLM ----------------
def test_text_sop_agent_tool_loop_and_isolation():
    diag = {"status": "root_cause_found", "cause_category": "memory_limit_too_low", "cause": "worker 内存限制 16Mi 过低",
            "object": {"kind": "Deployment", "name": "worker"}, "evidence": ["OOMKilled", "limits.memory=16Mi"], "unknowns": []}
    llm = FakeLLM([
        ChatResult("", [call("overview")]),
        ChatResult("", [call("get_workload", kind="deployment", name="worker")]),
        ChatResult("", [call("submit_diagnosis", **diag)]),
    ])
    tools, runner = make_tools("oom")
    result = TextSopAgent(llm).diagnose(SCENARIOS["S04"]["symptom"], NS, tools)
    assert grade(result.diagnosis, SCENARIOS["S04"]["answer"])["correct"]
    assert tools.budget.model_calls == 3 and tools.budget.tool_calls == 2
    prompt = json.dumps(llm.seen[0], ensure_ascii=False)
    assert "K8s 排查 SOP 总则" in prompt and "redis 排查 SOP" in prompt          # same SOP as the other arms
    assert "S04" not in prompt and "16Mi" not in prompt  # no scenario id or answer leaks into the prompt
    assert all(c[c.index("get") if "get" in c else c.index("logs")] in ("get", "logs") for c in runner.calls)


def test_text_sop_agent_nudges_once_then_parses_plain_answer():
    llm = FakeLLM([ChatResult("我觉得没问题"), ChatResult('{"status": "no_fault_found", "cause_category": "no_fault", '
                                                          '"cause": "无", "object": {}, "evidence": ["all ready"], "unknowns": []}')])
    tools, _ = make_tools("healthy")
    result = TextSopAgent(llm).diagnose(SCENARIOS["S07"]["symptom"], NS, tools)
    assert result.diagnosis.status == "no_fault_found" and result.stop_reason == "answered_without_tool"


def test_forbidden_namespace_surfaces_as_tool_error():
    tools, _ = make_tools("healthy")
    out = tools.overview(namespace="rec-feature")
    assert out["error"] == "forbidden"


# ---------------- code SOP + LLM ----------------
def test_code_sop_uses_one_judgment_call_on_a_matched_branch():
    def judge(messages):
        evidence = messages[-1]["content"]
        assert "PROCESSING_DELAY_MS must be within" in evidence  # previous logs were collected by code
        return ChatResult(json.dumps({"status": "root_cause_found", "cause_category": "invalid_config_value",
                                      "cause": "PROCESSING_DELAY_MS=20000 超出范围", "object": {"kind": "Deployment", "name": "worker"},
                                      "evidence": ["ValueError: PROCESSING_DELAY_MS must be within 0..10000", "PROCESSING_DELAY_MS=20000"],
                                      "unknowns": []}, ensure_ascii=False))
    llm = FakeLLM([judge])
    tools, _ = make_tools("config")
    result = CodeSopAgent(llm).diagnose(SCENARIOS["S03"]["symptom"], NS, tools)
    assert grade(result.diagnosis, SCENARIOS["S03"]["answer"])["correct"]
    assert tools.budget.model_calls == 1
    assert {"overview", "get_workload", "get_events", "get_logs"} <= {e["name"] for e in tools.trace.events if e["type"] == "tool"}


def test_code_sop_falls_back_to_bounded_free_investigation_when_sop_has_no_branch():
    llm = FakeLLM([
        ChatResult('{"decision": "investigate", "reason": "Pod 正常但调用方连不上，可能是 Service 层"}'),
        ChatResult("", [call("get_service", name="api")]),
        ChatResult("", [call("submit_diagnosis", status="root_cause_found", cause_category="other",
                             cause="Service api 的 selector 是 app=api-v2，选不中任何 Pod，endpoints 为空",
                             object={"kind": "Service", "name": "api"}, evidence=["selector app=api-v2", "ready_endpoints=[]"],
                             unknowns=[])]),
    ])
    tools, _ = make_tools("selector")
    result = CodeSopAgent(llm).diagnose(SCENARIOS["S09"]["symptom"], NS, tools)
    g = grade(result.diagnosis, SCENARIOS["S09"]["answer"])
    assert g["correct"], (result.diagnosis, g)
    assert "free_investigation" in result.notes
    assert tools.budget.model_calls == 3  # child budget usage absorbed back into the parent


def test_code_sop_accepts_no_fault_on_healthy_cluster():
    llm = FakeLLM([ChatResult('{"decision": "no_fault"}')])
    tools, _ = make_tools("healthy")
    result = CodeSopAgent(llm).diagnose(SCENARIOS["S07"]["symptom"], NS, tools)
    assert grade(result.diagnosis, SCENARIOS["S07"]["answer"])["correct"]


def test_code_sop_reports_undetermined_for_forbidden_namespace_via_free_mode():
    llm = FakeLLM([
        ChatResult('{"decision": "investigate", "reason": "症状在 rec-feature 命名空间"}'),
        ChatResult("", [call("overview", namespace="rec-feature")]),
        ChatResult("", [call("submit_diagnosis", status="undetermined", cause_category="other", cause="无法读取 rec-feature",
                             object={}, evidence=["forbidden: cannot list pods in rec-feature"],
                             unknowns=["缺少 rec-feature 命名空间的读权限"])]),
    ])
    tools, _ = make_tools("healthy")
    result = CodeSopAgent(llm).diagnose(SCENARIOS["S08"]["symptom"], NS, tools)
    assert grade(result.diagnosis, SCENARIOS["S08"]["answer"])["correct"]


# ---------------- budget and no-progress ----------------
def test_repeated_identical_calls_stop_the_run():
    repeat = [ChatResult("", [call("overview")]) for _ in range(10)]
    tools, _ = make_tools("healthy", Budget(max_duplicate_calls=2, max_model_calls=20))
    result = TextSopAgent(FakeLLM(repeat)).diagnose("x", NS, tools)
    assert result.diagnosis.status == "budget_exhausted"
    assert result.stop_reason == "no_progress_repeated_calls"


def test_tool_call_cap_and_deadline():
    tools, _ = make_tools("healthy", Budget(max_tool_calls=1))
    llm = FakeLLM([ChatResult("", [call("overview")]), ChatResult("", [call("get_events")])])
    assert TextSopAgent(llm).diagnose("x", NS, tools).stop_reason == "max_tool_calls"
    tools, _ = make_tools("healthy", Budget(total_seconds=-1))
    assert RuleBaselineAgent().diagnose("x", NS, tools).stop_reason == "deadline"


def test_runner_record_has_cost_budget_and_trace():
    from triage.runner import diagnose_once
    cluster = FakeCluster("oom")
    llm = FakeLLM([ChatResult(json.dumps({"status": "root_cause_found", "cause_category": "memory_limit_too_low",
                                          "cause": "内存限制过低", "object": {"kind": "Deployment", "name": "worker"},
                                          "evidence": ["OOMKilled"], "unknowns": []}))])
    rec = diagnose_once("code", llm, FakeRunner(cluster.handle), NS, "worker 一直在重启", "2026-11-20T10:01:00Z", 0.30, 1.20)
    assert rec["diagnosis"]["cause_category"] == "memory_limit_too_low"
    assert rec["usage"]["prompt_tokens"] == 1000 and rec["cost_usd"] == round(1000 / 1e6 * 0.30 + 100 / 1e6 * 1.20, 5)
    assert rec["budget"]["model_calls"] == 1 and rec["trace"]
    rule = diagnose_once("rule", None, FakeRunner(FakeCluster("oom").handle), NS, "x", "2026-11-20T10:01:00Z", 0.30, 1.20)
    assert rule["usage"]["prompt_tokens"] == 0 and rule["budget"]["model_calls"] == 0
