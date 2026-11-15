"""Append the first N wrong cases of a run (in the sample's order) to sop_badcases.json, and write the sample of
cases not yet used as bad cases (for mining the next batch with the next SOP version).

  python sop_collect_bad.py --tag sop-v8-hard --sample sample_sop_pool_hard.json --take 4 --rest /tmp/rest.json
"""
import argparse, json
from pathlib import Path

HERE = Path(__file__).parent; REPO = HERE.parents[1]
ap = argparse.ArgumentParser()
ap.add_argument("--tag", required=True); ap.add_argument("--sample", required=True)
ap.add_argument("--take", type=int, default=4); ap.add_argument("--rest", required=True)
a = ap.parse_args()
sample = json.loads(Path(a.sample).read_text(encoding="utf-8"))
rows = {r["id"]: r for r in json.loads((REPO / "results" / "cloudopsbench" / a.tag / "scores.json").read_text(encoding="utf-8"))["rows"]}
bp = HERE / "sop_badcases.json"; bad = json.loads(bp.read_text(encoding="utf-8"))
have = {c["id"] for c in bad["cases"]}
wrong = [c for c in sample["cases"] if c["id"] not in have and rows[c["id"]]["ran"] and not rows[c["id"]]["top1"]]
print(f"{a.tag}: {sum(r['top1'] for r in rows.values())}/{len(rows)} right; {len(wrong)} wrong, taking {min(a.take, len(wrong))}")
for c in wrong[: a.take]:
    r = rows[c["id"]]
    bad["cases"].append({**c, "run_tag": a.tag, "pred": r["pred"], "steps": r["steps"]})
    print(len(bad["cases"]), c["id"], c.get("why"), c["truth"]["root_cause"], "<-", (r["pred"] or {}).get("root_cause"))
bp.write_text(json.dumps(bad, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
have = {c["id"] for c in bad["cases"]}
rest = {**sample, "cases": [c for c in sample["cases"] if c["id"] not in have]}; rest["n"] = len(rest["cases"])
Path(a.rest).write_text(json.dumps(rest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
