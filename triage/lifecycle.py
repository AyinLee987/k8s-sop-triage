"""Environment lifecycle, run with ADMIN credentials only:
verify healthy -> precheck -> inject -> confirm fault is active -> (diagnose elsewhere) -> reset -> verify recovered.

Nothing here is visible to the diagnoser. Scenario ids, inject steps and answers never leave this module
and triage.grader.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
SCENARIO_FILE = REPO / "scenarios" / "scenarios.yaml"


def load_scenarios(path: Path = SCENARIO_FILE, split: str | None = None, ids: list[str] | None = None,
                   source: str = "handwritten", max_per_template: int | None = None) -> list[dict]:
    """source: handwritten | generated | all. Generated ones come from scenarios/templates.yaml."""
    items = []
    if source in ("handwritten", "all"):
        items += yaml.safe_load(Path(path).read_text(encoding="utf-8"))["scenarios"]
    if source in ("generated", "all"):
        from triage.generator import expand
        items += expand(max_per_template=max_per_template)
    if ids:
        source_ids = {s["id"] for s in items}
        missing = set(ids) - source_ids
        if missing and source != "all":  # allow `--scenario G-...` without remembering --source
            from triage.generator import expand
            items += [s for s in expand() if s["id"] in missing]
    if split and split != "all":
        items = [s for s in items if s["split"] == split]
    if ids:
        wanted = set(ids)
        items = [s for s in items if s["id"] in wanted]
    return items


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class StepLog:
    entries: list[dict] = field(default_factory=list)

    def add(self, phase: str, ok: bool, detail: str = "", seconds: float = 0.0) -> None:
        self.entries.append({"phase": phase, "ok": ok, "detail": detail[:500], "seconds": round(seconds, 2)})


class EnvironmentError_(Exception):
    """Environment failure: recorded separately and never counted against the diagnoser."""


class Lifecycle:
    def __init__(self, admin_runner, namespace: str, lab_k8s_dir: str, poll_interval: float = 3.0,
                 fresh_pods: bool = True, rollout_timeout: int = 180):
        self.k = admin_runner
        self.ns = namespace
        self.vars = {"ns": namespace, "lab_k8s": str(lab_k8s_dir), "lab": str(Path(lab_k8s_dir).parent), "repo": str(REPO)}
        self.poll = poll_interval
        self.fresh_pods = fresh_pods
        self.rollout_timeout = rollout_timeout

    # ---- public phases ----
    def verify_healthy(self, log: StepLog, phase: str = "verify_healthy", wait_s: float = 90.0) -> None:
        """Polls, because old pods may still be terminating right after a rollout."""
        started = time.monotonic()
        while True:
            problems = self.health_problems()
            if not problems or time.monotonic() - started >= wait_s:
                break
            time.sleep(self.poll)
        log.add(phase, not problems, "; ".join(problems) or "healthy", time.monotonic() - started)
        if problems:
            raise EnvironmentError_(f"{phase}: " + "; ".join(problems))

    def precheck(self, scenario: dict, log: StepLog) -> None:
        for check in scenario.get("precheck", []) or []:
            ok, detail = self._check(check)
            log.add("precheck", ok, detail)
            if not ok:
                raise EnvironmentError_(f"precheck failed: {detail}")

    def inject(self, scenario: dict, log: StepLog) -> str:
        """Returns the UTC timestamp just before injection (used to filter stale events).

        Old events are deleted first: Kubernetes keeps events for about an hour, and an agent that reads events
        without our time filter (HolmesGPT) would otherwise see earlier runs of the same fault (ISSUES E-016)."""
        res = self._run(["-n", self.ns, "delete", "events", "--all"], 60)
        log.add("clear_events", res.ok, "" if res.ok else res.stderr.strip()[:200])
        # Old ReplicaSets carry the same history (an agent reading rollout history sees earlier injections of
        # this fault, and later repetitions would see more of it than the first). Keep only the live ones.
        rs = self._json(["-n", self.ns, "get", "replicasets"]) or {}
        stale = [i["metadata"]["name"] for i in rs.get("items", []) if not (i.get("spec") or {}).get("replicas")]
        if stale:
            res = self._run(["-n", self.ns, "delete", "replicaset", *stale], 60)
            log.add("prune_history", res.ok, f"{len(stale)} old replicasets" + ("" if res.ok else f": {res.stderr.strip()[:200]}"))
        since = utc_now_iso()
        for step in scenario.get("inject", []) or []:
            self._kubectl_step(step, log, "inject")
        return since

    def confirm(self, scenario: dict, log: StepLog) -> None:
        spec = scenario.get("confirm") or {"timeout_s": 10, "checks": [{"type": "always"}]}
        deadline = time.monotonic() + float(spec.get("timeout_s", 60))
        started = time.monotonic()
        last = ""
        while True:
            results = [self._check(c) for c in spec.get("checks", [])]
            if all(ok for ok, _ in results):
                log.add("confirm", True, "; ".join(d for _, d in results), time.monotonic() - started)
                return
            last = "; ".join(d for ok, d in results if not ok)
            if time.monotonic() >= deadline:
                log.add("confirm", False, last, time.monotonic() - started)
                raise EnvironmentError_(f"fault did not take effect: {last}")
            time.sleep(self.poll)

    def reset(self, scenario: dict, log: StepLog) -> None:
        started = time.monotonic()
        errors = []
        for step in scenario.get("reset", []) or []:
            try:
                self._kubectl_step(step, log, "reset")
            except EnvironmentError_ as exc:
                errors.append(str(exc))
        if self.fresh_pods:
            self._run(["-n", self.ns, "rollout", "restart", "deployment/api", "deployment/worker"], 60)
        for target in ("deployment/api", "deployment/worker", "statefulset/redis"):
            res = self._run(["-n", self.ns, "rollout", "status", target, f"--timeout={self.rollout_timeout}s"],
                            self.rollout_timeout + 10)
            if not res.ok:
                errors.append(f"rollout {target}: {res.stderr.strip()[:200]}")
        log.add("reset", not errors, "; ".join(errors) or "reset steps applied", time.monotonic() - started)
        self.verify_healthy(log, phase="verify_recovered")

    def health_problems(self) -> list[str]:
        problems = []
        expected = {("Deployment", "api"): 2, ("Deployment", "worker"): 1, ("StatefulSet", "redis"): 1}
        data = self._json(["-n", self.ns, "get", "deployments,statefulsets,pods"])
        if data is None:
            return ["cannot read namespace with admin credentials"]
        seen = {}
        for item in data.get("items", []):
            kind, name = item.get("kind"), item["metadata"]["name"]
            if kind in ("Deployment", "StatefulSet"):
                seen[(kind, name)] = item.get("status", {}).get("readyReplicas", 0) or 0
            elif kind == "Pod":
                if item["metadata"].get("deletionTimestamp"):
                    problems.append(f"pod {name} terminating")
                    continue
                for cs in item.get("status", {}).get("containerStatuses", []) or []:
                    if not cs.get("ready"):
                        problems.append(f"pod {name} not ready")
                    if "waiting" in (cs.get("state") or {}):
                        problems.append(f"pod {name} waiting {cs['state']['waiting'].get('reason')}")
        for key, want in expected.items():
            if seen.get(key, 0) < want:
                problems.append(f"{key[0]}/{key[1]} ready {seen.get(key, 0)}/{want}")
        cm = self._json(["-n", self.ns, "get", "configmap", "redis-config"])
        conf = ((cm or {}).get("data") or {}).get("redis.conf", "")
        if "maxmemory 192mb" not in conf or "requirepass" in conf:
            problems.append("configmap redis-config differs from baseline (maxmemory / requirepass)")
        app_cfg = ((self._json(["-n", self.ns, "get", "configmap", "app-config"]) or {}).get("data") or {})
        if app_cfg.get("KEY_PREFIX") != "tiktok-lab":
            problems.append("configmap app-config KEY_PREFIX differs from baseline")
        svc = (self._json(["-n", self.ns, "get", "service", "redis"]) or {}).get("spec") or {}
        if svc.get("selector") != {"app": "redis"} or [p.get("targetPort") for p in svc.get("ports", [])] != ["redis"]:
            problems.append("service redis selector/targetPort differs from baseline")
        sts = self._json(["-n", self.ns, "get", "statefulset", "redis"]) or {}
        rc = ((sts.get("spec") or {}).get("template", {}).get("spec", {}).get("containers") or [{}])[0]
        mounts = {m.get("name"): m.get("mountPath") for m in rc.get("volumeMounts", []) or []}
        if mounts.get("data") != "/data":
            problems.append("statefulset redis data volume not mounted at /data")
        if ((rc.get("resources") or {}).get("limits") or {}).get("cpu") not in ("1", "1000m"):
            problems.append("statefulset redis cpu limit differs from baseline")
        # key names only (the go-template never prints values)
        keys = self._run(["-n", self.ns, "get", "secret", "redis-auth", "-o",
                          "go-template={{range $k, $v := .data}}{{$k}} {{end}}"], 20)
        if not keys.ok or set(keys.stdout.split()) != {"password", "redis-auth.conf"}:
            problems.append("secret redis-auth keys differ from baseline (password, redis-auth.conf)")
        if self._endpoints_ready("api") < 2:
            problems.append("service api has fewer than 2 ready endpoints")
        if self._run(["get", "namespace", "rec-feature"], 20).ok:
            problems.append("namespace rec-feature still exists")
        return problems

    # ---- checks ----
    def _check(self, check: dict) -> tuple[bool, str]:
        kind = check["type"]
        ns = check.get("namespace", self.ns)
        if kind == "always":
            return True, "always"
        if kind == "jsonpath_equals":
            res = self._run(["-n", ns, "get", check["resource"], "-o", f"jsonpath={check['path']}"], 20)
            return res.ok and res.stdout.strip() == str(check["value"]), f"{check['resource']} {check['path']}={res.stdout.strip()!r}"
        if kind == "service_endpoints_empty":
            n = self._endpoints_ready(check["service"], ns)
            return n == 0, f"service {check['service']} ready endpoints={n}"
        if kind == "logs_contain":
            res = self._run(["-n", ns, "logs", "-l", check["selector"], "--tail=200", "--all-containers",
                             "--prefix", "--max-log-requests=10"], 30)
            found = check["text"] in res.stdout
            return found, f"logs({check['selector']}) contain {check['text']!r}: {found}"
        pods = self._json(["-n", ns, "get", "pods", "-l", check["selector"]])
        statuses = [cs for p in (pods or {}).get("items", []) for cs in p.get("status", {}).get("containerStatuses", []) or []]
        if kind == "pod_waiting_reason":
            reasons = {(cs.get("state") or {}).get("waiting", {}).get("reason") for cs in statuses}
            ok = bool(reasons & set(check["reasons"]))
            return ok, f"waiting reasons {sorted(r for r in reasons if r)}"
        if kind == "pod_restart_count_at_least":
            top = max((cs.get("restartCount", 0) for cs in statuses), default=0)
            return top >= int(check["count"]), f"max restarts {top}"
        if kind == "pod_not_ready":
            # a running container that is not ready (readiness failing), as opposed to crashing/waiting
            running_unready = [cs for cs in statuses if "running" in (cs.get("state") or {}) and not cs.get("ready")]
            return bool(running_unready), f"running-but-unready containers {len(running_unready)}"
        if kind == "pod_last_terminated_reason":
            reasons = {(cs.get("lastState") or {}).get("terminated", {}).get("reason") for cs in statuses}
            return check["reason"] in reasons, f"last terminated reasons {sorted(r for r in reasons if r)}"
        return False, f"unknown check {kind}"

    # ---- helpers ----
    def _render(self, value: str) -> str:
        for k, v in self.vars.items():
            value = value.replace("{" + k + "}", v)
        return value

    def _kubectl_step(self, step: dict, log: StepLog, phase: str) -> None:
        args = [self._render(a) for a in step["kubectl"]]
        started = time.monotonic()
        res = self._run(args, 150)
        log.add(phase, res.ok, " ".join(args[:6]) + (f" -> {res.stderr.strip()[:200]}" if not res.ok else ""),
                time.monotonic() - started)
        if not res.ok:
            raise EnvironmentError_(f"{phase} step failed: {res.stderr.strip()[:300]}")

    def _run(self, args: list[str], timeout: float):
        return self.k.run(args, timeout=timeout)

    def _json(self, args: list[str]):
        res = self._run([*args, "-o", "json"], 30)
        if not res.ok:
            return None
        try:
            return json.loads(res.stdout)
        except json.JSONDecodeError:
            return None

    def _endpoints_ready(self, service: str, ns: str | None = None) -> int:
        data = self._json(["-n", ns or self.ns, "get", "endpointslices", "-l", f"kubernetes.io/service-name={service}"])
        count = 0
        for s in (data or {}).get("items", []):
            for ep in s.get("endpoints", []) or []:
                if (ep.get("conditions") or {}).get("ready"):
                    count += 1
        return count
