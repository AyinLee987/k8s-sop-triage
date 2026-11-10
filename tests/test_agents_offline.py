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

SCENARIOS = {s["id"]: s for s in load_scenarios(split="all", source="all")}


def make_tools(state, budget=None):
    cluster = FakeCluster(state)
    runner = FakeRunner(cluster.handle)
    return ReadOnlyTools(runner, NS, budget or Budget(), Trace(), events_since="2026-11-20T10:01:00Z"), runner


def call(_tool, **args):
    return {"id": f"call_{_tool}_{len(json.dumps(args))}", "name": _tool, "arguments": args}


# ---------------- rule baseline ----------------
RULE_EXPECTED_CORRECT = set(SCENARIO_STATE) - {"R14", "R16"}


@pytest.mark.parametrize("sid", sorted(SCENARIO_STATE))
def test_rule_baseline_on_every_scenario(sid):
    tools, _ = make_tools(SCENARIO_STATE[sid])
    result = RuleBaselineAgent().diagnose(SCENARIOS[sid]["symptom"], NS, tools)
    g = grade(result.diagnosis, SCENARIOS[sid]["answer"])
    assert g["correct"] == (sid in RULE_EXPECTED_CORRECT), (sid, result.diagnosis, g)


def test_rule_baseline_misses_sop_gaps_as_expected():
    """R16 (no permission, other namespace) and R14 (data loss: the SOP has no step for it) are outside the
    rules. That is the point of these scenarios, not a bug."""
    tools, _ = make_tools(SCENARIO_STATE["R16"])
    assert RuleBaselineAgent().diagnose(SCENARIOS["R16"]["symptom"], NS, tools).diagnosis.status == "no_fault_found"
    tools, _ = make_tools(SCENARIO_STATE["R14"])
    assert RuleBaselineAgent().diagnose(SCENARIOS["R14"]["symptom"], NS, tools).diagnosis.status == "undetermined"


def test_stale_events_are_filtered_by_injection_time():
    tools, _ = make_tools("healthy")
    events = tools.get_events()["events"]
    assert all(e["time"] >= "2026-11-20T10:01:00Z" for e in events)
    assert not any("old event" in e["message"] for e in events)


# ---------------- text SOP + LLM ----------------
def test_text_sop_agent_tool_loop_and_isolation():
    diag = {"status": "root_cause_found", "cause_category": "memory_limit_too_low", "cause": "worker 内存限制 24Mi 过低",
            "object": {"kind": "Deployment", "name": "worker"}, "evidence": ["OOMKilled", "limits.memory=24Mi"], "unknowns": []}
    llm = FakeLLM([
        ChatResult("", [call("overview")]),
        ChatResult("", [call("get_workload", kind="deployment", name="worker")]),
        ChatResult("", [call("submit_diagnosis", **diag)]),
    ])
    tools, runner = make_tools("oom")
    result = TextSopAgent(llm).diagnose(SCENARIOS["R05"]["symptom"], NS, tools)
    assert grade(result.diagnosis, SCENARIOS["R05"]["answer"])["correct"]
    assert tools.budget.model_calls == 3 and tools.budget.tool_calls == 2
    prompt = json.dumps(llm.seen[0], ensure_ascii=False)
    assert "K8s 排查 SOP 总则" in prompt and "redis 排查 SOP" in prompt          # same SOP as the other arms
    assert "R05" not in prompt and "24Mi" not in prompt  # no scenario id or answer leaks into the prompt
    assert all(c[c.index("get") if "get" in c else c.index("logs")] in ("get", "logs") for c in runner.calls)


def test_text_sop_agent_nudges_once_then_parses_plain_answer():
    llm = FakeLLM([ChatResult("我觉得没问题"), ChatResult('{"status": "no_fault_found", "cause_category": "no_fault", '
                                                          '"cause": "无", "object": {}, "evidence": ["all ready"], "unknowns": []}')])
    tools, _ = make_tools("healthy")
    result = TextSopAgent(llm).diagnose(SCENARIOS["R07"]["symptom"], NS, tools)
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
    result = CodeSopAgent(llm).diagnose(SCENARIOS["G-env_invalid-01"]["symptom"], NS, tools)
    assert grade(result.diagnosis, SCENARIOS["G-env_invalid-01"]["answer"])["correct"]
    assert tools.budget.model_calls == 1
    assert {"overview", "get_workload", "get_events", "get_logs"} <= {e["name"] for e in tools.trace.events if e["type"] == "tool"}


def test_code_sop_falls_back_to_bounded_free_investigation_when_sop_has_no_branch():
    """R14 is a deliberate SOP gap (data loss from a wrong mount path): the judgment is undetermined, so a bounded
    free investigation runs on a child budget and finds it."""
    llm = FakeLLM([
        ChatResult(json.dumps({"status": "undetermined", "cause_category": "other", "cause": "worker 有一次 ResponseError，Redis 侧没发现问题",
                               "object": {}, "evidence": ["redis_retry ResponseError"], "unknowns": ["数据为什么丢失"]}, ensure_ascii=False)),
        ChatResult("", [call("get_workload", kind="statefulset", name="redis")]),
        ChatResult("", [call("submit_diagnosis", status="root_cause_found", cause_category="other",
                             cause="redis 的数据卷挂到了 /var/lib/redis，而 redis.conf 的 dir 是 /data，重启后数据丢失",
                             object={"kind": "StatefulSet", "name": "redis"},
                             evidence=["volumeMounts data -> /var/lib/redis", "dir /data"], unknowns=[])]),
    ])
    tools, _ = make_tools("redis_mount_path")
    result = CodeSopAgent(llm).diagnose(SCENARIOS["R14"]["symptom"], NS, tools)
    g = grade(result.diagnosis, SCENARIOS["R14"]["answer"])
    assert g["correct"], (result.diagnosis, g)
    assert "free_investigation" in result.notes
    assert tools.budget.model_calls == 3  # child budget usage absorbed back into the parent


def test_code_sop_accepts_no_fault_on_healthy_cluster():
    llm = FakeLLM([ChatResult('{"decision": "no_fault"}')])
    tools, _ = make_tools("healthy")
    result = CodeSopAgent(llm).diagnose(SCENARIOS["R07"]["symptom"], NS, tools)
    assert grade(result.diagnosis, SCENARIOS["R07"]["answer"])["correct"]


def test_code_sop_reports_undetermined_for_forbidden_namespace_via_free_mode():
    llm = FakeLLM([
        ChatResult('{"decision": "investigate", "reason": "症状在 rec-feature 命名空间"}'),
        ChatResult("", [call("overview", namespace="rec-feature")]),
        ChatResult("", [call("submit_diagnosis", status="undetermined", cause_category="other", cause="无法读取 rec-feature",
                             object={}, evidence=["forbidden: cannot list pods in rec-feature"],
                             unknowns=["缺少 rec-feature 命名空间的读权限"])]),
    ])
    tools, _ = make_tools("healthy")
    result = CodeSopAgent(llm).diagnose(SCENARIOS["R16"]["symptom"], NS, tools)
    assert grade(result.diagnosis, SCENARIOS["R16"]["answer"])["correct"]


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


# ---------------- cross-component (symptom in A, root cause in B) ----------------
def test_code_sop_follows_dependency_jump_rule_before_judging():
    def judge(messages):
        evidence = messages[-1]["content"]
        assert "依赖检查（按 30-redis.md 收集）" in evidence and "targetPort=6380" in evidence
        assert "没有覆盖" in evidence  # the client override check ran first
        return ChatResult(json.dumps({"status": "root_cause_found", "cause_category": "service_misconfigured",
                                      "cause": "Service redis 的 targetPort 是 6380", "object": {"kind": "Service", "name": "redis"},
                                      "evidence": ["service/redis targetPort=6380", "redis_unavailable ConnectionError"],
                                      "unknowns": []}, ensure_ascii=False))
    tools, _ = make_tools("redis_target_port")
    result = CodeSopAgent(FakeLLM([judge])).diagnose(SCENARIOS["R04"]["symptom"], NS, tools)
    assert grade(result.diagnosis, SCENARIOS["R04"]["answer"])["correct"]
    assert any(e.get("name") == "follow_dependency" for e in tools.trace.events)


def test_code_sop_sees_redis_config_for_memory_scenario():
    def judge(messages):
        evidence = messages[-1]["content"]
        assert '"maxmemory": "192mb"' in evidence and "OutOfMemoryError" in evidence  # limit unchanged, writes rejected
        return ChatResult(json.dumps({"status": "root_cause_found", "cause_category": "dependency_memory_full",
                                      "cause": "已用内存到了 maxmemory", "object": {"kind": "StatefulSet", "name": "redis"},
                                      "evidence": ["maxmemory 192mb", "OutOfMemoryError"], "unknowns": []}))
    tools, _ = make_tools("redis_data_full")
    result = CodeSopAgent(FakeLLM([judge])).diagnose(SCENARIOS["R01"]["symptom"], NS, tools)
    g = grade(result.diagnosis, SCENARIOS["R01"]["answer"])
    assert g["correct"], g


def test_code_sop_j3_gets_baseline_comparison_and_may_answer_directly():
    def judge(messages):
        evidence = messages[-1]["content"]
        assert "与基线的对照" in evidence and "tiktok-lab -> tiktok-lab-v2" in evidence
        return ChatResult(json.dumps({"status": "root_cause_found", "cause_category": "config_drift",
                                      "cause": "app-config 的 KEY_PREFIX 改了，只有 worker 重启用上了新值",
                                      "object": {"kind": "ConfigMap", "name": "app-config"},
                                      "evidence": ["KEY_PREFIX: tiktok-lab -> tiktok-lab-v2", "last_updated 2026-11-20T10:02:00Z"],
                                      "unknowns": []}, ensure_ascii=False))
    tools, _ = make_tools("config_drift")
    result = CodeSopAgent(FakeLLM([judge])).diagnose(SCENARIOS["R11"]["symptom"], NS, tools)
    assert grade(result.diagnosis, SCENARIOS["R11"]["answer"])["correct"]
    assert tools.budget.model_calls == 1


def test_stopping_at_the_symptom_component_is_flagged():
    from triage.schema import Diagnosis
    d = Diagnosis("root_cause_found", "dependency_unreachable", "api 连不上 Redis", {"kind": "Deployment", "name": "api"},
                  ["redis_unavailable ConnectionError"])
    g = grade(d, SCENARIOS["R08"]["answer"])
    assert not g["correct"] and g["stopped_at_symptom"]
    other = Diagnosis("root_cause_found", "dependency_down", "x", {"kind": "Service", "name": "something"}, ["x"])
    assert not grade(other, SCENARIOS["R08"]["answer"])["stopped_at_symptom"]


def test_configmap_tool_reads_config_but_not_secrets():
    tools, runner = make_tools("config_drift")
    cm = tools.get_configmap("app-config")
    assert cm["data"]["KEY_PREFIX"] == "tiktok-lab-v2" and cm["last_updated"] == "2026-11-20T10:02:00Z"
    assert tools.get_configmap("redis-auth")["error"] == "kubectl_failed"  # not a ConfigMap; Secrets have no tool at all


def test_workload_summary_exposes_mounts_volumes_and_probe_timeouts_but_no_secret_values():
    tools, _ = make_tools("redis_mount_path")
    w = tools.get_workload("statefulset", "redis")
    c = w["containers"][0]
    assert "data -> /var/lib/redis" in c["volumeMounts"]
    assert "auth <- secret:redis-auth keys=['redis-auth.conf']" in w["volumes"]
    assert c["readinessProbe"]["timeoutSeconds"] == 2
    assert tools.get_pod("redis-0")["created"] == T1
