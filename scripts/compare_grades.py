"""Summarize one grader's file, or compare two graders on the same blind packet.

    python scripts/compare_grades.py results/grading/v4-pilot/grades_opus.json
    python scripts/compare_grades.py results/grading/v4-pilot/grades_opus.json results/grading/v4-pilot/grades_gemini.json

A grades file is either {"runs": [...]} or a bare list, one item per run in the format of
scripts/make_grading_packet.py; blind_key.json next to it maps codes back to batch runs.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    runs = data["runs"] if isinstance(data, dict) else data
    return {r["run"]: r for r in runs}


def tokens(key):
    out = {}
    for code, k in key.items():
        for line in (REPO / "results" / "holmes_batch" / k["tag"] / "runs.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r["answer_file"] == k["answer_file"]:
                out[code] = r["total_tokens"]
    return out


def summary(grades, key):
    tok = tokens(key)
    passed = [c for c, g in grades.items() if g["pass"]]
    by_sc = defaultdict(list)
    for c, g in grades.items():
        by_sc[c.rsplit("-", 1)[0]].append(g["pass"])
    allright = [s for s, v in by_sc.items() if all(v)]
    print(f"runs {len(grades)}  pass {len(passed)} ({len(passed) / len(grades):.0%})  strict {sum(g['strict_pass'] for g in grades.values())}  "
          f"verdicts {dict((v, sum(g['verdict'] == v for g in grades.values())) for v in ('通过', '部分', '错'))}")
    print(f"all-correct scenarios {len(allright)}/{len(by_sc)} {sorted(allright)}  "
          f"tokens per pass {sum(tok.values()) / max(1, len(passed)) / 1e6:.2f}M (total {sum(tok.values()) / 1e6:.2f}M)")
    for sid in sorted(by_sc):
        print(f"  {sid}: {sum(by_sc[sid])}/{len(by_sc[sid])}")
    loc, mech, kinds = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0]), defaultdict(int)
    for g in grades.values():
        for c in g["causes"]:
            loc[c["cause"]][0] += c["location"] == "对"
            loc[c["cause"]][1] += 1
            if c["location"] == "对":
                mech[c["cause"]][0] += c["mechanism"] in ("对", "如实未定")
                mech[c["cause"]][1] += 1
        for e in g["extra"]:
            kinds[e["kind"]] += 1
    print("  per cause  location / mechanism(given location):", {k: f"{v[0]}/{v[1]} {mech[k][0]}/{mech[k][1]}" for k, v in sorted(loc.items())})
    print("  extra by kind:", dict(kinds), " runs with extra:", sum(g["extra_count"] > 0 for g in grades.values()))


def compare(a, b):
    common = sorted(set(a) & set(b))
    missing = sorted(set(a) ^ set(b))
    agree_v = sum(a[c]["verdict"] == b[c]["verdict"] for c in common)
    agree_p = sum(a[c]["pass"] == b[c]["pass"] for c in common)
    cells = same = 0
    diffs = []
    for c in common:
        if a[c]["verdict"] != b[c]["verdict"] or a[c]["extra_count"] != b[c]["extra_count"]:
            diffs.append(f"{c}: verdict {a[c]['verdict']} vs {b[c]['verdict']}, extra {a[c]['extra_count']} vs {b[c]['extra_count']}")
        bc = {x["cause"]: x for x in b[c]["causes"]}
        for x in a[c]["causes"]:
            y = bc.get(x["cause"])
            for f in ("location", "mechanism"):
                cells += 1
                same += bool(y) and x[f] == y[f]
                if not y or x[f] != y[f]:
                    diffs.append(f"{c} {x['cause']} {f}: {x[f]} vs {y[f] if y else '缺'}")
    print(f"common runs {len(common)}  missing {missing}")
    print(f"verdict agree {agree_v}/{len(common)}  pass agree {agree_p}/{len(common)}  cause cells agree {same}/{cells}")
    for d in diffs:
        print("  " + d)


def main():
    first = Path(sys.argv[1])
    key = json.loads((first.parent / "blind_key.json").read_text(encoding="utf-8"))
    a = load(first)
    print(f"== {first.name}")
    summary(a, key)
    if len(sys.argv) > 2:
        b = load(sys.argv[2])
        print(f"== {Path(sys.argv[2]).name}")
        summary(b, key)
        print("== agreement")
        compare(a, b)


if __name__ == "__main__":
    main()
