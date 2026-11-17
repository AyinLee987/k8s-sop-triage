"""Fix-and-verify loop: inject one Online Boutique scenario, then let HolmesGPT diagnose, request a fix, see whether
users are better off, and try again.

  python scripts/holmes_loop.py BH01 [BH12 ...] [--rounds 3] [--max-steps 100] [--tag loop-pilot] [--skills dir,dir]

Each round: HolmesGPT investigates read-only and ends its answer with a JSON block (root causes + requested actions).
The harness runs the accepted actions with the admin credentials (triage/remediate.py: a fixed set, no shell), waits,
and measures what users see (latency, HTTP 500s, checkout failures). The numbers go into the next round's prompt.
The loop ends when users are fine, when the agent requests nothing more, or after --rounds.
Afterwards everything the agent changed is put back and the scenario is reset.
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
from triage.lifecycle import StepLog, load_scenarios, make_lifecycle  # noqa: E402
from triage.runner import cfg, load_dotenv, path_cfg  # noqa: E402

HOLMES = os.environ.get("HOLMES_BIN", "holmes")

FORMAT = """回答的最后必须是一个 ```json 代码块，格式如下（root_causes 写你认为的全部根因；不需要操作或现有操作修不了时 actions 留空数组，并在 explanation 里说明原因、该由谁处理）：
```json
{"root_causes": [{"object": "<服务或对象>", "cause": "<原因>"}], "actions": [], "expect": "<如果你的判断是对的，执行这些操作后用户侧的数字会怎么变，写具体数字；没有操作就留空>", "explanation": "<一两句话>"}
```"""


# --frame prod: the trajectories of the first runs show the agent working out that it is in a lab and reasoning about
# "what a test would inject" (boutique/loop_trajectory_notes.md). This says otherwise and asks for evidence only.
PROD_FRAME = ("这是线上生产环境里正在发生的真实故障，不是演练、测试或练习题，没有“出题人”，也没有预设的标准答案。"
              "只根据你在这个集群里实际观察到的证据下结论；不要推测“可能被注入了什么故障”，也不要因为某种原因更常见就选它。"
              "如果两个解释都说得通而你无法区分，就把两个都写出来，并说明还缺什么证据。\n\n")


def build_prompt(ns: str, symptom: str, rounds: int, history: list[dict], first_obs: dict, frame: str = "") -> str:
    n = len(history) + 1
    head = ((PROD_FRAME if frame == "prod" else "") + f"命名空间 {ns} 里出了问题。用户反馈：{symptom}\n"
            f"监控看到的用户侧情况：{remediate.describe(first_obs)}\n\n"
            f"请排查根因并提出修复。你只有只读权限；修复由运维按你的申请执行，执行后会把用户侧的最新情况告诉你，"
            f"没恢复的话可以继续排查；一轮操作如果没有让用户侧好转，会被全部撤销（重建过的 Pod 除外）。最多 {rounds} 轮，现在是第 {n} 轮。\n\n{remediate.ACTIONS_HELP}\n\n{FORMAT}")
    if not history:
        return head
    past = []
    for h in history:
        plan = h.get("plan") or {}
        acts = "\n".join(f"    {json.dumps(a['action'], ensure_ascii=False)} -> {a['result']}" for a in h["actions"]) or "    （没有执行任何操作）"
        kept = {True: "用户侧有好转，这一轮的改动已保留。", False: "用户侧没有好转，这一轮的改动已全部撤销（重建过的 Pod 除外）。", None: ""}[h.get("kept")]
        past.append(f"第 {h['round']} 轮你的结论：{json.dumps({'root_causes': plan.get('root_causes'), 'explanation': plan.get('explanation')}, ensure_ascii=False)}\n"
                    f"  执行的操作及结果：\n{acts}\n  你当时的预期：{plan.get('expect') or '（没有写）'}\n"
                    f"  执行后用户侧情况：{remediate.describe(h['observation'])}\n  {kept}")
    return (head + "\n\n之前的经过：\n" + "\n".join(past) +
            "\n\n问题还没有完全恢复。请继续排查：拿实际结果对照你当时的预期，之前的判断哪些被证实了、哪些被推翻了、还有哪些现象没有解释。")


def holmes_env(skills: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    env.update({
        "KUBECONFIG": path_cfg("DIAG_KUBECONFIG", ".local/diag.kubeconfig"),
        "OPENAI_API_KEY": cfg("LLM_API_KEY"),
        "OPENAI_API_BASE": cfg("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/v1",
        "HTTPS_PROXY": os.environ.get("HTTPS_PROXY", ""),
        "NO_PROXY": os.environ.get("NO_PROXY", os.environ.get("no_proxy", "")) + ",127.0.0.1,localhost",
        "TOOL_MEMORY_LIMIT_MB": "1536",   # see holmes_pilot.py
    })
    if skills:
        dirs = [str(REPO / d) for d in skills.split(",") if d]
        missing = [d for d in dirs if not Path(d).is_dir()]
        if missing:
            sys.exit(f"skill directories not found: {missing}")
        env["CUSTOM_SKILL_PATHS"] = ",".join(dirs)
    return env


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", nargs="+")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--max-steps", type=int, default=100)
    ap.add_argument("--model", default=None)
    ap.add_argument("--skills", default="")
    ap.add_argument("--tag", default="loop")
    ap.add_argument("--frame", default="prod", choices=["prod", "none"],
                    help="prod (default since 2026-11-17) = tell the agent this is a real production incident and to judge from evidence only; "
                         "none = the bare prompt of the first loop-pilot runs")
    args = ap.parse_args()
    load_dotenv()
    known = {s["id"]: s for s in load_scenarios(split="all", source="all")}
    for sid in [i for a in args.scenarios for i in a.split(",") if i]:
        status = run(known[sid], args)
        if status:
            print("stopping: the namespace was not left clean")
            return status
    return 0


def run(sc: dict, args) -> int:
    model = args.model or cfg("LLM_MODEL", "deepseek-flash")
    if sc.get("app") != "boutique":
        sys.exit("the fix-and-verify loop only exists for Online Boutique scenarios")
    if sc.get("split") == "holdout":
        sys.exit("refusing a holdout scenario")
    admin = KubectlRunner(kubeconfig=path_cfg("ADMIN_KUBECONFIG"), context=cfg("ADMIN_CONTEXT"))
    lc = make_lifecycle(sc, admin, cfg("NAMESPACE", "tiktok-k8s-lab"), cfg("LAB_K8S_DIR"))
    out_dir = REPO / "results" / "holmes_loop" / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    env = holmes_env(args.skills)
    log = StepLog()
    record = {"scenario": sc["id"], "model": model, "max_rounds": args.rounds, "frame": args.frame, "rounds": [], "outcome": "error"}
    lc.verify_healthy(log)
    try:
        lc.inject(sc, log)
    except Exception:
        lc.reset(sc, log)
        raise
    try:
        lc.confirm(sc, log)
        first_obs = remediate.observe(lc, lc.confirm_since, min_seconds=45, max_seconds=120)
        record["before"] = first_obs
        print(f"before: {remediate.describe(first_obs)}", flush=True)
        history: list[dict] = []
        for n in range(1, args.rounds + 1):
            prompt = build_prompt(lc.ns, sc["symptom"], args.rounds, history, first_obs, args.frame)
            json_out = out_dir / f"{sc['id']}-round{n}.json"
            started = time.monotonic()
            cmd = [HOLMES, "ask", prompt, "--model", f"openai/{model}", "--config", str(REPO / "holmes" / "config.yaml"),
                   "--max-steps", str(args.max_steps), "--no-interactive", "--no-echo", "--log-costs", "--json-output-file", str(json_out)]
            res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1500, stdin=subprocess.DEVNULL)
            data = json.loads(json_out.read_text(encoding="utf-8")) if json_out.exists() else {}
            answer = data.get("result") or ""
            plan, plan_error = remediate.parse_plan(answer)
            rnd = {"round": n, "wall_s": round(time.monotonic() - started), "exit": res.returncode,
                   "total_tokens": data.get("total_tokens"), "tool_calls": len(data.get("tool_calls") or []),
                   "answer": answer, "plan": plan, "plan_error": plan_error, "actions": [], "observation": None}
            if res.returncode and not answer:
                rnd["error"] = (res.stderr or res.stdout)[-1500:]
                record["rounds"].append(rnd)
                record["outcome"] = "agent_error"
                break
            requested = (plan or {}).get("actions") or []
            print(f"round {n}: causes={json.dumps((plan or {}).get('root_causes'), ensure_ascii=False)} actions={len(requested)} {plan_error}", flush=True)
            if not requested:
                record["rounds"].append(rnd)
                record["outcome"] = "no_plan" if plan is None else "agent_stopped"   # nothing (more) it wants changed
                break
            before_actions = remediate.snapshot(lc)
            rnd["actions"] = remediate.apply_actions(lc, requested)
            for a in rnd["actions"]:
                print(f"   {json.dumps(a['action'], ensure_ascii=False)} -> {a['result']}", flush=True)
            time.sleep(20)   # let restarts settle before measuring
            rnd["observation"] = remediate.observe(lc, now_iso())
            print(f"   after: {remediate.describe(rnd['observation'])}", flush=True)
            # a round that did not help is rolled back, so the next one does not start from a pile of failed fixes
            rnd["kept"] = remediate.improved(history[-1]["observation"] if history else first_obs, rnd["observation"])
            if not rnd["kept"]:
                rnd["undone"] = remediate.restore_to(lc, before_actions)
                print(f"   no improvement: undone {rnd['undone']}", flush=True)
                time.sleep(20)
            record["rounds"].append(rnd)
            history.append(rnd)
            if rnd["observation"]["resolved"]:
                record["outcome"] = "resolved"
                break
        else:
            record["outcome"] = "rounds_exhausted"
    finally:
        status = 0
        # the injector's own log shows whether the network fault followed a pod that the agent had restarted
        inj = lc._run(["-n", "infra-tools", "logs", "net-injector", "--tail=40"], 20)
        record["injector_log"] = inj.stdout[-3000:] if inj.ok else None
        try:
            record["restored"] = remediate.restore_baseline(lc)
            lc.reset(sc, log)
        except Exception as exc:   # still write what happened; the next run's health check will refuse a dirty namespace
            record["reset_error"] = str(exc)
            status = 2
        record["steps"] = log.entries
        record["total_tokens"] = sum(r.get("total_tokens") or 0 for r in record["rounds"])
        path = out_dir / f"{sc['id']}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{sc['id']}: outcome={record['outcome']} rounds={len(record['rounds'])} tokens={record['total_tokens']} "
              f"reset_ok={status == 0} -> {path}", flush=True)
    return status


if __name__ == "__main__":
    sys.exit(main())
