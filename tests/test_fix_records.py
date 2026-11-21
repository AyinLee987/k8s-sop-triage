"""Arm D's material: the engineer's fix, one operation at a time, on a simulated cluster (no kubectl, no model, no waiting)."""
import json
import sys

import yaml

from triage import lifecycle, remediate
from triage.lifecycle import REPO

sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tests"))
import fix_records as fr  # noqa: E402
from test_loop_e2e import Sim  # noqa: E402

HARD = {s["id"]: s for s in yaml.safe_load((REPO / "scenarios" / "boutique_hard.yaml").read_text(encoding="utf-8"))["scenarios"]}


def test_every_learning_scenario_has_a_describable_fix_and_the_node_comes_last():
    for sid, sc in HARD.items():
        steps = fr.fix_steps(sc)
        assert len(steps) == len([s for s in sc["reset"] if "kubectl" in s]) >= 1, sid
        texts = [fr.describe_step([str(x) for x in s["kubectl"]], sc) for s in steps]
        node = [i for i, t in enumerate(texts) if "tc 规则" in t]
        assert node in ([], [len(texts) - 1]), sid
        for t in texts:   # the operation, not the diagnosis: no old value, no fault parameters, nothing about the lab
            for leak in ("netem", "delay", "loss", "injector", "延迟", "丢包", "注入", "应为", "原来"):
                assert leak not in t, (sid, t)
    assert fr.netem_targets(HARD["BH04"]) == ["productcatalogservice", "redis-cart"] and fr.netem_targets(HARD["BH07"]) == []
    one = [fr.describe_step([str(x) for x in s["kubectl"]], HARD["BH15"]) for s in fr.fix_steps(HARD["BH15"])]
    assert one == ["把 Deployment adservice 的副本数设为 1", "把 Deployment checkoutservice 的环境变量 CART_SERVICE_ADDR 设为 cartservice:7070（Pod 随之滚动更新）",
                   "在节点上清除了 productcatalogservice 的 Pod 对应 veth 网卡上的 tc 规则（恢复为默认队列）"]
    assert "删除了 Deployment cartservice 的环境变量 RELEASE_TAG" in fr.describe_step([str(x) for x in fr.fix_steps(HARD["BH08"])[0]["kubectl"]], HARD["BH08"])
    assert fr.describe_step([str(x) for x in fr.fix_steps(HARD["BH10"])[1]["kubectl"]], HARD["BH10"]) == "把 Service cartservice 第一个端口的 targetPort 设为 7070"


def test_fix_record_end_to_end_on_a_simulated_cluster(tmp_path, monkeypatch):
    sim = Sim()
    observe = remediate.observe
    monkeypatch.setattr(remediate, "observe", lambda lc, since, **kw: observe(lc, since, **{**kw, "sleep": lambda s: None, "min_seconds": 0}))
    monkeypatch.setattr(fr.time, "sleep", lambda s: None)
    monkeypatch.setattr(lifecycle.time, "sleep", lambda s: None)
    monkeypatch.setattr(fr, "KubectlRunner", lambda **kw: sim)
    monkeypatch.setattr(fr, "REPO", tmp_path)
    monkeypatch.setattr(fr, "load_dotenv", lambda: None)
    monkeypatch.setattr(fr, "netem_targets", lambda sc: ["currencyservice"])
    monkeypatch.setattr(sys, "argv", ["fix_records.py", "BH01", "--tag", "dry"])
    assert fr.main() == 0
    r = json.loads((tmp_path / "results" / "fix_records" / "dry" / "BH01.json").read_text(encoding="utf-8"))
    assert r["outcome"] == "resolved" and r["before"]["checkout_failed"] > 0 and r["before"]["share_slow"] == 1.0
    env, node = r["steps"]
    # the address fix ends the failed orders and leaves the latency; clearing the node's rule ends the latency
    assert "PAYMENT_SERVICE_ADDR 设为 paymentservice:50051" in env["operation"] and env["improved"] is True
    assert env["observation"]["checkout_failed"] == 0 and env["observation"]["share_slow"] == 1.0 and not env["observation"]["resolved"]
    assert "tc 规则" in node["operation"] and node["observation"]["resolved"] and node["improved"] is True
    assert sim.state == lifecycle.BoutiqueLifecycle(sim, "/lab/k8s")._baseline() and not sim.injector and "reset_error" not in r
    # the same record as the reflecting model sees it
    import importlib.util
    spec = importlib.util.spec_from_file_location("reflect", REPO / "boutique" / "reflect.py")
    rf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rf)
    agent = {"before": r["before"], "rounds": [
        {"round": 1, "answer": "货币服务每次调用约 200ms", "plan": {"root_causes": [{"object": "currencyservice", "cause": "CPU 限流"}], "explanation": "调高上限", "expect": "降到 0",
                                                          "actions": [{"op": "set_cpu_limit", "deployment": "currencyservice", "cpu": "1"}]},
         "actions": [{"action": {"op": "set_cpu_limit", "deployment": "currencyservice", "cpu": "1"}, "result": "ok"}], "observation": r["before"], "kept": False},
        {"round": 2, "answer": "第二轮才想到网络", "plan": {"root_causes": [{"object": "currencyservice", "cause": "网络路径延迟"}], "actions": []}, "actions": []}]}
    d = rf.material(agent, "页面变慢", "D", fix=r)
    for part in ("agent 的一次性诊断", "CPU 限流", "建议的修复", "值班工程师实际做的操作", "1. 把 Deployment checkoutservice", "2. 在节点上清除了 currencyservice", "用户侧已恢复正常"):
        assert part in d, part
    for leak in ("网络路径延迟", "第二轮", "改动已撤销", "预期：", "50052", "应为", "网络被加了"):
        assert leak not in d, leak
    assert "已证实" in rf.ENGINEER and "没有写原因" in rf.ENGINEER


def test_a_cpu_change_is_logged_as_what_was_set():
    args = ["-n", "{ns}", "set", "resources", "deployment/redis-cart", "--limits=cpu=125m", "--requests=cpu=70m"]
    text = fr.describe_step(args, {})
    assert text == "把 Deployment redis-cart 的 CPU 上限设为 125m、CPU 请求设为 70m（Pod 随之滚动更新）" and "3m" not in text
