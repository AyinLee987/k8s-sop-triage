"""Source-code tools for the TrainTicket cases of Cloud-OpsBench.

The benchmark ships source files (and ListCodeFiles / GetSourceCode) only for Online Boutique. On TrainTicket the
agent therefore cannot tell "pod network delay" from "a delay written into the code": both look like one slow
service with healthy pods, and nothing it can query separates them. This adds the same two tools for TrainTicket,
backed by a checkout of the upstream project (FudanSELab/train-ticket). The benchmark injects no code defects
into TrainTicket, so the checkout is the code that was running.

TT_SOURCE_DIR (env): path of the train-ticket checkout. Installed only for the train-ticket system.
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

from tools.cloudops import SimpleTool, render_tools_description

MAX_CHARS = 12000


class ListCodeFilesInput(BaseModel):
    app_name: str = Field(description="Microservice name, e.g. ts-order-service")


class GetSourceCodeInput(BaseModel):
    app_name: str = Field(description="Microservice name, e.g. ts-order-service")
    file_path: str = Field(description="Service-relative path exactly as returned by ListCodeFiles")


def install(registry: dict, context, system: str) -> None:
    root = os.environ.get("TT_SOURCE_DIR")
    if not root or system != "trainticket" or "GetSourceCode" in registry:
        return
    root_path = Path(root).resolve()

    def service_dir(app_name: str) -> Path | None:
        name = str(app_name).strip().removeprefix("app/")
        d = (root_path / name).resolve()
        return d if d.is_dir() and d.parent == root_path and name.startswith("ts-") else None

    def list_files(app_name: str = "") -> str:
        d = service_dir(app_name)
        if d is None:
            return f"Unknown service '{app_name}'."
        files = sorted(p.relative_to(d).as_posix() for p in d.rglob("*.java") if "/test/" not in p.as_posix())
        return "\n".join(files) or "No source files."

    def get_source(app_name: str = "", file_path: str = "") -> str:
        d = service_dir(app_name)
        if d is None:
            return f"Unknown service '{app_name}'."
        target = (d / str(file_path)).resolve()
        if d not in target.parents or not target.is_file():
            return "File not found. Use a path exactly as returned by ListCodeFiles."
        text = target.read_text(encoding="utf-8", errors="replace")
        return text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + f"\n...[{len(text) - MAX_CHARS} more characters]"

    registry["ListCodeFiles"] = SimpleTool(
        name="ListCodeFiles", description="Returns the business-code file list for a given microservice (service-relative paths).",
        run_fn=list_files, args_schema=ListCodeFilesInput)
    registry["GetSourceCode"] = SimpleTool(
        name="GetSourceCode", description="Returns the source code of one selected file.",
        run_fn=get_source, args_schema=GetSourceCodeInput)
    context.tools_description = render_tools_description(registry)
