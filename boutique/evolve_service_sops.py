"""Write bad cases into the per-service SOPs, one per round: svc-sop-v(k-1) + bad case k -> svc-sop-vk.

  python boutique/evolve_service_sops.py [--rounds N]

Two ways of supplying the lesson (per case in boutique/badcases.json):
  - with `found_by` for the scenario (rounds 1-4): the model is given the confirmed root cause and how it is found.
  - without (rounds 5+, "self" mode): the model is told only that the conclusion was wrong. It first writes its own
    post-mortem from the evidence the agent collected (tool outputs, truncated), then writes that into the library.
    The post-mortem is logged next to the real cause so the two can be compared; the real cause is never shown to it.
It revises exactly one skill per round. Sizes and post-mortems are logged to holmes/skills/svc-sop-log.json.
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


POSTMORTEM = ("你是值班负责人，在做一次故障复盘。排障 agent 对下面这次故障给出的结论已被确认是错的（只知道错了，不知道正确答案）。"
              "根据 agent 排查时实际拿到的证据，重新判断真正的根因最可能是什么，以及应该怎么查才能查到。证据不足以确定时也要给出你认为最可能的判断。"
              '只输出 JSON：{"object": "<出问题的对象，如 Service/xxx、Deployment/xxx、Pod/xxx>", "cause": "<根因，一两句>", '
              '"how_found": "<应该看什么、看到什么就能确定，三五句>", "confidence": "<高|中|低>"}')
REWRITE_SELF = INSTRUCTION.replace("同时给你确认过的根因，以及这个根因是怎么查出来的。", "同时给你事后复盘得出的根因和查法。")


def evidence(run: dict, cap_each: int = 2500, cap_total: int = 120000) -> str:
    """What the agent saw: its tool calls with (truncated) outputs, in order."""
    parts, used = [], 0
    for i, t in enumerate(run["tool_calls"], 1):
        if t["tool_name"] == "fetch_skill":
            continue
        block = f"[{i}] {t.get('description')}\n{(t.get('data') or '')[:cap_each]}"
        if used + len(block) > cap_total:
            parts.append("（后面的工具输出省略）")
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


def split(text: str) -> tuple[str, str]:
    _, head, body = text.split("---\n", 2)
    return head, body.strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=99)
    args = ap.parse_args()
    spec = json.loads((REPO / "boutique" / "badcases.json").read_text(encoding="utf-8"))
    scenarios = {s["id"]: s for f in ("boutique.yaml", "boutique_gen.yaml")
                 for s in yaml.safe_load((REPO / "scenarios" / f).read_text(encoding="utf-8"))["scenarios"]}
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
        library = "## 现有 SOP 库\n\n" + "\n\n---\n\n".join(f"【skill {n}】\n{b}" for n, (_, b) in lib.items())
        incident = (f"## 这次故障\n用户反馈：{sc['symptom']}\n\nagent 读过的 SOP（按顺序）：{'、'.join(read)}\n\n"
                    f"agent 的结论（已确认是错的）：\n{run['result']}")

        def ask(system: str, user: str) -> dict:
            r = client.chat.completions.create(model=env.get("LLM_MODEL", "deepseek-flash"), temperature=0, max_tokens=60000,
                                               messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
            t = (r.choices[0].message.content or "").strip()
            return json.loads(t[t.index("{"): t.rindex("}") + 1])

        post = None
        if case["scenario"] in spec.get("found_by", {}):
            data = ask(INSTRUCTION, library + "\n\n" + incident + f"\n\n## 实际情况\n确认的根因：{sc['answer']['explanation']}\n\n"
                       f"是怎么查出来的：{spec['found_by'][case['scenario']]}")
        else:
            post = ask(POSTMORTEM, incident + "\n\n## agent 排查时拿到的证据\n" + evidence(run))
            data = ask(REWRITE_SELF, library + "\n\n" + incident + f"\n\n## 事后复盘\n出问题的对象：{post['object']}\n根因：{post['cause']}\n"
                       f"应该怎么查：{post['how_found']}")
        name = data["skill"].strip().strip("`")
        if name not in lib:
            raise SystemExit(f"round {k}: unknown skill '{name}'")
        shutil.copytree(prev, cur)
        (cur / name / "SKILL.md").write_text(f"---\n{lib[name][0]}---\n\n{data['new_text'].strip()}\n", encoding="utf-8")
        entry = {"round": k, "scenario": case["scenario"], "run": case["run"], "skill": name, "summary": data["summary"],
                 "chars_before": len(lib[name][1]), "chars_after": len(data["new_text"].strip())}
        if post:
            entry["postmortem"] = post
            entry["truth"] = {"object": sc["answer"]["object"], "explanation": sc["answer"]["explanation"]}
        log.append(entry)
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps(entry, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
