"""Bad-case overfitting dose experiment.

Question: when postmortem "experience notes" from past bad cases are written straight into the SOP/skill,
do previously-solved scenarios start failing (regression), and do unseen ones get better or worse?

Dose = number of notes appended to the SOP (0, 5, 10, 20, 40 by default). Doses are nested: the notes at
dose 10 include the notes at dose 5. Notes are interleaved by kind so every dose has a similar mix.

  # pilot on the FAKE cluster with the real model — needs only an API key
  python -m triage.experiments.overfit run --backend fake --doses 0,10,40 --arms text,code --reps 2
  # later on the real cluster
  python -m triage.experiments.overfit run --backend real --doses 0,10,40 --arms text,code --reps 2
  python -m triage.experiments.overfit report results/overfit-<time>.jsonl
  python -m triage.experiments.overfit show --dose 10          # print exactly what gets appended

Regression set = dev scenarios (where the notes were learned). Generalisation set = holdout scenarios.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path

import yaml

from triage.lifecycle import REPO, load_scenarios

NOTES_FILE = REPO / "experiments" / "badcases.yaml"
KIND_ORDER = ["specific", "overgeneral", "anchor", "shortcut", "conflicting", "noise"]
EXTRA_HEADER = "## 历史排障经验（来自 bad case 复盘，排查时请参考）"

# Fake-cluster coverage (tests/fake_cluster.py): which scenarios the pilot can run without Kubernetes.
FAKE_DEV = ["S01", "S02", "S03", "S04", "S07", "S11", "S12", "G-dependency_address-03"]
FAKE_HOLDOUT = ["S05", "S06", "S08", "S09", "S10", "S13", "S14", "G-worker_command-02", "G-redis_password-02",
                "G-api_probe-02"]


def load_notes(path: Path = NOTES_FILE, kinds: list[str] | None = None) -> list[dict]:
    notes = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["notes"]
    return [n for n in notes if not kinds or n["kind"] in kinds]


def ordered_notes(notes: list[dict], seed: int = 7) -> list[dict]:
    """Shuffle within each kind (seeded), then interleave kinds round-robin, so any prefix has a similar mix."""
    rng = random.Random(seed)
    groups = defaultdict(list)
    for n in notes:
        groups[n["kind"]].append(n)
    for g in groups.values():
        rng.shuffle(g)
    kinds = [k for k in KIND_ORDER if k in groups] + sorted(set(groups) - set(KIND_ORDER))
    out = []
    while any(groups[k] for k in kinds):
        for k in kinds:
            if groups[k]:
                out.append(groups[k].pop(0))
    return out


def render_extra(notes: list[dict]) -> str | None:
    if not notes:
        return None
    lines = [EXTRA_HEADER, ""]
    lines += [f"{i}. 复盘：{n['text']}" for i, n in enumerate(notes, 1)]
    return "\n".join(lines)


def dose_extras(doses: list[int], seed: int = 7, kinds: list[str] | None = None) -> dict[int, tuple[str | None, list[str]]]:
    order = ordered_notes(load_notes(kinds=kinds), seed)
    if max(doses) > len(order):
        raise SystemExit(f"dose {max(doses)} exceeds the {len(order)} available notes")
    return {d: (render_extra(order[:d]), [n["id"] for n in order[:d]]) for d in doses}


# ---------------------------------------------------------------- running
def _scenarios(backend: str, sets: str) -> list[dict]:
    all_sc = {s["id"]: s for s in load_scenarios(split="all", source="all")}
    if backend == "fake":
        ids = (FAKE_DEV if sets in ("dev", "both") else []) + (FAKE_HOLDOUT if sets in ("holdout", "both") else [])
        return [all_sc[i] for i in ids]
    wanted = {"dev": ["dev"], "holdout": ["holdout"], "both": ["dev", "holdout"]}[sets]
    return [s for s in load_scenarios(split="all") if s["split"] in wanted]  # real backend: handwritten scenarios


def run(args) -> int:
    from triage.grader import grade
    from triage.llm import LLMClient
    from triage.runner import cfg, diagnose_once, load_dotenv, make_budget, versions
    from triage.schema import Diagnosis
    load_dotenv()
    doses = [int(x) for x in args.doses.split(",")]
    arms = args.arms.split(",")
    extras = dose_extras(doses, args.seed, args.kinds.split(",") if args.kinds else None)
    scenarios = _scenarios(args.backend, args.sets)
    if args.sets in ("holdout", "both") and args.backend == "real" and not args.finalize:
        sys.exit("Running holdout scenarios on the real cluster requires --finalize (versions frozen).")
    llm = args.llm_factory() if getattr(args, "llm_factory", None) else LLMClient()
    price_in, price_out = float(cfg("PRICE_INPUT_PER_M", "0.30")), float(cfg("PRICE_OUTPUT_PER_M", "1.20"))
    out = Path(args.out or REPO / "results" / f"overfit-{args.backend}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    ver = versions()
    total = len(doses) * len(arms) * args.reps * len(scenarios)
    print(f"{len(scenarios)} 个场景 × 剂量 {doses} × {arms} × {args.reps} 次 = {total} 次诊断；费用上限 ${args.max_cost}；结果写入 {out}")

    if args.backend == "real":
        from triage.kube import KubectlRunner
        from triage.runner import _diag_kubeconfig, lifecycle, run_matrix
        for dose in doses:
            extra, ids = extras[dose]
            code = run_matrix(lifecycle(), scenarios, arms, args.reps, llm, KubectlRunner(kubeconfig=_diag_kubeconfig()),
                              cfg("NAMESPACE", "tiktok-k8s-lab"), out, price_in, price_out, sop_extra=extra,
                              extra_record={"experiment": "overfit", "dose": dose, "note_ids": ids, "seed": args.seed})
            if code:
                return code
        print(render_dose_report([json.loads(x) for x in out.read_text(encoding="utf-8").splitlines() if x.strip()]))
        return 0

    sys.path.insert(0, str(REPO / "tests"))
    from fake_cluster import GENERATED_STATE, NS, SCENARIO_STATE, FakeCluster

    from triage.kube import FakeRunner
    states = {**SCENARIO_STATE, **GENERATED_STATE}
    spent, records = 0.0, []
    for rep in range(1, args.reps + 1):  # repetitions outermost so a cost stop still leaves every dose sampled
        for dose in doses:
            extra, ids = extras[dose]
            for sc in scenarios:
                for arm in arms:
                    if spent >= args.max_cost:
                        print(f"已花费 ${spent:.3f}，达到上限，停止。")
                        print(render_dose_report(records))
                        return 0
                    rec = {"run_id": uuid.uuid4().hex[:10], "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "experiment": "overfit", "backend": "fake", "dose": dose, "note_ids": ids, "seed": args.seed,
                           "scenario": sc["id"], "split": sc["split"], "scenario_kind": sc.get("kind"),
                           "mechanism": sc.get("mechanism") or sc["answer"]["cause_category"],
                           "arm": arm, "rep": rep, "versions": ver, "env_failure": None}
                    rec.update(diagnose_once(arm, llm if arm != "rule" else None, FakeRunner(FakeCluster(states[sc["id"]]).handle),
                                             NS, sc["symptom"], "2026-11-20T10:01:00Z", price_in, price_out,
                                             budget_factory=make_budget, sop_extra=extra))
                    rec["grade"] = grade(Diagnosis(**rec["diagnosis"]), sc["answer"])
                    spent += rec["cost_usd"]
                    records.append(rec)
                    with out.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    g = rec["grade"]
                    print(f"rep{rep} dose{dose:3d} {sc['id']:24s} {arm:5s} {'对' if g['correct'] else '错'}"
                          f"{'（停在症状层）' if g.get('stopped_at_symptom') else ''}  ${rec['cost_usd']:.4f}")
    print(render_dose_report(records))
    print(f"\n总花费约 ${spent:.3f}。假集群试点只用来判断现象能否复现，不能直接当作正式实验结果。")
    return 0


# ---------------------------------------------------------------- report
def _rate(runs: list[dict]) -> str:
    valid = [r for r in runs if not r.get("env_failure")]
    if not valid:
        return "—"
    ok = sum(1 for r in valid if (r.get("grade") or {}).get("correct"))
    return f"{ok / len(valid) * 100:.0f}%（{ok}/{len(valid)}）"


def render_dose_report(records: list[dict]) -> str:
    recs = [r for r in records if r.get("experiment") == "overfit"]
    if not recs:
        return "没有剂量实验记录。"
    doses = sorted({r["dose"] for r in recs})
    arms = [a for a in ("rule", "text", "code") if any(r["arm"] == a for r in recs)]
    lines = ["## 剂量—效果（回归集 = 开发集场景；泛化集 = 保留测试集场景）", "",
             "| 配置 | 剂量（条） | 回归集正确率 | 泛化集正确率 | 停在症状层 | 平均 token | 费用 USD |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for arm in arms:
        for d in doses:
            cell = [r for r in recs if r["arm"] == arm and r["dose"] == d]
            dev = [r for r in cell if r["split"] == "dev"]
            hold = [r for r in cell if r["split"] == "holdout"]
            stopped = sum(1 for r in cell if (r.get("grade") or {}).get("stopped_at_symptom"))
            tokens = [r["usage"]["prompt_tokens"] + r["usage"]["completion_tokens"] for r in cell if r.get("usage")]
            lines.append(f"| {arm} | {d} | {_rate(dev)} | {_rate(hold)} | {stopped} | "
                         f"{(sum(tokens) / len(tokens)) if tokens else 0:.0f} | {sum(r.get('cost_usd', 0) for r in cell):.3f} |")

    # regressions: scenario solved in every run at dose 0, but failed at a higher dose
    lines += ["", "## 回退明细：剂量 0 时每次都对、加了经验后出错的场景", ""]
    base = min(doses)
    found = False
    for arm in arms:
        by = defaultdict(list)
        for r in recs:
            if r["arm"] == arm and not r.get("env_failure"):
                by[(r["scenario"], r["dose"])].append(r)
        for sc in sorted({k[0] for k in by}):
            base_runs = by.get((sc, base), [])
            if not base_runs or not all(r["grade"]["correct"] for r in base_runs):
                continue
            for d in doses:
                if d == base:
                    continue
                bad = [r for r in by.get((sc, d), []) if not r["grade"]["correct"]]
                if bad:
                    found = True
                    r = bad[0]
                    dg = r["diagnosis"]
                    lines.append(f"- {arm} / {sc}（{r['split']}）/ 剂量 {d}：{len(bad)}/{len(by[(sc, d)])} 次出错；"
                                 f"例：category={dg['cause_category']}，object={(dg.get('object') or {}).get('name')}"
                                 f"{'，停在症状层' if r['grade'].get('stopped_at_symptom') else ''}")
    if not found:
        lines.append("- 没有发现回退。")
    lines += ["", "判断标准：同一配置下，回归集正确率随剂量增加而稳定下降，且在多次重复里都出现，才算复现了过拟合。"
                  "只差一两次运行的波动不算。"]
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="overfit")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--backend", choices=["fake", "real"], default="fake")
    r.add_argument("--doses", default="0,10,40")
    r.add_argument("--arms", default="text,code")
    r.add_argument("--reps", type=int, default=2)
    r.add_argument("--sets", choices=["dev", "holdout", "both"], default="both")
    r.add_argument("--kinds", help="only use notes of these kinds, e.g. specific or overgeneral,anchor")
    r.add_argument("--seed", type=int, default=7)
    r.add_argument("--max-cost", type=float, default=5.0)
    r.add_argument("--out")
    r.add_argument("--finalize", action="store_true")
    rp = sub.add_parser("report")
    rp.add_argument("file")
    s = sub.add_parser("show")
    s.add_argument("--dose", type=int, default=10)
    s.add_argument("--seed", type=int, default=7)
    args = p.parse_args(argv)
    if args.cmd == "run":
        return run(args)
    if args.cmd == "report":
        print(render_dose_report([json.loads(x) for x in Path(args.file).read_text(encoding="utf-8").splitlines() if x.strip()]))
        return 0
    extra, ids = dose_extras([args.dose], args.seed)[args.dose]
    print(extra or "（剂量 0：不追加任何经验）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
