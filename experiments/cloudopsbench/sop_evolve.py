"""Write bad cases into the SOP library, one per round - the practice the interviewer described.

  python experiments/cloudopsbench/sop_evolve.py --dataset /path/to/Cloud-OpsBench --badcases sop_badcases.json [--rounds 8]

Round k takes library version v(k-1) and bad case k (an incident the agent got wrong while following the SOPs) and
asks the model - the same model as the agent - to write the case into the library. The model sees every SOP of that
system, what the agent did and concluded, the confirmed root cause and how an expert found it, and returns one
revised SOP in full. Result: sops/v<k>/ (a copy of v(k-1) with that one document replaced) and a line in
sops/evolve_log.json (which SOP changed, its size before and after, the model's one-line summary).
The instruction is deliberately plain ("write this bad case into the SOP"); it does not ask the model to protect
other cases, because the naive practice is what is being measured.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from openai import OpenAI

import make_experience as me

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
INSTRUCTION = (
    "You maintain the troubleshooting SOP library that an on-call diagnosis agent follows. The agent handled the "
    "incident below with the current SOPs and got it wrong. Below you also get the confirmed root cause and how an "
    "expert actually found it. Write this bad case into the SOP library so that the agent diagnoses such an "
    "incident correctly next time. Revise exactly one SOP: the one that should have handled this incident. Keep the "
    "document layout (title line '# SOP: <id>', 'Symptom:', 'Signals:', 'Start at:', then '## Steps' with "
    "'### Step:' blocks containing Goal / Actions / Evidence to collect / Next). "
    "Answer with JSON only: {\"sop_id\": \"<id of the SOP you revised>\", \"summary\": \"<one sentence: what you "
    "changed>\", \"new_text\": \"<the complete revised SOP document>\"}")


def agent_run(tag: str, case: dict) -> str:
    base = REPO / "results" / "cloudopsbench" / tag / case["system"]
    path = next(base.glob(f"*/{case['category']}/{case['case']}/{case['case']}.json"))
    d = json.loads(path.read_text(encoding="utf-8"))
    lines = []
    for s in d["steps"]:
        if s.get("action_name") and s["action_name"] != "Submit":
            lines.append(f"- {s['action_name']} {json.dumps(s.get('action_input'), ensure_ascii=False)[:160]} :: {(s.get('thought') or '')[:260]}")
    answer = (d.get("final_answer") or "no answer submitted")[:1500]
    return "Steps the agent took:\n" + "\n".join(lines) + "\n\nThe agent's final answer:\n" + answer


def stats(text: str) -> dict:
    return {"chars": len(text), "lines": text.count("\n"), "steps": text.count("### Step:")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--badcases", default=str(HERE / "sop_badcases.json"))
    ap.add_argument("--rounds", type=int, default=8)
    args = ap.parse_args()
    dataset = Path(args.dataset)
    bad = json.loads(Path(args.badcases).read_text(encoding="utf-8"))["cases"][: args.rounds]
    log_path = HERE / "sops" / "evolve_log.json"
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
    env = me.dotenv()
    client = OpenAI(api_key=env["LLM_API_KEY"], base_url=env.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/v1")
    for k, case in enumerate(bad, 1):
        prev, cur = HERE / "sops" / f"v{k - 1}", HERE / "sops" / f"v{k}"
        if cur.exists():
            continue
        library = {p.stem: p.read_text(encoding="utf-8") for p in sorted((prev / case["system"]).glob("*.md"))}
        user = (f"## Current SOP library ({case['system']})\n\n" + "\n\n---\n\n".join(library.values())
                + f"\n\n## The incident\nUser-reported symptom: {case['query']}\n\n{agent_run(case['run_tag'], case)}"
                + f"\n\n## What it really was\nConfirmed root cause: {case['truth']['root_cause']} on {case['truth']['fault_object']} "
                  f"(fault class: {case['truth']['fault_taxonomy']})\n\nHow the expert found it:\n{me.trajectory_text(dataset, case)}")
        resp = client.chat.completions.create(model=env.get("LLM_MODEL", "deepseek-flash"), temperature=0, max_tokens=60000,
                                              messages=[{"role": "system", "content": INSTRUCTION}, {"role": "user", "content": user}])
        text = (resp.choices[0].message.content or "").strip()
        data = json.loads(text[text.index("{"): text.rindex("}") + 1])
        sid = data["sop_id"].strip()
        if sid not in library:
            raise SystemExit(f"round {k}: model named an unknown SOP '{sid}'")
        shutil.copytree(prev, cur)
        (cur / case["system"] / f"{sid}.md").write_text(data["new_text"].strip() + "\n", encoding="utf-8")
        entry = {"round": k, "case": case["id"], "truth": case["truth"]["root_cause"], "sop": f"{case['system']}/{sid}",
                 "summary": data["summary"], "before": stats(library[sid]), "after": stats(data["new_text"])}
        log.append(entry)
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps(entry, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
