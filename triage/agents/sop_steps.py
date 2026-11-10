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
    branch: str  # "image" | "crash" | "not_ready"
    pods: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def representative(self) -> dict:
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
    order = {"image": 0, "crash": 1, "not_ready": 2}
    return sorted(found, key=lambda a: order.get(a.branch, 9))


def _pod_abnormal(p: dict) -> bool:
    return (not p.get("ready")) or p.get("restarts", 0) > 0 or any(c.get("waiting_reason") for c in p.get("containers", []))


def _branch(pods: list[dict]) -> str:
    containers = [c for p in pods for c in p.get("containers", [])]
    if any(c.get("waiting_reason") in IMAGE_REASONS for c in containers):
        return "image"
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
    elif ab.branch == "not_ready":
        jobs["logs"] = lambda: tools.get_logs(pod["name"], previous=False, tail=60)
    evidence = {"workload_ref": f"{ab.kind}/{ab.name}", "branch": ab.branch, "pod": pod, "reasons": ab.reasons}
    evidence.update(_parallel(jobs))
    return evidence


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
    errors = {}
    for ref, res in results.items():
        lines = [ln for ln in res.get("lines", []) if ERROR_PATTERN.search(ln)]
        if lines:
            errors[ref] = lines[-15:]
    return {"logs": results, "error_lines": errors}


def _parallel(jobs: dict) -> dict:
    with ThreadPoolExecutor(max_workers=max(1, min(4, len(jobs)))) as pool:
        futures = {k: pool.submit(fn) for k, fn in jobs.items()}
        return {k: f.result() for k, f in futures.items()}
