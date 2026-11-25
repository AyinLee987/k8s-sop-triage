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
sys.path.insert(0, str(REPO / "scripts"))

from triage.kube import KubectlRunner  # noqa: E402
from triage.lifecycle import EnvironmentError_, StepLog, load_scenarios, make_lifecycle  # noqa: E402
from triage.runner import cfg, load_dotenv, path_cfg  # noqa: E402

OUT = REPO / "results" / "validation"


def cadvisor(k: KubectlRunner, ns: str, by_container: bool = False) -> dict:
    """{pod: {metric: (value, ts)}} for every pod of `ns`, read through the API server's node proxy with the admin
    account (the same numbers the agent's metrics tool shows; read-only). by_container: keyed "pod/container"."""
    from pod_metrics import parse
    nodes = k.run(["get", "nodes", "-o", "jsonpath={.items[*].metadata.name}"], timeout=20)
    out: dict = {}
    for node in nodes.stdout.split() if nodes.ok else []:
        res = k.run(["get", "--raw", f"/api/v1/nodes/{node}/proxy/metrics/cadvisor"], timeout=40)
        if res.ok:
            out.update(parse(res.stdout, ns, by_container=by_container))
    return out


def throttling(first: dict, second: dict, wall: float) -> dict:
    """Per deployment (or "deployment/container" for by_container readings), between two readings: CPU used (millicores)
    and the share of CFS periods that were throttled."""
    rows = {}
    for pod, m in second.items():
        a = first.get(pod, {})
        d = lambda name: m.get(name, (0, 0))[0] - a.get(name, (0, 0))[0]  # noqa: E731
        secs = (m.get("container_cpu_usage_seconds_total", (0, 0))[1] - a.get("container_cpu_usage_seconds_total", (0, 0))[1]) / 1000 or wall
        periods, thr = d("container_cpu_cfs_periods_total"), d("container_cpu_cfs_throttled_periods_total")
        pod_name, _, container = pod.partition("/")
        dep = pod_name.rsplit("-", 2)[0] + (f"/{container}" if container else "")
        rows[dep] = {"cpu_m": round(d("container_cpu_usage_seconds_total") / secs * 1000, 1) if secs > 0 else None,
                     "throttled": f"{int(thr)}/{int(periods)}", "throttled_share": round(thr / periods, 3) if periods else 0.0}
    return dict(sorted(rows.items()))


def snapshot(k: KubectlRunner, ns: str, secret_value: str | None,
             selectors: tuple[str, ...] = ("app=api", "app=worker", "app=redis")) -> dict:
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
    snap["services"] = run(["get", "services", "-o", "wide"])
    for sel in selectors:
        snap[f"logs {sel}"] = run(["logs", "-l", sel, "--tail=25", "--all-containers", "--prefix", "--max-log-requests=10"])
        snap[f"previous logs {sel}"] = run(["logs", "-l", sel, "--tail=15", "--previous", "--prefix", "--max-log-requests=10"])
    return snap


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="+")
    ap.add_argument("--settle", type=float, default=15, help="seconds to wait after confirm before the snapshot")
    ap.add_argument("--observe", type=float, default=0,
                    help="Online Boutique: keep the fault this many more seconds, then record how slow the frontend's requests "
                         "were since the confirm step began (count, share >= 180 ms, percentiles). A scenario without a "
                         "latency fault gives the baseline to compare with.")
    args = ap.parse_args()
    load_dotenv()
    ns = cfg("NAMESPACE", "tiktok-k8s-lab")
    admin = KubectlRunner(kubeconfig=path_cfg("ADMIN_KUBECONFIG"), context=cfg("ADMIN_CONTEXT"))
    pw_file = Path(cfg("LAB_K8S_DIR")).parent / ".local" / "redis-password.txt"
    secret = pw_file.read_text().strip() if pw_file.exists() else None
    OUT.mkdir(parents=True, exist_ok=True)
    cluster = (cfg("ADMIN_CONTEXT") or "").removeprefix("kind-")
    scenarios = {s["id"]: s for s in load_scenarios(split="all", source="all")}
    status = 0
    # "BG*" = every scenario whose id starts with BG
    ids = [i for a in args.ids for i in ([k for k in scenarios if k.startswith(a[:-1])] if a.endswith("*") else [a])]
    for sid in ids:
        sc = scenarios[sid]
        lc = make_lifecycle(sc, admin, ns, cfg("LAB_K8S_DIR"))
        boutique = sc.get("app") == "boutique"
        log = StepLog()
        record = {"scenario": sid, "cluster": cluster, "validate_note": sc.get("realism", {}).get("validate_note"),
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
            if boutique and args.observe:
                first = cadvisor(admin, lc.ns) if sc.get("bank") else {}
                first_c = cadvisor(admin, lc.ns, by_container=True) if int(sc.get("bank") or 0) >= 4 else {}
                time.sleep(args.observe)
                if first:   # which containers were CPU-throttled while the fault was on (the third bank's harmless CPU changes must not be)
                    record["throttling"] = throttling(first, cadvisor(admin, lc.ns), args.observe)
                if first_c:   # fourth bank: the sidecar must be the throttled container of its pod, not the service's own
                    record["throttling_by_container"] = throttling(first_c, cadvisor(admin, lc.ns, by_container=True), args.observe)
                t = sorted(lc.request_times({"selector": "app=frontend", "field": "http.resp.took_ms", "match": {"message": "request complete"}},
                                            since=getattr(lc, "confirm_since", None)))
                pick = lambda q: round(t[min(len(t) - 1, int(len(t) * q))]) if t else None  # noqa: E731
                record["latency_since_confirm"] = {"requests": len(t), "slow_180ms": sum(x >= 180 for x in t),
                                                   "share_slow": round(sum(x >= 180 for x in t) / len(t), 3) if t else None,
                                                   "p50": pick(0.5), "p90": pick(0.9), "p99": pick(0.99)}
            record["snapshot"] = (snapshot(admin, lc.ns, None, ("app=frontend", "app=checkoutservice", "app=cartservice",
                                                               "app=productcatalogservice", "app=redis-cart", "app=paymentservice",
                                                               "app=emailservice", "app=currencyservice", "app=recommendationservice"))
                                  if boutique else snapshot(admin, ns, secret))
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
            if boutique and "snapshot" in record:
                import re
                rows = record["snapshot"]["pods"].splitlines()[1:]
                record["pods_not_ready_or_restarted"] = [r.split()[0] for r in rows if not re.search(r"\s(\d+)/\1\s+Running\s+0\s", r)]
            record["steps"] = log.entries
            # the laptop's original cluster keeps the plain name; another cluster (docs/PARALLEL.md) gets its own file
            path = OUT / (f"{sid}.json" if cluster in ("", "tiktok-k8s-lab", "sg-prod-01") else f"{sid}@{cluster}.json")
            path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
            seen = record.get("key_evidence_seen", {})
            print(f"{sid}: confirmed={record['confirmed']} evidence={seen} reset_ok={'reset_error' not in record}"
                  + (f" latency={record['latency_since_confirm']}" if "latency_since_confirm" in record else "") + f" -> {path}")
            if record.get("throttling"):
                print("    throttled: " + (", ".join(f"{d} {r['throttled']} ({r['cpu_m']}m)" for d, r in record["throttling"].items() if r["throttled_share"] > 0) or "none"))
            if record.get("throttling_by_container"):
                print("    by container: " + (", ".join(f"{d} {r['throttled']} ({r['cpu_m']}m)" for d, r in record["throttling_by_container"].items() if r["throttled_share"] > 0) or "none"))
        if status == 2:
            print("reset failed; stopping so the next scenario does not start from a broken environment")
            break
    return status


if __name__ == "__main__":
    sys.exit(main())
