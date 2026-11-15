"""Experience store for the Cloud-OpsBench reference agent, in the style of HolmesGPT skills.

This file is copied into a worker copy of the agent; the worker's run.py gets one extra line that calls
install(registry, context). It mirrors how HolmesGPT handles SuggestSkills output: one note per lesson, every
note's id and one-line title listed in a catalog in the prompt, and the full text fetched on demand with a tool
(HolmesGPT: fetch_skill; here: ReadExperience). Nothing else about the agent changes.

EXPERIENCE_DIR (env): folder of NN.md notes, first line = title.  EXPERIENCE_DOSE (env): use the first K notes.
EXPERIENCE_ORDER (env, optional): comma-separated note numbers giving the accumulation order.
EXPERIENCE_COMMUNITIES (env, optional): JSON file grouping the notes into symptom communities. The prompt then
lists only the communities (name + what is observed); OpenCommunity shows one community's guide for telling its
causes apart and the notes in it; ReadExperience is unchanged. Notes not named in the file are not offered.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

from tools.cloudops import SimpleTool

# Wording adapted from HolmesGPT's base_user_prompt.jinja2 (skill catalog section).
CATALOG_HEADER = """## Team Experience Notes
You have access to notes written by the team after past incidents.
If one of the following notes relates to the issue or matches the symptoms you observe, read it with the ReadExperience tool. Only read notes that clearly match - do not read them speculatively.
After reading a note, decide from its contents whether it is relevant to this incident. If it is, follow it; if it is not, ignore it.
"""


COMMUNITY_HEADER = """## Team Experience Notes
You have access to notes written by the team after past incidents, grouped by the symptom pattern they apply to.
If one of the following groups matches the symptoms you observe, open it with the OpenCommunity tool: it shows how to tell the group's causes apart and lists its notes. Then read the notes that fit with the ReadExperience tool. Only open a group that clearly matches - do not open groups speculatively.
After reading a note, decide from its contents whether it is relevant to this incident. If it is, follow it; if it is not, ignore it.
"""


class OpenCommunityArgs(BaseModel):
    community_id: str = Field(description="The id of the group to open, exactly as listed in Team Experience Notes, e.g. G2")


class ReadExperienceArgs(BaseModel):
    experience_id: str = Field(description="The id of the note to read, exactly as listed in Team Experience Notes, e.g. exp-03")


def load_notes() -> dict[str, tuple[str, str]]:
    folder, dose = os.environ.get("EXPERIENCE_DIR"), int(os.environ.get("EXPERIENCE_DOSE", "0"))
    if not folder or dose <= 0:
        return {}
    notes = {}
    paths = sorted(Path(folder).glob("[0-9][0-9].md"))
    order = [x for x in os.environ.get("EXPERIENCE_ORDER", "").split(",") if x]
    if order:  # a different accumulation order; ids stay tied to the note, not to its position
        by_stem = {p.stem: p for p in paths}
        paths = [by_stem[x] for x in order]
    for path in paths[:dose]:
        text = path.read_text(encoding="utf-8").strip()
        notes[f"exp-{path.stem}"] = (text.splitlines()[0].strip(), text)
    return notes


def install(registry: dict, context) -> None:
    notes = load_notes()
    if not notes:
        return

    def read(experience_id: str = "") -> str:
        key = str(experience_id).strip().lower()
        if key not in notes:
            return f"Unknown experience_id '{experience_id}'. Available: {', '.join(notes)}"
        return notes[key][1]

    registry["ReadExperience"] = SimpleTool(
        name="ReadExperience", description="Read one team experience note in full by its id.",
        run_fn=read, args_schema=ReadExperienceArgs)
    # the tool list in the prompt was rendered before the tool was added
    from tools.cloudops import render_tools_description
    context.tools_description = render_tools_description(registry)
    comm_file = os.environ.get("EXPERIENCE_COMMUNITIES")
    if not comm_file:
        catalog = "\n".join(f"* {key} | {title}" for key, (title, _) in notes.items())
        context.system_prompt = context.system_prompt + "\n\n" + CATALOG_HEADER + catalog
        return
    groups = {}
    for c in json.loads(Path(comm_file).read_text(encoding="utf-8"))["communities"]:
        members = [f"exp-{m}" for m in c["members"] if f"exp-{m}" in notes]
        if members:
            groups[c["id"].lower()] = (c, members)

    def open_community(community_id: str = "") -> str:
        key = str(community_id).strip().lower()
        if key not in groups:
            return f"Unknown community_id '{community_id}'. Available: {', '.join(g[0]['id'] for g in groups.values())}"
        c, members = groups[key]
        listing = "\n".join(f"* {m} | {notes[m][0]}" for m in members)
        return f"{c['name']}\n\nHow to tell the causes apart:\n{c['guide']}\n\nNotes in this group:\n{listing}"

    registry["OpenCommunity"] = SimpleTool(
        name="OpenCommunity", description="Open one group of team experience notes: how to tell its causes apart, and the notes in it.",
        run_fn=open_community, args_schema=OpenCommunityArgs)
    context.tools_description = render_tools_description(registry)
    catalog = "\n".join(f"* {c['id']} | {c['name']}: {c['symptom']} ({len(members)} notes)" for c, members in groups.values())
    context.system_prompt = context.system_prompt + "\n\n" + COMMUNITY_HEADER + catalog
