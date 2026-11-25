"""Build a blind grading packet for a second grader (e.g. another model in a web chat).

The packet holds the v4 grading rules, each scenario's standard answer and every run's final answer,
with the runs of one scenario shuffled and renamed (FV08-A, FV08-B, ...) so the grader cannot tell
repeats, clusters or batches apart. The key that maps codes back to runs is written next to it.

    python scripts/make_grading_packet.py --tags v4-pilot-cpu,v4-pilot-cpu2 --out results/grading/v4-pilot
"""
import argparse
import json
import random
import string
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
MECH = {"cpu": "cpu", "network": "network", "config": "config", "app": "app"}


def scenario_keys():
    data = yaml.safe_load((REPO / "scenarios" / "boutique_v4.yaml").read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else data.get("scenarios", data)
    return {s["id"]: s for s in items}


def causes(sc):
    a = sc["answer"]
    main = {"name": a["object"]["name"], "mechanism": a["mechanism"], "decidable": a["decidable"],
            "explanation": a["explanation"], "decidable_why": a.get("decidable_why", "")}
    rest = [{"name": x["object"]["name"], "mechanism": x["mechanism"], "decidable": x["decidable"],
             "explanation": x["explanation"], "decidable_why": x.get("decidable_why", "")} for x in a.get("also", [])]
    return [main, *rest]


def key_section(sid, sc):
    a = sc["answer"]
    out = [f"### {sid}", "", f"- 用户报告的症状：{sc['symptom']}"]
    if sc.get("ticket_guess"):
        out.append(f"- 工单里附带的猜测（错误猜测，不是原因）：{sc['ticket_guess']}")
    out.append(f"- 出题设计标签：{', '.join(sc.get('design') or [])}")
    out += ["", "真实原因（每个都要找到）："]
    for i, c in enumerate(causes(sc), 1):
        out.append(f"{i}. 原因编号 `{sid}#{i}`：**{c['name']}**，机制大类 **{MECH.get(c['mechanism'], c['mechanism'])}**，"
                   f"decidable={'true' if c['decidable'] else 'false'}。{c['explanation']}")
        out.append(f"   - 能不能分清：{c['decidable_why']}")
    out += ["", "同一次发布里的无害改动（写进 root_causes 就算多报）："]
    for h in a.get("harmless_changes", []):
        out.append(f"- **{h['object']}**（{h['change']}）：{h['why']}")
    out += ["", "命名空间里的后台服务（写进 root_causes 就算多报）："]
    for h in a.get("ambient", []):
        out.append(f"- **{h['name']}**：{h['why']}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tags", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=20261125)
    args = ap.parse_args()
    rules = json.loads((REPO / "boutique" / "eval_grades_v4.json").read_text(encoding="utf-8"))
    keys = scenario_keys()

    runs = {}
    for tag in args.tags.split(","):
        for line in (REPO / "results" / "holmes_batch" / tag / "runs.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            text = json.loads((REPO / r["answer_file"]).read_text(encoding="utf-8"))["result"]
            runs.setdefault(r["scenario"], []).append({"tag": tag, "repeat": r["repeat"], "cluster": r.get("cluster"),
                                                       "answer_file": r["answer_file"], "text": text})
    rng = random.Random(args.seed)
    blind_key, blocks = {}, []
    order = sorted(runs)
    for sid in order:
        rng.shuffle(runs[sid])
        for letter, run in zip(string.ascii_uppercase, runs[sid]):
            code = f"{sid}-{letter}"
            blind_key[code] = {k: run[k] for k in ("tag", "repeat", "cluster", "answer_file")}
            blocks.append(f"<<<回答开始 {code}>>>\n{run['text'].strip()}\n<<<回答结束 {code}>>>")

    n = sum(len(v) for v in runs.values())
    cause_ids = [f"`{sid}#{i}`" for sid in order for i in range(1, len(causes(keys[sid])) + 1)]
    pc, pr = rules["per_cause"], rules["per_run"]
    prompt = f"""# 判分任务：Kubernetes 故障排查 Agent 的回答（{n} 份）

你是这次评估的第二判分人，请独立判分。不要猜别人会怎么判，也不要因为回答写得长、推理写得好就放宽；只按下面的规则和标准答案判。

## 背景

- 被测的是一个排查故障的 AI Agent。环境是 Google 的 Online Boutique 微服务商城，跑在 Kubernetes 的 `boutique` 命名空间里。
- 每道题在一次"发布"里注入一到两个真实故障，同一次发布还带着几个无害的改动（干扰项）。其中一个服务被加了一个日志转发的 sidecar 容器 `log-shipper`，它的 CPU 上限只有 20m，一直被限流；指标表按 Pod 合计，所以这个 Pod 看起来被限流，但业务容器不受影响。命名空间里还有几个和用户请求无关的后台服务。
- Agent 只有只读权限。它的回答末尾有一段 JSON：`root_causes`（它认定的原因，每条有 object、role、mechanism、confidence）、`not_causes`（查过并排除的）、`unexplained`（没解释的现象）。
- 共 {len(order)} 道题、{n} 份回答。同一道题的几份回答已经打乱顺序并重新编号（如 `FV08-A`），编号不代表先后，也不代表组别。

## 判分规则（原文）

判分以回答末尾 JSON 的 `root_causes` 为准；JSON 缺失、解析不了，或者和正文明显矛盾时，读正文，并在说明里记一句。`role` 写 primary 还是 secondary 不影响判分。

**每个真实原因**分别记位置和机制：

- 位置·对：{pc['位置']['对']}
- 位置·没定：{pc['位置']['没定']}
- 位置·错：{pc['位置']['错']}
- 机制大类：{pc['机制']['大类']}
- 机制·对：{pc['机制']['对']}
- 机制·如实未定：{pc['机制']['如实未定']}
- 机制·没定：{pc['机制']['没定']}
- 机制·错：{pc['机制']['错']}
- 位置错时：{pc['机制']['位置错时']}
- 能不能分清：{pc['能不能分清']}

**每份回答**：

- 多报：{pr['多报']}
- 通过：{pr['通过']}
- 严格通过：{pr['严格通过']}
- 部分：{pr['部分']}
- 错：{pr['错']}
- 第二个原因的去向：{pr['第二个原因的去向']}

## 标准答案

{chr(10).join(key_section(sid, keys[sid]) + chr(10) for sid in order)}
## 输出格式

（下面的例子是虚构的格式示范，和这 {n} 份回答无关。）先逐份写简短的判分理由（每份几行：每个真实原因的位置和机制、依据是回答里的哪句话；多报了什么）。然后在最后给**一个** JSON 代码块，是一个数组，每份回答一项，{n} 项都要有，格式如下：

```json
[
  {{
    "run": "XX00-A",
    "json_ok": true,
    "causes": [
      {{"cause": "XX00#1", "location": "对", "mechanism": "对", "fate": "找到", "basis": "root_causes 第 1 条：<服务名>，mechanism=cpu"}}
    ],
    "extra": [
      {{"object": "<服务名>", "kind": "无害改动", "basis": "root_causes 第 2 条把这次发布里的无害改动写成了原因"}}
    ],
    "extra_count": 1,
    "pass": false,
    "strict_pass": false,
    "verdict": "部分",
    "note": ""
  }}
]
```

- `cause` 用标准答案里的原因编号：{', '.join(cause_ids)}。每份回答要列出它那道题的全部真实原因。
- `location` 取 对 / 没定 / 错；`mechanism` 取 对 / 如实未定 / 没定 / 错 / -（位置错时写 -）。
- `fate` 取 找到 / 机制没定 / 位置没定 / 漏掉 / 判给别处（"判给别处"指把这个原因造成的现象算到了别的对象头上）。
- `extra` 列出 root_causes 里每一个不是真实原因的对象；`kind` 取 无害改动 / sidecar 所在的服务 / 后台服务 / 被拖累的上游 / 不存在的问题。
- `verdict` 取 通过 / 部分 / 错。
- `json_ok`：回答末尾的 JSON 能不能正常解析（有语法错误就写 false，并按正文判）。
- 拿不准的地方在 `note` 里写清楚你是怎么取舍的。

## {n} 份回答

{chr(10).join(b + chr(10) for b in blocks)}"""
    out = REPO / args.out
    out.mkdir(parents=True, exist_ok=True)
    (out / "gemini_prompt.md").write_text(prompt, encoding="utf-8")
    (out / "blind_key.json").write_text(json.dumps(blind_key, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{n} runs, {len(prompt)} chars -> {out / 'gemini_prompt.md'}")


if __name__ == "__main__":
    main()
