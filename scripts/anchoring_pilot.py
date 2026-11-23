"""Small experiment on single-cause anchoring: the agent finds one real cause and explains every symptom with it.

  python scripts/anchoring_pilot.py --scenarios CV05,CL02 --arms base,m2,m6,m5 --repeats 1 --tag anch1 \
      --skills holmes/skills/svc-sop-v0 --metrics

Four ways of asking, one injection per (scenario, arm, repeat), everything else as in scripts/holmes_pilot.py:

  base  the prompt of holmes_pilot.py, unchanged.
  m2    coverage check: first list the findings without diagnosing, then diagnose and account for every finding.
        After the two-step prompt of Sonoda et al. (Jpn J Radiol 2024: summarise the case into categories first, then
        diagnose "based on the summarized information") and the covering idea of classic diagnosis. Same answer format.
  m6    answer format only: the minimal set of independent root causes, propagation edges, and for every symptom
        whether it is explained. After the agent_output.json of IBM's ITBench SRE agent prompt (entities with
        contributing_factor / reasoning / evidence, propagations, alerts_explained with an "explained" flag, and the
        rule not to mark both a cause and its derived symptom as a cause). No change to how it is told to investigate.
  m5    independent review with a clean context, after the roles of Ke et al. (JMIR 2024; arXiv 2401.14589): the agent
        gives a first diagnosis (= base); a colleague who only sees the incident and that report plays devil's advocate
        ("critically appraise the initial diagnosis, pinpoint inconsistencies, advocate alternatives"); a senior
        engineer names the biases and decides; if the senior does not accept, a fresh agent session reinvestigates with
        the first conclusion and the objections. The two reviewers have no tools and do not see the tool calls.

The papers print role descriptions, not the prompts themselves (Ke et al.) or only parts of them; the wording here is
ours. Only learning and validation scenarios are accepted: this tunes the method, test scenarios stay out of it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import holmes_loop  # noqa: E402  (holmes_env)
import holmes_pilot  # noqa: E402  (the base prompt)
from triage import remediate  # noqa: E402
from triage.kube import KubectlRunner  # noqa: E402
from triage.lifecycle import StepLog, load_scenarios, make_lifecycle  # noqa: E402
from triage.runner import cfg, load_dotenv, path_cfg  # noqa: E402

HOLMES = os.environ.get("HOLMES_BIN", "holmes")
ARMS = ("base", "m2", "m6", "m5")

M2_STEPS = """排查分两步做，顺序不能反：先整理发现，再下诊断。

第一步：只整理发现，不下结论。把观察到的异常整理成一张症状清单，至少包括：每一类用户请求（按接口分）现在的耗时或错误率、它健康时的水平、它调用了哪些下游各几次；以及各个 Pod 上看到的异常（限流、探针失败、重启、配置和上一版不一致等）。这一步不要写任何原因。

第二步：根据这张清单做诊断。下结论之前逐条核对：清单里的每一条症状由哪个原因解释、经过怎样的路径、证据是什么，这个原因能解释这条症状的多大一部分（例如多出来的毫秒数里它占多少），要写出估算。只要有一条症状没有被解释，或者只被解释了一部分，就把它标成“未解释”并继续排查，直到每一条都有证据支撑的解释，或者你确认在现有权限下已经查不下去。"""

M6_FORMAT = """回答的最后必须是一个 ```json 代码块，格式如下，三个字段都必须出现（没有内容时写空数组）：
```json
{"entities": [{"object": "<命名空间/类型/名字，写成 boutique/Deployment/某服务 这样的形式>", "independent_cause": true, "mechanism": "<network | cpu | config | app | unknown>", "reasoning": "<简短说明>", "evidence": "<依据的观察>"}],
 "propagations": [{"source": "<对象>", "target": "<对象>", "effect": "<source 的问题怎样表现在 target 上>"}],
 "symptoms_explained": [{"symptom": "<一条观察到的症状，带数字>", "explained": true, "by": ["<entities 里的 object>"], "explanation": "<它怎样、在多大程度上解释了这条症状>"}]}
```
要求：
- entities 里 independent_cause 为 true 的对象，合起来应当是能解释全部症状的最小的一组互相独立的根因。
- 只是被别的对象拖累、自身没有问题的对象，independent_cause 写 false，并在 propagations 里加一条从原因指向它的边。不要把一个原因和由它派生出来的症状都标成 independent_cause=true。
- symptoms_explained 要覆盖你观察到的每一条症状；现有原因解释不了、或者只能解释一部分的，explained 写 false。"""

DEVIL = """你是值班团队里的另一位工程师。你的同事刚对一次线上故障给出了初步诊断。你的任务是专门唱反调：严格审视这份初步诊断，指出其中前后不一致、证据不足的地方，并为其他可能的原因辩护。
你没有参与排查，手里只有下面的故障描述和同事的报告；不要假设报告之外的事实。

做法：
1. 把报告里出现的每一条观察逐条列出来（带数字的症状、各服务日志时间的对比、指标、事件）。
2. 对每一条判断：初步诊断给出的原因能不能解释它？能解释多大一部分（按数量级估算）？哪些观察被用来支持结论，哪些被提到却没有用上，哪些和结论相矛盾？
3. 提出至少一个替代的或额外的原因，说明它能解释哪些没有被解释的观察。
4. 列出用只读手段可以做的、能把这些解释区分开的具体检查。
不要因为初步诊断看起来有道理就附和。"""

SENIOR = """你是这个团队里经验最丰富的工程师，负责把关。下面有两位同事的意见：一份初步诊断，一份针对它的质疑。你的任务是指出初步诊断里可能存在的认知偏差（例如锚定在第一个找到的原因上、只找支持自己结论的证据、过早下结论），并引导团队得到更完整、更准确的诊断。

判断时看三点：初步诊断里的每个原因有没有证据支撑；这些原因合起来能不能解释全部症状（包括数量级）；质疑里提出的问题是否成立。
不要为了显得周全而凭空增加原因：如果初步诊断已经解释了全部症状，就认可它。

最后必须输出一个 ```json 代码块：
```json
{"verdict": "<accept 或 reinvestigate>", "biases": ["<发现的偏差，没有就留空数组>"], "unexplained": ["<还没有被解释的症状>"], "checks": ["<接下来要做的具体检查，最多 5 条>"]}
```"""


def head(sc: dict, ns: str, first_obs: dict) -> str:
    return (holmes_pilot.PROD_FRAME + f"命名空间 {ns} 里出了问题。用户反馈：{sc['symptom']}\n"
            f"监控看到的用户侧情况：{remediate.describe(first_obs)}\n\n")


TASK = "请排查根因。你只有只读权限；这次只需要定位原因，不需要提出或执行修复。\n\n"


def build_prompt(arm: str, sc: dict, ns: str, first_obs: dict) -> str:
    """The first (for base / m2 / m6: the only) prompt of an arm. base and m5 are exactly holmes_pilot's prompt."""
    if arm in ("base", "m5"):
        return holmes_pilot.build_prompt(sc, ns, "prod", first_obs)
    if arm == "m2":
        return head(sc, ns, first_obs) + TASK + M2_STEPS + "\n\n" + holmes_pilot.FORMAT
    if arm == "m6":
        return head(sc, ns, first_obs) + TASK + M6_FORMAT
    raise ValueError(arm)


def incident(sc: dict, first_obs: dict) -> str:
    return f"## 故障描述\n用户反馈：{sc['symptom']}\n监控看到的用户侧情况：{remediate.describe(first_obs)}"


def last_json(text: str) -> dict | None:
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text or "", flags=re.S)
    for b in blocks[::-1]:
        try:
            return json.loads(b)
        except json.JSONDecodeError:
            continue
    return None


def verdict_of(senior_reply: str) -> str:
    """accept only when the senior says so explicitly; an unreadable reply means the review did not clear the diagnosis."""
    v = str((last_json(senior_reply) or {}).get("verdict") or "").strip().lower()
    return "accept" if v == "accept" else "reinvestigate"


def second_prompt(sc: dict, ns: str, first_obs: dict, first_answer: str, devil: str, senior: str) -> str:
    conclusion = last_json(first_answer)
    shown = json.dumps(conclusion, ensure_ascii=False) if conclusion else (first_answer or "")[-1500:]
    return (head(sc, ns, first_obs) + "你之前对这次故障给出过一份初步诊断，团队复核后有不同意见。\n\n"
            f"## 你的初步诊断（结论部分）\n{shown}\n\n## 同事的质疑\n{devil.strip()}\n\n## 资深工程师的意见\n{senior.strip()}\n\n"
            "请带着这些意见重新排查：用工具核实上面列出的检查项，初步诊断可以保留、推翻或补充。然后给出你认为最可能的诊断；"
            "如果还有一个不能排除的备选，也写出来。你只有只读权限；这次只需要定位原因，不需要提出或执行修复。\n\n" + holmes_pilot.FORMAT)


def ask_agent(prompt: str, out: Path, env: dict, model: str, max_steps: int, metrics: bool) -> dict:
    cmd = [HOLMES, "ask", prompt, "--model", f"openai/{model}", "--config", str(REPO / "holmes" / "config.yaml"),
           "--max-steps", str(max_steps), "--no-interactive", "--no-echo", "--log-costs", "--json-output-file", str(out)]
    if metrics:
        cmd += ["--custom-toolsets", str(REPO / "holmes" / "toolsets-metrics.yaml")]
    started = time.monotonic()
    res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1500, stdin=subprocess.DEVNULL)
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    data.update({"prompt": prompt, "wall_s": round(time.monotonic() - started), "exit": res.returncode})
    if res.returncode and not data.get("result"):
        data["error"] = (res.stderr or res.stdout)[-1500:]
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def ask_reviewer(system: str, user: str, model: str) -> tuple[str, int]:
    """One plain model call: no tools, no history. Same model as the agent, so the arm changes the process, not the model."""
    from openai import OpenAI
    client = OpenAI(api_key=cfg("LLM_API_KEY"), base_url=cfg("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/v1")
    r = client.chat.completions.create(model=model, temperature=0, max_tokens=8000,
                                       messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    return (r.choices[0].message.content or "").strip(), int(getattr(r.usage, "total_tokens", 0) or 0)


def run_one(sc: dict, arm: str, out: Path, args, env: dict, model: str, admin) -> dict:
    lc = make_lifecycle(sc, admin, cfg("NAMESPACE", "tiktok-k8s-lab"), cfg("LAB_K8S_DIR"))
    log = StepLog()
    rec = {"scenario": sc["id"], "arm": arm, "answer_file": str(out.relative_to(REPO)), "stages": []}
    leftovers = remediate.recover(lc)
    if leftovers:
        rec["recovered"] = leftovers
    lc.verify_healthy(log)
    try:
        lc.inject(sc, log)
    except Exception:
        lc.reset(sc, log)
        raise
    started = time.monotonic()
    try:
        lc.confirm(sc, log)
        first_obs = remediate.observe(lc, lc.confirm_since, min_seconds=45, max_seconds=120)
        rec["before"] = first_obs
        first = ask_agent(build_prompt(arm, sc, lc.ns, first_obs), out.with_name(out.stem + "-a1.json") if arm == "m5" else out,
                          env, model, args.max_steps, args.metrics)
        rec["stages"].append({"stage": "agent", "tokens": first.get("total_tokens"), "llm_calls": first.get("num_llm_calls"), "wall_s": first.get("wall_s")})
        final = first
        if first.get("error"):
            rec["error"] = first["error"]
        elif arm == "m5":
            report = first.get("result") or ""
            devil, t1 = ask_reviewer(DEVIL, incident(sc, first_obs) + "\n\n## 同事的初步诊断报告\n" + report, model)
            senior, t2 = ask_reviewer(SENIOR, incident(sc, first_obs) + "\n\n## 初步诊断报告\n" + report + "\n\n## 同事的质疑\n" + devil, model)
            verdict = verdict_of(senior)
            rec["stages"].append({"stage": "review", "tokens": t1 + t2, "verdict": verdict})
            review = {"devil": devil, "senior": senior, "verdict": verdict}
            if verdict == "reinvestigate":
                final = ask_agent(second_prompt(sc, lc.ns, first_obs, report, devil, senior), out.with_name(out.stem + "-a2.json"),
                                  env, model, args.max_steps, args.metrics)
                rec["stages"].append({"stage": "agent2", "tokens": final.get("total_tokens"), "llm_calls": final.get("num_llm_calls"), "wall_s": final.get("wall_s")})
                if final.get("error"):
                    rec["error"] = final["error"]
            out.write_text(json.dumps({"result": final.get("result"), "first_result": report, "review": review,
                                       "total_tokens": sum(s.get("tokens") or 0 for s in rec["stages"]), "before": first_obs},
                                      ensure_ascii=False, indent=1), encoding="utf-8")
        rec["total_tokens"] = sum(s.get("tokens") or 0 for s in rec["stages"])
        rec["root_causes"] = (last_json(final.get("result") or "") or {}).get("root_causes" if arm != "m6" else "entities")
    finally:
        rec["wall_s"] = round(time.monotonic() - started)
        lc.reset(sc, log)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", required=True)
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--tag", default=time.strftime("anch-%Y%m%d-%H%M%S"))
    ap.add_argument("--skills", default="holmes/skills/svc-sop-v0")
    ap.add_argument("--max-steps", type=int, default=100)
    ap.add_argument("--metrics", action="store_true")
    args = ap.parse_args()
    load_dotenv()
    known = {s["id"]: s for s in load_scenarios(split="all", source="all")}
    ids = [i for i in args.scenarios.split(",") if i]
    arms = [a for a in args.arms.split(",") if a]
    bad = [a for a in arms if a not in ARMS]
    if bad:
        sys.exit(f"unknown arms {bad}; choose from {ARMS}")
    wrong = [i for i in ids if known[i].get("pool") not in ("learn", "val")]
    if wrong:
        sys.exit(f"refusing {wrong}: this experiment tunes the method, so only learning and validation scenarios are allowed")
    model = cfg("LLM_MODEL", "deepseek-flash")
    env = holmes_loop.holmes_env(args.skills)
    if args.metrics:
        token_file = os.environ.get("METRICS_TOKEN_FILE") or str(REPO / ".local" / "metrics.token")
        if not Path(token_file).is_file():
            sys.exit(f"--metrics needs the metrics token ({token_file}); the laptop entry script mints it")
        env["METRICS_TOKEN_FILE"] = token_file
    else:
        env.pop("METRICS_TOKEN_FILE", None)
    admin = KubectlRunner(kubeconfig=path_cfg("ADMIN_KUBECONFIG"), context=cfg("ADMIN_CONTEXT"))
    out_dir = REPO / "results" / "anchoring" / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    runs_file = out_dir / "runs.jsonl"
    runs = [json.loads(l) for l in runs_file.read_text(encoding="utf-8").splitlines()] if runs_file.exists() else []
    done = {(r["scenario"], r["arm"], r["repeat"]) for r in runs if not r.get("error")}
    plan = [(sid, arm, rep) for rep in range(1, args.repeats + 1) for sid in ids for arm in arms]   # arms alternate inside a scenario
    print(f"{len(plan)} runs planned, {len(done)} already done in {out_dir.relative_to(REPO)}", flush=True)
    for n, (sid, arm, rep) in enumerate(plan, 1):
        if (sid, arm, rep) in done:
            continue
        print(f"[{n}/{len(plan)}] {sid} {arm} repeat {rep} ...", flush=True)
        rec = run_one(known[sid], arm, out_dir / f"{sid}-{arm}-r{rep}.json", args, env, model, admin)
        rec.update({"repeat": rep, "skills": args.skills, "metrics": args.metrics})
        stages = " + ".join(f"{s['stage']} {s.get('tokens')}" + (f" ({s['verdict']})" if s.get("verdict") else "") for s in rec["stages"])
        print(f"    {'ERROR' if rec.get('error') else 'ok'}  {stages}; {rec['wall_s']} s", flush=True)
        runs = [r for r in runs if (r["scenario"], r["arm"], r["repeat"]) != (sid, arm, rep)] + [rec]
        runs_file.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in runs), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
