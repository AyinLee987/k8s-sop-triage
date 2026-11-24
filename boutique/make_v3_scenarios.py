"""Generate the third Online Boutique bank (scenarios/boutique_v3.yaml: EL = learning, EV = validation, ET = test).

  python boutique/make_v3_scenarios.py

Why a third bank (docs/V3.md): on the second bank the agent found every CPU and configuration fault by looking for the one
deployment whose spec had changed (last-applied annotation or previous ReplicaSet; 72 of 75 runs on CV02/06/08 cited it).
The faults stay the ones validated on this cluster; what changes is the situation around them:

  release batch      every scenario rolls out several deployments in the same minute with the same RELEASE_TAG, and the
                     faulty change (if there is one) is part of that batch. "What changed?" never has a single answer, and
                     in the network scenarios every change is harmless.
  harmless lookalikes  some batch members carry a change that looks like a cause and is not: an address rewritten to its
                     fully qualified form (same service), a CPU limit lowered but still far above what the service uses.
  no last-applied    the lifecycle strips the kubectl last-applied annotation before every run of this bank (bank: 3), so
                     "annotation differs from the live spec" is no longer a shortcut. Rollout history stays: it is real.
  wrong guesses      some tickets carry the reporter's guess; it never names a real cause.
  second-level faults  network faults on services only reached through another service (paymentservice, emailservice,
                     redis-cart, productcatalogservice behind recommendationservice), where the caller is easy to blame.
  paired scenarios   the same CPU fault alone and together with a network fault, so "always add a network cause" scores
                     on one and loses on the other.
  healthy traffic    settle_s: two minutes of normal traffic between the fresh pods and the fault, so the logs hold a
                     baseline to compare with and the start-up noise is old.

Every cause carries `decidable`: whether the agent's own tools (objects, logs, the pod metrics table) can tell its
mechanism apart from the alternatives. The grading rule (boutique/eval_grades_v3.json) asks for a committed mechanism
only where it is decidable. Every scenario carries `evidence_chain`: the observations, each reachable with those tools,
that lead from the ticket to the answer.

Label collisions (a second Deployment carrying another service's `app` label) were planned and dropped: every caller
holds one long-lived gRPC connection and every service has one replica, so the misrouted share would be 0% or 100% at
random from run to run, not a stable partial failure.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "boutique"))
from make_hard_scenarios import CPU_LIMIT, INJECTOR, LOSS, K  # noqa: E402  (same injector and limits as the second bank)

MANIFEST = REPO / "boutique" / "kubernetes-manifests-v0.10.3.yaml"
NETEM = REPO / "scenarios" / "netem"
OUT = REPO / "scenarios" / "boutique_v3.yaml"
GO = {"productcatalogservice", "checkoutservice", "shippingservice", "frontend"}
POOL = {"EL": "learn", "EV": "val", "ET": "test"}   # E: the third bank (B = first, C = second; D is skipped because "D" already names an experience group)
TAG = "2026.11.24-{n}"   # the release tag the batch rolls out; one number per scenario, looks like any release
SETTLE_S = 120

SLOW = "商城页面明显变慢了，但没有报错；Pod 看起来都是正常的。"
FLAKY = "商城时快时慢：大部分请求正常，偶尔有页面要等很久，没有报错。"
ORDER = "有用户反馈下单失败，订单提交不了；其它页面还能打开。"
ERR = "有用户反馈商城出问题了：有的页面打不开或者操作失败，报 500 错误。"
BOTH = "有用户反馈商城变慢了，另外还有人说下单失败、订单提交不了。"
CHECKOUT = "有用户反馈下单要等很久才成功，浏览商品和购物车基本正常；没有报错。"
SLOW_CHECKOUT = "有用户反馈商城变慢了，下单尤其慢；没有报错。"

# Services that do not call any other service of the app. A delay on such a pod's interface (netem delays the packets
# entering the pod) shows as "the request arrives late, then is handled in a few milliseconds": exactly what a process
# that stalls before its handler would show. With logs and the pod metrics table the two cannot be told apart.
LEAF = {"currencyservice", "productcatalogservice", "paymentservice", "emailservice", "shippingservice", "adservice", "redis-cart"}
# Services that call others and whose operations differ in how many replies cross their interface: the added time grows
# with that count (cartservice: GetCart one Redis reply, AddItem two; checkoutservice: every downstream reply of PlaceOrder;
# frontend: every RPC of a page), which a stall in the process does not do. recommendationservice is not in this list:
# every one of its requests is the same (one request in, one productcatalog reply), so there is no count to compare.
COUNTABLE = {"cartservice", "checkoutservice", "frontend"}


def main() -> None:
    docs = [d for d in yaml.safe_load_all(MANIFEST.read_text(encoding="utf-8")) if d]
    deps = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Deployment"}
    env = {n: {e["name"]: e.get("value") for e in d["spec"]["template"]["spec"]["containers"][0].get("env", []) or []} for n, d in deps.items()}
    container = {n: d["spec"]["template"]["spec"]["containers"][0]["name"] for n, d in deps.items()}
    cpu = {n: d["spec"]["template"]["spec"]["containers"][0]["resources"] for n, d in deps.items()}
    addr = {v.split(":")[0]: v for e in env.values() for k, v in e.items() if k.endswith("_ADDR") and v}

    def recreate(t):
        return [K("-n", "{ns}", "delete", "pod", "-l", f"app={t}", "--wait=true", "--timeout=90s"),
                K("-n", "{ns}", "rollout", "status", f"deployment/{t}", "--timeout=140s")]

    # ---- parts. A part changes deployments of the release batch (`changes`: deployment -> {"env": {...}, "cpu": "25m"}),
    # may add its own steps, and is either a root cause (`cause`) or a harmless member (`why_harmless`).
    def cpu_limit(t):
        lim, good = CPU_LIMIT[t], cpu[t]
        return {"changes": {t: {"cpu": lim}},
                "cause": {"object": {"kind": "Deployment", "name": t}, "mechanism": "cpu", "type": "cpu",
                          "explanation": f"Deployment {t} 的 CPU 上限在这次发布里被改成 {lim}（原来 {good['limits']['cpu']}），容器被限流，请求要排队等 CPU；网络没有问题。",
                          "key_evidence": [lim], "decidable": True,
                          "decidable_why": "指标表里它的限流周期占比明显大于 0，Deployment 里能看到上限；被限流的服务和变慢的页面对得上。",
                          "evidence": [f"Deployment {t} 的 CPU 上限是 {lim}，上一版 ReplicaSet 里是 {good['limits']['cpu']}",
                                       f"指标表：{t} 的 CPU 用量顶在上限附近，被限流的周期占比明显大于 0；别的服务不被限流或只有零星几次",
                                       ("所有页面都变慢，一个页面要调的后端越多、要渲染的越多就慢得越多；frontend 自己被限流，后端不被限流"
                                        if t == "frontend" else f"调用 {t} 次数多的页面慢得多，不调用它的请求不慢")]}}

    def addr_other(caller, var, other):
        good, bad = env[caller][var], addr[other]
        return {"changes": {caller: {"env": {var: bad}}}, "reset_env": {caller: {var: good}},
                "cause": {"object": {"kind": "Deployment", "name": caller}, "mechanism": "config", "type": "config",
                          "explanation": f"{caller} 的环境变量 {var} 在这次发布里被改成 {bad}（应为 {good}）：地址能解析、端口也通，但对面是 {other}，不提供这个接口；{good.split(':')[0]} 和 {other} 自身都没有问题。",
                          "key_evidence": [bad], "decidable": True,
                          "decidable_why": "环境变量的值在 Deployment 里看得到，调用方日志里有调用失败（未实现的接口）的报错。",
                          "evidence": [f"{caller} 的日志里调用 {good.split(':')[0]} 的请求报错（对方不认识这个接口）",
                                       f"Deployment {caller} 的 {var}={bad}，指向的是 {other} 的 Service；上一版 ReplicaSet 里是 {good}",
                                       f"{good.split(':')[0]} 自己的日志里没有收到这些请求"]}}

    def env_port(caller, var):
        good = env[caller][var]
        host, port = good.split(":")
        bad = f"{host}:{int(port) + 1}"
        return {"changes": {caller: {"env": {var: bad}}}, "reset_env": {caller: {var: good}},
                "cause": {"object": {"kind": "Deployment", "name": caller}, "mechanism": "config", "type": "config",
                          "explanation": f"{caller} 的环境变量 {var} 在这次发布里被改成 {bad}（应为 {good}），连不上 {host}；{host} 自身没有问题。",
                          "key_evidence": [bad], "decidable": True,
                          "decidable_why": "环境变量的值在 Deployment 里看得到，调用方日志里有连接失败的报错。",
                          "evidence": [f"{caller} 的日志里连接 {host} 失败（连接被拒或超时）",
                                       f"Deployment {caller} 的 {var}={bad}，Service {host} 的端口是 {port}"]}}

    def selector_other(t, other):
        patch = lambda v: json.dumps({"spec": {"selector": {"app": v}}})  # noqa: E731
        callers = sorted(c for c, e in env.items() if c != "loadgenerator" and any(k.endswith("_ADDR") and v.split(":")[0] == t for k, v in e.items()))
        return {"steps": [K("-n", "{ns}", "patch", "service", t, "--type=merge", "-p", patch(other)), *recreate(t)],
                "reset": [K("-n", "{ns}", "patch", "service", t, "--type=merge", "-p", patch(t)), *[st for c in callers for st in recreate(c)]],
                "cause": {"object": {"kind": "Service", "name": t}, "mechanism": "config", "type": "config",
                          "explanation": f"Service {t} 的 selector 被改成 app={other}：端点不为空，但选中的是 {other} 的 Pod，流量被送错了地方；{t} 和 {other} 的 Pod 自身都没有问题。",
                          "key_evidence": [f"app={other}"], "decidable": True,
                          "decidable_why": "Service 的 selector 和 EndpointSlice 里的 Pod 都看得到，调用方日志里有调用失败的报错。",
                          "evidence": [f"调用 {t} 的服务日志里请求失败（对方不认识这个接口）",
                                       f"Service {t} 的 selector 是 app={other}，EndpointSlice 里的地址是 {other} 的 Pod",
                                       f"{t} 自己的 Pod 正常，日志里没有收到这些请求"]}}

    def extra_latency(t="productcatalogservice"):
        return {"changes": {t: {"env": {"EXTRA_LATENCY": "290ms"}}}, "reset_env": {t: {"EXTRA_LATENCY": None}},
                "cause": {"object": {"kind": "Deployment", "name": t}, "mechanism": "app", "type": "app_delay",
                          "explanation": f"{t} 在这次发布里被加了环境变量 EXTRA_LATENCY=290ms，服务处理每个请求时主动等待约 290 毫秒；网络和资源都没有问题。",
                          "key_evidence": ["EXTRA_LATENCY"], "decidable": True,
                          "decidable_why": "环境变量在 Deployment 里看得到；CPU 不被限流。",
                          "evidence": [f"Deployment {t} 多了 EXTRA_LATENCY=290ms，上一版没有",
                                       f"调用 {t} 的每一跳都多了约 290 毫秒，{t} 不被限流"]}}

    centres = iter([195, 245, 175, 265, 215, 185, 235, 255, 205, 165, 275, 225] * 3)   # none is a multiple of 100, with or without +60

    def net(t, loss=None):
        if loss:
            args, kind = loss, "net_loss"
            what = f"{t} 的 Pod 网络有约 {loss.split()[1]} 的丢包；被丢的包要等重传，所以只有一部分请求变慢，它自身处理不慢。"
            ev = [f"变慢的只是一部分请求，慢的那些多出来的时间成一个个台阶（重传等待），不是平均地都慢一点",
                  f"慢的请求都经过 {t}；{t} 的 CPU、限流、重启都正常"]
        else:
            c = next(centres) + (60 if t in GO or t == "redis-cart" else 0)
            args, kind = f"delay {c}ms {round(c * 0.3)}ms", "net_delay"
            what = (f"{t} 的 Pod 网络被加了延迟：进入这个 Pod 的每个包延迟 {c - round(c * 0.3)} 到 {c + round(c * 0.3)} 毫秒之间的随机值（中心 {c} 毫秒）；"
                    f"它自身处理不慢，调用它的服务跟着变慢。")
            ev = [f"调用 {t} 的请求每次多出 {c - round(c * 0.3)} 到 {c + round(c * 0.3)} 毫秒左右，不随并发增长",
                  f"{t} 的 CPU、限流、重启都正常，没有报错"]
        decidable = t in COUNTABLE
        if decidable:
            why = (f"{t} 会调用别的服务，而且不同操作经过它网卡的回包次数不同；多出来的时间和这个次数成正比，进程内卡顿解释不了这一点；"
                   f"限流为 0 排除了 CPU。")
            ev.append(f"按操作比较：{t} 的不同操作多出来的时间之比，等于各自进入 {t} 的包（请求加下游回包）的次数之比")
        elif t == "recommendationservice":
            why = "它的每个请求都是同一种（进来一次请求、收到一次商品目录的回包），没有次数不同的操作可比；日志和指标分不开“网络”和“进程在处理前卡住”。"
        else:
            why = "它不调用别的服务：请求晚到、处理很快，和“进程在处理前卡住”在日志和指标上一模一样；没有节点、抓包或链路追踪就分不开。"
        if t in ("paymentservice", "emailservice"):
            ev.insert(0, f"checkoutservice 的日志里，从上一步到 {t} 收到请求（{t} 自己日志的时间）隔了一段，{t} 自己处理只要几毫秒")
        if t == "redis-cart":
            ev.insert(0, "cartservice 的 CPU、限流正常；购物车操作的耗时和它要发给 Redis 的命令条数成正比，加购（两条命令）比看购物车（一条）多一倍")
        if t == "productcatalogservice":
            ev.insert(0, "推荐服务慢得最明显（每次要取整个商品列表），但前端直接查商品、加购（先查一次商品）也都多出同样量级的时间")
        return {"net": [(t, args)],
                "cause": {"object": {"kind": "Pod", "name": t}, "accept_objects": [t], "mechanism": "network", "type": kind,
                          "explanation": what, "key_evidence": [t], "decidable": decidable, "decidable_why": why, "evidence": ev}}

    def tag(t, why="只是滚动发布了同一个版本（RELEASE_TAG 变了），配置和资源都没变"):
        return {"changes": {t: {}}, "harmless": t, "change": "RELEASE_TAG", "why_harmless": why}

    def fqdn(t, var):
        good = env[t][var]
        host, port = good.split(":")
        full = f"{host}.boutique.svc.cluster.local:{port}"
        return {"changes": {t: {"env": {var: full}}}, "reset_env": {t: {var: good}}, "harmless": t, "change": f"{var}={full}",
                "why_harmless": f"{var} 从 {good} 改成了完整域名 {full}，指向的是同一个 Service，调用正常"}

    def cpu_ok(t, lim):
        return {"changes": {t: {"cpu": lim}}, "harmless": t, "change": f"cpu limit {lim}",
                # validation 2026-11-24: shipping/checkout/email/payment at these limits are not throttled, except paymentservice at
                # 80m, which has a few throttled periods per minute (bursts of a single charge); it is on no slow request's path
                "why_harmless": f"CPU 上限从 {cpu[t]['limits']['cpu']} 调到 {lim}，仍远高于它的平均用量，最多有零星几次限流，解释不了用户看到的症状"}

    def zero(t, why="它的失败被调用方吞掉了，用户报告的症状不是它引起的"):
        return {"steps": [K("-n", "{ns}", "scale", f"deployment/{t}", "--replicas=0")], "reset": [K("-n", "{ns}", "scale", f"deployment/{t}", "--replicas=1")],
                "harmless": t, "change": "replicas 0", "why_harmless": why, "anomaly": True}

    slow_check = lambda count, min_ms=110: {"type": "slow_requests", "selector": "app=frontend", "field": "http.resp.took_ms",  # noqa: E731
                                            "match": {"message": "request complete"}, "min_ms": min_ms, "count": count, "since_confirm": True}
    checkout_check = {"type": "slow_requests", "selector": "app=frontend", "field": "http.resp.took_ms", "since_confirm": True,
                      "match": {"message": "request complete", "http.req.path": "/cart/checkout"}, "min_ms": 150, "count": 3}
    order_check = {"type": "logs_contain", "selector": "app=frontend", "text": "failed to complete the order"}
    err_check = {"type": "logs_contain", "selector": "app=frontend", "text": '"http.resp.status":500'}
    checks_for = {SLOW: [slow_check(15)], FLAKY: [slow_check(8, 180)], ORDER: [order_check], ERR: [err_check],
                  BOTH: [slow_check(10), order_check], CHECKOUT: [checkout_check], SLOW_CHECKOUT: [slow_check(10), checkout_check]}

    out = []

    def add(sid, symptom, note, causes, others, guess=None, design=()):
        n = len(out) + 1
        release = TAG.format(n=n)
        parts = causes + others
        members: dict[str, dict] = {}
        for p in parts:
            for d, ch in (p.get("changes") or {}).items():
                m = members.setdefault(d, {"env": {}, "cpu": None})
                m["env"].update(ch.get("env") or {})
                m["cpu"] = ch.get("cpu") or m["cpu"]
        assert len(members) >= 2, f"{sid}: a release batch needs at least two deployments"
        inject, reset = [], []
        for d, m in members.items():   # one rollout per deployment: the tag and whatever else changed in this release
            c = {"name": container[d], "env": [{"name": "RELEASE_TAG", "value": release}, *[{"name": k, "value": v} for k, v in m["env"].items()]]}
            if m["cpu"]:
                c["resources"] = {"limits": {"cpu": m["cpu"]}, "requests": {"cpu": m["cpu"]}}
            inject += [K("-n", "{ns}", "patch", "deployment", d, "--type=strategic", "-p", json.dumps({"spec": {"template": {"spec": {"containers": [c]}}}})),
                       K("-n", "{ns}", "rollout", "status", f"deployment/{d}", "--timeout=140s")]
            back = {k: v for p in parts for k, v in ((p.get("reset_env") or {}).get(d) or {}).items()}
            reset.append(K("-n", "{ns}", "set", "env", f"deployment/{d}", "RELEASE_TAG-", *[f"{k}-" if v is None else f"{k}={v}" for k, v in back.items()]))
            if m["cpu"]:
                good = cpu[d]
                reset.append(K("-n", "{ns}", "set", "resources", f"deployment/{d}", f"--limits=cpu={good['limits']['cpu']}", f"--requests=cpu={good['requests']['cpu']}"))
        for p in parts:
            inject += p.get("steps") or []
            reset += p.get("reset") or []
        inject.append({"sleep": 25})   # let the rollouts' own blips scroll out of the window the confirm step reads
        nets = [x for p in parts for x in p.get("net") or []]
        checks = list(checks_for[symptom])
        if nets:
            names = "、".join(t for t, _ in nets)
            specs = " ".join(f'"{t}|{a}"' for t, a in nets)
            (NETEM / f"injector-boutique-v3-{sid}.yaml").write_text(INJECTOR.format(sid=sid, names=names, specs=specs), encoding="utf-8")
            inject += [K("-n", "infra-tools", "delete", "pod", "net-injector", "--ignore-not-found", "--wait=true", "--timeout=90s"),
                       K("apply", "-f", "{repo}/scenarios/netem/" + f"injector-boutique-v3-{sid}.yaml"),
                       K("-n", "infra-tools", "wait", "--for=condition=Ready", "pod/net-injector", "--timeout=180s")]
            reset.insert(0, K("-n", "infra-tools", "delete", "pod", "net-injector", "--ignore-not-found", "--wait=true", "--timeout=90s"))
            checks.append({"type": "logs_contain", "namespace": "infra-tools", "selector": "app=net-injector", "text": f"netem applied: {len(nets)}"})
        cs = [p["cause"] for p in causes]
        answer = {"status": "root_cause_found", "cause_category": "other", **cs[0]}
        if cs[1:]:
            answer["also"] = cs[1:]
        harmless = [p for p in others if p.get("harmless")]
        # harmless_changes: every change in this release that is not a cause (reporting one as a cause is over-reporting);
        # not_cause: the objects among them that are not a cause in any way (a cause object can also carry a harmless change)
        answer["harmless_changes"] = [{"object": p["harmless"], "change": p["change"], "why": p["why_harmless"]} for p in harmless]
        cause_objects = {c["object"]["name"] for c in cs}
        answer["not_cause"] = [{"name": p["harmless"], "why": p["why_harmless"]} for p in harmless if p["harmless"] not in cause_objects]
        for c in cs:
            assert c["object"]["name"] not in (guess or "") and c["object"]["name"] not in symptom, f"{sid}: the ticket names a real cause"
        ticket = symptom + (f"{guess}" if guess else "")
        chain = [f"{c['object']['name']}（{c['mechanism']}）：" + "；".join(c["evidence"]) for c in cs]
        chain += [f"排除 {p['harmless']} 的改动（{p['change']}）：{p['why_harmless']}" for p in harmless]
        out.append({"id": sid, "pool": POOL[sid[:2]], "app": "boutique", "split": "dev", "bank": 3, "kind": "cross_service",
                    "mechanism": "+".join(c["type"] for c in cs), "hard": "+".join(design) or "release",
                    "root_causes": len(cs), "symptom": ticket, "settle_s": SETTLE_S,
                    "release": {"tag": release, "deployments": sorted(members), "changes": {d: {k: v for k, v in m.items() if v} for d, m in members.items()}},
                    "ticket_guess": guess, "design": list(design), "evidence_chain": chain,
                    "realism": {"pattern": note, "sources": ["Cloud-OpsBench", "k8s.af", "K8s排障Agent测试平台_故障难度提升方案"], "validated": False,
                                "validate_note": "确认每个故障都生效；无害成员的服务不变慢、不被限流（看验证记录里的限流表）；延迟和丢包场景确认被注入的 Pod 仍是 Ready、重启次数为 0。"},
                    "inject": inject,
                    "confirm": {"timeout_s": 180, "checks": checks,
                                "debug_on_fail": [["-n", "infra-tools", "logs", "net-injector", "--tail=20"], ["-n", "{ns}", "logs", "-l", "app=frontend", "--tail=6"],
                                                  ["-n", "{ns}", "get", "pods", "-o", "wide"]]},
                    "reset": reset, "answer": answer,
                    "mechanisms": sorted({c["type"] for c in cs})})

    G_SHIP = "（值班同事怀疑是 shippingservice 刚才那次发布引起的。）"

    # ---- EL: learning. Experience is written from these, so no single (service, mechanism) that is the only cause of a
    # test scenario appears here alone.
    add("EL01", SLOW, "currencyservice 的 CPU 上限在一次多服务发布里被改小；同一批里 shippingservice 的上限也调低了，但仍够用。",
        [cpu_limit("currencyservice")], [cpu_ok("shippingservice", "80m"), tag("adservice")], design=("release", "lookalike"))
    add("EL02", SLOW, "cartservice 的 Pod 网络延迟。同一时段有一批无害的发布。",
        [net("cartservice")], [tag("productcatalogservice"), tag("emailservice"), cpu_ok("paymentservice", "80m")], design=("release", "lookalike"))
    add("EL03", SLOW, "redis-cart 的 Pod 网络延迟（二级依赖）。它的调用方 cartservice 刚好在同一批发布里，工单还猜是它。",
        [net("redis-cart")], [tag("cartservice"), tag("recommendationservice")], guess="（有同事说 cartservice 刚发过版，可能是新版本有问题。）",
        design=("release", "second_level", "wrong_guess"))
    add("EL04", SLOW, "两个互不相关、机制也不同的故障：frontend 的 CPU 上限过低，加上 productcatalogservice 的 Pod 网络延迟（推荐服务会被当成原因）。",
        [cpu_limit("frontend"), net("productcatalogservice")], [tag("recommendationservice"), cpu_ok("checkoutservice", "120m")], design=("release", "double", "second_level", "lookalike"))
    add("EL05", ORDER, "checkoutservice 的支付服务地址被指到 emailservice；同一次发布里它的商品目录地址也改了，但只是写成了完整域名。",
        [addr_other("checkoutservice", "PAYMENT_SERVICE_ADDR", "emailservice")], [fqdn("checkoutservice", "PRODUCT_CATALOG_SERVICE_ADDR"), tag("currencyservice")],
        design=("release", "lookalike"))
    add("EL06", ERR, "shippingservice 的 Service 选中了 paymentservice 的 Pod。同一时段有一批无害的发布，工单猜是前端。",
        [selector_other("shippingservice", "paymentservice")], [tag("frontend"), cpu_ok("checkoutservice", "120m")],
        guess="（客服那边觉得是前端刚上线的新版本有 bug。）", design=("release", "lookalike", "wrong_guess"))
    add("EL07", FLAKY, "currencyservice 的 Pod 网络少量丢包。同一时段有一批无害的发布。",
        [net("currencyservice", LOSS)], [tag("cartservice"), fqdn("frontend", "CART_SERVICE_ADDR")], design=("release", "lookalike"))
    add("EL08", SLOW, "两个互不相关、机制也不同的故障：cartservice 的 CPU 上限过低，加上 recommendationservice 的 Pod 网络延迟。",
        [cpu_limit("cartservice"), net("recommendationservice")], [tag("shippingservice"), fqdn("frontend", "RECOMMENDATION_SERVICE_ADDR")], design=("release", "double", "lookalike"))
    add("EL09", SLOW, "不是网络：productcatalogservice 被配置了人为延迟（EXTRA_LATENCY）。工单猜是网络。",
        [extra_latency()], [tag("currencyservice"), cpu_ok("emailservice", "120m")], guess="（网络组说这两天机房交换机在割接，可能是网络抖动。）",
        design=("release", "lookalike", "wrong_guess"))
    add("EL10", CHECKOUT, "paymentservice 的 Pod 网络延迟（二级依赖，只有下单经过它）。它的调用方 checkoutservice 在同一批发布里。",
        [net("paymentservice")], [tag("checkoutservice"), tag("shippingservice")], guess="（有人怀疑是 checkoutservice 刚上的新版本。）",
        design=("release", "second_level", "wrong_guess"))

    # ---- EV: validation (admission check, and the pilot of the new bank)
    add("EV01", SLOW, "frontend 的 CPU 上限过低，只有这一个故障。和 EV02 成对：症状相同、少一个网络故障。",
        [cpu_limit("frontend")], [cpu_ok("checkoutservice", "120m"), tag("emailservice")], design=("release", "lookalike", "paired"))
    add("EV02", SLOW, "两个互不相关、机制也不同的故障：frontend 的 CPU 上限过低，加上 currencyservice 的 Pod 网络延迟。和 EV01 成对。",
        [cpu_limit("frontend"), net("currencyservice")], [cpu_ok("checkoutservice", "120m"), tag("emailservice")], design=("release", "double", "paired"))
    add("EV03", CHECKOUT, "checkoutservice 的 Pod 网络延迟：下单要等它的每一个下游回包，慢得很明显；机制可以靠“多出来的时间和回包次数成正比”判断。",
        [net("checkoutservice")], [tag("paymentservice"), cpu_ok("shippingservice", "80m")], guess=G_SHIP, design=("release", "lookalike", "wrong_guess"))
    add("EV04", ERR, "frontend 的运费服务地址被指到 cartservice；同一次发布里它的商品目录地址写成了完整域名。",
        [addr_other("frontend", "SHIPPING_SERVICE_ADDR", "cartservice")], [fqdn("frontend", "PRODUCT_CATALOG_SERVICE_ADDR"), tag("adservice")],
        design=("release", "lookalike"))
    add("EV05", SLOW, "productcatalogservice 的 Pod 网络延迟（二级依赖：推荐服务最慢，最容易被当成原因）。推荐服务在同一批发布里。",
        [net("productcatalogservice")], [tag("recommendationservice"), cpu_ok("paymentservice", "80m")], design=("release", "second_level", "lookalike"))
    add("EV06", FLAKY, "redis-cart 的 Pod 网络丢包。它的调用方 cartservice 在同一批发布里，地址写成了完整域名。",
        [net("redis-cart", LOSS)], [fqdn("cartservice", "REDIS_ADDR"), tag("currencyservice")], guess="（有人说 cartservice 新版本改了 Redis 地址，可能是这个。）",
        design=("release", "second_level", "lookalike", "wrong_guess"))
    add("EV07", SLOW_CHECKOUT, "两个互不相关的故障：currencyservice 的 CPU 上限过低（页面变慢），加上 emailservice 的 Pod 网络延迟（下单更慢，二级依赖）。",
        [cpu_limit("currencyservice"), net("emailservice")], [tag("checkoutservice"), cpu_ok("paymentservice", "100m")], design=("release", "double", "second_level", "lookalike"))
    add("EV08", SLOW, "cartservice 的 CPU 上限过低，只有这一个故障。同一批里 adservice 被缩到 0（取广告失败会被前端吞掉）。和 EL08 成对。",
        [cpu_limit("cartservice")], [tag("recommendationservice"), cpu_ok("paymentservice", "80m"), zero("adservice")], guess="（有人看到 adservice 没有 Pod 了，怀疑是它。）",
        design=("release", "anomaly", "wrong_guess", "paired"))

    # ---- ET: test. Not run until the method is fixed.
    add("ET01", SLOW, "frontend 的 Pod 网络延迟：每个后端的回包都经过它的网卡，页面越复杂越慢；机制可以靠“多出来的时间和 RPC 次数成正比”判断，CPU 不被限流。",
        [net("frontend")], [cpu_ok("shippingservice", "80m"), tag("productcatalogservice")], guess="（有同事怀疑是 productcatalogservice 刚发的版本。）",
        design=("release", "lookalike", "wrong_guess"))
    add("ET02", SLOW, "recommendationservice 的 Pod 网络延迟，只有这一个故障。",
        [net("recommendationservice")], [tag("cartservice"), fqdn("recommendationservice", "PRODUCT_CATALOG_SERVICE_ADDR")], design=("release", "lookalike"))
    add("ET03", CHECKOUT, "emailservice 的 Pod 网络延迟（二级依赖，只有下单经过它）。checkoutservice 在同一批发布里。",
        [net("emailservice")], [tag("checkoutservice"), cpu_ok("paymentservice", "80m")], design=("release", "second_level", "lookalike"))
    add("ET04", FLAKY, "productcatalogservice 的 Pod 网络少量丢包。",
        [net("productcatalogservice", LOSS)], [tag("recommendationservice"), tag("frontend")], design=("release", "second_level"))
    add("ET05", SLOW, "cartservice 的 CPU 上限过低，只有这一个故障。同一批里还有一个看着可疑的地址改写和一个调低但够用的 CPU 上限。和 ET16 成对。",
        [cpu_limit("cartservice")], [fqdn("frontend", "CURRENCY_SERVICE_ADDR"), cpu_ok("shippingservice", "80m")], design=("release", "lookalike", "paired"))
    add("ET06", SLOW, "frontend 的 CPU 上限过低，只有这一个故障。工单猜是货币服务。和 ET11 成对。",
        [cpu_limit("frontend")], [tag("currencyservice"), cpu_ok("emailservice", "120m")], guess="（有人说 currencyservice 昨天也慢过，可能是它的网络。）",
        design=("release", "lookalike", "wrong_guess", "paired"))
    add("ET07", ORDER, "paymentservice 的 Service 选中了 emailservice 的 Pod。同一批里 checkoutservice 的支付地址被写成完整域名（无害）。",
        [selector_other("paymentservice", "emailservice")], [fqdn("checkoutservice", "PAYMENT_SERVICE_ADDR"), tag("shippingservice")], design=("release", "lookalike"))
    add("ET08", ERR, "frontend 的货币服务地址被指到 productcatalogservice。",
        [addr_other("frontend", "CURRENCY_SERVICE_ADDR", "productcatalogservice")], [tag("cartservice"), cpu_ok("paymentservice", "80m")], design=("release", "lookalike"))
    add("ET09", ERR, "cartservice 的 Service 选中了 redis-cart 的 Pod。工单猜是推荐服务。",
        [selector_other("cartservice", "redis-cart")], [tag("recommendationservice"), tag("emailservice")], guess="（有人看到推荐服务刚发过版。）",
        design=("release", "wrong_guess"))
    add("ET10", SLOW, "两个机制不同的故障：productcatalogservice 被配置了人为延迟，加上 cartservice 的 Pod 网络延迟。",
        [extra_latency(), net("cartservice")], [tag("currencyservice"), cpu_ok("shippingservice", "100m")], design=("release", "double", "lookalike"))
    add("ET11", SLOW, "两个互不相关、机制也不同的故障：frontend 的 CPU 上限过低，加上 recommendationservice 的 Pod 网络延迟。和 ET06 成对。",
        [cpu_limit("frontend"), net("recommendationservice")], [tag("currencyservice"), cpu_ok("emailservice", "120m")], design=("release", "double", "paired"))
    add("ET12", SLOW, "两个互不相关、机制也不同的故障：currencyservice 的 CPU 上限过低，加上 productcatalogservice 的 Pod 网络延迟（推荐服务会被当成原因）。",
        [cpu_limit("currencyservice"), net("productcatalogservice")], [tag("recommendationservice"), cpu_ok("emailservice", "120m")], design=("release", "double", "second_level", "lookalike"))
    add("ET13", BOTH, "两个互不相关的故障：cartservice 的 CPU 上限过低，加上 checkoutservice 的运费服务端口配错。",
        [cpu_limit("cartservice"), env_port("checkoutservice", "SHIPPING_SERVICE_ADDR")], [tag("adservice"), fqdn("checkoutservice", "CART_SERVICE_ADDR")], design=("release", "double", "lookalike"))
    add("ET14", SLOW_CHECKOUT, "两个互不相关的故障：frontend 的 CPU 上限过低，加上 checkoutservice 的 Pod 网络延迟。",
        [cpu_limit("frontend"), net("checkoutservice")], [tag("paymentservice"), cpu_ok("shippingservice", "100m")], design=("release", "double", "lookalike"))
    add("ET15", SLOW, "两个服务的 CPU 上限在同一次发布里过低：currencyservice 和 frontend。没有网络故障。",
        [cpu_limit("currencyservice"), cpu_limit("frontend")], [cpu_ok("shippingservice", "80m")], design=("release", "double", "lookalike"))
    add("ET16", FLAKY, "两个互不相关、机制也不同的故障：cartservice 的 CPU 上限过低，加上 currencyservice 的 Pod 网络丢包。和 ET05 成对。",
        [cpu_limit("cartservice"), net("currencyservice", LOSS)], [fqdn("frontend", "CURRENCY_SERVICE_ADDR"), cpu_ok("shippingservice", "80m")],
        design=("release", "double", "paired"))

    OUT.write_text("# Generated by boutique/make_v3_scenarios.py — do not edit by hand. Third bank: EL = learning, EV = validation, ET = test.\n"
                   "# ET is not run by any agent until the method is fixed. See docs/V3.md.\n"
                   + yaml.safe_dump({"scenarios": out}, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
    from collections import Counter
    print(len(out), dict(Counter(s["pool"] for s in out)))


if __name__ == "__main__":
    main()
