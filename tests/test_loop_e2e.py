"""The whole fix-and-verify loop against a simulated cluster: no kubectl, no model, no waiting.
Covers what went wrong on the laptop on 2026-11-17: leftovers of a killed run, a round rolled back, stale result files."""
import copy
import json
import sys
import types

from triage import lifecycle, remediate
from triage.kube import KubeResult
from triage.lifecycle import REPO

sys.path.insert(0, str(REPO / "scripts"))
import holmes_loop as hl  # noqa: E402


class Sim:
    def __init__(self):
        lc = lifecycle.BoutiqueLifecycle(types.SimpleNamespace(run=lambda a, timeout=0: KubeResult(True, "{}")), "/lab/k8s")
        self.state = copy.deepcopy(lc._baseline())
        self.injector = False
        self.calls = []
    def obj(self, kind, name, sh):
        if kind == "Service":
            return {"kind": kind, "metadata": {"name": name}, "spec": {"selector": sh["selector"], "ports": [{"port": 1, "targetPort": p} for p in sh["targetPorts"]]}}
        return {"kind": kind, "metadata": {"name": name}, "status": {"readyReplicas": sh["replicas"]}, "spec": {"replicas": sh["replicas"], "template": {"metadata": {}, "spec": {"containers": [
            {"env": [{"name": k, "value": v} for k, v in sh["env"].items()], "resources": {"limits": {"cpu": sh["cpu_limit"]}}}]}}}}
    def run(self, args, timeout=30.0):
        a = [str(x) for x in args]; self.calls.append(" ".join(a)); j = " ".join(a)
        ok = lambda out="": KubeResult(True, out)
        if "infra-tools" in a and "get" in a: return ok("x") if self.injector else KubeResult(False, "", "NotFound", 1)
        if "apply" in a: self.injector = True; return ok()
        if "infra-tools" in a and "delete" in a: self.injector = False; return ok()
        if "infra-tools" in a and "logs" in a: return ok("netem applied: 1\n")
        if "--raw" in a: return ok("<title>Online Boutique</title>")
        if "get" in a and "-o" in a:
            kinds = a[a.index("get") + 1]
            items = [self.obj(k, n, sh) for (k, n), sh in self.state.items() if (k == "Service" and "services" in kinds) or (k == "Deployment" and "deployments" in kinds)]
            return ok(json.dumps({"items": items}))
        if "logs" in a:
            pay_ok = self.state[("Deployment", "checkoutservice")]["env"]["PAYMENT_SERVICE_ADDR"].endswith(":50051")
            ms = 400 if self.injector else 10
            rows = [{"message": "request complete", "http.req.path": "/", "http.resp.status": 200, "http.resp.took_ms": ms}] * 60
            rows += [{"message": "request complete", "http.req.path": "/cart/checkout", "http.resp.status": 200 if pay_ok else 500, "http.resp.took_ms": ms}] * 3
            text = "\n".join(json.dumps(r) for r in rows) + ("" if pay_ok else "\nfailed to complete the order")
            return ok(text)
        if "set" in a and "env" in a:
            d = a[a.index("env") + 1].split("/")[1]
            for kv in a[a.index("env") + 2:]:
                if kv.endswith("-"): self.state[("Deployment", d)]["env"].pop(kv[:-1], None)
                else: k, v = kv.split("=", 1); self.state[("Deployment", d)]["env"][k] = v
            return ok()
        if "set" in a and "resources" in a:
            d = a[a.index("resources") + 1].split("/")[1]; self.state[("Deployment", d)]["cpu_limit"] = a[-1].split("cpu=")[1]; return ok()
        if "scale" in a:
            d = a[a.index("scale") + 1].split("/")[1]; self.state[("Deployment", d)]["replicas"] = int(a[-1].split("=")[1]); return ok()
        if "patch" in a and "service" in a and "managedFields" not in j:
            n = a[a.index("service") + 1]; p = json.loads(a[-1])
            if isinstance(p, dict): self.state[("Service", n)]["selector"] = p["spec"]["selector"]
            else: self.state[("Service", n)]["targetPorts"][0] = p[0]["value"]
            return ok()
        return ok("{}")


PLANS = [
    {"root_causes": [{"object": "checkoutservice", "cause": "addr"}, {"object": "currencyservice", "cause": "cpu"}], "expect": "慢请求降到 0", "explanation": "x",
     "actions": [{"op": "set_env", "deployment": "checkoutservice", "name": "PAYMENT_SERVICE_ADDR", "value": "paymentservice:50051"},
                 {"op": "set_cpu_limit", "deployment": "currencyservice", "cpu": "1"}]},
    {"root_causes": [{"object": "frontend", "cause": "cpu"}], "expect": "降到 0", "explanation": "y",
     "actions": [{"op": "set_cpu_limit", "deployment": "frontend", "cpu": "1"}, {"op": "drop_database"}]},
    {"root_causes": [{"object": "currencyservice", "cause": "网络"}], "expect": "", "actions": [], "explanation": "z"}]


def test_loop_end_to_end_on_a_simulated_cluster(tmp_path, monkeypatch):
    sim = Sim()
    sim.injector = True                                           # a previous run was killed mid-scenario
    sim.state[("Deployment", "adservice")]["replicas"] = 0
    calls = {"n": 0}

    def fake_holmes(cmd, **kw):
        plan = PLANS[calls["n"]]
        calls["n"] += 1
        with open(cmd[cmd.index("--json-output-file") + 1], "w", encoding="utf-8") as fh:
            json.dump({"result": "分析……\n```json\n" + json.dumps(plan, ensure_ascii=False) + "\n```", "total_tokens": 1000, "tool_calls": [1, 2]}, fh)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    observe = remediate.observe
    monkeypatch.setattr(remediate, "observe", lambda lc, since, **kw: observe(lc, since, **{**kw, "sleep": lambda s: None, "min_seconds": 0}))
    monkeypatch.setattr(hl.time, "sleep", lambda s: None)
    monkeypatch.setattr(hl, "KubectlRunner", lambda **kw: sim)
    monkeypatch.setattr(hl.subprocess, "run", fake_holmes)
    monkeypatch.setattr(hl, "REPO", tmp_path)
    monkeypatch.setattr(hl, "holmes_env", lambda skills: {})
    monkeypatch.setattr(hl, "load_dotenv", lambda: None)
    monkeypatch.setattr(sys, "argv", ["holmes_loop.py", "BH01", "--rounds", "3", "--tag", "dry"])
    (tmp_path / "holmes").mkdir()
    out = tmp_path / "results" / "holmes_loop" / "dry"
    out.mkdir(parents=True)
    (out / "BH01-round3.json").write_text('{"result": "STALE"}')     # from an earlier attempt into the same tag
    assert hl.main() == 0
    r = json.loads((out / "BH01.json").read_text(encoding="utf-8"))
    assert r["outcome"] == "agent_stopped" and r["frame"] == "prod" and "net-injector removed" in r["recovered"]
    one, two, three = r["rounds"]
    # round 1: orders work again, still slow -> kept, even though half of it (the CPU limit) did nothing
    assert one["kept"] is True and one["observation"]["checkout_failed"] == 0 and one["plan"]["expect"] == "慢请求降到 0"
    # round 2: nothing moved in either window -> only this round's change is undone; the unknown action never ran
    assert two["kept"] is False and "observation_first" in two and two["undone"] == ["Deployment/frontend: set resources deployment/frontend -> ok"]
    assert [a["accepted"] for a in two["actions"]] == [True, False] and not any("drop_database" in c for c in sim.calls)
    assert three["actions"] == [] and "STALE" not in (out / "BH01-round3.json").read_text(encoding="utf-8")
    # afterwards the namespace is the release manifest again and the injector is gone
    assert sim.state == lifecycle.BoutiqueLifecycle(sim, "/lab/k8s")._baseline() and not sim.injector and "reset_error" not in r
    assert "你当时的预期：慢请求降到 0" in hl.build_prompt("boutique", "慢", 3, [one], r["before"])
