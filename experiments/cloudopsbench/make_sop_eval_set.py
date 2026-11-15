"""Build the evaluation set for SOP versions: simple regression cases, the bad cases written into the SOPs, and unseen cases.

  python make_sop_eval_set.py --dataset /path/to/Cloud-OpsBench

simple : the 5 always-right canaries of sample_disc31.json + 3 cases from each easy category never sampled before
written: the first 8 bad cases of sop_badcases.json (rounds 1-8 of sop_evolve.py)
unseen : the remaining bad cases + 16 pool cases the v0 SOPs got right (round-robin over categories)
"""
import argparse, json, random
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
EASY = [("boutique", "runtime"), ("boutique", "scheduling"), ("boutique", "startup"),
        ("trainticket", "runtime"), ("trainticket", "startup")]
ap = argparse.ArgumentParser(); ap.add_argument("--dataset", required=True); ap.add_argument("--seed", type=int, default=20261117)
a = ap.parse_args(); root = Path(a.dataset); rng = random.Random(a.seed)
load = lambda f: json.loads((HERE / f).read_text(encoding="utf-8"))
out = []
disc = load("sample_disc31.json")
for c in disc["cases"]:
    if c["id"] in disc["canaries"]: out.append({**c, "group": "simple"})
for system, cat in EASY:
    by = defaultdict(list)
    for d in sorted((p for p in (root / "benchmark" / system / cat).iterdir() if p.name.isdigit()), key=lambda p: int(p.name)):
        m = json.loads((d / "metadata.json").read_text(encoding="utf-8")); by[m["result"]["root_cause"]].append((d.name, m))
    causes = sorted(by); rng.shuffle(causes)
    for c in causes[:3]:
        name, m = rng.choice(by[c])
        out.append({"id": f"{system}/{cat}/{name}", "system": system, "category": cat, "case": name,
                    "difficulty": m.get("difficulty"), "query": m.get("query"), "truth": m["result"], "group": "simple"})
bad = load("sop_badcases.json")["cases"]
keep = ("id", "system", "category", "case", "difficulty", "query", "truth")
for i, c in enumerate(bad):
    out.append({**{k: c[k] for k in keep}, "group": "written" if i < 8 else "unseen"})
badids = {c["id"] for c in bad}
by = defaultdict(list)
for f in ("sample_sop_pool40.json", "sample_sop_pool80.json"):
    for c in load(f)["cases"]:
        if c["id"] not in badids: by[(c["system"], c["category"])].append(c)
cats = sorted(by); [rng.shuffle(by[k]) for k in cats]; n = 0
while n < 16:
    for k in cats:
        if by[k] and n < 16: out.append({**by[k].pop(), "group": "unseen"}); n += 1
res = {"dataset": "LLM4Ops/Cloud-OpsBench", "dataset_commit": disc["dataset_commit"], "seed": a.seed, "n": len(out),
       "note": "evaluation set for SOP versions; group = simple | written | unseen", "cases": out}
(HERE / "sample_sop_eval.json").write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
from collections import Counter
print(len(out), Counter(c["group"] for c in out), Counter(c["difficulty"] for c in out if c["group"] == "simple"))
