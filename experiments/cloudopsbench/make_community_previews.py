"""Turn communities.json into the two-level catalog used by experience_ext (EXPERIENCE_COMMUNITIES).

  python experiments/cloudopsbench/make_community_previews.py [--dose 16]

1. The three latency communities the model split by cause (C5 code/CPU, C6 pod network delay, C7 node packet
   loss) are merged into one: at the start of an incident they look the same ("slow, pods healthy").
2. Only notes 01..DOSE are kept. For each community the model writes, from the member notes only:
   symptom - one sentence on what is observed (shown in the prompt), and
   guide   - how to tell the community's causes apart (shown when the community is opened).
Output: communities<DOSE>.json. No test case is shown to the model.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from openai import OpenAI

import make_experience as me

HERE = Path(__file__).resolve().parent
MERGE = {"C5": "LAT", "C6": "LAT", "C7": "LAT"}
INSTRUCTION = (
    "You maintain a troubleshooting knowledge base for Kubernetes on-call engineers. The notes below all apply to "
    "the same symptom pattern but may point to different root causes. Write, using only what the notes say:\n"
    "name: a short name for the symptom pattern (do not name a root cause).\n"
    "symptom: one sentence describing what an engineer observes at the start of such an incident (no root cause).\n"
    "guide: for each distinct root cause covered by the notes, one line of the form "
    "'<root cause> - check <the observable evidence that separates it from the others in this group> (exp-NN)'. "
    "Then one last line starting with 'Do not conclude before:' naming what must be ruled out. At most 8 lines.\n"
    "Answer with JSON only: {\"name\": ..., \"symptom\": ..., \"guide\": \"line1\\nline2...\"}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dose", type=int, default=16)
    args = ap.parse_args()
    src = json.loads((HERE / "communities.json").read_text(encoding="utf-8"))
    groups: dict[str, list[str]] = {}
    for c in src["communities"]:
        for m in c["members"]:
            if int(m) <= args.dose:
                groups.setdefault(MERGE.get(c["id"], c["id"]), []).append(m)
    env = me.dotenv()
    client = OpenAI(api_key=env["LLM_API_KEY"], base_url=env.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/v1")
    out = []
    for i, (key, members) in enumerate(sorted(groups.items()), 1):
        members = sorted(members)
        notes = "\n\n".join(f"[exp-{m}]\n" + (HERE / "experiences" / "general" / f"{m}.md").read_text(encoding="utf-8").strip() for m in members)
        resp = client.chat.completions.create(model=env.get("LLM_MODEL", "deepseek-flash"), temperature=0, max_tokens=12000,
                                              messages=[{"role": "system", "content": INSTRUCTION}, {"role": "user", "content": notes}])
        text = (resp.choices[0].message.content or "").strip()
        data = json.loads(text[text.index("{"): text.rindex("}") + 1])
        out.append({"id": f"G{i}", "source": key, "name": data["name"], "symptom": data["symptom"], "guide": data["guide"], "members": members})
        print(f"G{i} [{key}] {data['name']} | {data['symptom']} | {' '.join(members)}\n{data['guide']}\n", flush=True)
    (HERE / f"communities{args.dose}.json").write_text(json.dumps({"dose": args.dose, "communities": out}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
