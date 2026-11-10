"""The SOPs (sop/*.md) written as code. Used by the rule baseline and the code-SOP arm,
so both collect exactly the same evidence; the text-SOP arm gets the same SOP as prose."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

SOP_DIR = Path(__file__).resolve().parents[2] / "sop"
IMAGE_REASONS = {"ErrImagePull", "ImagePullBackOff", "ErrImageNeverPull", "InvalidImageName"}
CRASH_REASONS = {"CrashLoopBackOff", "RunContainerError", "StartError", "CreateContainerError"}
MOUNT_REASONS = {"ContainerCreating"}
ERROR_PATTERN = re.compile(
    r"error|exception|traceback|retry|failed|refused|timed? ?out|wrongpass|authentication|denied|not known|no module",
    re.IGNORECASE,
)


def sop_files() -> list[Path]:
    return sorted(SOP_DIR.glob("*.md"))


def sop_text(extra: str | None = None) -> str:
    """All SOP files concatenated in file-name order, plus optional appended experience notes
    (the bad-case dose experiment injects them here, i.e. "written into the skill")."""
    parts = [f"<!-- {p.name} -->\n{p.read_text(encoding='utf-8')}" for p in sop_files()]
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


@dataclass
class Abnormal:
    kind: str
    name: str
    branch: str  # "image" | "scaled_zero" | "mount" | "crash" | "not_ready"
    pods: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def representative(self) -> dict:
        if not self.pods:
            return {}

        def score(p):
            waiting = any(c.get("waiting_reason") for c in p.get("containers", []))
            return (waiting, p.get("restarts", 0), not p.get("ready"))
        return max(self.pods, key=score)


def owner_workload(pod: dict) -> tuple[str, str] | None:
    owner = pod.get("owner") or ""
    kind, _, name = owner.partition("/")
    if kind == "ReplicaSet":
        return "Deployment", name.rsplit("-", 1)[0]
    if kind == "StatefulSet":
        return "StatefulSet", name
    return None


# Step 2 ---------------------------------------------------------------
def find_abnormal(overview: dict) -> list[Abnormal]:
    by_workload: dict[tuple[str, str], list[dict]] = {}
    for pod in overview.get("pods", []):
        key = owner_workload(pod)
        if key:
            by_workload.setdefault(key, []).append(pod)
    found = []
    for w in overview.get("workloads", []):
        key = (w["kind"], w["name"])
        pods = by_workload.get(key, [])
        reasons = []
        if w.get("desired") == 0:  # 01-pod-common step 2: desired 0 never shows up as "ready < desired"
            found.append(Abnormal(w["kind"], w["name"], "scaled_zero", pods, [f"desired 0 ({w['kind']}/{w['name']})"]))
            continue
        if (w.get("ready") or 0) < (w.get("desired") or 0):
            reasons.append(f"ready {w.get('ready') or 0}/{w.get('desired')}")
        bad_pods = [p for p in pods if _pod_abnormal(p)]
        for p in bad_pods:
            if not p.get("ready"):
                reasons.append(f"{p['name']} not ready")
            for c in p.get("containers", []):
                if c.get("waiting_reason"):
                    reasons.append(f"{p['name']} waiting {c['waiting_reason']}")
                if c.get("restarts"):
                    reasons.append(f"{p['name']} restarts {c['restarts']}")
        if reasons:
            found.append(Abnormal(w["kind"], w["name"], _branch(bad_pods or pods), bad_pods or pods, reasons))
    order = {"image": 0, "scaled_zero": 1, "mount": 2, "crash": 3, "not_ready": 4}
    return sorted(found, key=lambda a: order.get(a.branch, 9))


def _pod_abnormal(p: dict) -> bool:
    return (not p.get("ready")) or p.get("restarts", 0) > 0 or any(c.get("waiting_reason") for c in p.get("containers", []))


def _branch(pods: list[dict]) -> str:
    containers = [c for p in pods for c in p.get("containers", [])]
    if any(c.get("waiting_reason") in IMAGE_REASONS for c in containers):
        return "image"
    if any(c.get("waiting_reason") in MOUNT_REASONS for c in containers):
        return "mount"
    if any(c.get("waiting_reason") in CRASH_REASONS or c.get("restarts", 0) > 0 for c in containers):
        return "crash"
    return "not_ready"


# Step 3: branch-specific evidence, collected in parallel --------------------------
def collect_branch_evidence(tools, ab: Abnormal) -> dict:
    pod = ab.representative
    jobs = {
        "workload": lambda: tools.get_workload(ab.kind.lower(), ab.name),
        "events": lambda: tools.get_events(object_name=ab.name),
    }
    if ab.branch == "crash":
        jobs["previous_logs"] = lambda: tools.get_logs(pod["name"], previous=True, tail=60)
    elif ab.branch == "not_ready" and pod:
        jobs["logs"] = lambda: tools.get_logs(pod["name"], previous=False, tail=60)
    if ab.name == DEPENDENCY:  # 30-redis.md step 2: the dependency itself is abnormal -> check its config too
        jobs["configmap"] = lambda: tools.get_configmap(f"{DEPENDENCY}-config")
    evidence = {"workload_ref": f"{ab.kind}/{ab.name}", "branch": ab.branch, "pod": pod, "reasons": ab.reasons}
    evidence.update(_parallel(jobs))
    if evidence.get("configmap") and "error" not in evidence["configmap"]:
        evidence["config_diff"] = redis_conf_diff((evidence["configmap"].get("data") or {}).get("redis.conf"))
    return evidence


# Dependency handling (00-index jump rules, 10-api / 20-worker step 5, 30-redis) -------------
DEPENDENCY = "redis"
DEP_ERROR = re.compile(r"redis_unavailable|redis_retry|ConnectionError|TimeoutError|AuthenticationError|"
                       r"OutOfMemoryError|OOM command|WRONGPASS|NOAUTH|Name or service not known|connection refused",
                       re.IGNORECASE)
REDIS_BASELINE = ["bind 0.0.0.0", "protected-mode yes", "port 6379", "dir /data", "appendonly yes",
                  "appendfsync everysec", 'save ""', "maxmemory 192mb", "maxmemory-policy noeviction",
                  "include /etc/redis-auth/redis-auth.conf"]
SLOW_MS = 5000
APP_CONFIG_BASELINE = {"REDIS_HOST": "redis", "REDIS_PORT": "6379", "REDIS_TIMEOUT_SECONDS": "1",
                       "KEY_PREFIX": "tiktok-lab", "CONSUMER_GROUP": "click-workers", "PROCESSING_DELAY_MS": "300",
                       "RECLAIM_IDLE_MS": "15000", "RECLAIM_INTERVAL_MS": "1000", "READ_BLOCK_MS": "500",
                       "SHUTDOWN_SECONDS": "15"}
WORKER_COMMAND = ["python", "-m", "app.worker"]
SERVICE_BASELINE = {"api": ({"app": "api"}, "http"), "redis": ({"app": "redis"}, "redis")}
CPU_BASELINE_M = {"redis": 1000, "worker": 500, "api": 500}
AUTH_FAIL = re.compile(r"AUTH failed|WRONGPASS|NOAUTH|AuthenticationError|invalid password", re.IGNORECASE)
MOUNT_FAIL = re.compile(r"couldn't find key (\S+) in (Secret|ConfigMap) (?:[\w-]+/)?([\w.-]+)", re.IGNORECASE)


def has_dependency_error(text: str) -> bool:
    return bool(DEP_ERROR.search(text or ""))


def client_redis_overrides(workload: dict) -> list[str]:
    """REDIS_* variables written directly into the workload (a literal value), instead of coming from
    app-config / the redis-auth Secret. Per the SOP these point at a client-side root cause."""
    out = []
    for c in (workload or {}).get("containers", []):
        for e in c.get("env", []):
            name, sep, _ = e.partition("=")
            if sep and name in ("REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD"):
                out.append(e)
    return out


def redis_conf_diff(conf: str) -> dict:
    lines = [ln.strip() for ln in (conf or "").splitlines() if ln.strip()]
    added = [ln for ln in lines if ln not in REDIS_BASELINE]
    removed = [ln for ln in REDIS_BASELINE if ln not in lines]
    maxmemory = next((ln.split(None, 1)[1] for ln in lines if ln.startswith("maxmemory ")), None)
    return {"added_or_changed": added, "missing": removed, "maxmemory": maxmemory,
            "auth_override": [ln for ln in added if ln.startswith(("requirepass", "user ", "aclfile"))]}


def cpu_millis(value) -> float | None:
    if value is None:
        return None
    v = str(value).strip()
    try:
        return float(v[:-1]) if v.endswith("m") else float(v) * 1000
    except ValueError:
        return None


def service_issues(svc: dict, name: str) -> list[str]:
    """Service selector / targetPort against the baseline in 00-index.md."""
    if not svc or "error" in svc or name not in SERVICE_BASELINE:
        return []
    selector, target = SERVICE_BASELINE[name]
    issues = []
    if svc.get("selector") != selector:
        issues.append(f"service/{name} selector={svc.get('selector')}（基线 {selector}）")
    targets = [p.get("targetPort") for p in svc.get("ports", [])]
    if targets and str(targets[0]) not in (target, "6379" if name == "redis" else "8000"):
        issues.append(f"service/{name} targetPort={targets[0]}（基线 {target}）")
    if "ready_endpoints" in svc and not svc["ready_endpoints"]:
        issues.append(f"service/{name} ready_endpoints=[]")
    return issues


def workload_spec_facts(workload: dict, conf: str | None = None) -> dict:
    """Spec facts the SOP compares with the baseline: command, CPU limit, data mount vs redis.conf `dir`."""
    c = ((workload or {}).get("containers") or [{}])[0]
    name = (workload or {}).get("name")
    cpu = cpu_millis(((c.get("resources") or {}).get("limits") or {}).get("cpu"))
    facts = {"command": c.get("command"), "cpu_limit": ((c.get("resources") or {}).get("limits") or {}).get("cpu"),
             "cpu_low": bool(cpu is not None and name in CPU_BASELINE_M and cpu <= CPU_BASELINE_M[name] / 10)}
    mounts = dict(m.split(" -> ", 1) for m in c.get("volumeMounts", []) if " -> " in m)
    if name == DEPENDENCY:
        conf_dir = next((ln.split(None, 1)[1] for ln in (conf or "").splitlines() if ln.strip().startswith("dir ")), None)
        facts.update({"data_mount": mounts.get("data"), "conf_dir": conf_dir,
                      "mount_mismatch": bool(conf_dir and mounts.get("data") and mounts["data"] != conf_dir)})
    return facts


def app_config_diff(cm: dict) -> dict:
    data = (cm or {}).get("data") or {}
    changed = {k: f"{APP_CONFIG_BASELINE[k]} -> {data.get(k)}" for k in APP_CONFIG_BASELINE
               if k in data and data.get(k) != APP_CONFIG_BASELINE[k]}
    return {"changed": changed, "last_updated": (cm or {}).get("last_updated")}


def pod_times(overview: dict, app: str) -> list[dict]:
    return [{"name": p["name"], "created": p.get("created"), "ready": p.get("ready")}
            for p in (overview or {}).get("pods", []) if p.get("app") == app]


def baseline_checks(tools, overview: dict) -> dict:
    """01-pod-common step 4: things that break while every Pod looks healthy."""
    jobs = {
        "app_config": lambda: tools.get_configmap("app-config"),
        "worker": lambda: tools.get_workload("deployment", "worker"),
        "service_api": lambda: tools.get_service("api"),
        "service_redis": lambda: tools.get_service(DEPENDENCY),
        "redis": lambda: tools.get_workload("statefulset", DEPENDENCY),
        "redis_config": lambda: tools.get_configmap(f"{DEPENDENCY}-config"),
    }
    r = _parallel(jobs)
    conf = ((r["redis_config"] or {}).get("data") or {}).get("redis.conf")
    worker_cmd = workload_spec_facts(r["worker"]).get("command")
    return {
        "app_config_diff": app_config_diff(r["app_config"]) if "error" not in r["app_config"] else r["app_config"],
        "worker_command": worker_cmd,
        "worker_command_differs": bool(worker_cmd) and worker_cmd != WORKER_COMMAND,
        "service_issues": service_issues(r["service_api"], "api") + service_issues(r["service_redis"], DEPENDENCY),
        "redis_spec": workload_spec_facts(r["redis"], conf),
        "pod_created": {"api": pod_times(overview, "api"), "worker": pod_times(overview, "worker")},
    }


def follow_dependency(tools, overview: dict | None = None) -> dict:
    """30-redis.md as code: workload, pods, Service, redis-config diff, spec facts, recent events."""
    jobs = {
        "workload": lambda: tools.get_workload("statefulset", DEPENDENCY),
        "configmap": lambda: tools.get_configmap(f"{DEPENDENCY}-config"),
        "events": lambda: tools.get_events(object_name=DEPENDENCY),
        "service": lambda: tools.get_service(DEPENDENCY),
    }
    out = _parallel(jobs)
    w = out["workload"]
    out["desired_replicas"] = w.get("desired") if "error" not in w else None
    out["ready_replicas"] = w.get("ready") if "error" not in w else None
    out["pods"] = [p for p in (overview or {}).get("pods", []) if (owner_workload(p) or ("", ""))[1] == DEPENDENCY]
    conf = ((out["configmap"] or {}).get("data") or {}).get("redis.conf")
    out["config_diff"] = redis_conf_diff(conf) if conf else None
    redis_ready = any(p.get("ready") for p in out["pods"])
    # empty endpoints only point at the Service when redis itself is ready (30-redis step 3)
    out["service_issues"] = [i for i in service_issues(out["service"], DEPENDENCY)
                             if redis_ready or not i.endswith("ready_endpoints=[]")]
    out["spec"] = workload_spec_facts(w if "error" not in w else {}, conf)
    messages = [e.get("message", "") for e in (out["events"] or {}).get("events", [])]
    out["probe_auth_failures"] = [m for m in messages if "probe failed" in m.lower() and AUTH_FAIL.search(m)][:3]
    out["mount_failures"] = [m for m in messages if MOUNT_FAIL.search(m)][:3]
    out["client_pods"] = {"api": pod_times(overview, "api"), "worker": pod_times(overview, "worker")}
    return out


def slow_lines(lines: list[str]) -> list[str]:
    hits = []
    for ln in lines:
        m = re.search(r'"elapsed_ms"\s*:\s*([0-9.]+)', ln)
        if m and float(m.group(1)) >= SLOW_MS and '"committed"' in ln:
            hits.append(ln)
    return hits


# Step 4: application logs when every workload looks healthy ------------------------
def collect_app_logs(tools, overview: dict) -> dict:
    jobs = {}
    seen = set()
    for pod in overview.get("pods", []):
        key = owner_workload(pod)
        if key and key not in seen:
            seen.add(key)
            jobs[f"{key[0]}/{key[1]}"] = (lambda name=pod["name"]: tools.get_logs(name, previous=False, tail=60))
    results = _parallel(jobs)
    errors, slow = {}, {}
    for ref, res in results.items():
        lines = [ln for ln in res.get("lines", []) if ERROR_PATTERN.search(ln)]
        if lines:
            errors[ref] = lines[-15:]
        s = slow_lines(res.get("lines", []))
        if s:
            slow[ref] = s[-5:]
    return {"logs": results, "error_lines": errors, "slow_lines": slow}


def _parallel(jobs: dict) -> dict:
    with ThreadPoolExecutor(max_workers=max(1, min(4, len(jobs)))) as pool:
        futures = {k: pool.submit(fn) for k, fn in jobs.items()}
        return {k: f.result() for k, f in futures.items()}
