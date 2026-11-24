"""Pilot: inject one scenario, ask HolmesGPT the scenario's symptom, save its answer + cost, reset.

  python scripts/holmes_pilot.py R06 [--model deepseek-flash] [--max-steps 25]

HolmesGPT runs with the read-only diagnoser kubeconfig and holmes/config.yaml (Kubernetes tools only).
The API key goes to HolmesGPT through the environment, never on the command line.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from triage import remediate  # noqa: E402
from triage.kube import KubectlRunner  # noqa: E402
from triage.lifecycle import StepLog, load_noises, load_scenarios, make_lifecycle  # noqa: E402
from triage.runner import cfg, load_dotenv, path_cfg  # noqa: E402

HOLMES = os.environ.get("HOLMES_BIN", "holmes")
OUT = REPO / "results" / "holmes_pilot"

# The default since 2026-11-17 (see scripts/holmes_loop.py, which uses the same words): the agent is told this is a real
# production incident and to judge from evidence only. Without it the agent reasons about "what a test would inject".
PROD_FRAME = ("这是线上生产环境里正在发生的真实故障，不是演练、测试或练习题，没有“出题人”，也没有预设的标准答案。"
              "只根据你在这个集群里实际观察到的证据下结论；不要推测“可能被注入了什么故障”，也不要因为某种原因更常见就选它。"
              "如果两个解释都说得通而你无法区分，就把两个都写出来，并说明还缺什么证据。\n\n")
# Online Boutique scenarios: diagnosis only, with the same user-side numbers the fix-and-verify loop gives in its first
# round, and the root causes repeated in a JSON block so that grading does not depend on reading free text.
FORMAT = """回答的最后必须是一个 ```json 代码块，格式如下（root_causes 写你认为的全部根因）：
```json
{"root_causes": [{"object": "<服务或对象>", "cause": "<原因>"}], "explanation": "<一两句话>"}
```"""


# ---- the third bank's prompt (docs/V3.md). Three changes, decided 2026-11-24 after the anchoring experiment and the
# trajectory analysis of the hedged answers (docs/ANCHORING-pilot.md, docs/HEDGING-analysis.md):
#   1. HolmesGPT's own "general_instructions" and "style_guide" blocks are switched off (ENABLED_PROMPTS) and replaced by
#      holmes/prompts/system-v3.md: the same text except the lines that ask to hedge anything not observed directly.
#   2. PROD_FRAME loses "if two explanations fit and you cannot tell them apart, write both": commit when the evidence
#      separates the explanations, say "cannot tell" only when it does not, and name what would separate them.
#   3. The coverage check (arm m2 of the anchoring experiment: list the symptoms first, then explain every one with an
#      estimate of how much of it each cause accounts for) and a structured answer with mechanism and confidence.
PROD_FRAME_V3 = ("这是线上生产环境里正在发生的真实故障，不是演练、测试或练习题，没有“出题人”，也没有预设的标准答案。"
                 "只根据你在这个集群里实际观察到的证据下结论，不要推测“可能被注入了什么故障”，也不要只因为某种原因更常见就选它。"
                 "证据支持一个解释、并且和其它解释矛盾时，就把它写成结论，并给出把握程度；只有在你能拿到的数据下几种解释预测的现象完全一样时，"
                 "才写分不开，并说明哪条观察能把它们区分开。\n\n")
CHECK_STEPS = """排查分两步做，顺序不能反：先整理发现，再下诊断。

第一步：只整理发现，不下结论。把观察到的异常整理成一张症状清单，至少包括：每一类用户请求（按接口分）现在的耗时或错误率、它健康时的水平、它调用了哪些下游各几次；以及各个 Pod 上看到的异常（限流、探针失败、重启、配置和上一版不一致等）。这一步不要写任何原因。

第二步：根据这张清单做诊断。下结论之前逐条核对：清单里的每一条症状由哪个原因解释、经过怎样的路径、证据是什么，这个原因能解释这条症状的多大一部分（例如多出来的毫秒数里它占多少），要写出估算。只要有一条症状没有被解释，或者只被解释了一部分，就把它标成“未解释”并继续排查，直到每一条都有证据支撑的解释，或者你确认在现有权限下已经查不下去。"""
FORMAT_V3 = """回答的最后必须是一个 ```json 代码块，格式如下（没有内容的字段写空数组）：
```json
{"root_causes": [{"object": "<命名空间/类型/名字>", "mechanism": "<network | cpu | config | app | unknown>", "cause": "<原因>", "confidence": "<high | medium | low>", "evidence": ["<支持这个结论的关键观察，带数字>"], "explains": "<它解释了哪些症状、大约多大一部分>", "alternatives": [{"mechanism": "<另一种排除不了的机制>", "why_not_ruled_out": "<为什么排除不了>", "would_separate": "<哪条观察能把两者分开>"}]}],
 "not_causes": [{"object": "<查过并排除的对象或改动>", "why": "<依据>"}],
 "unexplained": ["<还没有被任何根因解释的症状>"],
 "explanation": "<一两句话>"}
```
要求：
- root_causes 只写你判断为原因的对象，每个独立的原因写一条；被别的原因拖累、自身没有问题的对象不写进来。
- mechanism 写你判断最可能的那一种，把握不大就把 confidence 写低。只有在你能拿到的数据下几种机制预测的现象完全一样时才写 unknown，并在 alternatives 里写出候选和能把它们分开的观察。
- 先找到的原因，没有被证据推翻，就不要因为又找到了别的原因而删掉；在 explains 里写清各自解释了多少。"""
SYSTEM_V3 = REPO / "holmes" / "prompts" / "system-v3.md"
# every HolmesGPT prompt component except the two replaced ones (and TodoWrite, which is off by default anyway)
ENABLED_PROMPTS_V3 = ("files,time_skills,intro,ask_user,toolset_instructions,permission_errors,cluster_name,scoped_namespaces,"
                      "conversation_link,system_prompt_additions")


def prompt_version(sc: dict, requested: str = "auto") -> str:
    """auto: the third bank gets the v3 prompt, everything else the prompt it was built with."""
    if requested != "auto":
        return requested
    return "v3" if int(sc.get("bank") or 0) >= 3 else "v2"


def holmes_overrides(version: str) -> tuple[dict, list[str]]:
    """Environment and `holmes ask` arguments for a prompt version. v2: none (HolmesGPT's own system prompt)."""
    if version != "v3":
        return {}, []
    return {"ENABLED_PROMPTS": ENABLED_PROMPTS_V3}, ["--system-prompt-additions", SYSTEM_V3.read_text(encoding="utf-8")]


def build_prompt(sc: dict, ns: str, frame: str, first_obs: dict | None, version: str = "v2") -> str:
    if version == "v3":
        return (PROD_FRAME_V3 + f"命名空间 {ns} 里出了问题。用户反馈：{sc['symptom']}\n"
                f"监控看到的用户侧情况：{remediate.describe(first_obs, checkout=True)}\n\n"
                f"请排查根因。你只有只读权限；这次只需要定位原因，不需要提出或执行修复。\n\n{CHECK_STEPS}\n\n{FORMAT_V3}")
    head = (PROD_FRAME if frame == "prod" else "") + f"命名空间 {ns} 里出了问题。用户反馈：{sc['symptom']}\n"
    if first_obs is None:   # the lab application: no user-side measurement
        return head + "请排查根因。"
    return (head + f"监控看到的用户侧情况：{remediate.describe(first_obs)}\n\n"
            f"请排查根因。你只有只读权限；这次只需要定位原因，不需要提出或执行修复。\n\n{FORMAT}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-steps", type=int, default=25)
    ap.add_argument("--noise", default="", help="comma-separated ids from scenarios/noise.yaml to apply before the fault")
    ap.add_argument("--skills", default="", help="comma-separated skill directories (relative to the repo), e.g. "
                    "holmes/skills/sop,holmes/skills/cases; empty = HolmesGPT without any SOP")
    ap.add_argument("--frame", default="prod", choices=["prod", "none"], help="prod (default) = say this is a real production incident; none = the bare prompt")
    ap.add_argument("--metrics", action="store_true", help="also give the agent the boutique_pod_metrics tool (holmes/toolsets-metrics.yaml): "
                    "CPU usage, CPU throttling, memory and pod network counters from the kubelet")
    ap.add_argument("--allow-test", action="store_true", help="run a test scenario of the third bank (only once the method is fixed)")
    ap.add_argument("--prompt", default="auto", choices=["auto", "v2", "v3"],
                    help="auto (default) = v3 for the third bank (bank: 3), v2 otherwise; see docs/V3.md")
    ap.add_argument("--out", default=None, help="where to write HolmesGPT's JSON answer (default: results/holmes_pilot/<id>-<time>.json)")
    args = ap.parse_args()
    load_dotenv()
    ns = cfg("NAMESPACE", "tiktok-k8s-lab")
    model = args.model or cfg("LLM_MODEL", "deepseek-flash")
    sc = {s["id"]: s for s in load_scenarios(split="all", source="all")}[args.scenario]
    if int(sc.get("bank") or 0) >= 3 and sc.get("pool") == "test" and not args.allow_test:
        sys.exit(f"refusing {args.scenario}: the third bank's test scenarios are not run until the method is fixed (--allow-test)")
    admin = KubectlRunner(kubeconfig=path_cfg("ADMIN_KUBECONFIG"), context=cfg("ADMIN_CONTEXT"))
    lc = make_lifecycle(sc, admin, ns, cfg("LAB_K8S_DIR"))
    ns = lc.ns  # boutique scenarios live in their own namespace
    log = StepLog()
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    json_out = Path(args.out) if args.out else OUT / f"{args.scenario}-{stamp}.json"
    json_out.parent.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    env.update({
        "KUBECONFIG": path_cfg("DIAG_KUBECONFIG", ".local/diag.kubeconfig"),
        "OPENAI_API_KEY": cfg("LLM_API_KEY"),
        "OPENAI_API_BASE": cfg("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/v1",
        # LiteLLM still needs the egress proxy to reach the model API; kubectl does not (local cluster).
        "HTTPS_PROXY": os.environ.get("HTTPS_PROXY", ""),
        "NO_PROXY": os.environ.get("NO_PROXY", os.environ.get("no_proxy", "")) + ",127.0.0.1,localhost",
        # HolmesGPT runs tools under `ulimit -v`; the default crashes the Go kubectl binary at startup.
        # 1536 MB is the value HolmesGPT's own CI settled on (.github/workflows/eval-benchmarks.yaml).
        "TOOL_MEMORY_LIMIT_MB": "1536",
    })
    if args.skills:
        dirs = [str(REPO / d) for d in args.skills.split(",") if d]
        missing = [d for d in dirs if not Path(d).is_dir()]
        if missing:
            sys.exit(f"skill directories not found: {missing}")
        env["CUSTOM_SKILL_PATHS"] = ",".join(dirs)
    toolsets = REPO / "holmes" / "toolsets-metrics.yaml"
    if args.metrics:
        token_file = os.environ.get("METRICS_TOKEN_FILE") or str(REPO / ".local" / "metrics.token")
        if not Path(token_file).is_file():
            sys.exit(f"--metrics needs the metrics token ({token_file}); the laptop entry script mints it")
        env["METRICS_TOKEN_FILE"] = token_file
    else:
        env.pop("METRICS_TOKEN_FILE", None)   # without the flag the agent's tools cannot reach the kubelet
    noises = load_noises([i for i in args.noise.split(",") if i]) if args.noise else []
    lc.verify_healthy(log)
    try:
        lc.inject(sc, log, noises)
    except Exception:
        lc.reset(sc, log, noises)  # never leave noise or a half-applied fault behind
        raise
    started = time.monotonic()
    try:
        lc.confirm(sc, log)
        first_obs = remediate.observe(lc, lc.confirm_since, min_seconds=45, max_seconds=120) if sc.get("app") == "boutique" else None
        version = prompt_version(sc, args.prompt)
        if version == "v3" and first_obs is None:
            sys.exit("the v3 prompt is for Online Boutique scenarios")
        prompt = build_prompt(sc, ns, args.frame, first_obs, version)
        extra_env, extra_args = holmes_overrides(version)
        env.update(extra_env)
        cmd = [HOLMES, "ask", prompt, "--model", f"openai/{model}", "--config", str(REPO / "holmes" / "config.yaml"),
               "--max-steps", str(args.max_steps), "--no-interactive", "--no-echo", "--log-costs",
               "--json-output-file", str(json_out), *extra_args]
        if args.metrics:
            cmd += ["--custom-toolsets", str(toolsets)]
        # stdin must be closed: with an open stdin HolmesGPT can wait for input forever
        res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1500, stdin=subprocess.DEVNULL)
        print(res.stdout[-6000:])
        if res.returncode:
            print("stderr:", res.stderr[-3000:])
        if json_out.exists():   # keep what the agent was told next to what it answered
            data = json.loads(json_out.read_text(encoding="utf-8"))
            data.update({"prompt": prompt, "frame": args.frame, "before": first_obs, "metrics": args.metrics, "prompt_version": version})
            json_out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        print(f"diagnosis wall time {time.monotonic() - started:.0f}s")
        lc.reset(sc, log, noises)
    print("answer saved to", json_out)
    print("expected:", json.dumps(sc["answer"]["object"], ensure_ascii=False), sc["answer"]["cause_category"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
