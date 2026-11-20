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


def build_prompt(sc: dict, ns: str, frame: str, first_obs: dict | None) -> str:
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
    ap.add_argument("--out", default=None, help="where to write HolmesGPT's JSON answer (default: results/holmes_pilot/<id>-<time>.json)")
    args = ap.parse_args()
    load_dotenv()
    ns = cfg("NAMESPACE", "tiktok-k8s-lab")
    model = args.model or cfg("LLM_MODEL", "deepseek-flash")
    sc = {s["id"]: s for s in load_scenarios(split="all", source="all")}[args.scenario]
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
        prompt = build_prompt(sc, ns, args.frame, first_obs)
        cmd = [HOLMES, "ask", prompt, "--model", f"openai/{model}", "--config", str(REPO / "holmes" / "config.yaml"),
               "--max-steps", str(args.max_steps), "--no-interactive", "--no-echo", "--log-costs",
               "--json-output-file", str(json_out)]
        # stdin must be closed: with an open stdin HolmesGPT can wait for input forever
        res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1500, stdin=subprocess.DEVNULL)
        print(res.stdout[-6000:])
        if res.returncode:
            print("stderr:", res.stderr[-3000:])
        if json_out.exists():   # keep what the agent was told next to what it answered
            data = json.loads(json_out.read_text(encoding="utf-8"))
            data.update({"prompt": prompt, "frame": args.frame, "before": first_obs})
            json_out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        print(f"diagnosis wall time {time.monotonic() - started:.0f}s")
        lc.reset(sc, log, noises)
    print("answer saved to", json_out)
    print("expected:", json.dumps(sc["answer"]["object"], ensure_ascii=False), sc["answer"]["cause_category"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
