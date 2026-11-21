"""Old (deepseek-flash) and new (Opus 5.5 + method rule) experience libraries side by side: counts and surface features."""
import json, re, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
ITEM = re.compile(r"^- \[E(\d+)\]（(.*?)）(.*)$")
SVC = ["frontend", "cartservice", "redis-cart", "productcatalogservice", "currencyservice", "recommendationservice", "checkoutservice",
       "paymentservice", "shippingservice", "emailservice", "adservice"]
def entries(d):
    out = []
    for p in sorted((REPO / "holmes/skills" / d).glob("*/SKILL.md")):
        for ln in p.read_text(encoding="utf-8").splitlines():
            m = ITEM.match(ln.strip())
            if m: out.append((p.parent.name, m.group(2), m.group(3)))
    return out
rows = []
for d in ["xinc-B", "xinc-B-opus", "xinc-C", "xinc-C-opus", "xinc-D", "xinc-D-opus"]:
    es = entries(d); n = len(es) or 1
    num = sum(bool(re.search(r"\d+\s*(ms|毫秒|秒|%)", t)) for _, _, t in es)
    node = sum(bool(re.search(r"tc qdisc|veth|netem|到节点|节点上", t)) for _, _, t in es)
    multi = sum(len(s.replace("、", ",").split(",")) > 1 for _, s, _ in es)
    ov = sum(sk == "boutique-triage-overview" for sk, _, _ in es)
    svc = sum(sum(s in t for s in SVC) for _, _, t in es) / n
    log = json.loads((REPO / "holmes/skills" / (d + ".log.json")).read_text(encoding="utf-8"))
    ops = [c["change"].get("op") for e in log for c in e["changes"] if c["applied"]]
    rows.append((d, len(es), round(sum(len(t) for _, _, t in es) / n), f"{num}/{len(es)}", f"{node}/{len(es)}", f"{ov}/{len(es)}", f"{multi}/{len(es)}", round(svc, 1),
                 f"add {ops.count('add')} mod {ops.count('modify')} rm {ops.count('remove')}", sum(not e["changes"] for e in log)))
print("lib | entries | avg chars | has concrete number | node-level cmd | in overview | merged from >1 incident | service names per entry | ops | incidents with no change")
for r in rows: print(" | ".join(map(str, r)))
if len(sys.argv) > 1:
    for sk, src, t in entries(sys.argv[1]): print(f"\n[{sk}]（{src}）{t}")
