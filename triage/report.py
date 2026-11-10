"""Results JSONL -> markdown. Reports per scenario (successes out of reps, failure types), never pools
three repetitions of one scenario as three independent samples."""
from __future__ import annotations

from collections import defaultdict
from statistics import mean


def render(records: list[dict]) -> str:
    arms = sorted({r["arm"] for r in records}, key=lambda a: ["rule", "text", "code"].index(a) if a in ("rule", "text", "code") else 9)
    scenarios = sorted({r["scenario"] for r in records})
    cell = defaultdict(list)
    for r in records:
        cell[(r["scenario"], r["arm"])].append(r)

    lines = ["## 按场景的结果（成功次数 / 有效次数；环境失败不计入）", "",
             "| 场景 | 类型 | " + " | ".join(arms) + " |", "| --- | --- | " + " | ".join("---" for _ in arms) + " |"]
    for sc in scenarios:
        kind = next((r.get("scenario_kind") for r in records if r["scenario"] == sc), "")
        row = [sc, kind or ""]
        for arm in arms:
            runs = cell[(sc, arm)]
            valid = [r for r in runs if not r.get("env_failure")]
            ok = sum(1 for r in valid if (r.get("grade") or {}).get("correct"))
            review = sum(1 for r in valid if (r.get("grade") or {}).get("needs_review"))
            text = f"{ok}/{len(valid)}" if valid else "—"
            if review:
                text += f"（{review} 待复核）"
            env = len(runs) - len(valid)
            if env:
                text += f"，环境失败 {env}"
            row.append(text)
        lines.append("| " + " | ".join(row) + " |")

    lines += ["", "## 按故障机制汇总（场景级：一个场景的有效运行全部答对才算这个场景答对）", "",
              "| 机制 | 集合 | 场景数 | " + " | ".join(arms) + " |", "| --- | --- | --- | " + " | ".join("---" for _ in arms) + " |"]
    mech_split = {}
    for r in records:
        mech = r.get("mechanism") or "unknown"
        mech_split.setdefault((mech, r.get("split", "")), set()).add(r["scenario"])
    for (mech, split), scs in sorted(mech_split.items()):
        row = [mech, split, str(len(scs))]
        for arm in arms:
            solved = 0
            for sc in scs:
                valid = [r for r in cell[(sc, arm)] if not r.get("env_failure")]
                if valid and all((r.get("grade") or {}).get("correct") for r in valid):
                    solved += 1
            row.append(f"{solved}/{len(scs)}")
        lines.append("| " + " | ".join(row) + " |")

    lines += ["", "## 按配置的开销（只算有效运行）", "",
              "| 配置 | 有效运行 | 平均耗时 s | 平均工具调用 | 平均模型调用 | 平均重复调用 | 平均 token | 总费用 USD | 预算耗尽次数 | 停在症状层次数 |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for arm in arms:
        valid = [r for r in records if r["arm"] == arm and not r.get("env_failure")]
        if not valid:
            continue
        b = [r["budget"] for r in valid]
        tokens = [r["usage"]["prompt_tokens"] + r["usage"]["completion_tokens"] for r in valid]
        exhausted = sum(1 for r in valid if r["diagnosis"]["status"] == "budget_exhausted")
        stopped = sum(1 for r in valid if (r.get("grade") or {}).get("stopped_at_symptom"))
        lines.append(f"| {arm} | {len(valid)} | {mean(x['wall_seconds'] for x in b):.1f} | "
                     f"{mean(x['tool_calls'] for x in b):.1f} | {mean(x['model_calls'] for x in b):.1f} | "
                     f"{mean(x['duplicate_calls'] for x in b):.1f} | {mean(tokens):.0f} | "
                     f"{sum(r['cost_usd'] for r in valid):.3f} | {exhausted} | {stopped} |")

    failures = [r for r in records if not r.get("env_failure") and not (r.get("grade") or {}).get("correct")]
    lines += ["", f"## 失败与待复核明细（{len(failures)} 条）", ""]
    for r in failures:
        d, g = r["diagnosis"], r.get("grade") or {}
        lines.append(f"- {r['scenario']} / {r['arm']} / rep{r['rep']}：status={d['status']}，category={d['cause_category']}，"
                     f"object={d.get('object')}，cause={d['cause'][:80]}；"
                     f"status_ok={g.get('status_ok')} object_ok={g.get('object_ok')} cause_ok={g.get('cause_ok')}"
                     f"{'；' + '；'.join(g.get('notes', [])) if g.get('notes') else ''}")
    env_fail = [r for r in records if r.get("env_failure")]
    lines += ["", f"环境失败 {len(env_fail)} 次，占全部 {len(records)} 次运行的 "
              f"{(len(env_fail) / len(records) * 100 if records else 0):.0f}%（单独公开，不计入诊断准确率）。"]
    return "\n".join(lines)
