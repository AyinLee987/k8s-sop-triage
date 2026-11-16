"""Generate the Online Boutique scenario pool (scenarios/boutique_gen.yaml) and the netem injector manifests.

  python boutique/make_scenarios.py [--seed 20261116]

Five fault templates applied across the services, all read from the release manifest:
  svc_port   Service targetPort off by one (backend pod recreated so that callers reconnect)
  selector   Service selector matches no pod
  env_addr   a caller's *_ADDR points at the wrong port
  netem      about 300 ms (200 ms for non-Go services) of network delay on the service's pod
  replicas0  Deployment scaled to zero
Every scenario also applies one harmless change to an unrelated service (a RELEASE_TAG environment variable, i.e.
"somebody deployed something else at the same time"). Symptoms are deliberately vague, as user reports are.
pool: mine = used to find bad cases that get written into the SOPs; regress = never written in, only evaluated.
frontend (the entry), adservice and emailservice (their failures are swallowed by the callers) are not targets;
netem on productcatalogservice and redis-cart is left out because B04/B05 were already written into the SOPs.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "boutique" / "kubernetes-manifests-v0.10.3.yaml"
NETEM = REPO / "scenarios" / "netem"
SKIP = {"frontend", "adservice", "emailservice", "loadgenerator"}
GO = {"productcatalogservice", "checkoutservice", "shippingservice", "frontend"}
ERR = "有用户反馈商城出问题了：有的页面打不开或者操作失败，报 500 错误。"
SLOW = "商城页面明显变慢了，但没有报错；Pod 看起来都是正常的。"
ERR_CONFIRM = {"timeout_s": 180, "checks": [{"type": "logs_contain", "selector": "app=frontend", "text": '"http.resp.status":500'}],
               "debug_on_fail": [["-n", "{ns}", "logs", "-l", "app=frontend", "--tail=6"], ["-n", "{ns}", "get", "pods", "-o", "wide"]]}
SLOW_CONFIRM = {"timeout_s": 150, "checks": [{"type": "slow_requests", "selector": "app=frontend", "field": "http.resp.took_ms",
                                              "match": {"message": "request complete"}, "min_ms": 180, "count": 15}],
                "debug_on_fail": [["-n", "infra-tools", "logs", "net-injector", "--tail=20"], ["-n", "{ns}", "get", "pods", "-o", "wide"]]}


def K(*args) -> dict:
    return {"kubectl": [str(a) for a in args]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261116)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    docs = [d for d in yaml.safe_load_all(MANIFEST.read_text(encoding="utf-8")) if d]
    deps = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Deployment"}
    svcs = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Service" and d["metadata"]["name"] in deps}
    env = {n: {e["name"]: e.get("value") for e in d["spec"]["template"]["spec"]["containers"][0].get("env", []) or []} for n, d in deps.items()}
    targets = [n for n in deps if n not in SKIP]
    edges = [(a, k, v) for a in deps if a != "loadgenerator" for k, v in env[a].items()
             if k.endswith("_ADDR") and v and v.split(":")[0] in targets]
    template = (NETEM / "injector-boutique-productcatalogservice.yaml").read_text(encoding="utf-8")
    out = []

    def add(tpl: str, target: str, symptom: str, inject: list, confirm: dict, reset: list, answer: dict, note: str, involved: set) -> None:
        # not frontend (the entry) and not redis-cart (restarting it drops every cart and causes real errors)
        others = sorted(n for n in deps if n not in involved and n not in ("loadgenerator", "frontend", "redis-cart"))
        decoy = others[len(out) * 7 % len(others)]
        out.append({"template": tpl, "target": target, "app": "boutique", "split": "dev", "kind": "cross_service", "mechanism": tpl,
                    "symptom": symptom, "decoy": decoy,
                    "realism": {"pattern": note + f" 同时 {decoy} 被加了一个无害的环境变量 RELEASE_TAG（一次无关的发布），作为干扰项。",
                                "sources": ["Cloud-OpsBench"], "validated": False,
                                "validate_note": "确认故障生效；延迟类场景确认所有 Pod 仍是 Ready、没有重启。"},
                    "inject": [K("-n", "{ns}", "set", "env", f"deployment/{decoy}", "RELEASE_TAG=2026.11.16-1"),
                               K("-n", "{ns}", "rollout", "status", f"deployment/{decoy}", "--timeout=140s"),
                               {"sleep": 25},   # let the unrelated rollout's own blips scroll out of the logs the confirm step reads
                               *inject],
                    "confirm": confirm,
                    "reset": [*reset, K("-n", "{ns}", "set", "env", f"deployment/{decoy}", "RELEASE_TAG-")],
                    "answer": {"status": "root_cause_found", "cause_category": "other", **answer}})

    for t in targets:
        port = svcs[t]["spec"]["ports"][0]
        good = port.get("targetPort", port["port"])
        patch = lambda v: json.dumps([{"op": "replace", "path": "/spec/ports/0/targetPort", "value": v}])  # noqa: E731
        recreate = [K("-n", "{ns}", "delete", "pod", "-l", f"app={t}", "--wait=true", "--timeout=90s"),
                    K("-n", "{ns}", "rollout", "status", f"deployment/{t}", "--timeout=140s")]
        add("svc_port", t, ERR, [K("-n", "{ns}", "patch", "service", t, "--type=json", "-p", patch(good + 1)), *recreate], ERR_CONFIRM,
            [K("-n", "{ns}", "patch", "service", t, "--type=json", "-p", patch(good))],
            {"object": {"kind": "Service", "name": t}, "explanation": f"Service {t} 的 targetPort 被改成 {good + 1}（容器监听 {good}），Pod 正常但流量到不了。",
             "key_evidence": [str(good + 1)], "accept_cause_keywords": ["targetport", "端口", "port"]},
            "Service 的 targetPort 配错（Cloud-OpsBench: service_port_mapping_mismatch）。", {t})
        sel = lambda v: json.dumps({"spec": {"selector": {"app": v}}})  # noqa: E731
        add("selector", t, ERR, [K("-n", "{ns}", "patch", "service", t, "--type=merge", "-p", sel(t + "-v2")), *recreate], ERR_CONFIRM,
            [K("-n", "{ns}", "patch", "service", t, "--type=merge", "-p", sel(t))],
            {"object": {"kind": "Service", "name": t}, "explanation": f"Service {t} 的 selector 被改成 app={t}-v2，匹配不到 Pod，端点为空。",
             "key_evidence": [t + "-v2"], "accept_cause_keywords": ["selector", "选择器", "endpoint", "端点"]},
            "Service 的 selector 配错导致没有后端（Cloud-OpsBench: service_selector_mismatch）。", {t})
        add("replicas0", t, ERR, [K("-n", "{ns}", "scale", f"deployment/{t}", "--replicas=0")], ERR_CONFIRM,
            [K("-n", "{ns}", "scale", f"deployment/{t}", "--replicas=1")],
            {"object": {"kind": "Deployment", "name": t}, "explanation": f"Deployment {t} 的副本数被改成 0，没有 Pod 在运行。",
             "key_evidence": [t], "accept_cause_keywords": ["replicas", "副本", "scale", "缩"]},
            "Deployment 副本数被缩到 0（Cloud-OpsBench: deployment_zero_replicas）。", {t})
        if t not in ("productcatalogservice", "redis-cart"):
            delay = "300ms 30ms" if t in GO else "200ms 20ms"
            (NETEM / f"injector-boutique-{t}.yaml").write_text(
                template.replace("productcatalogservice", t).replace("delay 300ms 30ms", f"delay {delay}"), encoding="utf-8")
            add("netem", t, SLOW,
                [K("-n", "infra-tools", "delete", "pod", "net-injector", "--ignore-not-found", "--wait=true", "--timeout=90s"),
                 K("apply", "-f", "{repo}/scenarios/netem/" + f"injector-boutique-{t}.yaml"),
                 K("-n", "infra-tools", "wait", "--for=condition=Ready", "pod/net-injector", "--timeout=180s")], SLOW_CONFIRM,
                [K("-n", "infra-tools", "delete", "pod", "net-injector", "--ignore-not-found", "--wait=true", "--timeout=90s")],
                {"object": {"kind": "Pod", "name": t}, "accept_objects": [t],
                 "explanation": f"{t} 的 Pod 网络被加了约 {delay.split()[0]} 延迟；它自身处理不慢，调用它的服务跟着变慢。",
                 "key_evidence": [t], "accept_cause_keywords": ["网络", "network", "延迟", "latency", "delay"]},
                "Pod 网络延迟（Cloud-OpsBench: pod_network_delay），集群对象上没有痕迹。", {t})
    for caller, var, value in edges:
        host, port = value.split(":")
        bad = f"{host}:{int(port) + 1}"
        add("env_addr", caller, ERR,
            [K("-n", "{ns}", "set", "env", f"deployment/{caller}", f"{var}={bad}"), K("-n", "{ns}", "rollout", "status", f"deployment/{caller}", "--timeout=140s")],
            ERR_CONFIRM, [K("-n", "{ns}", "set", "env", f"deployment/{caller}", f"{var}={value}")],
            {"object": {"kind": "Deployment", "name": caller}, "explanation": f"{caller} 的环境变量 {var} 被改成 {bad}（应为 {value}），调不到 {host}；{host} 自身没有问题。",
             "key_evidence": [bad.split(':')[1]], "accept_cause_keywords": [var.lower(), "环境变量", "env"]},
            "调用方的下游地址环境变量配错（Cloud-OpsBench: service_env_var_address_mismatch）。", {caller, host})
    by_tpl: dict[str, list] = {}
    for s in out:
        by_tpl.setdefault(s["template"], []).append(s)
    final = []
    for tpl in sorted(by_tpl):
        items = by_tpl[tpl]
        rng.shuffle(items)
        for i, s in enumerate(items):
            s["pool"] = "regress" if i % 5 in (1, 3) else "mine"   # 2 in 5 are kept for regression
            final.append(s)
    for i, s in enumerate(final, 1):
        s_id = {"id": f"BG{i:02d}"}
        final[i - 1] = {**s_id, **s}
    (REPO / "scenarios" / "boutique_gen.yaml").write_text(
        "# Generated by boutique/make_scenarios.py — do not edit by hand.\n" + yaml.safe_dump({"scenarios": final}, allow_unicode=True, sort_keys=False, width=200),
        encoding="utf-8")
    from collections import Counter
    print(len(final), Counter((s["template"], s["pool"]) for s in final))


if __name__ == "__main__":
    main()
