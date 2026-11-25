"""Generate the fourth Online Boutique bank (scenarios/boutique_v4.yaml: FL = learning, FV = validation, FT = test) from the
third bank (scenarios/boutique_v3.yaml, which this script does not change).

  python boutique/make_v3_scenarios.py && python boutique/make_v4_scenarios.py

Why a fourth bank (docs/V4.md, from the trajectories of the third bank's pilot, 2026-11-24):
  - the namespace's logs held nothing but request lines, so any warning the agent filtered for was the fault, and every
    run started minutes after the fault with the onset in the first screen of logs;
  - the CPU fault was the only throttled row in the metrics table;
  - the probes of a pod whose packets are delayed by about 300 ms time out (a new connection needs three packets into the
    pod), which tells "network" from "process stalls" although the third bank had labelled such causes undecidable; and
    packet loss makes probe connections time out too.
The faults, release batches, harmless lookalikes and wrong guesses are the third bank's. What changes:

  background workloads  boutique/ambient-v4.yaml, applied by the lifecycle for bank 4 only: a batch job with recurring
                        harmless warnings and errors whose small CPU limit keeps it throttled, a synthetic uptime check and
                        a crawler. Reporting any of them as a cause is over-reporting (answer.ambient).
  delayed investigation investigate_after_s: the agent is asked 10 minutes after the fault took effect; the ticket says
                        the problem started about ten-odd minutes ago.
  network delays        the centre of every network delay is chosen so that the label is not left to chance: on a service
                        with a gRPC probe, either at most 215 ms (three delayed packets stay under the 1 s probe timeout:
                        undecidable) or 285 ms (about 5% of the probes time out, some ten failures in a 20-minute
                        window: decidable; three in a row, which restarts the pod or takes it out of the Service, stay
                        rare, about one in 10,000 probes). Delays around 250-270 ms make the probe evidence appear in some
                        runs only; 315 ms (the third bank's EV05) times out a quarter of the probes.
  labels                decidable = the service's own operations differ in how many packets enter it (cartservice,
                        checkoutservice, frontend), or packet loss (probe connections time out), or a gRPC probe and a
                        delay of 285 ms or more. redis-cart's probe is a bare TCP connect (one packet in), so a delay there never
                        shows on its probe.
  throttled sidecar     (2026-11-25, the user asked for harder CPU questions: on the third bank's pilot every CPU and
                        configuration cause was found, and the harmless CPU lookalikes were never throttled, so "the
                        throttled shop service is the cause" still worked; promo-feed-sync is off every request path and
                        easy to set aside.) Every scenario's release batch now also adds a log-shipper sidecar container
                        with a 20m CPU limit to one shop service (SIDECAR). The sidecar burns 20-25 ms of CPU every few
                        seconds, so it is throttled all the time, and the metrics table, which sums a pod's containers,
                        shows that service's pod throttled at about the level of a real CPU fault. CPU quotas are per
                        container: the service's own container is untouched and no request waits for the sidecar, so it
                        is not a cause (answer.harmless_changes; reporting it is over-reporting). It cannot be set aside
                        by the request path: the service sits on the path of the reported symptom. What tells it apart:
                        the pod's CPU use is far below its business container's limit (a throttled business container
                        sits at its limit), the pod has a second container with a 20m limit, and the sidecar's own log
                        shows each batch stretched to about a second. It is never put on a cause, on a direct caller of
                        a service with a network fault (that trap is the second-level one, not stacked on it), on the
                        service the ticket guesses, on the frontend (the user-side numbers and confirm checks read its
                        logs) or on redis-cart. Paired scenarios put it on the same service.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "boutique"))
from make_hard_scenarios import INJECTOR, K  # noqa: E402
from make_v3_scenarios import COUNTABLE  # noqa: E402

V3 = REPO / "scenarios" / "boutique_v3.yaml"
OUT = REPO / "scenarios" / "boutique_v4.yaml"
NETEM = REPO / "scenarios" / "netem"
MANIFEST = REPO / "boutique" / "kubernetes-manifests-v0.10.3.yaml"
INVESTIGATE_AFTER_S = 600
DURATION = "这个情况大概从十几分钟前开始。"
TAG_V3, TAG_V4 = "2026.11.24-", "2026.11.25-"
UNDECIDABLE_MAX, PROBE_DECIDABLE = 215, (285, 295)

# Third-bank id -> {service: centre of the delay in ms}. Loss stays "loss 6%" (decidable through the probes).
CENTRE = {
    "EL02": {"cartservice": 195}, "EL03": {"redis-cart": 305}, "EL04": {"productcatalogservice": 205},
    "EL08": {"recommendationservice": 285}, "EL10": {"paymentservice": 215},
    "EV02": {"currencyservice": 185}, "EV03": {"checkoutservice": 285}, "EV05": {"productcatalogservice": 285},
    "EV07": {"emailservice": 205},
    "ET01": {"frontend": 225}, "ET02": {"recommendationservice": 195}, "ET03": {"emailservice": 285},
    "ET10": {"cartservice": 195}, "ET11": {"recommendationservice": 285}, "ET12": {"productcatalogservice": 185},
    "ET14": {"checkoutservice": 275},
}
AMBIENT = [
    {"name": "promo-feed-sync", "why": "后台批处理任务，不在任何用户请求的路径上；CPU 上限只有 20m，平时就一直被限流；日志里的外部接口超时、上传失败和商城请求无关"},
    {"name": "uptime-checker", "why": "拨测，只是一个流量来源；它看到的变慢是症状，不是原因"},
    {"name": "catalog-crawler", "why": "爬虫，只是一个流量来源；它的 404 是在访问不存在的路径"},
]
# Which shop service gets the log-shipper sidecar in each scenario. It is on the path of the reported symptom (slow pages:
# a service the pages call; orders: one the checkout calls; errors: one the failing pages call where possible) and obeys the
# rules checked in add_sidecar(). A service already in the release batch is preferred; otherwise it joins the batch.
SIDECAR = {
    "FL01": "adservice", "FL02": "productcatalogservice", "FL03": "recommendationservice", "FL04": "cartservice",
    "FL05": "currencyservice", "FL06": "currencyservice", "FL07": "cartservice", "FL08": "shippingservice",
    "FL09": "currencyservice", "FL10": "shippingservice",
    "FV01": "productcatalogservice", "FV02": "productcatalogservice", "FV03": "paymentservice", "FV04": "adservice",
    "FV05": "currencyservice", "FV06": "currencyservice", "FV07": "cartservice", "FV08": "recommendationservice",
    "FT01": "currencyservice", "FT02": "cartservice", "FT03": "shippingservice", "FT04": "currencyservice",
    "FT05": "productcatalogservice", "FT06": "cartservice", "FT07": "shippingservice", "FT08": "cartservice",
    "FT09": "currencyservice", "FT10": "currencyservice", "FT11": "cartservice", "FT12": "cartservice",
    "FT13": "adservice", "FT14": "paymentservice", "FT15": "productcatalogservice", "FT16": "productcatalogservice",
}
PAIRED = [("FV01", "FV02"), ("FT05", "FT16"), ("FT06", "FT11")]
NEVER_SIDECAR = {"frontend", "redis-cart", "loadgenerator"}
SIDECAR_NAME, SIDECAR_CPU = "log-shipper", "20m"
SIDECAR_IMAGE = "us-central1-docker.pkg.dev/google-samples/microservices-demo/loadgenerator:v0.10.3"   # already on the node
SIDECAR_VOLUME = {"name": SIDECAR_NAME, "secret": {"secretName": "ops-jobs", "defaultMode": 0o444,
                                                   "items": [{"key": "log_shipper.py", "path": "log_shipper.py"}]}}
# Reset: strategic-merge deletes by name. Runs first, before any `kubectl set resources`/`set env`, which without -c would
# also change the sidecar. Deleting a container or volume that is not there is a no-op, so the reset may run twice.
SIDECAR_DELETE = {"spec": {"template": {"spec": {"containers": [{"name": SIDECAR_NAME, "$patch": "delete"}],
                                                 "volumes": [{"name": SIDECAR_NAME, "$patch": "delete"}]}}}}
CPU_ALONE = "别的服务不被限流或只有零星几次"
# v3 wrote that the faulty container's use sits at its limit; measured on the cluster (FV01/FL01 2026-11-25) the average is
# only about 40-50% of it: CFS throttles when a burst of requests uses up the 100ms quota, not because the average is high.
CPU_AT_LIMIT = "的 CPU 用量顶在上限附近，被限流的周期占比明显大于 0"
CPU_MEASURED = "被限流的周期占比明显大于 0，平均 CPU 用量约为上限的四到五成（不一定顶到上限：请求成批到来时会把每 100ms 的配额用完）"


def sidecar_container() -> dict:
    return {"name": SIDECAR_NAME, "image": SIDECAR_IMAGE, "imagePullPolicy": "IfNotPresent",
            "command": ["python3", "-u", "/opt/log-shipper/log_shipper.py"],
            "env": [{"name": "PYTHONDONTWRITEBYTECODE", "value": "1"}],
            "resources": {"requests": {"cpu": SIDECAR_CPU, "memory": "32Mi"}, "limits": {"cpu": SIDECAR_CPU, "memory": "64Mi"}},
            "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}, "privileged": False,
                                "readOnlyRootFilesystem": True},
            "volumeMounts": [{"name": SIDECAR_NAME, "mountPath": "/opt/log-shipper", "readOnly": True}]}


def sidecar_why(t: str, limit: str) -> str:
    return (f"同一次发布给 {t} 加了日志转发的 sidecar 容器 {SIDECAR_NAME}，CPU 上限只有 {SIDECAR_CPU}。它每隔几秒处理一批日志，每批要二十多毫秒 CPU，"
            f"在 {SIDECAR_CPU} 的配额下要排一秒左右，所以一直被限流；指标表按 Pod 合计，{t} 这个 Pod 的限流占比明显大于 0，上限一列是两个容器之和。"
            f"但 CPU 配额按容器算：{t} 的业务容器上限 {limit}，Pod 的用量远低于这个数，业务容器没有被限流；sidecar 不在任何请求的路径上，"
            f"它自己的日志里每批耗时一秒左右，被拉长的是它自己。所以 {t} 处理请求不慢，解释不了用户看到的症状")


def manifest_facts() -> dict:
    """Main container name and CPU limit per deployment, and who calls whom (the *_ADDR variables)."""
    main, limit, callers = {}, {}, {}
    for d in yaml.safe_load_all(MANIFEST.read_text(encoding="utf-8")):
        if not d or d["kind"] != "Deployment":
            continue
        n, c = d["metadata"]["name"], d["spec"]["template"]["spec"]["containers"][0]
        main[n], limit[n] = c["name"], c["resources"]["limits"]["cpu"]
        if n == "loadgenerator":
            continue
        for e in c.get("env", []) or []:
            if e["name"].endswith("_ADDR") and e.get("value"):
                callers.setdefault(e["value"].split(":")[0], set()).add(n)
    return {"main": main, "limit": limit, "callers": callers}


def add_sidecar(s: dict, t: str, causes: list[dict], facts: dict) -> None:
    """Put the log-shipper sidecar on deployment `t` in scenario `s`: inject, reset, release, harmless changes."""
    sid = s["id"]
    nets = [c["object"]["name"] for c in causes if c["mechanism"] == "network"]
    assert t not in NEVER_SIDECAR and t not in {c["object"]["name"] for c in causes}, f"{sid}: no sidecar on {t}"
    assert not any(t in facts["callers"].get(n, ()) for n in nets), f"{sid}: {t} calls a service with a network fault"
    assert t not in (s.get("ticket_guess") or ""), f"{sid}: the ticket guesses {t}"
    assert not any(st.get("kubectl", [])[2:4] == ["scale", f"deployment/{t}"] for st in s["inject"]), f"{sid}: {t} is scaled"
    member = next((st["kubectl"] for st in s["inject"] if st.get("kubectl", [])[2:5] == ["patch", "deployment", t]), None)
    if member:   # already in the batch: the same rollout also adds the sidecar
        patch = json.loads(member[-1])
        pod = patch["spec"]["template"]["spec"]
        assert [c["name"] for c in pod["containers"]] == [facts["main"][t]] and "resources" not in pod["containers"][0], sid
        pod["containers"].append(sidecar_container())
        pod["volumes"] = [SIDECAR_VOLUME]
        member[-1] = json.dumps(patch)
        s["release"]["changes"][t]["sidecar"] = SIDECAR_NAME
    else:        # joins the batch: same release tag, plus the sidecar
        patch = {"spec": {"template": {"spec": {"containers": [{"name": facts["main"][t], "env": [{"name": "RELEASE_TAG", "value": s["release"]["tag"]}]},
                                                               sidecar_container()], "volumes": [SIDECAR_VOLUME]}}}}
        at = next(i for i, st in enumerate(s["inject"]) if "sleep" in st)
        s["inject"][at:at] = [K("-n", "{ns}", "patch", "deployment", t, "--type=strategic", "-p", json.dumps(patch)),
                              K("-n", "{ns}", "rollout", "status", f"deployment/{t}", "--timeout=140s")]
        s["reset"].append(K("-n", "{ns}", "set", "env", f"deployment/{t}", "RELEASE_TAG-"))
        s["release"]["deployments"] = sorted([*s["release"]["deployments"], t])
        s["release"]["changes"][t] = {"sidecar": SIDECAR_NAME}
    s["reset"].insert(0, K("-n", "{ns}", "patch", "deployment", t, "--type=strategic", "-p", json.dumps(SIDECAR_DELETE)))
    a = s["answer"]
    why = sidecar_why(t, facts["limit"][t])
    a["harmless_changes"] = [h for h in a["harmless_changes"] if not (h["object"] == t and h["change"] == "RELEASE_TAG")]
    a["harmless_changes"].append({"object": t, "change": f"RELEASE_TAG，并新增 sidecar 容器 {SIDECAR_NAME}（CPU 上限 {SIDECAR_CPU}）", "why": why})
    a["not_cause"] = [n for n in a["not_cause"] if n["name"] != t] + [{"name": t, "why": why}]
    s["realism"]["validate_note"] += (f"第四版加难（sidecar）：{t} 的 Pod 有 2 个容器、2/2 就绪、不重启；按容器看，{SIDECAR_NAME} 被限流、{t} 的业务容器不被限流"
                                      f"（最多零星几次）；按 Pod 合计的窗口限流占比在 15% 到 45% 之间，和真实 CPU 故障在同一量级。")


# How many packets of one probe enter the pod, one after another: a gRPC probe opens a new connection each time (the TCP
# connect, the HTTP/2 preface, the health request), an HTTP probe needs the connect and the request, a TCP probe the connect.
LEGS = {"grpc": 3, "httpGet": 2, "tcpSocket": 1}


def probe_kind() -> dict:
    out = {}
    for d in yaml.safe_load_all(MANIFEST.read_text(encoding="utf-8")):
        if d and d["kind"] == "Deployment":
            p = d["spec"]["template"]["spec"]["containers"][0].get("readinessProbe") or {}
            out[d["metadata"]["name"]] = next((k for k in LEGS if k in p), None)
    return out


def network_cause(t: str, args: str, probes: dict) -> dict:
    loss = args.startswith("loss")
    if loss:
        kind = "net_loss"
        what = f"{t} 的 Pod 网络有约 {args.split()[1]} 的丢包；被丢的包要等重传，所以只有一部分请求变慢，它自身处理不慢。"
        ev = ["变慢的只是一部分请求，慢的那些多出来的时间成一个个台阶（重传等待），不是平均地都慢一点",
              f"慢的请求都经过 {t}；{t} 的 CPU、限流、重启都正常"]
    else:
        c = int(args.split()[1].rstrip("ms"))
        j = round(c * 0.3)
        kind = "net_delay"
        what = (f"{t} 的 Pod 网络被加了延迟：进入这个 Pod 的每个包延迟 {c - j} 到 {c + j} 毫秒之间的随机值（中心 {c} 毫秒）；"
                f"它自身处理不慢，调用它的服务跟着变慢。")
        ev = [f"调用 {t} 的请求每次多出 {c - j} 到 {c + j} 毫秒左右，不随并发增长", f"{t} 的 CPU、限流、重启都正常，没有报错"]
    if t in ("paymentservice", "emailservice"):
        ev.insert(0, f"checkoutservice 的日志里，从上一步到 {t} 收到请求（{t} 自己日志的时间）隔了一段，{t} 自己处理只要几毫秒")
    if t == "redis-cart":
        ev.insert(0, "cartservice 的 CPU、限流正常；购物车操作的耗时和它要发给 Redis 的命令条数成正比，加购（两条命令）比看购物车（一条）多一倍")
    if t == "productcatalogservice":
        ev.insert(0, "推荐服务慢得最明显（每次要取整个商品列表），但前端直接查商品、加购（先查一次商品）也都多出同样量级的时间")
    probe = probes[t]
    if t in COUNTABLE:
        decidable = True
        why = (f"{t} 会调用别的服务，而且不同操作经过它网卡的回包次数不同；多出来的时间和这个次数成正比，进程内卡顿解释不了这一点；"
               f"限流为 0 排除了 CPU。")
        ev.append(f"按操作比较：{t} 的不同操作多出来的时间之比，等于各自进入 {t} 的包（请求加下游回包）的次数之比")
    elif loss:
        decidable = True
        why = ("连接请求的包丢了要等 1 秒才重发，所以它的探针（超时 1 秒）会间歇报连接超时；进程在处理前卡住不会让连接建不起来；"
               "限流为 0 排除了 CPU。")
        ev.append(f"{t} 的就绪/存活探针间歇报连接超时（failed to connect … within 1s 或 dial tcp … i/o timeout），它的 CPU 空闲、不被限流")
    elif probe == "grpc" and c >= PROBE_DECIDABLE[0]:
        decidable = True
        why = (f"它的探针每次新建连接，要有 {LEGS[probe]} 个包依次进入这个 Pod；每个包晚约 {c} 毫秒，加起来有时会超过 1 秒的探针超时（大约二十次里一次，二十分钟里能看到十来次）。"
               f"进程内每个请求卡一次只会让探针慢 {c} 毫秒左右，不会超时；限流为 0 排除了 CPU。")
        ev.append(f"{t} 的就绪/存活探针间歇超时（timeout: health rpc did not complete within 1s），而它 CPU 空闲、不被限流")
    else:
        decidable = False
        if probe == "tcpSocket":
            why = ("它的探针只建一次 TCP 连接，只有 1 个包进入 Pod，延迟再大也不会让探针超时；请求晚到、处理很快，"
                   "和“进程在处理前卡住”在日志和指标上一样，分不开。")
        elif t == "recommendationservice":
            why = (f"它的每个请求都是同一种（进来一次请求、收到一次商品目录的回包），没有次数不同的操作可比；每个包晚不到 {UNDECIDABLE_MAX + 1} 毫秒，"
                   f"探针的 {LEGS[probe]} 个包加起来也到不了 1 秒，探针一直正常。日志和指标分不开“网络”和“进程在处理前卡住”。")
        else:
            why = (f"它不调用别的服务：请求晚到、处理很快，和“进程在处理前卡住”在日志和指标上一样；每个包晚不到 {UNDECIDABLE_MAX + 1} 毫秒，"
                   f"探针的 {LEGS[probe]} 个包加起来也到不了 1 秒，探针一直正常，没有别的观察能把两者分开。")
        ev.append(f"{t} 的探针一直正常")
    return {"object": {"kind": "Pod", "name": t}, "accept_objects": [t], "mechanism": "network", "type": kind,
            "explanation": what, "key_evidence": [t], "decidable": decidable, "decidable_why": why, "evidence": ev}


def check_centre(sid: str, t: str, c: int, probe: str | None) -> None:
    if t in COUNTABLE:
        assert c <= 285, f"{sid}: {c} ms on {t} would time out enough probes in a row to restart it"
    elif probe == "grpc":
        assert c <= UNDECIDABLE_MAX or PROBE_DECIDABLE[0] <= c <= PROBE_DECIDABLE[1], f"{sid}: {c} ms on {t} leaves the probe evidence to chance"
    assert c % 100, f"{sid}: {c} ms is a multiple of the 100 ms CFS period"


def main() -> None:
    probes = probe_kind()
    facts = manifest_facts()
    v3 = yaml.safe_load(V3.read_text(encoding="utf-8"))["scenarios"]
    assert sorted(SIDECAR) == sorted("F" + s["id"][1:] for s in v3) and all(SIDECAR[x] == SIDECAR[y] for x, y in PAIRED)
    out, joined = [], 0
    for s in v3:
        old, sid = s["id"], "F" + s["id"][1:]
        s = json.loads(json.dumps(s))   # deep copy
        a = s["answer"]
        extra = {k: a.pop(k) for k in ("status", "cause_category", "also", "harmless_changes", "not_cause") if k in a}
        causes = [a, *extra.get("also", [])]
        nets = []
        for i, c in enumerate(causes):
            if c["mechanism"] != "network":
                continue
            t = c["object"]["name"]
            if c["type"] == "net_loss":
                args = "loss 6%"
            else:
                centre = CENTRE[old][t]
                check_centre(sid, t, centre, probes[t])
                args = f"delay {centre}ms {round(centre * 0.3)}ms"
            nets.append((t, args))
            causes[i] = network_cause(t, args, probes)
        side = SIDECAR[sid]
        for c in causes:   # a real CPU fault is no longer the only throttled shop pod
            if c["mechanism"] == "cpu":
                assert CPU_ALONE in c["evidence"][1] and CPU_AT_LIMIT in c["evidence"][1], sid
                c["evidence"][1] = c["evidence"][1].replace(CPU_AT_LIMIT, CPU_MEASURED)
                c["evidence"][1] = c["evidence"][1].replace(CPU_ALONE, f"另外被限流的还有 {side} 的 Pod（来自同一次发布新加的 {SIDECAR_NAME} 容器，"
                                                                      f"这个 Pod 的用量远低于业务容器的上限）和后台的 promo-feed-sync")
                c["evidence"] = [e.replace("后端不被限流", f"后端的业务容器不被限流（{side} 的 Pod 被限流来自 sidecar）") for e in c["evidence"]]
                c["decidable_why"] += (f"{side} 的 Pod 虽然也被限流，但用量远低于它业务容器的上限，被限流的是新加的 sidecar；"
                                       f"真正被限流的业务容器，平均用量占它自己上限的比例要高得多（实测四到五成），"
                                       f"sidecar 所在 Pod 的用量只有 Pod 上限的几个百分点。")
        if nets:
            names = "、".join(t for t, _ in nets)
            specs = " ".join(f'"{t}|{x}"' for t, x in nets)
            (NETEM / f"injector-boutique-v4-{sid}.yaml").write_text(INJECTOR.format(sid=sid, names=names, specs=specs), encoding="utf-8")
        steps = json.dumps(s["inject"], ensure_ascii=False).replace(f"injector-boutique-v3-{old}.yaml", f"injector-boutique-v4-{sid}.yaml")
        s["inject"] = json.loads(steps.replace(TAG_V3, TAG_V4))
        s["release"]["tag"] = s["release"]["tag"].replace(TAG_V3, TAG_V4)
        guess = s.get("ticket_guess") or ""
        base = s["symptom"][: len(s["symptom"]) - len(guess)] if guess else s["symptom"]
        cause_objects = {c["object"]["name"] for c in causes}
        s["answer"] = {"status": extra["status"], "cause_category": extra["cause_category"], **causes[0],
                       **({"also": causes[1:]} if causes[1:] else {}),
                       "harmless_changes": extra["harmless_changes"], "not_cause": extra["not_cause"],
                       "ambient": [x for x in AMBIENT if x["name"] not in cause_objects]}
        s.update({"id": sid, "bank": 4, "symptom": base + DURATION + guess, "investigate_after_s": INVESTIGATE_AFTER_S,
                  "design": [*s["design"], "ambient", "delayed", "sidecar"]})
        s["hard"] = "+".join(s["design"])
        s["realism"]["validate_note"] = (s["realism"]["validate_note"] + "第四版：确认后台服务都在运行、promo-feed-sync 被限流；"
                                         "标成可分清的延迟（285 毫秒）要在观察期里出现探针超时，但不能让 Pod 重启；标成分不清的不能出现探针超时。")
        joined += side not in s["release"]["deployments"]
        add_sidecar(s, side, causes, facts)
        # same wording as the third bank's chain, rebuilt because the sidecar changed one batch member's entry
        s["evidence_chain"] = ([f"{c['object']['name']}（{c['mechanism']}）：" + "；".join(c["evidence"]) for c in causes]
                               + [f"排除 {h['object']} 的改动（{h['change']}）：{h['why']}" for h in s["answer"]["harmless_changes"]]
                               + ["排除后台服务：promo-feed-sync 一直被限流、日志里有外部接口的报错，uptime-checker 和 catalog-crawler 只是流量来源；"
                                  "它们都不在慢请求或失败请求的路径上，故障前后表现一样"])
        out.append(s)
    OUT.write_text("# Generated by boutique/make_v4_scenarios.py from scenarios/boutique_v3.yaml — do not edit by hand.\n"
                   "# Fourth bank: FL = learning, FV = validation, FT = test. FT is not run by any agent until the method is fixed. See docs/V4.md.\n"
                   + yaml.safe_dump({"scenarios": out}, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
    from collections import Counter
    print(len(out), dict(Counter(s["pool"] for s in out)),
          "decidable network causes:", sum(c["decidable"] for s in out for c in [s["answer"], *s["answer"].get("also", [])] if c["mechanism"] == "network"),
          "of", sum(1 for s in out for c in [s["answer"], *s["answer"].get("also", [])] if c["mechanism"] == "network"))
    print("sidecar:", dict(Counter(SIDECAR.values())), "joined the batch:", joined)


if __name__ == "__main__":
    main()
