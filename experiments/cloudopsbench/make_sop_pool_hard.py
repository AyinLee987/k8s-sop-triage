"""Candidate pool for SOP rewrite rounds 9+: harder cases, with priority to causes no experience note covers
and causes the SOP library never names, plus the cases that stayed wrong across the nine t100-* runs.

  python make_sop_pool_hard.py --dataset /path/to/Cloud-OpsBench

Never contains a case of sample_sop_eval.json. Order is shuffled with the seed; bad cases are taken in this order.
"""
import argparse, json, random
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
STABLE_WRONG = ["trainticket/performance/29", "trainticket/service/16", "trainticket/service/2",
                "trainticket/performance/32", "trainticket/performance/45"]   # top-1 right in <= 3 of 9 t100-* runs
# (system, category, root cause) -> how many unused cases to take; tags: noexp = no experience note, nosop = not named in v0-v8
QUOTA = {("boutique", "codedefect", "code_excessive_file_writes"): (6, "noexp"),
         ("boutique", "codedefect", "code_excessive_file_reads"): (4, ""), ("boutique", "codedefect", "code_missing_parameter"): (3, ""),
         ("boutique", "codedefect", "code_wrong_return"): (3, ""), ("boutique", "codedefect", "code_wrong_argument_order"): (3, ""),
         ("boutique", "codedefect", "code_busy_loop"): (3, ""), ("boutique", "codedefect", "code_artificial_delay"): (3, ""),
         ("boutique", "codedefect", "code_memory_leak"): (1, ""), ("boutique", "performance", "pod_network_delay"): (1, ""),
         ("trainticket", "performance", "pod_network_delay"): (12, ""),
         ("trainticket", "service", "service_dns_resolution_failure"): (5, "noexp"), ("trainticket", "service", "gateway_misrouted"): (4, ""),
         ("boutique", "admission", "namespace_memory_quota_exceeded"): (5, "noexp,nosop"),
         ("boutique", "admission", "namespace_pod_quota_exceeded"): (3, "nosop"), ("boutique", "admission", "namespace_cpu_quota_exceeded"): (2, "nosop"),
         ("boutique", "infrastructure", "kube_scheduler_unavailable"): (3, "nosop"),
         ("boutique", "service", "service_selector_mismatch"): (4, "nosop"), ("boutique", "service", "service_port_mapping_mismatch"): (3, "nosop"),
         ("boutique", "service", "service_protocol_mismatch"): (1, "nosop"), ("boutique", "service", "service_env_var_address_mismatch"): (1, "")}
ap = argparse.ArgumentParser(); ap.add_argument("--dataset", required=True); ap.add_argument("--seed", type=int, default=20261118)
a = ap.parse_args(); root = Path(a.dataset) / "benchmark"; rng = random.Random(a.seed)
load = lambda f: json.loads((HERE / f).read_text(encoding="utf-8"))
used = set()
for f in ("sample60.json", "sample_sop_pool40.json", "sample_sop_pool80.json", "sample_sop_eval.json", "sample_hard28.json", "sample_tt16.json"):
    used |= {c["id"] for c in load(f)["cases"]}
used |= {i["id"] for i in load("pool.json")["incidents"]}
evalset = {c["id"] for c in load("sample_sop_eval.json")["cases"]}

def case(cid, tag):
    s, c, n = cid.split("/"); m = json.loads((root / s / c / n / "metadata.json").read_text(encoding="utf-8"))
    return {"id": cid, "system": s, "category": c, "case": n, "difficulty": m.get("difficulty"), "query": m.get("query"),
            "truth": m["result"], "why": tag}

out = [case(c, "stable_wrong") for c in STABLE_WRONG if c not in evalset]
by = defaultdict(list)
for s, c in sorted({(k[0], k[1]) for k in QUOTA}):
    for d in sorted((p for p in (root / s / c).iterdir() if p.name.isdigit()), key=lambda p: int(p.name)):
        cid = f"{s}/{c}/{d.name}"
        if cid not in used:
            by[(s, c, json.loads((d / "metadata.json").read_text(encoding="utf-8"))["result"]["root_cause"])].append(cid)
for k, (n, tag) in QUOTA.items():
    rng.shuffle(by[k]); out += [case(c, tag or "hard_unused") for c in by[k][:n]]
rng.shuffle(out)
(HERE / "sample_sop_pool_hard.json").write_text(json.dumps({"dataset": "LLM4Ops/Cloud-OpsBench", "dataset_commit": load("sample60.json")["dataset_commit"],
    "seed": a.seed, "n": len(out), "note": __doc__.strip().splitlines()[0], "cases": out}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
from collections import Counter
print(len(out), len({c["id"] for c in out}), Counter(c["why"] for c in out), Counter(c["system"] for c in out), len({c["id"] for c in out} & evalset))
