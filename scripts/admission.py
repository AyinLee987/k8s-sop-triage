"""Admission check: try a library's experience entries on the validation scenarios, each run with a random half of them.

  python scripts/admission.py run --lib holmes/skills/xinc-D-cand --scenarios CV01,CV02 --repeats 10 --tag adm-D --metrics
  python scripts/admission.py effects --tag adm-D --grades <file.json>     # after the runs were graded by hand
  python scripts/admission.py build --lib holmes/skills/xinc-D-cand --drop boutique-triage-overview:E9 --out holmes/skills/xinc-D-adm

Why halves instead of "the whole library, with and without": a library that is only accepted or rejected as a whole
gives the same library as "used as written" whenever it passes, and says nothing about which entry did the damage.
With a random half per run every entry is present in half of the runs and absent in the other half, so one batch of
runs gives each entry an estimate: pass rate with it minus pass rate without it (docs/RESEARCH-admission.md, ASSAY).

The halves are balanced inside each scenario: repeats come in pairs, the second run of a pair gets exactly the entries
the first one did not. So an entry is present in half of every scenario's runs and a hard scenario cannot make an entry
look bad. The halves depend only on (seed, library name, scenario, pair), so a rerun uses the same ones.

Only validation scenarios (pool: val) are accepted. Grading is by hand (boutique/eval_grades_v2.json); this program
never decides whether a run passed.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "boutique"))

import reflect  # noqa: E402  (boutique/reflect.py: the entry section's format)


def entries(lib: Path) -> list[str]:
    """Every entry of the library as "<sop>:<id>", in a fixed order."""
    out = []
    for p in sorted(lib.glob("*/SKILL.md")):
        _, body = reflect.split(p.read_text(encoding="utf-8"))
        out += [f"{p.parent.name}:{i}" for i, _, _ in reflect.items_of(body)]
    return out


def halves(keys: list[str], seed: str, lib_name: str, sid: str, rep: int) -> list[str]:
    """The entries present in repeat `rep` (1-based). Repeats 2k+1 and 2k+2 are complements of each other."""
    pair = (rep - 1) // 2
    order = sorted(keys)
    random.Random(f"{seed}|{lib_name}|{sid}|{pair}").shuffle(order)
    first = set(order[: (len(order) + (pair % 2)) // 2])   # odd counts: alternate which half gets the extra entry
    return sorted(first if rep % 2 else set(keys) - first)


def write_subset(lib: Path, keep: set[str], out: Path) -> None:
    """A copy of the library with only the entries in `keep`; everything else in the SOPs is unchanged."""
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(lib, out)
    for p in sorted(out.glob("*/SKILL.md")):
        head, body = reflect.split(p.read_text(encoding="utf-8"))
        items = reflect.items_of(body)
        kept = [it for it in items if f"{p.parent.name}:{it[0]}" in keep]
        if len(kept) != len(items):
            p.write_text(f"---\n{head}---\n\n{reflect.with_items(body, kept)}\n", encoding="utf-8")


def cmd_run(args) -> int:
    import holmes_batch  # noqa: E402  (needs the cluster libraries; not needed by the other subcommands)
    from triage.lifecycle import load_scenarios
    lib = REPO / args.lib
    keys = entries(lib)
    if not keys:
        sys.exit(f"{args.lib} has no experience entries")
    known = {s["id"]: s for s in load_scenarios(split="all", source="all")}
    ids = [i for i in args.scenarios.split(",") if i]
    wrong = [i for i in ids if known[i].get("pool") != "val"]
    if wrong:
        sys.exit(f"refusing {wrong}: the admission check only runs on validation scenarios (pool: val)")
    out_dir = REPO / "results" / "admission" / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    runs_file = out_dir / "runs.jsonl"
    runs = [json.loads(l) for l in runs_file.read_text(encoding="utf-8").splitlines()] if runs_file.exists() else []
    done = {(r["scenario"], r["repeat"]) for r in runs if not r.get("error")}
    plan = [(sid, rep) for rep in range(1, args.repeats + 1) for sid in ids]
    print(f"{len(plan)} runs planned, {len(done)} already done in {out_dir.relative_to(REPO)}; {len(keys)} entries in {args.lib}", flush=True)
    for n, (sid, rep) in enumerate(plan, 1):
        if (sid, rep) in done:
            continue
        present = halves(keys, args.seed, lib.name, sid, rep)
        subset = out_dir / "_skills"
        write_subset(lib, set(present), subset)
        print(f"[{n}/{len(plan)}] {sid} repeat {rep}: {len(present)}/{len(keys)} entries ...", flush=True)
        rec = holmes_batch.run_one(sid, out_dir / f"{sid}-r{rep}.json", args.max_steps, [], str(subset.relative_to(REPO)), "prod", args.metrics)
        shutil.rmtree(subset, ignore_errors=True)
        rec.update({"repeat": rep, "lib": args.lib, "entries_present": present, "entries_all": keys, "seed": args.seed})
        rec.pop("answer", None)   # the full answer stays in the per-run JSON
        print(f"    {'ERROR' if rec.get('error') else 'ok'}  {rec.get('total_tokens')} tokens, {rec.get('num_llm_calls')} model calls, {rec['wall_s']} s", flush=True)
        runs = [r for r in runs if (r["scenario"], r["repeat"]) != (sid, rep)] + [rec]
        runs_file.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in runs), encoding="utf-8")
    return 0


def effects(runs: list[dict], passed: dict[tuple[str, int], bool]) -> list[dict]:
    """Per entry: pass rate of the runs that had it minus pass rate of the runs that did not, averaged over scenarios
    (each scenario counts the same, so its difficulty cancels). Runs without a grade are left out."""
    keys = runs[0]["entries_all"]
    by_sc: dict[str, list[dict]] = {}
    for r in runs:
        if (r["scenario"], r["repeat"]) in passed:
            by_sc.setdefault(r["scenario"], []).append(r)
    out = []
    for k in keys:
        diffs, n_with, n_without, p_with, p_without = [], 0, 0, 0, 0
        for sid, rs in by_sc.items():
            w = [passed[(r["scenario"], r["repeat"])] for r in rs if k in r["entries_present"]]
            wo = [passed[(r["scenario"], r["repeat"])] for r in rs if k not in r["entries_present"]]
            n_with += len(w); n_without += len(wo); p_with += sum(w); p_without += sum(wo)
            if w and wo:
                diffs.append(sum(w) / len(w) - sum(wo) / len(wo))
        out.append({"entry": k, "with": f"{p_with}/{n_with}", "without": f"{p_without}/{n_without}",
                    "effect": round(sum(diffs) / len(diffs), 3) if diffs else None})
    return out


def cmd_effects(args) -> int:
    out_dir = REPO / "results" / "admission" / args.tag
    runs = [json.loads(l) for l in (out_dir / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    grades = json.loads(Path(args.grades).read_text(encoding="utf-8"))   # {"CV01": {"1": true, ...}, ...}
    passed = {(sid, int(rep)): bool(v) for sid, d in grades.items() for rep, v in d.items()}
    rows = effects(runs, passed)
    for row in sorted(rows, key=lambda r: (r["effect"] is None, r["effect"])):
        drop = row["effect"] is not None and row["effect"] <= args.threshold
        print(f"{row['entry']:45s} with {row['with']:>6s}  without {row['without']:>6s}  effect {row['effect']!s:>7s}  {'DROP' if drop else ''}")
    (out_dir / "effects.json").write_text(json.dumps({"threshold": args.threshold, "rows": rows}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


def cmd_build(args) -> int:
    lib = REPO / args.lib
    keys = entries(lib)
    drop = {d for d in args.drop.split(",") if d}
    unknown = drop - set(keys)
    if unknown:
        sys.exit(f"not in {args.lib}: {sorted(unknown)}")
    write_subset(lib, set(keys) - drop, REPO / args.out)
    print(f"{args.out}: {len(keys) - len(drop)} of {len(keys)} entries kept; dropped {sorted(drop)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--lib", required=True)
    r.add_argument("--scenarios", required=True)
    r.add_argument("--repeats", type=int, default=10)
    r.add_argument("--tag", default=time.strftime("adm-%Y%m%d-%H%M%S"))
    r.add_argument("--seed", default="2026-11-22")
    r.add_argument("--max-steps", type=int, default=100)
    r.add_argument("--metrics", action="store_true")
    e = sub.add_parser("effects")
    e.add_argument("--tag", required=True)
    e.add_argument("--grades", required=True)
    e.add_argument("--threshold", type=float, default=-0.15, help="entries with an effect at or below this are marked DROP")
    b = sub.add_parser("build")
    b.add_argument("--lib", required=True)
    b.add_argument("--drop", default="")
    b.add_argument("--out", required=True)
    args = ap.parse_args()
    return {"run": cmd_run, "effects": cmd_effects, "build": cmd_build}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
