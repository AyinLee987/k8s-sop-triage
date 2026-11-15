"""Document-form SOP library for the Cloud-OpsBench reference agent.

Copied into a worker copy of the agent; the worker's run.py gets one extra line calling install(registry, context,
system). The prompt lists the SOPs of the case's system (id, symptom, signals); ReadSOP returns one SOP document in
full. Unlike the benchmark's own skill agent, the SOP is plain text, so a rewritten SOP needs no schema.

SOP_DIR (env): one version of the library, <SOP_DIR>/<system>/<sop_id>.md.
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

from tools.cloudops import SimpleTool, render_tools_description

SOP_HEADER = """## Diagnostic SOPs
The team maintains standard operating procedures (SOPs) for troubleshooting, one per symptom pattern.
If one of the following SOPs matches the symptoms you observe, read it with the ReadSOP tool and follow its steps: collect the evidence each step asks for and conclude where the SOP says to conclude.
If the evidence shows that the SOP you opened does not fit this incident, you may read another one.
"""


class ReadSOPArgs(BaseModel):
    sop_id: str = Field(description="The id of the SOP to read, exactly as listed under Diagnostic SOPs")


def describe(text: str) -> tuple[str, list[str]]:
    lines = text.splitlines()
    symptom = next((ln.split(":", 1)[1].strip() for ln in lines if ln.lower().startswith("symptom:")), "")
    signals, on = [], False
    for ln in lines:
        if ln.strip().lower().startswith("signals"):
            on = True
        elif on and ln.strip().startswith("- "):
            signals.append(ln.strip()[2:])
        elif on and ln.strip() and not ln.strip().startswith("- "):
            break
    return symptom, signals


def install(registry: dict, context, system: str) -> None:
    root = os.environ.get("SOP_DIR")
    if not root:
        return
    folder = Path(root) / system
    sops = {p.stem: p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.md"))}
    if not sops:
        return

    def read(sop_id: str = "") -> str:
        key = str(sop_id).strip()
        if key not in sops:
            return f"Unknown sop_id '{sop_id}'. Available: {', '.join(sops)}"
        return sops[key]

    registry["ReadSOP"] = SimpleTool(name="ReadSOP", description="Read one diagnostic SOP in full by its id.",
                                     run_fn=read, args_schema=ReadSOPArgs)
    context.tools_description = render_tools_description(registry)
    rows = []
    for key, text in sops.items():
        symptom, signals = describe(text)
        rows.append(f"* {key} | {symptom}" + (" | signals: " + "; ".join(signals[:5]) if signals else ""))
    context.system_prompt = context.system_prompt + "\n\n" + SOP_HEADER + "\n".join(rows)
