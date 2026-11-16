"""Online Boutique scenarios: spec completeness, isolation from the lab scenario set, lifecycle selection, http_probe."""
from triage.kube import FakeRunner, KubeResult
from triage.lifecycle import (REPO, BoutiqueLifecycle, Lifecycle, StepLog, load_boutique, load_scenarios, make_lifecycle)

B = {s["id"]: s for s in load_boutique()}


def test_boutique_specs_are_complete():
    assert set(B) == {"B01", "B02", "B03", "B04", "B05", "B06", "B08"}
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
    assert B["B06"]["symptom"] == B["B04"]["symptom"]
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
    assert ok._check({"type": "http_probe", "repeat": 2})[0] and "median" in ok._check({"type": "http_probe"})[1]
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


# ---------------- generated pool ----------------
G = {s["id"]: s for s in load_boutique(generated=True)}


def test_generated_pool_is_complete_and_split():
    assert len(G) >= 40 and {s["pool"] for s in G.values()} == {"mine", "regress"}
    assert {s["template"] for s in G.values()} == {"svc_port", "selector", "env_addr", "netem", "replicas0"}
    for s in G.values():
        assert s["app"] == "boutique" and s["decoy"] != s["target"] and s["decoy"] not in ("frontend", "redis-cart")
        for kw in s["answer"]["key_evidence"]:
            assert kw.lower() not in s["symptom"].lower()
        steps = [st for st in s["inject"] + s["reset"] if "kubectl" in st]
        assert any("RELEASE_TAG-" in st["kubectl"] for st in s["reset"] if "kubectl" in st), "the harmless side change must be undone"
        for st in steps:
            args = st["kubectl"]
            if "-n" in args:
                assert args[args.index("-n") + 1] in ("{ns}", "infra-tools"), (s["id"], args)
            assert "delete" not in args or args[args.index("delete") + 1] == "pod", (s["id"], args)
            for arg in args:
                if "{repo}/" in arg:
                    assert (REPO / arg.split("{repo}/")[1]).exists(), arg
    # every template appears in both pools, so a rule learned on one service is tested on another
    for tpl in {s["template"] for s in G.values()}:
        assert {s["pool"] for s in G.values() if s["template"] == tpl} == {"mine", "regress"}


def test_baseline_is_read_from_the_manifest():
    lc = BoutiqueLifecycle(FakeRunner(lambda a: KubeResult(True, "{}")), "/lab/k8s")
    base = lc._baseline()
    assert base[("Service", "productcatalogservice")] == {"selector": {"app": "productcatalogservice"}, "targetPorts": [3550]}
    assert base[("Deployment", "checkoutservice")]["env"]["SHIPPING_SERVICE_ADDR"] == "shippingservice:50051"
    assert base[("Deployment", "redis-cart")]["cpu_limit"] == "125m" and ("Service", "frontend-external") not in base
    assert len([k for k in base if k[0] == "Deployment"]) == 12


def test_slow_requests_reads_the_frontend_log_format():
    import json
    lines = "\n".join(json.dumps({"message": "request complete", "http.resp.took_ms": ms}) for ms in (5, 300, 420, 9))
    lc = BoutiqueLifecycle(FakeRunner(lambda a: KubeResult(True, lines)), "/lab/k8s")
    check = {"type": "slow_requests", "selector": "app=frontend", "field": "http.resp.took_ms", "match": {"message": "request complete"}, "min_ms": 180}
    assert lc._check({**check, "count": 2})[0] and not lc._check({**check, "count": 3})[0]
