"""Draw a fixed, stratified sample of Cloud-OpsBench medium/hard cases and save exactly which cases were drawn.

  python experiments/cloudopsbench/sample.py --dataset /path/to/Cloud-OpsBench [--seed 20261111] [--out sample60.json]

Strata are (system, fault category); inside a stratum cases are taken round-robin over root causes (shuffled with
the seed) so that every root cause is represented before any repeats. The output lists each case with its
ground truth, plus the seed and the dataset commit, so the same 60 cases can be re-run or audited later.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
from collections import defaultdict
from pathlib import Path

# medium: admission, service, infrastructure; hard: performance, codedefect (the benchmark's own labels)
QUOTA = {("boutique", "admission"): 8, ("boutique", "service"): 10, ("boutique", "infrastructure"): 6,
         ("boutique", "performance"): 8, ("boutique", "codedefect"): 12,
         ("trainticket", "service"): 8, ("trainticket", "performance"): 8}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--seed", type=int, default=20261111)
    ap.add_argument("--out", default=str(Path(__file__).with_name("sample60.json")))
    args = ap.parse_args()
    root = Path(args.dataset)
    rng = random.Random(args.seed)
    cases = []
    for (system, category), quota in QUOTA.items():
        by_cause = defaultdict(list)
        for d in sorted((p for p in (root / "benchmark" / system / category).iterdir() if p.name.isdigit()),
                        key=lambda p: int(p.name)):
            meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
            by_cause[meta["result"]["root_cause"]].append((d.name, meta))
        causes = sorted(by_cause)
        rng.shuffle(causes)
        for c in causes:
            rng.shuffle(by_cause[c])
        picked = []
        while len(picked) < quota and any(by_cause.values()):
            for c in causes:
                if by_cause[c] and len(picked) < quota:
                    picked.append(by_cause[c].pop())
        for name, meta in sorted(picked, key=lambda x: int(x[0])):
            cases.append({"id": f"{system}/{category}/{name}", "system": system, "category": category, "case": name,
                          "difficulty": meta.get("difficulty"), "query": meta.get("query"), "truth": meta["result"]})
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    out = {"dataset": "LLM4Ops/Cloud-OpsBench", "dataset_commit": commit, "seed": args.seed,
           "quota": {f"{s}/{c}": n for (s, c), n in QUOTA.items()}, "n": len(cases), "cases": cases}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: {len(cases)} cases")


if __name__ == "__main__":
    main()
