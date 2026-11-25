"""Run HolmesGPT on several scenarios, several times each, and summarise how stable the results are.

  python scripts/holmes_batch.py --split dev --repeats 3 [--scenarios R01,R06] [--tag baseline]

Each run goes through scripts/holmes_pilot.py (inject -> ask HolmesGPT -> reset), one at a time.
Output: results/holmes_batch/<tag>/runs.jsonl (one line per run) and summary.md.

Grading here is PROVISIONAL: HolmesGPT answers in free text, so a run counts as "auto_correct" when the
answer names the expected cause (accept_cause_keywords) and quotes at least one key_evidence string.
Every answer is kept, so runs can be re-graded once the structured-output adapter exists (ISSUES E-010).
Holdout scenarios are refused unless --finalize is given.

Two clusters at once (docs/PARALLEL.md): start one batch per cluster with the same arguments and tag, and --shard 1/2 on
one, --shard 2/2 on the other. Run (scenario, repeat) goes to shard (index of the scenario + repeat) mod n, so each
scenario's repeats alternate between the clusters (a cluster that is a little slower cannot pass for a harder scenario).
Each shard keeps its own runs-shard<k>of<n>.jsonl; after every run it rewrites runs.jsonl and summary.md as the union of
all the shard files, so the tag reads the same as an unsharded batch. Every record says which cluster ran it.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from triage import remediate  # noqa: E402
from triage.lifecycle import load_noises, load_scenarios  # noqa: E402


def pick_noise(mode: str, sc_id: str, rep: int) -> list[str]:
    """none | random | comma-separated ids. random = 1-3 kinds, chosen from (scenario, repeat) so a rerun
    of the same tag gets the same noise, while the three repeats of a scenario get different noise."""
    if mode in ("", "none"):
        return []
    known = [n["id"] for n in load_noises()]
    if mode != "random":
        return [i for i in mode.split(",") if i]
    rng = random.Random(f"{sc_id}#{rep}")
    return sorted(rng.sample(known, rng.randint(1, len(known))))


def grade(answer_text: str, expected: dict, noise_ids: list[str] | None = None) -> dict:
    low = answer_text.lower()
    noise_named = [k for n in load_noises(noise_ids or []) for k in n.get("blame_keywords", []) if k.lower() in low]
    # scenarios with several root causes (answer.also): every one of them has to be named
    parts = []
    for cause in [expected, *expected.get("also", [])]:
        name = (cause.get("object") or {}).get("name", "")
        cause_hits = [k for k in cause.get("accept_cause_keywords", []) if k.lower() in low]
        evidence_hits = [k for k in cause.get("key_evidence", []) if k.lower() in low]
        parts.append({"object": name, "object_mentioned": bool(name) and name.lower() in low, "cause_hits": cause_hits,
                      "evidence_hits": evidence_hits, "ok": bool(cause_hits) and bool(evidence_hits)})
    out = {
        "object_mentioned": parts[0]["object_mentioned"],
        "cause_hits": parts[0]["cause_hits"],
        "evidence_hits": parts[0]["evidence_hits"],
        "auto_correct": all(p["ok"] for p in parts),
        # the answer names a noise object: read it to see whether it blamed the noise or just listed it as checked
        "noise_named": noise_named,
    }
    if len(parts) > 1:
        out["root_causes"] = parts
    if expected.get("not_cause"):
        # a real anomaly that is not the cause: naming it is fine, blaming it is not. Keywords cannot tell, so read these.
        out["decoy_named"] = [d["name"] for d in expected["not_cause"] if d["name"].lower() in low]
    return out


def parse_shard(text: str) -> tuple[int, int]:
    """'k/n' -> (k, n) with 1 <= k <= n."""
    try:
        k, n = (int(x) for x in text.split("/"))
    except ValueError:
        raise SystemExit(f"--shard must look like 1/2, not {text!r}")
    if not 1 <= k <= n:
        raise SystemExit(f"--shard {text}: need 1 <= k <= n")
    return k, n


def shard_of(ids: list[str], sc_id: str, rep: int, n: int) -> int:
    """Which shard (1..n) runs this (scenario, repeat): the repeats of each scenario alternate between the shards."""
    return (ids.index(sc_id) + rep) % n + 1


def shard_files(out_dir: Path) -> list[Path]:
    return sorted(out_dir.glob("runs-shard*of*.jsonl"))


def read_runs(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def write_atomic(path: Path, text: str) -> None:
    """Two shards may rewrite the merged files at about the same time; a reader never sees half a file."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def merge_shards(out_dir: Path, ids: list[str], plan: list[tuple[str, int]]) -> list[dict]:
    """runs.jsonl and summary.md = the union of the shard files, in plan order."""
    by_key = {(r["scenario"], r["repeat"]): r for f in shard_files(out_dir) for r in read_runs(f)}
    order = {key: i for i, key in enumerate(plan)}
    runs = sorted(by_key.values(), key=lambda r: order.get((r["scenario"], r["repeat"]), len(order)))
    write_atomic(out_dir / "runs.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in runs))
    write_atomic(out_dir / "summary.md", summarise(runs, ids))
    return runs


def run_one(sc_id: str, out: Path, max_steps: int, noise_ids: list[str], skills: str = "", frame: str = "prod", metrics: bool = False,
            prompt: str = "auto", allow_test: bool = False) -> dict:
    started = time.monotonic()
    proc = subprocess.run([sys.executable, str(REPO / "scripts" / "holmes_pilot.py"), sc_id,
                           "--max-steps", str(max_steps), "--out", str(out), "--noise", ",".join(noise_ids),
                           "--skills", skills, "--frame", frame, "--prompt", prompt, *(["--metrics"] if metrics else []),
                           *(["--allow-test"] if allow_test else [])],
                          capture_output=True, text=True, timeout=2400)
    rec = {"scenario": sc_id, "noise": noise_ids, "exit": proc.returncode, "wall_s": round(time.monotonic() - started),
           "answer_file": str(out.relative_to(REPO))}
    if proc.returncode or not out.exists():
        rec["error"] = (proc.stderr or proc.stdout)[-1500:]
        return rec
    data = json.loads(out.read_text(encoding="utf-8"))
    rec.update({k: data.get(k) for k in ("total_tokens", "prompt_tokens", "completion_tokens", "cached_tokens",
                                         "num_llm_calls", "finish_reason")})
    rec["tool_calls"] = len(data.get("tool_calls") or [])
    # which skills (SOP documents) the agent chose to read
    rec["skills_fetched"] = [str(t.get("description") or "") for t in data.get("tool_calls") or []
                             if t.get("tool_name") == "fetch_skill"]
    rec["answer"] = data.get("result") or ""
    rec["frame"], rec["before"], rec["metrics"] = data.get("frame"), data.get("before"), data.get("metrics")
    rec["prompt_version"] = data.get("prompt_version")
    rec["metrics_calls"] = sum(1 for t in data.get("tool_calls") or [] if t.get("tool_name") == "boutique_pod_metrics")
    plan, _ = remediate.parse_plan(rec["answer"])   # the JSON block the Online Boutique prompt asks for
    rec["root_causes"] = (plan or {}).get("root_causes")
    return rec


def summarise(runs: list[dict], scenarios: dict) -> str:
    lines = ["# HolmesGPT batch summary", "",
             "Grading is provisional (keyword match on the free-text answer); read the answers before trusting it.", "",
             "| scenario | runs | auto_correct | names noise | tokens (mean / min-max) | model calls | tool calls | wall s |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for sc_id in scenarios:
        rs = [r for r in runs if r["scenario"] == sc_id]
        ok = [r for r in rs if not r.get("error")]
        if not rs:
            continue
        correct = sum(1 for r in ok if r["grade"]["auto_correct"])
        named = sum(1 for r in ok if r["grade"].get("noise_named"))
        toks = [r["total_tokens"] or 0 for r in ok]
        calls = [r["num_llm_calls"] or 0 for r in ok]
        tools = [r["tool_calls"] for r in ok]
        walls = [r["wall_s"] for r in ok]
        fmt = lambda xs: f"{statistics.mean(xs):.0f} / {min(xs)}-{max(xs)}" if xs else "-"  # noqa: E731
        lines.append(f"| {sc_id} | {len(rs)} ({len(rs) - len(ok)} failed) | {correct}/{len(ok)} | {named}/{len(ok)} | {fmt(toks)} | "
                     f"{fmt(calls)} | {fmt(tools)} | {fmt(walls)} |")
    ok_all = [r for r in runs if not r.get("error")]
    total = sum(1 for r in ok_all if r["grade"]["auto_correct"])
    lines += ["", f"Runs: {len(ok_all)} finished, {len(runs) - len(ok_all)} failed. Auto-grade (keyword match, needs a human read): {total}/{len(ok_all)} correct.",
              "A scenario is stable when all its runs agree (all correct or all wrong)."]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev", choices=["dev", "holdout", "all"])
    ap.add_argument("--scenarios", default="", help="comma-separated ids; default = every scenario in --split")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--max-steps", type=int, default=25)
    ap.add_argument("--noise", default="none", help="none | random | comma-separated ids from scenarios/noise.yaml")
    ap.add_argument("--skills", default="", help="comma-separated skill directories relative to the repo; empty = no SOP")
    ap.add_argument("--source", default="handwritten", choices=["handwritten", "generated", "all"])
    ap.add_argument("--frame", default="prod", choices=["prod", "none"])
    ap.add_argument("--metrics", action="store_true", help="give the agent the boutique_pod_metrics tool")
    ap.add_argument("--tag", default=time.strftime("%Y%m%d-%H%M%S"))
    ap.add_argument("--prompt", default="auto", choices=["auto", "v2", "v3", "v4"], help="see holmes_pilot.py --prompt")
    ap.add_argument("--finalize", action="store_true", help="required to run holdout scenarios and the third bank's test scenarios")
    ap.add_argument("--shard", default="1/1", help="k/n: run only this cluster's share of the plan (see the module docstring)")
    args = ap.parse_args()
    shard, shards = parse_shard(args.shard)
    cluster = os.environ.get("ADMIN_CONTEXT", "").removeprefix("kind-")

    all_sc = {s["id"]: s for s in load_scenarios(split="all", source="all")}
    in_source = {s["id"] for s in load_scenarios(split="all", source=args.source)}
    ids = [i for i in args.scenarios.split(",") if i] or [i for i, s in all_sc.items() if i in in_source
                                                          and (args.split == "all" or s.get("split") == args.split)]
    holdout = [i for i in ids if all_sc[i].get("split") == "holdout"]
    if holdout and not args.finalize:
        sys.exit(f"refusing holdout scenarios {holdout} without --finalize (keeps the held-out set clean)")
    test_v3 = [i for i in ids if int(all_sc[i].get("bank") or 0) >= 3 and all_sc[i].get("pool") == "test"]
    if test_v3 and not args.finalize:
        sys.exit(f"refusing the third bank's test scenarios {test_v3} without --finalize (not run until the method is fixed)")

    out_dir = REPO / "results" / "holmes_batch" / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = [(sc_id, rep) for rep in range(1, args.repeats + 1) for sc_id in ids]  # round-robin: repeat 1 of all, then 2...
    if shards == 1:
        if shard_files(out_dir):
            sys.exit(f"{out_dir.relative_to(REPO)} was run in shards: continue it with the same --shard values")
        runs_file = out_dir / "runs.jsonl"
        runs = read_runs(runs_file)
        done = {(r["scenario"], r["repeat"]) for r in runs if not r.get("error")}
    else:
        if (out_dir / "runs.jsonl").exists() and not shard_files(out_dir):
            sys.exit(f"{out_dir.relative_to(REPO)} already holds an unsharded batch: use a new tag for a sharded one")
        others = [f.name for f in shard_files(out_dir) if not f.name.endswith(f"of{shards}.jsonl")]
        if others:
            sys.exit(f"{out_dir.relative_to(REPO)} has shard files for another number of shards: {others}")
        runs_file = out_dir / f"runs-shard{shard}of{shards}.jsonl"
        runs = read_runs(runs_file)
        done = {(r["scenario"], r["repeat"]) for f in shard_files(out_dir) for r in read_runs(f) if not r.get("error")}
    mine = {(sc_id, rep) for sc_id, rep in plan if shard_of(ids, sc_id, rep, shards) == shard}
    print(f"{len(plan)} runs planned"
          + (f", {len(mine)} of them on this shard ({shard}/{shards}, cluster {cluster or '?'})" if shards > 1 else "")
          + f", {len(done)} already done in {out_dir.relative_to(REPO)}", flush=True)
    for n, (sc_id, rep) in enumerate(plan, 1):
        if (sc_id, rep) in done or (sc_id, rep) not in mine:
            continue
        print(f"[{n}/{len(plan)}] {sc_id} repeat {rep} ...", flush=True)
        noise_ids = pick_noise(args.noise, sc_id, rep)
        print(f"    noise: {noise_ids or 'none'}", flush=True)
        rec = run_one(sc_id, out_dir / f"{sc_id}-r{rep}.json", args.max_steps, noise_ids, args.skills, args.frame, args.metrics,
                      args.prompt, allow_test=args.finalize)
        rec["repeat"] = rep
        rec["cluster"] = cluster
        if shards > 1:
            rec["shard"] = f"{shard}/{shards}"
        rec["grade"] = grade(rec.get("answer", ""), all_sc[sc_id]["answer"], noise_ids) if not rec.get("error") else None
        status = "ERROR" if rec.get("error") else ("ok" if rec["grade"]["auto_correct"] else "WRONG?")
        print(f"    skills read: {rec.get('skills_fetched')}", flush=True)
        print(f"    {status}  {rec.get('total_tokens')} tokens, {rec.get('num_llm_calls')} model calls, "
              f"{rec['wall_s']} s", flush=True)
        rec.pop("answer", None)  # the full answer stays in the per-run JSON
        runs = [r for r in runs if (r["scenario"], r["repeat"]) != (sc_id, rep)] + [rec]
        runs_file.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in runs), encoding="utf-8")
        if shards == 1:
            (out_dir / "summary.md").write_text(summarise(runs, ids), encoding="utf-8")
        else:
            merge_shards(out_dir, ids, plan)
    if shards > 1:   # the shard that finishes last leaves the complete union behind
        merge_shards(out_dir, ids, plan)
    summary = out_dir / "summary.md"
    print(summary.read_text(encoding="utf-8") if summary.exists() else "no runs recorded yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
