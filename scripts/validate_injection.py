"""Check that a scenario's injection produces the evidence its answer assumes — without running any agent.

For each scenario: verify healthy -> precheck -> inject -> confirm -> wait -> snapshot (admin view: pods, events,
recent logs of every component, the workload specs that matter) -> reset -> verify recovered.
The snapshot is what a diagnoser *could* see; compare it with the scenario's key_evidence and validate_note.

Safe for holdout scenarios: no diagnoser runs, so no holdout result is looked at.
Secrets are never read; log lines are scanned for the Redis password and redacted just in case.

  python scripts/validate_injection.py R01 R03 --settle 20
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from triage.kube import KubectlRunner  # noqa: E402
from triage.lifecycle import EnvironmentError_, Lifecycle, StepLog, load_scenarios  # noqa: E402
from triage.runner import cfg, load_dotenv, path_cfg  # noqa: E402

OUT = REPO / "results" / "validation"


def snapshot(k: KubectlRunner, ns: str, secret_value: str | None) -> dict:
    def run(args, timeout=30):
        res = k.run(["-n", ns, *args], timeout=timeout)
        text = res.stdout if res.ok else f"[error] {res.stderr.strip()[:300]}"
        if secret_value and secret_value in text:
            text = text.replace(secret_value, "<redacted>")
        return text

    snap = {
        "pods": run(["get", "pods", "-o", "wide"]),
        "events": run(["get", "events", "--sort-by=.lastTimestamp"]),
        "workloads": run(["get", "deploy,sts", "-o", "wide"]),
        "endpoints": run(["get", "endpointslices"]),
    }
    for sel in ("app=api", "app=worker", "app=redis"):
        snap[f"logs {sel}"] = run(["logs", "-l", sel, "--tail=25", "--all-containers", "--prefix", "--max-log-requests=10"])
        snap[f"previous logs {sel}"] = run(["logs", "-l", sel, "--tail=15", "--previous", "--prefix", "--max-log-requests=10"])
    return snap


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="+")
    ap.add_argument("--settle", type=float, default=15, help="seconds to wait after confirm before the snapshot")
    args = ap.parse_args()
    load_dotenv()
    ns = cfg("NAMESPACE", "tiktok-k8s-lab")
    admin = KubectlRunner(kubeconfig=path_cfg("ADMIN_KUBECONFIG"), context=cfg("ADMIN_CONTEXT"))
    lc = Lifecycle(admin, ns, cfg("LAB_K8S_DIR"))
    pw_file = Path(cfg("LAB_K8S_DIR")).parent / ".local" / "redis-password.txt"
    secret = pw_file.read_text().strip() if pw_file.exists() else None
    OUT.mkdir(parents=True, exist_ok=True)
    scenarios = {s["id"]: s for s in load_scenarios(split="all", source="all")}
    status = 0
    for sid in args.ids:
        sc = scenarios[sid]
        log = StepLog()
        record = {"scenario": sid, "validate_note": sc.get("realism", {}).get("validate_note"),
                  "key_evidence": sc["answer"].get("key_evidence"), "confirmed": False}
        injected = False
        try:
            lc.verify_healthy(log)
            lc.precheck(sc, log)
            injected = True
            lc.inject(sc, log)
            try:
                lc.confirm(sc, log)
                record["confirmed"] = True
            except EnvironmentError_ as exc:
                record["confirm_error"] = str(exc)
            time.sleep(args.settle)
            record["snapshot"] = snapshot(admin, ns, secret)
            blob = json.dumps(record["snapshot"], ensure_ascii=False)
            record["key_evidence_seen"] = {e: (e in blob) for e in record["key_evidence"] or []}
        except EnvironmentError_ as exc:
            record["error"] = str(exc)
            status = 1
        finally:
            if injected:
                try:
                    lc.reset(sc, log)
                except EnvironmentError_ as exc:
                    record["reset_error"] = str(exc)
                    status = 2
            record["steps"] = log.entries
            path = OUT / f"{sid}.json"
            path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
            seen = record.get("key_evidence_seen", {})
            print(f"{sid}: confirmed={record['confirmed']} evidence={seen} reset_ok={'reset_error' not in record} -> {path}")
        if status == 2:
            print("reset failed; stopping so the next scenario does not start from a broken environment")
            break
    return status


if __name__ == "__main__":
    sys.exit(main())
