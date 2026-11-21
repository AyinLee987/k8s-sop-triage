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
            {"env": [{"name": k, "value": v} for k, v in shape["env"].items()], "resources": {"limits": {"cpu": shape["cpu_limit"]}, "requests": {"cpu": shape["cpu_request"]}}}]}}}}
    items = [obj(k, n, s) for (k, n), s in base.items()]
    for it in items:
        name = it["metadata"]["name"]
        if it["kind"] == "Deployment" and name == "currencyservice":
            it["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["cpu"] = "1"
            it["spec"]["template"]["spec"]["containers"][0]["env"].append({"name": "RELEASE_TAG", "value": "x"})
        if it["kind"] == "Deployment" and name == "adservice":
            it["spec"]["replicas"] = 0
        if it["kind"] == "Deployment" and name == "redis-cart":      # a throttling scenario lowers limit and request together
            it["spec"]["template"]["spec"]["containers"][0]["resources"] = {"limits": {"cpu": "3m"}, "requests": {"cpu": "3m"}}
        if it["kind"] == "Deployment" and name == "shippingservice":  # only the request drifted
            it["spec"]["template"]["spec"]["containers"][0]["resources"]["requests"]["cpu"] = "5m"
        if it["kind"] == "Service" and name == "cartservice":
            it["spec"]["ports"][0]["targetPort"] = 7071
    lc = lifecycle(lambda a: KubeResult(True, json.dumps({"items": items})))
    done = remediate.restore_baseline(lc)
    flat = [" ".join(c) for c in lc.k.calls]
    assert any("set env deployment/currencyservice RELEASE_TAG-" in c for c in flat)
    assert any("set resources deployment/currencyservice --limits=cpu=" + base[("Deployment", "currencyservice")]["cpu_limit"] in c for c in flat)
    assert any("scale deployment/adservice --replicas=1" in c for c in flat) and any("patch service cartservice" in c and "7070" in c for c in flat)
    assert any("set resources deployment/redis-cart --limits=cpu=125m --requests=cpu=70m" in c for c in flat)
    assert any("set resources deployment/shippingservice --limits=cpu=200m --requests=cpu=100m" in c for c in flat)
    assert len(done) == 6   # nothing else is touched


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
                {"env": [{"name": k, "value": v} for k, v in shape["env"].items()], "resources": {"limits": {"cpu": cpu}, "requests": {"cpu": shape["cpu_request"]}}}]}}}})
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


def test_no_orders_in_the_window_is_not_orders_fixed():
    before = {"http_500": 4, "checkout_failed": 4, "share_slow": 0.8, "requests": 200, "checkout_attempts": 4, "resolved": False}
    quiet = {"http_500": 0, "checkout_failed": 0, "share_slow": 0.8, "requests": 200, "checkout_attempts": 0, "resolved": False}
    assert not remediate.improved(before, quiet)
    assert remediate.improved(before, {**quiet, "checkout_attempts": 3})
    assert not remediate.improved(before, {**quiet, "requests": 0})            # frontend scaled away: silence is not health
    assert not remediate.improved(before, {**quiet, "checkout_attempts": 3, "http_500": 9})   # orders pass but more pages fail than before


def test_orders_refused_for_their_card_type_are_reported_but_not_held_against_a_fix():
    """About 2% of the load generator's orders are refused by the payment service for the card type, fault or no fault."""
    import types
    ok = [{"message": "request complete", "http.req.path": "/", "http.resp.status": 200, "http.resp.took_ms": 9}] * 60
    order = lambda status: {"message": "request complete", "http.req.path": "/cart/checkout", "http.resp.status": status, "http.resp.took_ms": 30}  # noqa: E731
    refused = {"message": "request error", "error": "failed to complete the order: rpc error: code = Internal desc = failed to charge card: could not charge the card: "
               "rpc error: code = Code(400) desc = Sorry, we cannot process visa_electron credit cards. Only VISA or MasterCard is accepted."}
    broken = {"message": "request error", "error": "failed to complete the order: rpc error: code = Unavailable desc = connection error"}

    def obs(rows):
        lc = types.SimpleNamespace(_run=lambda a, t: types.SimpleNamespace(ok=True, stdout="\n".join(json.dumps(r) for r in rows), stderr=""))
        return remediate.observe(lc, "t", min_seconds=0, sleep=lambda s: None)
    healthy = obs(ok + [order(200)] * 6 + [order(500), refused])
    assert healthy["checkout_failed"] == 1 and healthy["card_rejected"] == 1 and healthy["resolved"] is True
    assert "失败 1 次（其中 1 次是支付服务拒收卡种" in remediate.describe(healthy)
    faulty = obs(ok + [order(200)] * 5 + [order(500), refused, order(500), broken])
    assert faulty["card_rejected"] == 1 and faulty["checkout_failed"] == 2 and faulty["resolved"] is False
    assert remediate.improved(faulty, healthy) is True and remediate.improved(healthy, healthy) is True
    # a refusal alone is not an error that a fix can be credited with removing
    clean = obs(ok + [order(200)] * 6)
    slow = {**healthy, "resolved": False, "share_slow": 0.3}
    assert "拒收" not in remediate.describe(clean) and remediate.improved(slow, {**clean, "resolved": False, "share_slow": 0.3}) is False
    assert remediate.improved({"http_500": 3, "checkout_failed": 3, "share_slow": 0.0}, {"http_500": 0, "checkout_failed": 0, "share_slow": 0.0}) is True   # records without the new field
