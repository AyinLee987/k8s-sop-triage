"""CLI.

  python -m triage.runner check-env                 # admin sees a healthy env; diag identity is read-only
  python -m triage.runner smoke --scenario S01 --rounds 3   # week-1 checkpoint: healthy-inject-confirm-reset, no agent
  python -m triage.runner run --split dev --arms rule,text,code --reps 3
  python -m triage.runner run --split holdout --finalize    # held-out set: only after versions are frozen
  python -m triage.runner run --split dev --source all      # handwritten + generated (scenarios/templates.yaml)
  python -m triage.runner report results/<file>.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from triage.agents import ARMS
from triage.budget import Budget
from triage.grader import grade
from triage.kube import KubectlRunner
from triage.lifecycle import REPO, EnvironmentError_, Lifecycle, StepLog, load_scenarios
from triage.llm import LLMClient, Usage
from triage.schema import Diagnosis
from triage.tools import ReadOnlyTools, Trace


def load_dotenv(path: Path = REPO / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"'))


def cfg(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        sys.exit(f"missing config {name} (set it in .env)")
    return value


def path_cfg(name: str, default: str | None = None) -> str | None:
    """A path from config; relative paths are resolved against the repo, so commands work from any directory."""
    value = os.getenv(name, default)
    if not value:
        return None
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else (REPO / path))


def versions() -> dict:
    files = [*(f"sop/{p.name}" for p in sorted((REPO / "sop").glob("*.md"))), "scenarios/scenarios.yaml", "scenarios/templates.yaml", "triage/tools.py", "triage/agents/sop_steps.py",
             "triage/agents/rule_baseline.py", "triage/agents/text_sop.py", "triage/agents/code_sop.py"]
    return {f: hashlib.sha256((REPO / f).read_bytes()).hexdigest()[:12] for f in files}


def _diag_kubeconfig() -> str:
    path = path_cfg("DIAG_KUBECONFIG", ".local/diag.kubeconfig")
    if not Path(path).exists():
        sys.exit(f"诊断器凭据不存在：{path}。先运行 python scripts/make_diag_kubeconfig.py")
    return path


def make_budget() -> Budget:
    return Budget(
        total_seconds=float(cfg("BUDGET_TOTAL_SECONDS", "180")),
        per_call_timeout=float(cfg("BUDGET_PER_CALL_TIMEOUT", "30")),
        max_tool_calls=int(cfg("BUDGET_MAX_TOOL_CALLS", "25")),
        max_model_calls=int(cfg("BUDGET_MAX_MODEL_CALLS", "12")),
    )


def lifecycle() -> Lifecycle:
    admin = KubectlRunner(kubeconfig=path_cfg("ADMIN_KUBECONFIG"), context=os.getenv("ADMIN_CONTEXT") or None)
    return Lifecycle(admin, cfg("NAMESPACE", "tiktok-k8s-lab"), path_cfg("LAB_K8S_DIR", "") or "")


# ---------------- commands ----------------
def cmd_check_env(_args) -> int:
    lc = lifecycle()
    log = StepLog()
    ok = True
    try:
        lc.verify_healthy(log, wait_s=5)
    except EnvironmentError_ as exc:
        ok = False
        print("ENV NOT HEALTHY:", exc)
    diag = KubectlRunner(kubeconfig=_diag_kubeconfig())
    ns = cfg("NAMESPACE", "tiktok-k8s-lab")
    can_read = diag.run(["auth", "can-i", "list", "pods", "-n", ns], 15).stdout.strip()
    checks = {
        "diag can list pods": (can_read == "yes", can_read),
        "diag cannot read secrets": (diag.run(["auth", "can-i", "get", "secrets", "-n", ns], 15).stdout.strip() == "no", ""),
        "diag cannot patch deployments": (diag.run(["auth", "can-i", "patch", "deployments", "-n", ns], 15).stdout.strip() == "no", ""),
        "diag cannot list pods in rec-feature": (diag.run(["auth", "can-i", "list", "pods", "-n", "rec-feature"], 15).stdout.strip() == "no", ""),
    }
    for name, (passed, detail) in checks.items():
        ok &= passed
        print(f"[{'ok' if passed else 'FAIL'}] {name} {detail}")
    print(json.dumps(log.entries, ensure_ascii=False, indent=1))
    return 0 if ok else 1


def cmd_smoke(args) -> int:
    lc = lifecycle()
    scenarios = load_scenarios(split="all", ids=args.scenario.split(","))
    failures = 0
    for sc in scenarios:
        for r in range(args.rounds):
            log, started = StepLog(), time.monotonic()
            try:
                lc.verify_healthy(log)
                lc.precheck(sc, log)
                lc.inject(sc, log)
                lc.confirm(sc, log)
                ok = True
            except EnvironmentError_ as exc:
                ok, failures = False, failures + 1
                print(f"{sc['id']} round {r + 1}: ENV FAILURE {exc}")
            finally:
                try:
                    lc.reset(sc, log)
                except EnvironmentError_ as exc:
                    ok, failures = False, failures + 1
                    print(f"{sc['id']} round {r + 1}: RESET FAILURE {exc}")
            print(f"{sc['id']} round {r + 1}: {'ok' if ok else 'FAILED'} in {time.monotonic() - started:.0f}s")
            for e in log.entries:
                print("   ", e)
    return 1 if failures else 0


def cmd_run(args) -> int:
    if args.split in ("holdout", "all") and not args.finalize:
        sys.exit("Held-out scenarios are only run after SOP/prompts/code are frozen. Re-run with --finalize.")
    scenarios = load_scenarios(split=args.split, ids=args.scenario.split(",") if args.scenario else None,
                               source=args.source, max_per_template=args.max_per_template)
    arms = args.arms.split(",")
    out = Path(args.out or REPO / "results" / f"{time.strftime('%Y%m%d-%H%M%S')}-{args.split}.jsonl")
    print(f"writing {out}")
    return run_matrix(
        lc=lifecycle(),
        scenarios=scenarios,
        arms=arms,
        reps=args.reps,
        llm=LLMClient() if any(ARMS[a].uses_llm for a in arms) else None,
        diag_runner=KubectlRunner(kubeconfig=_diag_kubeconfig()),
        ns=cfg("NAMESPACE", "tiktok-k8s-lab"),
        out=out,
        price_in=float(cfg("PRICE_INPUT_PER_M", "0.30")),
        price_out=float(cfg("PRICE_OUTPUT_PER_M", "1.20")),
    )


def run_matrix(lc, scenarios, arms, reps, llm, diag_runner, ns, out: Path, price_in=0.30, price_out=1.20,
               budget_factory=None) -> int:
    """Serial loop over scenario x repetition x arm (arms interleaved inside each repetition).
    Returns 0 on completion, 2 if the environment is broken (unhealthy before injection, or reset failed)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    ver = versions()
    for sc in scenarios:
        for rep in range(1, reps + 1):
            for arm in arms:
                record = {"run_id": uuid.uuid4().hex[:10], "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                          "scenario": sc["id"], "split": sc["split"], "scenario_kind": sc.get("kind"),
                          "template": sc.get("template"), "mechanism": sc.get("mechanism") or sc["answer"]["cause_category"],
                          "arm": arm, "rep": rep, "versions": ver}
                log = StepLog()
                injected = False
                try:
                    try:
                        lc.verify_healthy(log)
                    except EnvironmentError_ as exc:
                        record.update(env_failure=str(exc), env_steps=log.entries)
                        _append(out, record)
                        print("Environment is unhealthy before injection; stopping:", exc)
                        return 2
                    lc.precheck(sc, log)
                    injected = True
                    since = lc.inject(sc, log)
                    lc.confirm(sc, log)
                    record.update(diagnose_once(arm, llm, diag_runner, ns, sc["symptom"], since, price_in, price_out,
                                                budget_factory=budget_factory))
                    record["grade"] = grade(Diagnosis(**record["diagnosis"]), sc["answer"])
                    record["env_failure"] = None
                except EnvironmentError_ as exc:
                    record["env_failure"] = str(exc)
                finally:
                    if injected:
                        try:
                            lc.reset(sc, log)
                        except EnvironmentError_ as exc:
                            record["reset_failure"] = str(exc)
                record["env_steps"] = log.entries
                _append(out, record)
                g = record.get("grade") or {}
                print(f"{sc['id']} rep{rep} {arm:5s} -> "
                      f"{'ENV FAIL' if record.get('env_failure') else ('correct' if g.get('correct') else 'wrong')}"
                      f"{' (review)' if g.get('needs_review') else ''}")
                if record.get("reset_failure"):
                    print("Reset failed; stopping so later runs are not contaminated:", record["reset_failure"])
                    return 2
    return 0


def _append(out: Path, record: dict) -> None:
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def diagnose_once(arm: str, llm, diag_runner, ns: str, symptom: str, since: str,
                  price_in: float, price_out: float, budget_factory=None) -> dict:
    budget, trace = (budget_factory or make_budget)(), Trace()
    tools = ReadOnlyTools(diag_runner, ns, budget, trace, events_since=since)
    before = asdict(llm.usage) if llm else asdict(Usage())
    agent = ARMS[arm](llm) if ARMS[arm].uses_llm else ARMS[arm]()
    result = agent.diagnose(symptom, ns, tools)
    after = asdict(llm.usage) if llm else asdict(Usage())
    usage = Usage(**{k: after[k] - before[k] for k in after})
    return {
        "agent": agent.describe(),
        "diagnosis": result.diagnosis.to_dict(),
        "stop_reason": result.stop_reason,
        "notes": result.notes,
        "budget": budget.snapshot(),
        "usage": asdict(usage),
        "cost_usd": usage.cost_usd(price_in, price_out),
        "trace": trace.events,
    }


def cmd_report(args) -> int:
    from triage.report import render
    print(render([json.loads(line) for line in Path(args.file).read_text(encoding="utf-8").splitlines() if line.strip()]))
    return 0


def main(argv=None) -> int:
    load_dotenv()
    p = argparse.ArgumentParser(prog="triage")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check-env")
    s = sub.add_parser("smoke")
    s.add_argument("--scenario", required=True)
    s.add_argument("--rounds", type=int, default=3)
    r = sub.add_parser("run")
    r.add_argument("--split", choices=["dev", "holdout", "all"], default="dev")
    r.add_argument("--scenario")
    r.add_argument("--arms", default="rule,text,code")
    r.add_argument("--reps", type=int, default=3)
    r.add_argument("--out")
    r.add_argument("--finalize", action="store_true")
    r.add_argument("--source", choices=["handwritten", "generated", "all"], default="handwritten")
    r.add_argument("--max-per-template", type=int, help="cap generated variants per template")
    rep = sub.add_parser("report")
    rep.add_argument("file")
    args = p.parse_args(argv)
    return {"check-env": cmd_check_env, "smoke": cmd_smoke, "run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
