"""Build the "错题集": turn past incidents (Cloud-OpsBench cases NOT in the test sample) into experience entries.

  python experiments/cloudopsbench/make_experience.py --dataset /path/to/Cloud-OpsBench [--n 16] [--limit 4]

Step 1 (deterministic): draw N pool incidents with distinct root causes from the medium/hard categories, excluding
every case in sample60.json, and fix their order (pool.json). Dose k of the experiment = the first k entries.
Step 2 (model): for each incident, give the model the user query, the confirmed root cause and one expert
trajectory (tool calls with truncated outputs), and have it write the entry in two styles:
  specific - the note an on-call engineer appends to the runbook right after the incident
  general  - a reusable principle that names the evidence separating this cause from look-alike causes
Entries go to experiences/<style>/<nn>.md. The model is the same one being evaluated (LLM_MODEL in .env).
"""
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

from openai import OpenAI

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CATEGORIES = {"boutique": ["admission", "service", "infrastructure", "performance", "codedefect"],
              "trainticket": ["service", "performance"]}

STYLE = {
    "specific": (
        "You are the on-call engineer who just resolved this incident. Append a short note to the team's "
        "troubleshooting runbook so that the next engineer who sees the same symptom gets to the answer faster. "
        "Write it the way such notes are usually written right after an incident: what was reported, what the cause "
        "turned out to be, and what to check first next time. Be concrete (tools, signals, object names). "
        "5 lines at most, plain text, start with a one-line title."),
    "general": (
        "You are a senior SRE reviewing this incident for the team's troubleshooting runbook. Write a reusable "
        "diagnostic principle that would still be correct for other services and other incidents with the same "
        "symptom. State which evidence distinguishes this root cause from other causes that produce the same "
        "symptom, and what must be ruled out before concluding. Do not name the specific service or node. "
        "5 lines at most, plain text, start with a one-line title."),
}


def dotenv() -> dict:
    env = {}
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"')
    return env


def draw_pool(dataset: Path, n: int, seed: int) -> list[dict]:
    held_out = {c["id"] for c in json.loads((HERE / "sample60.json").read_text(encoding="utf-8"))["cases"]}
    rng = random.Random(seed)
    by_cause = defaultdict(list)
    for system, cats in CATEGORIES.items():
        for cat in cats:
            for d in sorted((dataset / "benchmark" / system / cat).iterdir()):
                cid = f"{system}/{cat}/{d.name}"
                if not d.name.isdigit() or cid in held_out:
                    continue
                meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
                by_cause[(system, meta["result"]["root_cause"])].append(
                    {"id": cid, "system": system, "category": cat, "case": d.name, "query": meta.get("query"),
                     "difficulty": meta.get("difficulty"), "truth": meta["result"]})
    keys = sorted(by_cause)
    rng.shuffle(keys)
    return [rng.choice(by_cause[k]) for k in keys[:n]]


def extend_pool(dataset: Path, pool: list[dict], total: int, seed: int) -> list[dict]:
    """Grow the pool to TOTAL incidents. The extra ones are drawn uniformly from every case outside the sample
    and the pool, so their mix of fault classes follows the benchmark's own proportions (repeats of a cause allowed,
    as in a real incident history). Existing entries keep their positions."""
    held_out = {c["id"] for c in json.loads((HERE / "sample60.json").read_text(encoding="utf-8"))["cases"]}
    held_out |= {inc["id"] for inc in pool}
    rest = []
    for system, cats in CATEGORIES.items():
        for cat in cats:
            for d in sorted((dataset / "benchmark" / system / cat).iterdir()):
                cid = f"{system}/{cat}/{d.name}"
                if not d.name.isdigit() or cid in held_out:
                    continue
                meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
                rest.append({"id": cid, "system": system, "category": cat, "case": d.name, "query": meta.get("query"),
                             "difficulty": meta.get("difficulty"), "truth": meta["result"]})
    random.Random(seed + 1).shuffle(rest)
    return pool + rest[: max(0, total - len(pool))]


def trajectory_text(dataset: Path, case: dict, max_out: int = 1200) -> str:
    path = dataset / "golden-trajectory" / case["system"] / case["category"] / case["case"] / "path1.json"
    steps = json.loads(path.read_text(encoding="utf-8")).get("diagnostic_trace", [])
    parts = []
    for i, s in enumerate(steps, 1):
        out = str(s.get("output", ""))
        if len(out) > max_out:
            out = out[:max_out] + f"\n...[{len(out) - max_out} more characters]"
        parts.append(f"Step {i}: {s.get('calling')}\nOutput:\n{out}")
    return "\n\n".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--seed", type=int, default=20261111)
    ap.add_argument("--extend", type=int, default=0, help="grow the existing pool to this many incidents")
    ap.add_argument("--styles", default="specific,general")
    ap.add_argument("--limit", type=int, default=0, help="only write the first LIMIT entries (for a look at the style)")
    args = ap.parse_args()
    dataset = Path(args.dataset)
    pool_file = HERE / "pool.json"
    if pool_file.exists():
        pool = json.loads(pool_file.read_text(encoding="utf-8"))["incidents"]
        if args.extend > len(pool):
            pool = extend_pool(dataset, pool, args.extend, args.seed)
            pool_file.write_text(json.dumps({"seed": args.seed, "excluded": "every case in sample60.json",
                                             "extended_to": args.extend, "incidents": pool},
                                            ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    else:
        pool = draw_pool(dataset, args.n, args.seed)
        pool_file.write_text(json.dumps({"seed": args.seed, "excluded": "every case in sample60.json",
                                         "incidents": pool}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    env = dotenv()
    client = OpenAI(api_key=env["LLM_API_KEY"], base_url=env.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/v1")
    model = env.get("LLM_MODEL", "deepseek-flash")
    for i, inc in enumerate(pool[: args.limit or len(pool)], 1):
        incident = (f"System: {inc['system']}\nUser-reported symptom: {inc['query']}\n"
                    f"Confirmed root cause: {inc['truth']['root_cause']} on {inc['truth']['fault_object']} "
                    f"(fault class: {inc['truth']['fault_taxonomy']})\n\nHow the expert diagnosed it:\n"
                    f"{trajectory_text(dataset, inc)}")
        for style, instruction in STYLE.items():
            if style not in args.styles.split(","):
                continue
            target = HERE / "experiences" / style / f"{i:02d}.md"
            if target.exists():
                continue
            resp = client.chat.completions.create(model=model, temperature=0, max_tokens=4000, messages=[
                {"role": "system", "content": instruction}, {"role": "user", "content": incident}])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(resp.choices[0].message.content.strip() + "\n", encoding="utf-8")
            print(f"wrote {target.relative_to(REPO)}  ({inc['id']}: {inc['truth']['root_cause']})", flush=True)


if __name__ == "__main__":
    main()
