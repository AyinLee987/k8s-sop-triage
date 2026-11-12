"""Turn the Cloud-OpsBench experience notes (general style) into HolmesGPT skills, one skill per note.

  python scripts/build_exp_skills.py

Writes holmes/skills/exp2/  (notes 02 and 07: pod network delay, application code delay)
       holmes/skills/exp16/ (the first 16 notes) and holmes/skills/exp50/ (all 50).
The notes were written from Cloud-OpsBench incidents outside our sample; none of them was written from this lab.
The first line of a note becomes the skill description (what the agent sees in the catalog); the body is unchanged.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "experiments/cloudopsbench/experiences/general"
SETS = {"exp2": ["02", "07"], "exp16": [f"{i:02d}" for i in range(1, 17)], "exp50": [f"{i:02d}" for i in range(1, 51)]}


def main() -> None:
    for name, ids in SETS.items():
        for nid in ids:
            title, _, body = (SRC / f"{nid}.md").read_text(encoding="utf-8").strip().partition("\n")
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48].rstrip("-")
            skill = f"note-{nid}-{slug}"
            out = REPO / "holmes/skills" / name / skill
            out.mkdir(parents=True, exist_ok=True)
            desc = title.replace('"', "'")
            (out / "SKILL.md").write_text(
                f'---\nname: {skill}\ndescription: "Team experience note: {desc}"\n---\n\n# {title}\n\n{body.strip()}\n', encoding="utf-8")
        print(name, len(ids), "skills")


if __name__ == "__main__":
    main()
