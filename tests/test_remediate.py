"""Fix-and-verify loop: only the listed actions can run, the plan is read from the answer, users' view is measured,
and the namespace is put back to the release manifest."""
import json

from triage import remediate
from triage.kube import FakeRunner, KubeResult
from triage.lifecycle import BoutiqueLifecycle


def lifecycle(handler=None):
    return BoutiqueLifecycle(FakeRunner(handler or (lambda a: KubeResult(True, "{}"))), "/lab/k8s")


def test_plan_is_the_last_json_block():
    text = '先看一个例子 ```json\n{"actions": [{"op": "restart", "deployment": "x"}]}\n```\n结论……\n```json\n{"root_causes": [{"object": "a", "cause": "b"}], "actions": []}\n```'
    plan, err = remediate.parse_plan(text)
    assert not err and plan["root_causes"][0]["object"] == "a" and plan["actions"] == []
    bare, _ = remediate.parse_plan('结论 {"root_causes": [], "actions": [{"op": "restart", "deployment": "cartservice"}]} 完')
    assert bare["actions"][0]["op"] == "restart"
    assert remediate.parse_plan("没有 JSON")[0] is None


def test_only_listed_actions_on_boutique_workloads_become_commands():
    ok = [{"op": "restart", "deployment": "cartservice"}, {"op": "set_env", "deployment": "checkoutservice", "name": "PAYMENT_SERVICE_ADDR", "value": "paymentservice:50051"},
          {"op": "unset_env", "deployment": "cartservice", "name": "RELEASE_TAG"}, {"op": "scale", "deployment": "adservice", "replicas": 1},
          {"op": "set_cpu_limit", "deployment": "currencyservice", "cpu": "500m"}, {"op": "set_service_selector", "service": "paymentservice", "app": "paymentservice"},
          {"op": "set_service_target_port", "service": "cartservice", "port": 7070}]
    for action in ok:
        cmds, err = remediate.to_kubectl(action)
        assert not err and cmds, action
        for args in cmds:
            assert args[:2] == ["-n", "boutique"] and args[2] in ("delete", "rollout", "set", "scale", "patch")
            assert args[2] != "delete" or args[3] == "pod"
    bad = [{"op": "restart", "deployment": "loadgenerator"}, {"op": "restart", "deployment": "x; kubectl delete ns boutique"},
           {"op": "delete_namespace"}, {"op": "set_env", "deployment": "cartservice", "name": "A", "value": "$(reboot)"},
           {"op": "set_env", "deployment": "cartservice", "name": "a b", "value": "1"}, {"op": "scale", "deployment": "cartservice", "replicas": 50},
           {"op": "scale", "deployment": "cartservice", "replicas": True}, {"op": "set_cpu_limit", "deployment": "cartservice", "cpu": "64"},
           {"op": "set_service_selector", "service": "kube-dns", "app": "x"}, {"op": "set_service_target_port", "service": "cartservice", "port": "7070"},
           {"op": "exec", "deployment": "cartservice", "command": "sh"}, "restart everything"]
    for action in bad:
        assert remediate.to_kubectl(action)[1], action


def test_rejected_actions_are_reported_and_not_run():
    lc = lifecycle(lambda a: KubeResult(True, "done"))
    actions = [{"op": "restart", "deployment": "nope"}, {"op": "restart", "deployment": "cartservice"}] + [{"op": "restart", "deployment": "adservice"}] * 6
    out = remediate.apply_actions(lc, actions)
    assert [r["accepted"] for r in out] == [False, True, True, True, True, True, False, False]
    assert all("nope" not in " ".join(c) for c in lc.k.calls) and out[1]["ok"]


def frontend_log(rows):
    return "\n".join(json.dumps({"message": "request complete", "http.req.path": p, "http.resp.status": s, "http.resp.took_ms": ms}) for p, s, ms in rows)


def test_observation_says_whether_users_are_fine():
    healthy = frontend_log([("/", 200, 12)] * 40 + [("/cart/checkout", 200, 60)] * 2)
    obs = remediate.observe(lifecycle(lambda a: KubeResult(True, healthy)), "2026-11-16T00:00:00Z", min_seconds=0, sleep=lambda s: None)
    assert obs["resolved"] and obs["checkout_attempts"] == 2 and "失败 0 次" in remediate.describe(obs)
    slow = frontend_log([("/", 200, 12)] * 36 + [("/", 200, 220)] * 4 + [("/cart/checkout", 200, 60)] * 2)
    assert not remediate.observe(lifecycle(lambda a: KubeResult(True, slow)), "x", min_seconds=0, sleep=lambda s: None)["resolved"]
    failing = frontend_log([("/", 200, 12)] * 40 + [("/cart/checkout", 500, 900)] * 2)
    obs = remediate.observe(lifecycle(lambda a: KubeResult(True, failing)), "x", min_seconds=0, sleep=lambda s: None)
    assert not obs["resolved"] and obs["checkout_failed"] == 2
    # no order placed yet: keep waiting up to the limit, and do not call it resolved
    waits = []
    no_order = frontend_log([("/", 200, 12)] * 40)
    obs = remediate.observe(lifecycle(lambda a: KubeResult(True, no_order)), "x", min_seconds=0, max_seconds=30, sleep=waits.append)
    assert not obs["resolved"] and sum(waits) == 30


def test_restore_puts_back_what_differs_from_the_manifest():
    lc = lifecycle()
    base = lc._baseline()
    def obj(kind, name, shape):
        if kind == "Service":
            return {"kind": kind, "metadata": {"name": name}, "spec": {"selector": shape["selector"], "ports": [{"port": 1, "targetPort": p} for p in shape["targetPorts"]]}}
        return {"kind": kind, "metadata": {"name": name}, "spec": {"replicas": shape["replicas"], "template": {"spec": {"containers": [
            {"env": [{"name": k, "value": v} for k, v in shape["env"].items()], "resources": {"limits": {"cpu": shape["cpu_limit"]}}}]}}}}
    items = [obj(k, n, s) for (k, n), s in base.items()]
    for it in items:
        name = it["metadata"]["name"]
        if it["kind"] == "Deployment" and name == "currencyservice":
            it["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["cpu"] = "1"
            it["spec"]["template"]["spec"]["containers"][0]["env"].append({"name": "RELEASE_TAG", "value": "x"})
        if it["kind"] == "Deployment" and name == "adservice":
            it["spec"]["replicas"] = 0
        if it["kind"] == "Service" and name == "cartservice":
            it["spec"]["ports"][0]["targetPort"] = 7071
    lc = lifecycle(lambda a: KubeResult(True, json.dumps({"items": items})))
    done = remediate.restore_baseline(lc)
    flat = [" ".join(c) for c in lc.k.calls]
    assert any("set env deployment/currencyservice RELEASE_TAG-" in c for c in flat)
    assert any("set resources deployment/currencyservice --limits=cpu=" + base[("Deployment", "currencyservice")]["cpu_limit"] in c for c in flat)
    assert any("scale deployment/adservice --replicas=1" in c for c in flat) and any("patch service cartservice" in c and "7070" in c for c in flat)
    assert len(done) == 4   # nothing else is touched


def test_a_round_counts_as_helpful_only_beyond_the_noise():
    base = {"http_500": 0, "checkout_failed": 0, "share_slow": 0.24, "resolved": False}
    assert not remediate.improved(base, {**base, "share_slow": 0.14})          # BH15: 24% -> 14% is what the share does on its own
    assert remediate.improved(base, {**base, "share_slow": 0.10})
    assert remediate.improved({**base, "http_500": 9, "checkout_failed": 9}, base)   # errors gone, still slow: keep the fix
    assert remediate.improved(base, {**base, "share_slow": 0.0, "resolved": True})
    assert not remediate.improved(base, {**base, "http_500": 12})


def test_a_failed_round_is_rolled_back_to_the_state_before_it():
    lc = lifecycle()
    before = dict(lc._baseline())
    before[("Deployment", "checkoutservice")] = {**before[("Deployment", "checkoutservice")], "replicas": 1}
    items = []
    for (kind, name), shape in before.items():
        if kind == "Service":
            items.append({"kind": kind, "metadata": {"name": name}, "spec": {"selector": shape["selector"], "ports": [{"port": 1, "targetPort": p} for p in shape["targetPorts"]]}})
        else:
            cpu = "1" if name == "currencyservice" else shape["cpu_limit"]          # what the round changed
            items.append({"kind": kind, "metadata": {"name": name}, "spec": {"replicas": shape["replicas"], "template": {"spec": {"containers": [
                {"env": [{"name": k, "value": v} for k, v in shape["env"].items()], "resources": {"limits": {"cpu": cpu}}}]}}}})
    lc = lifecycle(lambda a: KubeResult(True, json.dumps({"items": items})))
    assert remediate.snapshot(lc)[("Deployment", "currencyservice")]["cpu_limit"] == "1"
    done = remediate.restore_to(lc, before)
    assert len(done) == 1 and "currencyservice" in done[0]


def test_recover_removes_a_left_over_injector_and_is_quiet_on_a_clean_namespace():
    def dirty(args):
        if args[:5] == ["-n", "infra-tools", "get", "pod", "net-injector"]:
            return KubeResult(True, "net-injector 1/1 Running")
        return KubeResult(True, "{}")
    lc = lifecycle(dirty)
    done = remediate.recover(lc)
    flat = [" ".join(c) for c in lc.k.calls]
    assert done == ["net-injector removed"] and any("delete pod net-injector" in c for c in flat) and any("rollout status" in c for c in flat)
    clean = lifecycle(lambda a: KubeResult(False, "", "NotFound", 1) if "net-injector" in a else KubeResult(True, "{}"))
    assert remediate.recover(clean) == [] and not any("delete" in c or "rollout" in c for c in (" ".join(x) for x in clean.k.calls))
