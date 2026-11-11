"""Score a Cloud-OpsBench run against the sample's ground truth (outcome only; process metrics come later).

  python experiments/cloudopsbench/score.py --tag base [--sample sample60.json]

top1 = the first prediction names both the right object and the right root cause (the benchmark's joint accuracy);
object / cause are the two halves; top3 = the right pair appears anywhere in the three predictions.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def predictions(path: Path) -> tuple[list[dict], dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    done = raw.get("Completed")
    if isinstance(done, str):
        try:
            done = json.loads(done)
        except json.JSONDecodeError:
            done = {}
    return list((done or {}).get("top_3_predictions") or []), raw


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--sample", default=str(Path(__file__).with_name("sample60.json")))
    ap.add_argument("--out", default=str(REPO / "results" / "cloudopsbench"))
    ap.add_argument("--model", default="deepseek-flash")
    args = ap.parse_args()
    cases = json.loads(Path(args.sample).read_text(encoding="utf-8"))["cases"]
    rows, agg = [], defaultdict(lambda: defaultdict(int))
    for c in cases:
        p = Path(args.out) / args.tag / c["system"] / args.model / c["category"] / c["case"] / "result_raw.json"
        row = {"id": c["id"], "difficulty": c["difficulty"], "truth": c["truth"], "ran": p.exists()}
        if p.exists():
            preds, raw = predictions(p)
            t = c["truth"]
            hit = lambda x: x.get("fault_object") == t["fault_object"] and x.get("root_cause") == t["root_cause"]  # noqa: E731
            first = preds[0] if preds else {}
            row.update(top1=bool(preds) and hit(first), object=first.get("fault_object") == t["fault_object"],
                       cause=first.get("root_cause") == t["root_cause"], top3=any(hit(x) for x in preds),
                       pred={k: first.get(k) for k in ("fault_object", "root_cause")}, steps=raw.get("steps_used"),
                       finished=raw.get("finished"))
        rows.append(row)
        for key in (("all",), (c["difficulty"],), (c["system"], c["category"])):
            a = agg["/".join(key)]
            a["n"] += 1
            for m in ("ran", "top1", "object", "cause", "top3"):
                a[m] += int(bool(row.get(m)))
    out = Path(args.out) / args.tag / "scores.json"
    out.write_text(json.dumps({"summary": agg, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{'group':28} n  ran top1 object cause top3")
    for k, a in agg.items():
        print(f"{k:28} {a['n']:2} {a['ran']:3} {a['top1']:4} {a['object']:6} {a['cause']:5} {a['top3']:4}")
    print("wrong top1:")
    for r in rows:
        if r["ran"] and not r["top1"]:
            print(f"  {r['id']:32} truth={r['truth']['fault_object']}|{r['truth']['root_cause']}  pred={r['pred']['fault_object']}|{r['pred']['root_cause']}")


if __name__ == "__main__":
    main()
