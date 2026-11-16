"""Online Boutique scenarios: spec completeness, isolation from the lab scenario set, lifecycle selection, http_probe."""
from triage.kube import FakeRunner, KubeResult
from triage.lifecycle import (REPO, BoutiqueLifecycle, Lifecycle, StepLog, load_boutique, load_scenarios, make_lifecycle)

B = {s["id"]: s for s in load_boutique()}


def test_boutique_specs_are_complete():
    assert set(B) == {"B01", "B02", "B03", "B04", "B05", "B06", "B07"}
    for s in B.values():
        assert s["app"] == "boutique" and s["split"] == "dev" and s["hops"] in (1, 2)
        for key in ("symptom", "inject", "confirm", "reset", "answer", "realism"):
            assert key in s, (s["id"], key)
        assert s["id"] not in s["symptom"]
        for kw in s["answer"]["key_evidence"]:
            assert kw.lower() not in s["symptom"].lower()
        for step in s["inject"] + s["reset"]:
            for arg in step["kubectl"]:
                if "{repo}/" in str(arg):
                    assert (REPO / str(arg).split("{repo}/")[1]).exists(), arg
    # same symptom, different depth: the pairs the per-service SOP experiment is about
    assert B["B01"]["symptom"] == B["B03"]["symptom"] and B["B04"]["symptom"] == B["B05"]["symptom"]
    # same symptom on the same service, other mechanism: what a rule learned from B04/B05 can get wrong
    assert B["B06"]["symptom"] == B["B04"]["symptom"] and B["B07"]["symptom"] == B["B05"]["symptom"]
    assert B["B06"]["answer"]["object"]["name"] == B["B04"]["answer"]["object"]["name"]


def test_boutique_scenarios_stay_out_of_the_default_set():
    assert not any(s["id"].startswith("B0") for s in load_scenarios(split="all"))
    assert {s["id"] for s in load_scenarios(split="all", source="all")} >= set(B)


def test_lifecycle_is_chosen_by_app():
    k = FakeRunner(lambda a: KubeResult(True, ""))
    assert isinstance(make_lifecycle(B["B01"], k, "tiktok-k8s-lab", "/lab/k8s"), BoutiqueLifecycle)
    lab = make_lifecycle({"id": "R01"}, k, "tiktok-k8s-lab", "/lab/k8s")
    assert type(lab) is Lifecycle and lab.ns == "tiktok-k8s-lab"


def test_http_probe_expectations():
    ok = BoutiqueLifecycle(FakeRunner(lambda a: KubeResult(True, "<title>Online Boutique</title>")), "/lab/k8s")
    assert ok._check({"type": "http_probe", "contains": "Online Boutique"})[0]
    assert not ok._check({"type": "http_probe", "expect": "fail"})[0]
    assert not ok._check({"type": "http_probe", "min_seconds": 5})[0]          # fast answer is not a latency fault
    assert ok.k.calls[0] == ["get", "--raw", "/api/v1/namespaces/boutique/services/frontend:80/proxy/"]
    down = BoutiqueLifecycle(FakeRunner(lambda a: KubeResult(False, "", "Error from server (InternalError)", 1)), "/lab/k8s")
    assert down._check({"type": "http_probe", "expect": "fail"})[0]
    assert not down._check({"type": "http_probe"})[0]


def test_inject_and_reset_only_touch_boutique_and_the_injector_namespace():
    for s in B.values():
        for step in s["inject"] + s["reset"]:
            args = step["kubectl"]
            if "-n" in args:
                assert args[args.index("-n") + 1] in ("{ns}", "infra-tools"), (s["id"], args)
            assert "delete" not in args or args[args.index("delete") + 1] == "pod", (s["id"], args)


def test_inject_starts_from_fresh_pods_and_leaves_no_restart_annotation():
    import json
    dep = {"items": [{"metadata": {"name": "redis-cart"}, "status": {"readyReplicas": 1},
                      "spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": "x"}}}}}]}

    def handler(args):
        if args[-2:] == ["-o", "json"] and "deployments" in args:
            return KubeResult(True, json.dumps(dep))
        return KubeResult(True, "{}")
    k = FakeRunner(handler)
    lc = BoutiqueLifecycle(k, "/lab/k8s")
    lc.health_problems = lambda: []
    lc.inject(B["B03"], StepLog())
    flat = [" ".join(c) for c in k.calls]
    wipe = next(i for i, c in enumerate(flat) if "delete pods --all" in c)
    fault = next(i for i, c in enumerate(flat) if "redis-cart-v2" in c)
    assert wipe < fault and any("restartedAt" in c and "remove" in c for c in flat[:wipe])
    assert not any("rollout restart" in c for c in flat)
