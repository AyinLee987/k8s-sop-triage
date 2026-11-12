"""Experience sets of the same size (50) that differ only in which cause is the majority.

  python experiments/cloudopsbench/make_ratio_sets.py --dataset /path/to/Cloud-OpsBench

The 50-note pool has 9 notes on pod network delay (ids below) and 1 on application code delay (07).
  net-heavy  (= exp50, already built)      9 network, 1 code delay, 40 other
  neutral    (exp50-neutral)               1 network, 1 code delay, 40 other + 8 filler notes
  code-heavy (exp50-code)                  1 network, 4 code delay, 40 other + 5 filler notes
neutral differs from net-heavy only by swapping 8 network notes for unrelated fillers; code-heavy differs from
neutral only by swapping 3 fillers for code-delay notes. The dataset has only 3 code-delay incidents left outside
the test sample and the pool, so 4 is the most code-delay notes we can have without writing two notes per incident.
A replacement note takes the number of the note it replaces, so its place in the catalog stays the same.
Fillers are drawn uniformly from the remaining cases whose cause is neither a network delay nor a code delay.
"""
from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

from openai import OpenAI

import make_experience as me

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
NET = ["14", "17", "20", "22", "29", "36", "38", "46"]   # the 8 network-delay notes to swap out (02 stays)
CODE_SLOTS = NET[:3]                                       # in code-heavy, these three slots hold code-delay notes


def candidates(dataset: Path) -> list[dict]:
    pool = json.loads((HERE / "pool.json").read_text(encoding="utf-8"))["incidents"]
    held = {c["id"] for c in json.loads((HERE / "sample60.json").read_text(encoding="utf-8"))["cases"]} | {i["id"] for i in pool}
    out = []
    for system, cats in me.CATEGORIES.items():
        for cat in cats:
            for d in sorted((dataset / "benchmark" / system / cat).iterdir()):
                cid = f"{system}/{cat}/{d.name}"
                if d.name.isdigit() and cid not in held:
                    meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
                    out.append({"id": cid, "system": system, "category": cat, "case": d.name, "query": meta.get("query"),
                                "difficulty": meta.get("difficulty"), "truth": meta["result"]})
    return out


def write_note(client, model, dataset: Path, inc: dict, target: Path) -> None:
    if target.exists():
        return
    incident = (f"System: {inc['system']}\nUser-reported symptom: {inc['query']}\n"
                f"Confirmed root cause: {inc['truth']['root_cause']} on {inc['truth']['fault_object']} "
                f"(fault class: {inc['truth']['fault_taxonomy']})\n\nHow the expert diagnosed it:\n"
                f"{me.trajectory_text(dataset, inc)}")
    resp = client.chat.completions.create(model=model, temperature=0, max_tokens=4000, messages=[
        {"role": "system", "content": me.STYLE["general"]}, {"role": "user", "content": incident}])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(resp.choices[0].message.content.strip() + "\n", encoding="utf-8")
    print(f"wrote {target.relative_to(REPO)}  ({inc['id']}: {inc['truth']['root_cause']})", flush=True)


def skill(set_name: str, nid: str, src: Path) -> None:
    title, _, body = src.read_text(encoding="utf-8").strip().partition("\n")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48].rstrip("-")
    out = REPO / "holmes/skills" / set_name / f"note-{nid}-{slug}"
    out.mkdir(parents=True, exist_ok=True)
    desc = title.replace('"', "'")
    (out / "SKILL.md").write_text(f'---\nname: note-{nid}-{slug}\ndescription: "Team experience note: {desc}"\n---\n\n# {title}\n\n{body.strip()}\n', encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    args = ap.parse_args()
    dataset = Path(args.dataset)
    cands = candidates(dataset)
    code = sorted((c for c in cands if c["truth"]["root_cause"] == "code_artificial_delay"), key=lambda c: c["id"])[:3]
    rest = [c for c in cands if "network_delay" not in c["truth"]["root_cause"] and c["truth"]["root_cause"] != "code_artificial_delay"]
    random.Random(20261112).shuffle(rest)
    fill = rest[:8]
    extra = HERE / "experiences" / "general-extra"
    (HERE / "ratio_sets.json").write_text(json.dumps(
        {"swapped_out_network_notes": NET, "code_delay_incidents": code, "filler_incidents": fill,
         "code_heavy_code_slots": CODE_SLOTS}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    env = me.dotenv()
    client = OpenAI(api_key=env["LLM_API_KEY"], base_url=env.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/v1")
    model = env.get("LLM_MODEL", "deepseek-flash")
    for i, inc in enumerate(code, 1):
        write_note(client, model, dataset, inc, extra / f"code{i}.md")
    for i, inc in enumerate(fill, 1):
        write_note(client, model, dataset, inc, extra / f"fill{i}.md")
    base = HERE / "experiences" / "general"
    for n in range(1, 51):
        nid = f"{n:02d}"
        if nid in NET:
            k = NET.index(nid) + 1
            skill("exp50-neutral", nid, extra / f"fill{k}.md")
            skill("exp50-code", nid, extra / (f"code{k}.md" if nid in CODE_SLOTS else f"fill{k}.md"))
        else:
            skill("exp50-neutral", nid, base / f"{nid}.md")
            skill("exp50-code", nid, base / f"{nid}.md")
    print("built exp50-neutral and exp50-code")


if __name__ == "__main__":
    main()
