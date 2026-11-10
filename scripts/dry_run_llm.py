"""Real-model dry run against the FAKE cluster — no Kubernetes needed, only an API key.

Checks what offline tests with a scripted model cannot:
  * the provider accepts our tool schemas and returns tool calls,
  * the model actually ends by calling submit_diagnosis / returns parseable JSON,
  * real token usage and cost per diagnosis, per arm.

  python scripts/dry_run_llm.py                       # 14 fake scenarios x arms text,code,rule, 1 repetition
  python scripts/dry_run_llm.py --scenario R05,R11 --arms code
  python scripts/dry_run_llm.py --max-cost 1.0        # stop once spend reaches $1 (default 2)

Results go to results/dryrun-<time>.jsonl (same format as real runs) and a report is printed.
The fake cluster only knows the data coded in tests/fake_cluster.py, so accuracy here is a smoke signal
about prompts and plumbing, NOT a result for the write-up.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from fake_cluster import GENERATED_STATE, NS, SCENARIO_STATE, FakeCluster  # noqa: E402

from triage.budget import Budget, BudgetExceeded  # noqa: E402
from triage.kube import FakeRunner  # noqa: E402
from triage.lifecycle import load_scenarios  # noqa: E402
from triage.llm import LLMClient  # noqa: E402
from triage.report import render  # noqa: E402
from triage.runner import cfg, diagnose_once, load_dotenv, make_budget, versions  # noqa: E402

STATES = {**SCENARIO_STATE, **GENERATED_STATE}
SINCE = "2026-11-20T10:01:00Z"


def preflight(llm: LLMClient) -> bool:
    """One tiny plain call and one tool call, so config problems show up before spending on the full run."""
    try:
        r = llm.chat([{"role": "user", "content": "只回复两个字母：ok"}], Budget(total_seconds=60, max_model_calls=1))
        print(f"[ok] 模型可以连通（{llm.describe().get('model')}）：{r.content.strip()[:20]!r}")
        tool = {"type": "function", "function": {"name": "ping", "description": "连通性测试",
                                                 "parameters": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}}}
        r = llm.chat([{"role": "user", "content": "请调用 ping 工具，参数 x 填 hello。"}],
                     Budget(total_seconds=60, max_model_calls=1), tools=[tool])
        if r.tool_calls and r.tool_calls[0]["name"] == "ping":
            print("[ok] 模型支持工具调用")
            return True
        print("[FAIL] 模型没有返回工具调用；文本 SOP 这一组跑不起来。返回内容：", r.content[:200])
    except BudgetExceeded as exc:
        print(f"[FAIL] 调用模型失败：{exc.reason}。检查 .env 里的 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL。")
    return False


def main() -> int:
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", help="comma-separated ids; default: all 14 fake scenarios")
    p.add_argument("--arms", default="text,code,rule")
    p.add_argument("--reps", type=int, default=1)
    p.add_argument("--max-cost", type=float, default=2.0)
    a = p.parse_args()

    if not cfg("LLM_API_KEY", ""):
        print("缺少 LLM_API_KEY：复制 .env.example（Windows 用 windows.env.example）为 .env 并填写。")
        return 1
    llm = LLMClient()
    if not preflight(llm):
        return 1

    wanted = a.scenario.split(",") if a.scenario else list(STATES)
    scenarios = [s for s in load_scenarios(split="all", source="all") if s["id"] in wanted and s["id"] in STATES]
    price_in, price_out = float(cfg("PRICE_INPUT_PER_M", "0.30")), float(cfg("PRICE_OUTPUT_PER_M", "1.20"))
    out = REPO / "results" / f"dryrun-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    spent, records, ver = 0.0, [], versions()
    print(f"\n{len(scenarios)} 个假集群场景 × {a.arms} × {a.reps} 次，结果写入 {out}\n")
    from triage.grader import grade
    from triage.schema import Diagnosis
    for sc in scenarios:
        for rep in range(1, a.reps + 1):
            for arm in a.arms.split(","):
                if spent >= a.max_cost:
                    print(f"已花费 ${spent:.3f}，达到上限 ${a.max_cost}，停止。")
                    break
                runner = FakeRunner(FakeCluster(STATES[sc["id"]]).handle)
                rec = {"run_id": uuid.uuid4().hex[:10], "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "dry_run": True,
                       "scenario": sc["id"], "split": sc["split"], "scenario_kind": sc.get("kind"),
                       "template": sc.get("template"), "mechanism": sc.get("mechanism") or sc["answer"]["cause_category"],
                       "arm": arm, "rep": rep, "versions": ver, "env_failure": None}
                rec.update(diagnose_once(arm, llm if arm != "rule" else None, runner, NS, sc["symptom"], SINCE,
                                         price_in, price_out, budget_factory=make_budget))
                rec["grade"] = grade(Diagnosis(**rec["diagnosis"]), sc["answer"])
                spent += rec["cost_usd"]
                records.append(rec)
                with out.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                d, g = rec["diagnosis"], rec["grade"]
                print(f"{sc['id']:24s} {arm:5s} {'对' if g['correct'] else '错'}  status={d['status']:16s} "
                      f"category={d['cause_category']:22s} 工具{rec['budget']['tool_calls']:2d} 模型{rec['budget']['model_calls']:2d} "
                      f"{rec['budget']['wall_seconds']:5.1f}s ${rec['cost_usd']:.4f}  停止原因={rec['stop_reason']}")
    print("\n" + render(records))
    print(f"\n总花费约 ${spent:.3f}。注意：这是假集群上的试跑，只用来检查提示词和流程，不能当作实验结果。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
