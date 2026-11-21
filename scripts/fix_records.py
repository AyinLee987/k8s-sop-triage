"""What an on-call engineer's fix looks like from the outside: inject one Online Boutique learning scenario, apply the
correct fix one operation at a time, and record what users see after each operation. No model is called.

  python scripts/fix_records.py BH01 [BH02 ...] [--tag fix-v1]

This is the material for arm D of the cross-incident comparison (boutique/reflect.py --mode D). In production the
diagnosing agent is read-only; an engineer does the fix, possibly a different one from what the agent proposed, and what
is left behind is the list of operations and the monitoring before and after. So the record holds exactly that:
  - each operation as it was run (the object and the new value; not the old value, not a reason),
  - the user-side numbers after each operation (same measurement as the fix-and-verify loop).
The operations are the scenario's own reset steps. Changes to cluster objects come first, in the listed order, and the
node-level network clean-up last: an engineer fixes what `kubectl` shows before digging into a node. A step that changes
nothing for users is measured over a second, later window before that is recorded (callers reconnect with back-off).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from triage import remediate  # noqa: E402
from triage.kube import KubectlRunner  # noqa: E402
from triage.lifecycle import BOUTIQUE_DEPLOYMENTS, StepLog, load_scenarios, make_lifecycle  # noqa: E402
from triage.runner import cfg, load_dotenv, path_cfg  # noqa: E402


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def netem_targets(sc: dict) -> list[str]:
    """The services whose pod interfaces this scenario's injector touches (from the injector manifest it applies)."""
    for step in sc.get("inject") or []:
        args = step.get("kubectl") or []
        if "apply" in args:
            text = Path(str(args[-1]).replace("{repo}", str(REPO))).read_text(encoding="utf-8")
            line = next((ln for ln in text.splitlines() if ln.strip().startswith("for spec in ")), "")
            return list(dict.fromkeys(re.findall(r'"([a-z0-9-]+)\|', line)))
    return []


def describe_step(args: list[str], sc: dict) -> str:
    """One reset step as an entry in the engineer's operation log: what was changed to what. No old value, no reason."""
    a = [str(x) for x in args]
    if "net-injector" in a:
        targets = "、".join(netem_targets(sc)) or "相关服务"
        return f"在节点上清除了 {targets} 的 Pod 对应 veth 网卡上的 tc 规则（恢复为默认队列）"
    if "set" in a and "env" in a:
        dep = a[a.index("env") + 1].split("/")[-1]
        kv = a[a.index("env") + 2]
        if kv.endswith("-"):
            return f"删除了 Deployment {dep} 的环境变量 {kv[:-1]}（Pod 随之滚动更新）"
        name, value = kv.split("=", 1)
        return f"把 Deployment {dep} 的环境变量 {name} 设为 {value}（Pod 随之滚动更新）"
    if "set" in a and "resources" in a:
        dep = a[a.index("resources") + 1].split("/")[-1]
        value = {x.split("=")[0].lstrip("-"): x.split("=")[-1] for x in a if x.startswith("--")}
        parts = [f"CPU 上限设为 {value['limits']}"] + ([f"CPU 请求设为 {value['requests']}"] if "requests" in value else [])
        return f"把 Deployment {dep} 的 {'、'.join(parts)}（Pod 随之滚动更新）"
    if "scale" in a:
        dep = a[a.index("scale") + 1].split("/")[-1]
        return f"把 Deployment {dep} 的副本数设为 {a[-1].split('=')[1]}"
    if "patch" in a and "service" in a:
        svc = a[a.index("service") + 1]
        patch = json.loads(a[-1])
        if isinstance(patch, dict):
            sel = "、".join(f"{k}={v}" for k, v in patch["spec"]["selector"].items())
            return f"把 Service {svc} 的 selector 设为 {sel}"
        return f"把 Service {svc} 第一个端口的 targetPort 设为 {patch[0]['value']}"
    raise ValueError(f"no description for reset step: {' '.join(a)}")


def fix_steps(sc: dict) -> list[dict]:
    """The scenario's reset steps in the order an engineer would take them: cluster objects first, the node last."""
    steps = [s for s in sc.get("reset") or [] if "kubectl" in s]
    objects = [s for s in steps if "net-injector" not in [str(x) for x in s["kubectl"]]]
    node = [s for s in steps if s not in objects]
    return objects + node


def run(sc: dict, tag: str) -> int:
    if sc.get("app") != "boutique" or sc.get("pool") != "learn":
        sys.exit(f"{sc['id']}: fix records are only made for Online Boutique learning scenarios")
    admin = KubectlRunner(kubeconfig=path_cfg("ADMIN_KUBECONFIG"), context=cfg("ADMIN_CONTEXT"))
    lc = make_lifecycle(sc, admin, cfg("NAMESPACE", "tiktok-k8s-lab"), cfg("LAB_K8S_DIR"))
    out_dir = REPO / "results" / "fix_records" / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{sc['id']}.json"
    path.unlink(missing_ok=True)
    steps = fix_steps(sc)
    described = [describe_step([lc._render(str(x)) for x in s["kubectl"]], sc) for s in steps]   # fails before anything is injected
    log = StepLog()
    record = {"scenario": sc["id"], "symptom": sc["symptom"], "steps": [], "resolved": False, "outcome": "error"}
    leftovers = remediate.recover(lc)
    if leftovers:
        print(f"cleaned up after an interrupted run: {leftovers}", flush=True)
        record["recovered"] = leftovers
    lc.verify_healthy(log)
    try:
        lc.inject(sc, log)
    except Exception:
        lc.reset(sc, log)
        raise
    status = 0
    try:
        lc.confirm(sc, log)
        prev = record["before"] = remediate.observe(lc, lc.confirm_since, min_seconds=45, max_seconds=120)
        print(f"{sc['id']} before: {remediate.describe(prev)}", flush=True)
        for n, (step, text) in enumerate(zip(steps, described), 1):
            lc._kubectl_step(step, log, "fix")
            for name in BOUTIQUE_DEPLOYMENTS:   # a changed environment variable or replica count is a rollout
                lc._run(["-n", lc.ns, "rollout", "status", f"deployment/{name}", "--timeout=140s"], 150)
            time.sleep(20)
            entry = {"n": n, "operation": text, "observation": remediate.observe(lc, now_iso())}
            entry["improved"] = remediate.improved(prev, entry["observation"])
            if not entry["improved"] and not prev.get("resolved"):
                entry["observation_first"] = entry["observation"]
                entry["observation"] = remediate.observe(lc, now_iso())
                entry["improved"] = remediate.improved(prev, entry["observation"])
            print(f"  {n}. {text}\n     after: {remediate.describe(entry['observation'])}", flush=True)
            record["steps"].append(entry)
            prev = entry["observation"]
        record["resolved"] = bool(prev.get("resolved"))
        record["outcome"] = "resolved" if record["resolved"] else "not_resolved"
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        raise
    finally:
        try:
            record["restored"] = remediate.restore_baseline(lc)
            lc.reset(sc, log)
        except Exception as exc:   # still write what happened; the next run's health check will refuse a dirty namespace
            record["reset_error"] = str(exc)
            status = 2
        record["log"] = log.entries
        path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{sc['id']}: outcome={record['outcome']} steps={len(record['steps'])} reset_ok={status == 0} -> {path}", flush=True)
    return status


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", nargs="+")
    ap.add_argument("--tag", default="fix-v1")
    args = ap.parse_args()
    load_dotenv()
    known = {s["id"]: s for s in load_scenarios(split="all", source="all")}
    for sid in [i for a in args.scenarios for i in a.split(",") if i]:
        try:
            status = run(known[sid], args.tag)
        except Exception as exc:   # a fault that did not take effect ends this scenario, not the batch
            print(f"{sid}: {type(exc).__name__}: {str(exc)[:300]}", flush=True)
            status = 0
            lc_check = make_lifecycle(known[sid], KubectlRunner(kubeconfig=path_cfg("ADMIN_KUBECONFIG"), context=cfg("ADMIN_CONTEXT")),
                                      cfg("NAMESPACE", "tiktok-k8s-lab"), cfg("LAB_K8S_DIR"))
            if lc_check.health_problems():
                status = 2
        if status:
            print("stopping: the namespace was not left clean")
            return status
    return 0


if __name__ == "__main__":
    sys.exit(main())
