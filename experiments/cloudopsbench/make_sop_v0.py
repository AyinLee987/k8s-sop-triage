"""Initial SOP library (v0) in document form, converted from the benchmark's diagnostic-graph skills.

  python experiments/cloudopsbench/make_sop_v0.py --dataset /path/to/Cloud-OpsBench

The benchmark ships 18 symptom SOPs as YAML graphs (agents/cloudops_skill_agent/harness/skills/<system>/*.yaml:
nodes with goal / actions / evidence / next). This writes each one as a Markdown document under
sops/v0/<system>/<symptom_id>.md with the same content and order, nothing added or removed, so that a model can
later rewrite the document the way an engineer would ("write this bad case into the SOP").
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent


def render(d: dict) -> str:
    out = [f"# SOP: {d['symptom_id']}", "", f"Symptom: {d['description']}", "", "Signals:"]
    out += [f"- {s}" for s in d.get("signals", [])]
    out += ["", f"Start at: {', '.join(d['entry'])}", "", "## Steps"]
    for name, node in d["nodes"].items():
        out += ["", f"### Step: {name}", f"Goal: {node['goal']}", "Actions:"]
        for a in node.get("actions", []):
            args = ", ".join(f"{k}={v}" for k, v in (a.get("args") or {}).items())
            out.append(f"- {a['tool']}({args})")
        out.append("Evidence to collect: " + ", ".join(node.get("evidence", [])))
        nxt = node.get("next") or []
        out.append("Next: " + (", ".join(nxt) if nxt else "(end of this SOP - conclude here)"))
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    args = ap.parse_args()
    src = Path(args.dataset) / "agents" / "cloudops_skill_agent" / "harness" / "skills"
    n = 0
    for f in sorted(src.glob("*/*.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        target = HERE / "sops" / "v0" / f.parent.name / f"{d['symptom_id']}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(d), encoding="utf-8")
        n += 1
    print(n, "SOPs written to", (HERE / "sops" / "v0").relative_to(HERE.parents[1]))


if __name__ == "__main__":
    main()
