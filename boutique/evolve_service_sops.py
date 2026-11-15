"""Write bad cases into the per-service SOPs, one per round: svc-sop-v(k-1) + bad case k -> svc-sop-vk.

  python boutique/evolve_service_sops.py [--rounds N]

The model gets the whole SOP library, what the agent answered (and which SOPs it read), the confirmed root cause
and how it can be found, with a plain instruction to write the case into the library. It revises exactly one
skill. Same procedure as experiments/cloudopsbench/sop_evolve.py; sizes are logged to holmes/skills/svc-sop-log.json.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import yaml
from openai import OpenAI

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "experiments" / "cloudopsbench"))
import make_experience as me  # noqa: E402

SKILLS = REPO / "holmes" / "skills"
INSTRUCTION = ("你维护一套按服务组织的排查 SOP，值班的排障 agent 照着它做。下面这次故障，agent 按现有 SOP 排查后结论是错的。"
               "同时给你确认过的根因，以及这个根因是怎么查出来的。把这个 bad case 写进 SOP 库，让 agent 下次遇到这类故障能查对。"
               "只修改一份 SOP：最应该处理好这次故障的那一份。保持原有版式（信号 → 步骤表、步骤、什么时候跳到别的服务、结论怎么写）。"
               '只输出 JSON：{"skill": "<你修改的 skill 名>", "summary": "<一句话说明改了什么>", "new_text": "<修改后的完整 SOP 正文，不含开头的 --- 元数据>"}')


def split(text: str) -> tuple[str, str]:
    _, head, body = text.split("---\n", 2)
    return head, body.strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=99)
    args = ap.parse_args()
    spec = json.loads((REPO / "boutique" / "badcases.json").read_text(encoding="utf-8"))
    scenarios = {s["id"]: s for s in yaml.safe_load((REPO / "scenarios" / "boutique.yaml").read_text(encoding="utf-8"))["scenarios"]}
    log_path = SKILLS / "svc-sop-log.json"
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
    env = me.dotenv()
    client = OpenAI(api_key=env["LLM_API_KEY"], base_url=env.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/v1")
    for k, case in enumerate(spec["cases"][: args.rounds], 1):
        prev, cur = SKILLS / f"svc-sop-v{k - 1}", SKILLS / f"svc-sop-v{k}"
        if cur.exists():
            continue
        lib = {p.parent.name: split(p.read_text(encoding="utf-8")) for p in sorted(prev.glob("*/SKILL.md"))}
        sc = scenarios[case["scenario"]]
        run = json.loads((REPO / case["run"]).read_text(encoding="utf-8"))
        read = [t["description"].replace("Skills: Fetch Skill ", "") for t in run["tool_calls"] if t["tool_name"] == "fetch_skill"]
        user = ("## 现有 SOP 库\n\n" + "\n\n---\n\n".join(f"【skill {n}】\n{b}" for n, (_, b) in lib.items())
                + f"\n\n## 这次故障\n用户反馈：{sc['symptom']}\n\nagent 读过的 SOP（按顺序）：{'、'.join(read)}\n\nagent 的结论：\n{run['result']}"
                + f"\n\n## 实际情况\n确认的根因：{sc['answer']['explanation']}\n\n是怎么查出来的：{spec['found_by'][case['scenario']]}")
        resp = client.chat.completions.create(model=env.get("LLM_MODEL", "deepseek-flash"), temperature=0, max_tokens=60000,
                                              messages=[{"role": "system", "content": INSTRUCTION}, {"role": "user", "content": user}])
        text = (resp.choices[0].message.content or "").strip()
        data = json.loads(text[text.index("{"): text.rindex("}") + 1])
        name = data["skill"].strip().strip("`")
        if name not in lib:
            raise SystemExit(f"round {k}: unknown skill '{name}'")
        shutil.copytree(prev, cur)
        (cur / name / "SKILL.md").write_text(f"---\n{lib[name][0]}---\n\n{data['new_text'].strip()}\n", encoding="utf-8")
        entry = {"round": k, "scenario": case["scenario"], "run": case["run"], "skill": name, "summary": data["summary"],
                 "chars_before": len(lib[name][1]), "chars_after": len(data["new_text"].strip())}
        log.append(entry)
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps(entry, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
