"""Group the experience notes into communities by OBSERVABLE SYMPTOM (not by root cause).

  python experiments/cloudopsbench/make_communities.py

One model call (same model as the agent). Input: the 50 general-style notes. Output: communities.json
  {"communities": [{"id": "C1", "name": ..., "symptom": ..., "members": ["02", ...]}], "secondary": {"14": ["C3"]}}
Every note gets exactly one primary community; a note may also be listed as secondary in others.
The model is not shown any test case, only the notes.
"""
from __future__ import annotations

import json
from pathlib import Path

from openai import OpenAI

import make_experience as me

HERE = Path(__file__).resolve().parent
INSTRUCTION = (
    "You maintain a troubleshooting knowledge base for Kubernetes on-call engineers. Below are experience notes, "
    "each with an id. Group them into communities by the SYMPTOM PATTERN an engineer observes at the start of an "
    "incident (what the service and the pods look like), NOT by root cause: notes about different root causes that "
    "present the same way belong to the same community, and notes about the same root cause that present "
    "differently belong to different communities. Use between 4 and 9 communities. Every note must have exactly one "
    "primary community; if a note also clearly applies to another symptom pattern, list it there as secondary. "
    "Answer with JSON only: {\"communities\": [{\"id\": \"C1\", \"name\": \"short name\", \"symptom\": \"one "
    "sentence describing what is observed\", \"members\": [\"01\", ...]}], \"secondary\": {\"<note id>\": [\"C2\"]}}")


def main() -> None:
    notes = []
    for n in range(1, 51):
        notes.append(f"[{n:02d}]\n" + (HERE / "experiences" / "general" / f"{n:02d}.md").read_text(encoding="utf-8").strip())
    env = me.dotenv()
    client = OpenAI(api_key=env["LLM_API_KEY"], base_url=env.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/v1")
    resp = client.chat.completions.create(model=env.get("LLM_MODEL", "deepseek-flash"), temperature=0, max_tokens=30000,
                                          messages=[{"role": "system", "content": INSTRUCTION},
                                                    {"role": "user", "content": "\n\n".join(notes)}])
    text = (resp.choices[0].message.content or "").strip()
    if "{" not in text:
        raise SystemExit(f"empty answer; finish_reason={resp.choices[0].finish_reason} usage={resp.usage}")
    text = text[text.index("{"): text.rindex("}") + 1]
    data = json.loads(text)
    seen = [m for c in data["communities"] for m in c["members"]]
    assert sorted(seen) == [f"{n:02d}" for n in range(1, 51)], sorted(set(f"{n:02d}" for n in range(1, 51)) ^ set(seen))
    (HERE / "communities.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for c in data["communities"]:
        print(c["id"], c["name"], "|", c["symptom"], "|", " ".join(c["members"]))
    print("secondary", data.get("secondary"))


if __name__ == "__main__":
    main()
