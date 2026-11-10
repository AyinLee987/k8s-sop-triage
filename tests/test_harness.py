"""Grader, lifecycle sequencing, scenario spec completeness, answer isolation, report."""
import ast
import json
from pathlib import Path

import pytest

from triage.grader import grade
from triage.kube import FakeRunner, KubeResult
from triage.lifecycle import REPO, EnvironmentError_, Lifecycle, StepLog, load_scenarios
from triage.report import render
from triage.schema import Diagnosis

SC = {s["id"]: s for s in load_scenarios(split="all")}
GEN = {s["id"]: s for s in load_scenarios(split="all", source="generated")}


# ---------------- scenario specs ----------------
def test_scenario_specs_are_complete_and_split_by_mechanism():
    assert len(SC) == 16
    dev = {k for k, s in SC.items() if s["split"] == "dev"}
    holdout = {k for k, s in SC.items() if s["split"] == "holdout"}
    assert dev == {f"R{i:02d}" for i in range(1, 10)}
    assert holdout == {f"R{i:02d}" for i in range(10, 17)}
    for s in SC.values():
        for key in ("symptom", "inject", "confirm", "reset", "answer"):
            assert key in s, (s["id"], key)
        assert s["answer"]["status"] in ("root_cause_found", "no_fault_found", "undetermined")
        assert s["id"] not in s["symptom"]
    dev_categories = {SC[k]["answer"]["cause_category"] for k in dev}
    holdout_categories = {SC[k]["answer"]["cause_category"] for k in holdout} - {"other"}
    assert not (dev_categories & holdout_categories), "a fault mechanism appears in both splits"
    kinds = [s["kind"] for s in SC.values()]
    assert kinds.count("same_symptom_diff_cause") >= 3 and "normal" in kinds and "insufficient_evidence" in kinds
    assert kinds.count("cross_component") >= 6 and "sop_not_covered" in kinds


def test_every_scenario_cites_its_real_world_or_benchmark_source():
    for s in SC.values():
        r = s["realism"]
        assert r["pattern"] and r["sources"] and isinstance(r["validated"], bool), s["id"]
        if not r["validated"]:
            assert r.get("validate_note"), (s["id"], "unvalidated scenarios must say what to check on the real cluster")


def test_same_symptom_pairs_cross_the_dev_holdout_split():
    queued = [s for s in SC.values() if s["symptom"] == SC["R02"]["symptom"]]
    assert {s["split"] for s in queued} == {"dev", "holdout"} and len({s["answer"]["cause_category"] for s in queued}) == 3


def test_symptoms_do_not_leak_answers():
    for s in SC.values():
        for kw in s["answer"].get("key_evidence", []):
            assert kw.lower() not in s["symptom"].lower(), (s["id"], kw)


def test_agent_code_cannot_reach_scenarios_or_grader():
    for path in (REPO / "triage" / "agents").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | \
                   {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not imported & {"triage.lifecycle", "triage.grader", "triage.runner", "yaml"}, path
        assert "scenarios" not in path.read_text(encoding="utf-8"), path


def test_rbac_is_read_only_and_excludes_secrets():
    import yaml
    docs = list(yaml.safe_load_all((REPO / "k8s" / "rbac-diag.yaml").read_text(encoding="utf-8")))
    role = next(d for d in docs if d["kind"] == "Role")
    for rule in role["rules"]:
        assert set(rule["verbs"]) <= {"get", "list", "watch"}
        assert "secrets" not in rule["resources"]


# ---------------- grader ----------------
def test_grader_separates_object_and_cause():
    ans = SC["R05"]["answer"]
    wrong_obj = Diagnosis("root_cause_found", "memory_limit_too_low", "内存过低", {"kind": "Deployment", "name": "api"}, ["OOMKilled"])
    g = grade(wrong_obj, ans)
    assert g["cause_ok"] and not g["object_ok"] and not g["correct"]
    pod_level = Diagnosis("root_cause_found", "memory_limit_too_low", "内存过低", {"kind": "Pod", "name": "worker-7d4b-a"}, ["OOMKilled"])
    assert grade(pod_level, ans)["correct"]


def test_grader_flags_symptom_only_answers_for_review():
    d = Diagnosis("root_cause_found", "other", "worker 处于 CrashLoopBackOff", {"kind": "Deployment", "name": "worker"}, ["CrashLoopBackOff"])
    g = grade(d, GEN["G-env_invalid-01"]["answer"])
    assert not g["correct"]


def test_grader_undetermined_must_name_the_gap():
    vague = Diagnosis("undetermined", unknowns=["不清楚"])
    assert not grade(vague, SC["R16"]["answer"])["correct"]
    named = Diagnosis("undetermined", unknowns=["缺少 rec-feature 的读权限"])
    assert grade(named, SC["R16"]["answer"])["correct"]


# ---------------- lifecycle sequencing with a fake admin cluster ----------------
class AdminFake:
    """Healthy unless `fault_active`; inject sets the flag, reset clears it."""

    def __init__(self, confirm_works=True):
        self.fault_active = False
        self.confirm_works = confirm_works
        self.calls = []

    def run(self, args, timeout=30):
        self.calls.append(args)
        a = args
        if "set" in a or "patch" in a:
            self.fault_active = "image" in a and "v1.1" in " ".join(a)
            return KubeResult(True, "")
        if "rollout" in a or "apply" in a or "delete" in a:
            return KubeResult(True, "")
        if a[:3] == ["get", "namespace", "rec-feature"]:
            return KubeResult(False, "", "NotFound", 1)
        if "configmap" in a and "app-config" in a:
            return KubeResult(True, json.dumps({"data": {"KEY_PREFIX": "tiktok-lab"}}))
        if "service" in a and "redis" in a:
            return KubeResult(True, json.dumps({"spec": {"selector": {"app": "redis"}, "ports": [{"targetPort": "redis"}]}}))
        if "statefulset" in a and "redis" in a:
            c = {"volumeMounts": [{"name": "data", "mountPath": "/data"}], "resources": {"limits": {"cpu": "1"}}}
            return KubeResult(True, json.dumps({"spec": {"template": {"spec": {"containers": [c]}}}}))
        if "secret" in a:
            assert any(x.startswith("go-template=") for x in a), "health check must read key names only"
            return KubeResult(True, "password redis-auth.conf ")
        if "configmap" in a and "redis-config" in a:
            return KubeResult(True, json.dumps({"data": {"redis.conf": "maxmemory 192mb\ninclude /etc/redis-auth/redis-auth.conf\n"}}))
        if "endpointslices" in a:
            return KubeResult(True, json.dumps({"items": [{"endpoints": [{"conditions": {"ready": True}}] * 2}]}))
        if "deployments,statefulsets,pods" in a:
            items = [{"kind": "Deployment", "metadata": {"name": "api"}, "status": {"readyReplicas": 2}},
                     {"kind": "Deployment", "metadata": {"name": "worker"}, "status": {"readyReplicas": 1}},
                     {"kind": "StatefulSet", "metadata": {"name": "redis"}, "status": {"readyReplicas": 1}}]
            return KubeResult(True, json.dumps({"items": items}))
        if "pods" in a and "-l" in a:
            reason = "ErrImageNeverPull" if (self.fault_active and self.confirm_works) else None
            cs = {"state": {"waiting": {"reason": reason}} if reason else {"running": {}}, "restartCount": 0}
            return KubeResult(True, json.dumps({"items": [{"status": {"containerStatuses": [cs]}}]}))
        return KubeResult(True, "{}")


def test_lifecycle_full_round():
    fake = AdminFake()
    lc = Lifecycle(fake, "tiktok-k8s-lab", "/lab/k8s", poll_interval=0.01)
    log = StepLog()
    lc.verify_healthy(log)
    since = lc.inject(SC["R06"], log)
    lc.confirm(SC["R06"], log)
    lc.reset(SC["R06"], log)
    phases = [e["phase"] for e in log.entries]
    assert phases[:4] == ["verify_healthy", "clear_events", "inject", "confirm"] and phases[-1] == "verify_recovered"
    assert since.endswith("Z")
    # old events are deleted before injecting, so an agent without a time filter cannot see earlier runs
    assert ["-n", "tiktok-k8s-lab", "delete", "events", "--all"] in fake.calls
    assert any(c[:3] == ["-n", "tiktok-k8s-lab", "rollout"] and "restart" in c for c in fake.calls)


def test_fault_that_never_takes_effect_is_an_environment_failure():
    lc = Lifecycle(AdminFake(confirm_works=False), "tiktok-k8s-lab", "/lab/k8s", poll_interval=0.01)
    sc = dict(SC["R06"], confirm={"timeout_s": 0.05, "checks": SC["R06"]["confirm"]["checks"]})
    log = StepLog()
    lc.inject(sc, log)
    with pytest.raises(EnvironmentError_):
        lc.confirm(sc, log)


# ---------------- report ----------------
def test_report_counts_per_scenario_and_separates_env_failures():
    base = {"split": "dev", "scenario_kind": "x", "budget": {"wall_seconds": 10, "tool_calls": 3, "model_calls": 1, "duplicate_calls": 0},
            "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "cached_tokens": 0}, "cost_usd": 0.001,
            "diagnosis": Diagnosis("root_cause_found").to_dict()}
    recs = [dict(base, scenario="S01", arm="code", rep=1, grade={"correct": True}),
            dict(base, scenario="S01", arm="code", rep=2, grade={"correct": False, "notes": []}),
            dict(base, scenario="S01", arm="code", rep=3, env_failure="fault did not take effect")]
    text = render(recs)
    assert "| S01 | x | 1/2，环境失败 1 |" in text
    assert "环境失败 1 次" in text


def test_json_patch_ops_follow_rfc6902():
    """A `move` written with `to` instead of `path` passes JSON parsing but the API server rejects it (found in R09)."""
    required = {"add": {"path", "value"}, "replace": {"path", "value"}, "test": {"path", "value"},
                "remove": {"path"}, "move": {"from", "path"}, "copy": {"from", "path"}}
    for s in load_scenarios(split="all", source="all"):
        for step in (s.get("inject") or []) + (s.get("reset") or []):
            args = step["kubectl"]
            if "--type=json" not in args:
                continue
            ops = json.loads(args[args.index("-p") + 1])
            for op in ops:
                assert op["op"] in required, (s["id"], op)
                assert set(op) == required[op["op"]] | {"op"}, (s["id"], op)
