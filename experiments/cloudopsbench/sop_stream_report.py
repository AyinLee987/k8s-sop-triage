"""Regression results for the sop_stream.py libraries: top-1 by group on sample_sop_eval.json, next to library size."""
import json
from pathlib import Path
HERE = Path(__file__).parent; R = HERE.parents[1] / "results" / "cloudopsbench"
cases = json.loads((HERE / "sample_sop_eval.json").read_text(encoding="utf-8"))["cases"]
diff = {c["id"]: c["difficulty"] for c in cases}
log = json.loads((HERE / "sops_stream" / "log.json").read_text(encoding="utf-8"))
tags = [("s0", "sopeval-v0")] + [(f"s{v}", f"streameval-s{v}") for v in (10, 20, 30, 40, 50, 60)]
rows = {}
for name, tag in tags:
    p = R / tag / "scores.json"
    if p.exists():
        rows[name] = {r["id"]: r for r in json.loads(p.read_text(encoding="utf-8"))["rows"]}
print(f"{'version':8}{'easy/13':>9}{'medium/17':>11}{'hard/16':>9}{'all/46':>8}{'unfinished':>12}{'steps':>7}{'library chars':>15}")
for name, r in rows.items():
    n = lambda d: sum(bool(r[i]["top1"]) for i in r if diff[i] == d)  # noqa: E731
    k = int(name[1:]); size = sum(log[k - 1]["library_chars"].values()) if k and k <= len(log) else 79969
    print(f"{name:8}{n('easy'):>9}{n('medium'):>11}{n('hard'):>9}{sum(bool(x['top1']) for x in r.values()):>8}"
          f"{sum(not x['finished'] for x in r.values()):>12}{sum(x['steps'] or 0 for x in r.values()) / len(r):>7.1f}{size:>15}")
if len(rows) > 1:
    names = list(rows)
    print("\ncases that changed against s0:")
    for i in rows["s0"]:
        t = [int(bool(rows[n].get(i, {}).get("top1"))) for n in names]
        if len(set(t)) > 1:
            print(f"  {diff[i]:6} {i:32} {t}  truth={rows['s0'][i]['truth']['root_cause']}")
