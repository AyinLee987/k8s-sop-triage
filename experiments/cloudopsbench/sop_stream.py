"""Rewrite the SOP library after EVERY incident, with no answer and no right/wrong signal, for N rounds.

  python sop_stream.py --dataset /path/to/Cloud-OpsBench --draw 60     # draw the incident stream (once)
  python sop_stream.py --dataset /path/to/Cloud-OpsBench               # run / resume the rounds

Round k: the agent diagnoses incident k with library s(k-1); the model then reflects on that run (what it thinks
the cause was and what was learnt) and rewrites one whole SOP of that system -> library sk. It never sees the
ground truth or whether the agent was right; both are only written to the log. This is the setting in which
"context collapse" was reported (ACE, arXiv 2510.04618: whole-document rewriting at every step, no labels).
Libraries: sops_stream/s0 (= sops/v0) ... sN. Log: sops_stream/log.json (sizes of every SOP after every round).
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

from openai import OpenAI

import make_experience as me

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = HERE / "sops_stream"
STREAM = HERE / "sample_sop_stream60.json"
LIMITS = ["--max-tokens", "100000", "--llm-timeout", "600", "--max-iterations", "100", "--case-timeout", "1200"]
INSTRUCTION = (
    "You maintain the troubleshooting SOP library that an on-call diagnosis agent follows. After every incident the "
    "library is updated with what was learnt. Below is the current library and the record of the incident the agent "
    "just handled: the reported symptom, the steps it took with what it observed, and its conclusion. Nobody has "
    "reviewed the conclusion. First reflect: what do you believe the root cause was, and what should the library "
    "say so that such an incident is handled well next time? Then write that into the library by revising exactly "
    "one SOP, the one most relevant to this incident. Keep the document layout (title line '# SOP: <id>', "
    "'Symptom:', 'Signals:', 'Start at:', then '## Steps' with '### Step:' blocks containing Goal / Actions / "
    "Evidence to collect / Next). Answer with JSON only: {\"believed_object\": \"<kind/name you believe is at "
    "fault>\", \"believed_cause\": \"<one sentence>\", \"lesson\": \"<one or two sentences>\", \"sop_id\": \"<id of "
    "the SOP you revised>\", \"summary\": \"<one sentence: what you changed>\", \"new_text\": \"<the complete revised "
    "SOP document>\"}")


def draw(dataset: Path, n: int, seed: int, n_medium: int) -> None:
    rng = random.Random(seed)
    used = {c["id"] for c in json.loads((HERE / "sample_sop_eval.json").read_text(encoding="utf-8"))["cases"]}
    pools = {"hard": [], "medium": []}
    for system in ("boutique", "trainticket"):
        for cat in sorted(p for p in (dataset / "benchmark" / system).iterdir() if p.is_dir()):
            for d in sorted((p for p in cat.iterdir() if p.name.isdigit()), key=lambda p: int(p.name)):
                m = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
                cid = f"{system}/{cat.name}/{d.name}"
                if cid not in used and m.get("difficulty") in pools:
                    pools[m["difficulty"]].append({"id": cid, "system": system, "category": cat.name, "case": d.name,
                                                   "difficulty": m["difficulty"], "query": m.get("query"), "truth": m["result"]})
    cases = rng.sample(pools["hard"], n - n_medium) + rng.sample(pools["medium"], n_medium)
    rng.shuffle(cases)
    STREAM.write_text(json.dumps({"dataset": "LLM4Ops/Cloud-OpsBench", "seed": seed, "n": len(cases),
                                  "note": f"incident stream for sop_stream.py: {n - n_medium} hard + {n_medium} medium cases drawn at random "
                                          "(benchmark difficulty labels), none of them in sample_sop_eval.json; order = order of arrival",
                                  "cases": cases}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    from collections import Counter
    print(len(cases), Counter((c["system"], c["category"]) for c in cases))


def record(tag: str, case: dict, cap: int = 700, total: int = 60000) -> tuple[str, dict]:
    base = REPO / "results" / "cloudopsbench" / tag / case["system"]
    d = json.loads(next(base.glob(f"*/{case['category']}/{case['case']}/{case['case']}.json")).read_text(encoding="utf-8"))
    lines, used = [], 0
    for s in d["steps"]:
        if not s.get("action_name") or s["action_name"] in ("Submit", "ReadSOP"):
            if s.get("action_name") == "ReadSOP":
                lines.append(f"- ReadSOP {s.get('action_input')}")
            continue
        block = (f"- {s['action_name']} {json.dumps(s.get('action_input'), ensure_ascii=False)[:160]}\n  thought: {(s.get('thought') or '')[:300]}\n"
                 f"  observed: {str(s.get('observation') or '')[:cap]}")
        if used + len(block) > total:
            lines.append("- (later steps omitted)")
            break
        lines.append(block)
        used += len(block)
    answer = str(d.get("final_answer") or "no answer submitted")[:2000]
    return "Steps the agent took:\n" + "\n".join(lines) + "\n\nThe agent's conclusion:\n" + answer, {"steps": len(d["steps"])}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--draw", type=int, default=0)
    ap.add_argument("--medium", type=int, default=10)
    ap.add_argument("--seed", type=int, default=20261160)
    ap.add_argument("--rounds", type=int, default=999)
    args = ap.parse_args()
    dataset = Path(args.dataset)
    if args.draw:
        return draw(dataset, args.draw, args.seed, args.medium)
    cases = json.loads(STREAM.read_text(encoding="utf-8"))["cases"][: args.rounds]
    if not (OUT / "s0").exists():
        OUT.mkdir(exist_ok=True)
        shutil.copytree(HERE / "sops" / "v0", OUT / "s0")
    log_path = OUT / "log.json"
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
    env = me.dotenv()
    client = OpenAI(api_key=env["LLM_API_KEY"], base_url=env.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/v1")
    tmp = Path("/tmp/sop_stream_case.json")
    for k, case in enumerate(cases, 1):
        prev, cur = OUT / f"s{k - 1}", OUT / f"s{k}"
        if cur.exists():
            continue
        tag = f"stream-r{k:02d}"
        tmp.write_text(json.dumps({"cases": [case]}, ensure_ascii=False), encoding="utf-8")
        subprocess.run([sys.executable, str(HERE / "run.py"), "--dataset", str(dataset), "--tag", tag, "--sample", str(tmp),
                        "--sop", str(prev), "--workers", "1", *LIMITS], check=True, capture_output=True, text=True)
        subprocess.run([sys.executable, str(HERE / "score.py"), "--tag", tag, "--sample", str(tmp), "--quiet"], check=True, capture_output=True, text=True)
        shutil.rmtree(REPO / "results" / "cloudopsbench" / "_workers" / tag, ignore_errors=True)
        row = json.loads((REPO / "results" / "cloudopsbench" / tag / "scores.json").read_text(encoding="utf-8"))["rows"][0]
        text, meta = record(tag, case)
        library = {p.stem: p.read_text(encoding="utf-8") for p in sorted((prev / case["system"]).glob("*.md"))}
        user = (f"## Current SOP library ({case['system']})\n\n" + "\n\n---\n\n".join(library.values())
                + f"\n\n## The incident just handled\nReported symptom: {case['query']}\n\n{text}")
        data = None
        for attempt in range(3):
            resp = client.chat.completions.create(model=env.get("LLM_MODEL", "deepseek-flash"), temperature=0, max_tokens=60000,
                                                  messages=[{"role": "system", "content": INSTRUCTION}, {"role": "user", "content": user}])
            out = (resp.choices[0].message.content or "").strip()
            try:
                data = json.loads(out[out.index("{"): out.rindex("}") + 1])
                if data["sop_id"].strip() in library and data["new_text"].strip():
                    break
            except (ValueError, KeyError):
                pass
            data = None
        entry = {"round": k, "case": case["id"], "truth": {"object": case["truth"]["fault_object"], "cause": case["truth"]["root_cause"]},
                 "agent_pred": row.get("pred"), "agent_top1": bool(row.get("top1")), "agent_steps": meta["steps"]}
        shutil.copytree(prev, cur)
        if data:
            sid = data["sop_id"].strip()
            (cur / case["system"] / f"{sid}.md").write_text(data["new_text"].strip() + "\n", encoding="utf-8")
            entry.update({"believed_object": data.get("believed_object"), "believed_cause": data.get("believed_cause"), "lesson": data.get("lesson"),
                          "sop": f"{case['system']}/{sid}", "summary": data.get("summary"),
                          "chars_before": len(library[sid]), "chars_after": len(data["new_text"].strip()),
                          "steps_before": library[sid].count("### Step:"), "steps_after": data["new_text"].count("### Step:")})
        else:
            entry["rewrite_failed"] = True
        entry["library_chars"] = {f"{p.parent.name}/{p.stem}": len(p.read_text(encoding="utf-8")) for p in sorted(cur.glob("*/*.md"))}
        log.append(entry)
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps({k2: v for k2, v in entry.items() if k2 != "library_chars"}, ensure_ascii=False)[:600], flush=True)


if __name__ == "__main__":
    main()
