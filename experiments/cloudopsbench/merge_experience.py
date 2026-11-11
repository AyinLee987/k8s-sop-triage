"""Collision-aware accumulation: merge experience notes that share a symptom into one differential-diagnosis note.

  python experiments/cloudopsbench/merge_experience.py --dose 16 [--source experiences/general]

Why: with one note per incident, two notes can describe the same symptom with different causes ("latency rose,
pods healthy" -> pod network delay / -> a sleep in the code). The agent then reads one and misses the other
(results: cases that read the network-delay note were right about 2 in 3 times, cases that read only the
look-alike note were never right). Here the first K notes are grouped by overlapping symptom, and each group of
two or more is rewritten as ONE note: the shared symptom, the candidate causes, and the evidence that separates
them. Notes without a look-alike are kept unchanged. Only the notes are used - no test case, no ground truth.
Output: experiences/merged-d<K>/NN.md plus groups.json (which source notes went into which merged note).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from openai import OpenAI

from make_experience import HERE, dotenv

GROUP_PROMPT = """Below are troubleshooting notes written after separate incidents, each with an id.
Group together the notes that an engineer could confuse at the start of an investigation because the
OBSERVABLE SYMPTOMS they describe overlap (for example several notes that all start from "latency rose while
pods stay healthy"), even though their root causes differ. Notes whose symptoms are distinctive stay alone.
Answer with JSON only: {"groups": [["01"], ["02", "07"], ...]} - every id exactly once."""

MERGE_PROMPT = """Below are troubleshooting notes that start from overlapping symptoms but end in different root causes.
Rewrite them as ONE differential-diagnosis note for the team runbook:
- line 1: a title naming the shared symptom (not any single cause);
- then the candidate root causes, and for each one the specific evidence that confirms it and the evidence that rules it out;
- then the order in which to check them, cheapest and most discriminating check first.
Keep every root cause that appears in the source notes. Do not add causes that are not in them. Do not name specific
services or nodes. Plain text, at most 14 lines."""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dose", type=int, required=True)
    ap.add_argument("--source", default=str(HERE / "experiences" / "general"))
    args = ap.parse_args()
    env = dotenv()
    client = OpenAI(api_key=env["LLM_API_KEY"], base_url=env.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/v1")
    model = env.get("LLM_MODEL", "deepseek-flash")

    def ask(system: str, user: str) -> str:
        r = client.chat.completions.create(model=model, temperature=0, max_tokens=16000,
                                           messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        return r.choices[0].message.content.strip()

    notes = {p.stem: p.read_text(encoding="utf-8").strip() for p in sorted(Path(args.source).glob("[0-9][0-9].md"))[: args.dose]}
    listing = "\n\n".join(f"[{k}]\n{v}" for k, v in notes.items())
    raw = ask(GROUP_PROMPT, listing)
    if "{" not in raw:
        raise SystemExit(f"grouping call returned no JSON: {raw[:300]!r}")
    groups = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])["groups"]
    seen = [i for g in groups for i in g]
    assert sorted(seen) == sorted(notes), f"grouping lost or duplicated notes: {groups}"
    out = HERE / "experiences" / f"merged-d{args.dose}"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.md"):
        old.unlink()
    record = []
    for n, group in enumerate(sorted(groups, key=lambda g: min(g)), 1):
        text = notes[group[0]] if len(group) == 1 else ask(MERGE_PROMPT, "\n\n".join(f"[{k}]\n{notes[k]}" for k in group))
        (out / f"{n:02d}.md").write_text(text + "\n", encoding="utf-8")
        record.append({"note": f"{n:02d}", "from": group})
        print(f"{n:02d} <- {group}", flush=True)
    (out / "groups.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
