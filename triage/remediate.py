"""Fix-and-verify loop for the Online Boutique scenarios: what the agent may change, how the result is observed,
and how the namespace is put back afterwards.

The agent itself stays read-only. At the end of a round it lists the changes it wants in a JSON block; the harness
checks every one against the small set below and runs it with the admin credentials (argument lists, no shell), the
way an engineer approves and executes a proposed fix. Then the harness reports what users see now.
"""
from __future__ import annotations

import json
import re
import time

from triage.lifecycle import BOUTIQUE_DEPLOYMENTS

NS = "boutique"
TARGETS = [d for d in BOUTIQUE_DEPLOYMENTS if d != "loadgenerator"]   # the load generator is the "users"; leave it alone
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,60}$")
ENV_VALUE = re.compile(r"^[A-Za-z0-9_.:/-]{0,120}$")
APP_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,40}[a-z0-9])?$")
CPU = re.compile(r"^([1-9][0-9]{1,3}m|[1-4])$")
MAX_ACTIONS = 6

ACTIONS_HELP = """可以申请的修复操作（只有这些；每轮最多 6 个；由运维在 boutique 命名空间执行）：
  {"op": "restart", "deployment": "<名字>"}                                  重建该服务的 Pod
  {"op": "set_env", "deployment": "<名字>", "name": "<变量>", "value": "<值>"}   设置环境变量
  {"op": "unset_env", "deployment": "<名字>", "name": "<变量>"}                 删除环境变量
  {"op": "scale", "deployment": "<名字>", "replicas": <0-3>}                   调整副本数
  {"op": "set_cpu_limit", "deployment": "<名字>", "cpu": "<如 500m 或 1>"}      调整 CPU 上限
  {"op": "set_service_selector", "service": "<名字>", "app": "<标签值>"}        修改 Service 的 selector（app 标签）
  {"op": "set_service_target_port", "service": "<名字>", "port": <端口>}        修改 Service 第一个端口的 targetPort"""


def parse_plan(text: str) -> tuple[dict | None, str]:
    """The last ```json block of the answer (or the last {...} that has an "actions" key)."""
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text or "", flags=re.S)
    candidates = blocks[::-1]
    if not candidates:   # no fence: the last parseable object in the text
        decoder = json.JSONDecoder()
        for start in [m.start() for m in re.finditer(r"\{", text or "")][::-1]:
            try:
                obj, end = decoder.raw_decode(text, start)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "actions" in obj:
                candidates = [text[start:end]]
                break
    for raw in candidates:
        try:
            plan = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(plan, dict) and isinstance(plan.get("actions", []), list):
            return plan, ""
    return None, "no JSON block with root_causes/actions at the end of the answer"


def to_kubectl(action: dict) -> tuple[list[list[str]], str]:
    """One requested action -> kubectl argument lists, or an error. Nothing outside this function decides what may run."""
    if not isinstance(action, dict):
        return [], "action is not an object"
    op = action.get("op")
    dep, svc = action.get("deployment"), action.get("service")
    if op in ("restart", "set_env", "unset_env", "scale", "set_cpu_limit") and dep not in TARGETS:
        return [], f"unknown deployment {dep!r}"
    if op in ("set_service_selector", "set_service_target_port") and svc not in TARGETS:
        return [], f"unknown service {svc!r}"
    wait = ["-n", NS, "rollout", "status", f"deployment/{dep}", "--timeout=140s"]
    if op == "restart":
        return [["-n", NS, "delete", "pod", "-l", f"app={dep}", "--wait=true", "--timeout=90s"], wait], ""
    if op in ("set_env", "unset_env"):
        name = str(action.get("name", ""))
        value = str(action.get("value", ""))
        if not ENV_NAME.match(name) or (op == "set_env" and not ENV_VALUE.match(value)):
            return [], "environment variable name or value not accepted"
        return [["-n", NS, "set", "env", f"deployment/{dep}", f"{name}={value}" if op == "set_env" else f"{name}-"], wait], ""
    if op == "scale":
        n = action.get("replicas")
        if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n <= 3:
            return [], "replicas must be 0-3"
        return [["-n", NS, "scale", f"deployment/{dep}", f"--replicas={n}"], *([wait] if n else [])], ""
    if op == "set_cpu_limit":
        cpu = str(action.get("cpu", ""))
        if not CPU.match(cpu):
            return [], "cpu must look like 500m or 1 (at most 4)"
        return [["-n", NS, "set", "resources", f"deployment/{dep}", f"--limits=cpu={cpu}"], wait], ""
    if op == "set_service_selector":
        app = str(action.get("app", ""))
        if not APP_LABEL.match(app):
            return [], "selector value not accepted"
        return [["-n", NS, "patch", "service", svc, "--type=merge", "-p", json.dumps({"spec": {"selector": {"app": app}}})]], ""
    if op == "set_service_target_port":
        port = action.get("port")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            return [], "port must be 1-65535"
        return [["-n", NS, "patch", "service", svc, "--type=json", "-p",
                 json.dumps([{"op": "replace", "path": "/spec/ports/0/targetPort", "value": port}])]], ""
    return [], f"unknown op {op!r}"


def apply_actions(lc, actions: list) -> list[dict]:
    """Run the accepted actions in order; rejected ones are reported back, not run."""
    results = []
    for action in (actions or [])[:MAX_ACTIONS]:
        cmds, error = to_kubectl(action)
        rec = {"action": action, "accepted": not error}
        if error:
            rec["result"] = f"拒绝：{error}"
        else:
            outs = []
            for args in cmds:
                res = lc._run(args, 150)
                outs.append(("ok" if res.ok else "失败") + ": " + (res.stdout + res.stderr).strip()[-200:])
                if not res.ok:
                    break
            rec["ok"] = all(o.startswith("ok") for o in outs)
            rec["result"] = " | ".join(outs)
        results.append(rec)
    for action in (actions or [])[MAX_ACTIONS:]:
        results.append({"action": action, "accepted": False, "result": f"拒绝：每轮最多 {MAX_ACTIONS} 个操作"})
    return results


def observe(lc, since: str, min_seconds: float = 75, max_seconds: float = 180, sleep=time.sleep) -> dict:
    """What users see from `since` on: latency of the frontend's requests, HTTP 500s, checkout attempts and failures.
    Waits at least min_seconds, and up to max_seconds for two checkout attempts (the load generator orders rarely)."""
    waited = 0.0
    while True:
        res = lc._run(["-n", NS, "logs", "-l", "app=frontend", f"--since-time={since}", "--tail=-1", "--max-log-requests=10"], 30)
        rows = []
        for line in res.stdout.splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and rec.get("message") == "request complete":
                rows.append(rec)
        checkouts = [r for r in rows if r.get("http.req.path") == "/cart/checkout"]
        if waited >= min_seconds and (len(checkouts) >= 2 or waited >= max_seconds):
            break
        sleep(15)
        waited += 15
    times = sorted(float(r.get("http.resp.took_ms") or 0) for r in rows)
    pick = lambda q: round(times[min(len(times) - 1, int(len(times) * q))]) if times else None  # noqa: E731
    slow = sum(t >= 180 for t in times)
    out = {"since": since, "seconds": waited, "requests": len(rows), "slow_180ms": slow,
           "share_slow": round(slow / len(rows), 3) if rows else None, "p50_ms": pick(0.5), "p90_ms": pick(0.9), "p99_ms": pick(0.99),
           "http_500": sum(int(r.get("http.resp.status") or 0) >= 500 for r in rows),
           "checkout_attempts": len(checkouts), "checkout_failed": sum(int(r.get("http.resp.status") or 0) >= 500 for r in checkouts)}
    # healthy on this cluster: no request over 180 ms and no 500 (boutique/hard_validation.json); 2% leaves room for a blip
    out["resolved"] = bool(len(rows) >= 30 and out["share_slow"] <= 0.02 and out["http_500"] == 0 and out["checkout_attempts"] >= 1)
    return out


def describe(obs: dict) -> str:
    """The observation as a user/on-call would report it: numbers only, no hint about causes."""
    if not obs["requests"]:
        return f"最近 {obs['seconds']:.0f} 秒前端没有处理任何请求。"
    return (f"最近 {obs['seconds']:.0f} 秒前端处理了 {obs['requests']} 个请求：{obs['slow_180ms']} 个超过 180 毫秒（占 {obs['share_slow']:.1%}；"
            f"这个系统正常时没有请求超过 180 毫秒），中位数 {obs['p50_ms']} 毫秒，90 分位 {obs['p90_ms']} 毫秒，99 分位 {obs['p99_ms']} 毫秒；"
            f"返回 500 的请求 {obs['http_500']} 个；下单 {obs['checkout_attempts']} 次，失败 {obs['checkout_failed']} 次。")


def snapshot(lc) -> dict:
    """The fields the agent's actions can change, for every Service and Deployment, as they are now."""
    items = (lc._json(["-n", NS, "get", "services,deployments"]) or {}).get("items", [])
    return {(i["kind"], i["metadata"]["name"]): lc._shape(i) for i in items if (i["kind"], i["metadata"]["name"]) in lc._baseline()}


def improved(before: dict, after: dict) -> bool:
    """Did users get better off between two observations? Errors gone, or the share of slow requests at least halved
    (a smaller change is within what the share does on its own from one window to the next)."""
    if after.get("resolved"):
        return True
    errors_gone = (before["http_500"] > 0 and after["http_500"] == 0) or (before["checkout_failed"] > 0 and after["checkout_failed"] == 0)
    b, a = before.get("share_slow") or 0, after.get("share_slow") or 0
    return bool(errors_gone or (b >= 0.04 and a <= b / 2))


def restore_baseline(lc) -> list[str]:
    """Undo whatever the agent changed: every Deployment and Service back to the release manifest's replicas,
    environment, CPU limit, selector and targetPort (the same fields the health check compares)."""
    return restore_to(lc, lc._baseline())


def restore_to(lc, target: dict) -> list[str]:
    """Put every Service and Deployment back to `target` (a snapshot() or the manifest baseline)."""
    done = []
    live = {(i["kind"], i["metadata"]["name"]): i for i in (lc._json(["-n", NS, "get", "services,deployments"]) or {}).get("items", [])}
    for (kind, name), want in target.items():
        if (kind, name) not in live:
            continue
        got = lc._shape(live[(kind, name)])
        cmds = []
        if kind == "Deployment":
            env = [f"{k}-" for k in got["env"] if k not in want["env"]] + [f"{k}={v}" for k, v in want["env"].items() if got["env"].get(k) != v]
            if env:
                cmds.append(["-n", NS, "set", "env", f"deployment/{name}", *env])
            if got["cpu_limit"] != want["cpu_limit"] and want["cpu_limit"]:
                cmds.append(["-n", NS, "set", "resources", f"deployment/{name}", f"--limits=cpu={want['cpu_limit']}"])
            if got["replicas"] != want["replicas"]:
                cmds.append(["-n", NS, "scale", f"deployment/{name}", f"--replicas={want['replicas']}"])
        else:
            if got["selector"] != want["selector"]:
                cmds.append(["-n", NS, "patch", "service", name, "--type=merge", "-p", json.dumps({"spec": {"selector": want["selector"]}})])
            for i, port in enumerate(want["targetPorts"]):
                if i < len(got["targetPorts"]) and got["targetPorts"][i] != port:
                    cmds.append(["-n", NS, "patch", "service", name, "--type=json", "-p",
                                 json.dumps([{"op": "replace", "path": f"/spec/ports/{i}/targetPort", "value": port}])])
        for args in cmds:
            res = lc._run(args, 60)
            done.append(f"{kind}/{name}: {' '.join(args[2:5])} -> {'ok' if res.ok else res.stderr.strip()[:120]}")
    return done


def recover(lc) -> list[str]:
    """Clean up after a run that was killed before its own reset (Ctrl+C, a closed terminal): remove the fault injector,
    put every Service and Deployment back to the release manifest, wait for the rollouts. Safe to call on a clean namespace."""
    done = []
    if lc._run(["-n", "infra-tools", "get", "pod", "net-injector"], 20).ok:
        res = lc._run(["-n", "infra-tools", "delete", "pod", "net-injector", "--ignore-not-found", "--wait=true", "--timeout=90s"], 120)
        done.append("net-injector removed" if res.ok else f"net-injector: {res.stderr.strip()[:120]}")
    done += restore_baseline(lc)
    if done:
        for name in BOUTIQUE_DEPLOYMENTS:
            lc._run(["-n", NS, "rollout", "status", f"deployment/{name}", "--timeout=140s"], 150)
    return done
