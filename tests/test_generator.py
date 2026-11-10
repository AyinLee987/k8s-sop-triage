"""Parameterized scenario generation: structure, split purity, no answer leakage, and the rule baseline on
fake-cluster versions of the new branches."""
import json

import pytest
from fake_cluster import NS, FakeCluster

from triage.agents import RuleBaselineAgent
from triage.budget import Budget
from triage.generator import TemplateError, _render, expand
from triage.grader import grade
from triage.kube import FakeRunner
from triage.lifecycle import load_scenarios
from triage.tools import ReadOnlyTools, Trace

GEN = expand()
GEN_BY_ID = {s["id"]: s for s in GEN}
ALL = load_scenarios(split="all", source="all")


def test_generated_scenarios_are_complete_and_unique():
    assert len(GEN) == 26
    assert len({s["id"] for s in ALL}) == len(ALL), "duplicate ids across handwritten and generated"
    for s in GEN:
        assert s["generated"] and s["answer"]["cause_category"] == s["mechanism"]
        assert s["inject"] and s["reset"] and s["confirm"]["checks"]
        assert isinstance(s["answer"]["key_evidence"], list) and s["answer"]["key_evidence"]


def test_each_mechanism_lives_in_exactly_one_split_across_all_sources():
    splits = {}
    for s in ALL:
        splits.setdefault(s["answer"]["cause_category"], set()).add(s["split"])
    mixed = {k: v for k, v in splits.items() if len(v) > 1 and k != "no_fault"}
    assert not mixed, f"mechanism in both splits: {mixed}"


def test_generated_symptoms_do_not_leak_answers():
    for s in GEN:
        for kw in s["answer"]["key_evidence"] + [str(s["answer"]["object"]["name"])]:
            if kw in ("api", "worker"):  # the workload name is legitimately part of the symptom
                continue
            assert kw.lower() not in s["symptom"].lower(), (s["id"], kw)


def test_json_patches_render_to_valid_json():
    for s in GEN:
        for step in s["inject"] + s["reset"]:
            for arg in step["kubectl"]:
                if arg.startswith("[{"):
                    json.loads(arg)


def test_same_symptom_maps_to_several_mechanisms():
    by_symptom = {}
    for s in ALL:
        by_symptom.setdefault(s["symptom"], set()).add(s["answer"]["cause_category"])
    assert len(by_symptom["worker 一直在重启，事件堆积处理不掉。"]) >= 3
    assert len(by_symptom["api 发布后新 Pod 一直不 Ready，发布卡住。"]) >= 3


def test_render_rejects_missing_params_and_unknown_symptoms():
    with pytest.raises(TemplateError):
        _render("{{nope}}", {}, {})
    with pytest.raises(TemplateError):
        _render("{{sym:nope}}", {}, {})
    assert _render("{{ev}}", {"ev": ["a", "b"]}, {}) == ["a", "b"]


def test_load_scenarios_sources_and_caps():
    assert len(load_scenarios(split="all")) == 16
    assert len(load_scenarios(split="all", source="generated")) == 26
    capped = load_scenarios(split="all", source="generated", max_per_template=1)
    assert len(capped) == len({s["template"] for s in GEN})
    assert [s["id"] for s in load_scenarios(ids=["G-image_tag-02"])] == ["G-image_tag-02"]


# fake-cluster checks of the new SOP branches (rule baseline, no model)
@pytest.mark.parametrize("sid,state", [
    ("G-worker_command-02", "start_error"),        # never starts, evidence only in waiting message
    ("G-dependency_address-03", "api_host_notready"),  # not ready because of the dependency, not the probe
    ("G-redis_password-02", "api_password_notready"),
    ("G-api_probe-02", "api_readiness_path"),       # not ready because the probe itself is wrong
    ("G-service_routing-01", "selector"),           # all pods healthy; the Service selects nothing
])
def test_rule_baseline_on_new_branches(sid, state):
    tools = ReadOnlyTools(FakeRunner(FakeCluster(state).handle), NS, Budget(), Trace(), events_since="2026-11-20T10:01:00Z")
    result = RuleBaselineAgent().diagnose(GEN_BY_ID[sid]["symptom"], NS, tools)
    g = grade(result.diagnosis, GEN_BY_ID[sid]["answer"])
    assert g["correct"], (sid, result.diagnosis, g)
