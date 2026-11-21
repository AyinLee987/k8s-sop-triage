"""Container metrics for the pods of one namespace, the way a monitoring system would show them.

  python scripts/pod_metrics.py [--namespace boutique] [--window 15]

Reads the kubelet's cAdvisor endpoint twice, `--window` seconds apart, and prints one table: CPU usage against the
limit, how often the container was throttled (in the window and since it started), memory, and the pod interface's
packet / drop / error counters. This is what the agent could not see before: it had no way to tell "the container is
being CPU-throttled" from "the delay is somewhere else".

Credentials: the pod list comes from the diagnosing account ($KUBECONFIG, read-only on the namespace); the metrics come
from the kubelet with a token that may only read node metrics ($METRICS_TOKEN_FILE, k8s/rbac-metrics.yaml). The kubelet's
serving certificate on kind is self-signed, so it is not verified (same as metrics-server on kind); the connection stays
inside Docker's private network. The token is never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.request

WANTED = ("container_cpu_usage_seconds_total", "container_cpu_cfs_periods_total", "container_cpu_cfs_throttled_periods_total",
          "container_cpu_cfs_throttled_seconds_total", "container_memory_working_set_bytes",
          "container_network_receive_packets_total", "container_network_transmit_packets_total",
          "container_network_receive_packets_dropped_total", "container_network_transmit_packets_dropped_total",
          "container_network_receive_errors_total", "container_network_transmit_errors_total")
LINE = re.compile(r"^(\w+)\{(.*)\}\s+(\S+)(?:\s+(\d+))?$")
LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def parse(text: str, namespace: str) -> dict:
    """{pod: {metric: (value, timestamp_ms)}} summed over the pod's containers (network series are per pod, eth0 only)."""
    out: dict = {}
    for line in text.splitlines():
        if not line.startswith("container_"):
            continue
        m = LINE.match(line)
        if not m or m.group(1) not in WANTED:
            continue
        labels = dict(LABEL.findall(m.group(2)))
        if labels.get("namespace") != namespace or not labels.get("pod"):
            continue
        name = m.group(1)
        if name.startswith("container_network_"):
            if labels.get("interface") != "eth0":
                continue
        elif labels.get("container") in ("", "POD", None):   # the pod-level cgroup and the pause container
            continue
        try:
            value = float(m.group(3))
        except ValueError:
            continue
        ts = int(m.group(4)) if m.group(4) else 0
        old = out.setdefault(labels["pod"], {}).get(name, (0.0, 0))
        out[labels["pod"]][name] = (old[0] + value, max(old[1], ts))
    return out


def cpu_m(text: str | None) -> float | None:
    if not text:
        return None
    return float(text[:-1]) if text.endswith("m") else float(text) * 1000


def mem_mi(text: str | None) -> float | None:
    if not text:
        return None
    units = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024, "K": 1 / 1048.576, "M": 1 / 1.048576, "G": 953.674}
    for u, k in units.items():
        if text.endswith(u):
            return float(text[:-len(u)]) * k
    return float(text) / 1048576


def table(pods: list[dict], first: dict, second: dict, window: float) -> str:
    """One row per pod. A window value is '-' when the kubelet did not refresh that counter between the two readings."""
    def val(sample, pod, name):
        return sample.get(pod, {}).get(name, (None, 0))

    def delta(pod, name):
        a, ta = val(first, pod, name)
        b, tb = val(second, pod, name)
        if a is None or b is None or b < a:
            return None, 0.0
        seconds = (tb - ta) / 1000 if ta and tb else window
        return (b - a, seconds) if seconds > 0 else (None, 0.0)

    def pct(part, whole):
        return "-" if part is None or not whole else f"{100 * part / whole:.0f}%"

    def num(x, fmt="{:.0f}"):
        return "-" if x is None else fmt.format(x)

    rows = ["| Pod | CPU 用量(m) | CPU 上限(m) | 被限流周期占比(窗口) | 被限流周期占比(启动以来) | 累计被限流(秒) | 内存(Mi) / 上限 | "
            "收包/发包(窗口) | 收丢包/发丢包(启动以来) | 收错误/发错误(启动以来) |", "|---|---|---|---|---|---|---|---|---|---|"]
    for p in sorted(pods, key=lambda p: p["name"]):
        pod = p["name"]
        d_cpu, s_cpu = delta(pod, "container_cpu_usage_seconds_total")
        d_per, _ = delta(pod, "container_cpu_cfs_periods_total")
        d_thr, _ = delta(pod, "container_cpu_cfs_throttled_periods_total")
        periods, _ = val(second, pod, "container_cpu_cfs_periods_total")
        throttled, _ = val(second, pod, "container_cpu_cfs_throttled_periods_total")
        thr_s, _ = val(second, pod, "container_cpu_cfs_throttled_seconds_total")
        mem, _ = val(second, pod, "container_memory_working_set_bytes")
        rx, _ = delta(pod, "container_network_receive_packets_total")
        tx, _ = delta(pod, "container_network_transmit_packets_total")
        g = lambda name: val(second, pod, name)[0]  # noqa: E731
        rows.append(f"| {pod} | {num(1000 * d_cpu / s_cpu if d_cpu is not None and s_cpu else None)} | {num(p['cpu_limit_m'])} | "
                    f"{pct(d_thr, d_per)} | {pct(throttled, periods)} | {num(thr_s, '{:.1f}')} | "
                    f"{num(mem / 1048576 if mem is not None else None)} / {num(p['mem_limit_mi'])} | {num(rx)}/{num(tx)} | "
                    f"{num(g('container_network_receive_packets_dropped_total'))}/{num(g('container_network_transmit_packets_dropped_total'))} | "
                    f"{num(g('container_network_receive_errors_total'))}/{num(g('container_network_transmit_errors_total'))} |")
    notes = (f"\n说明：窗口 = 最近约 {window:.0f} 秒；“-” = 这段时间内 kubelet 没有刷新该计数。"
             "被限流周期占比 = 容器有任务可运行的 CFS 调度周期（100 毫秒一个）里，因为用完 CPU 配额而被暂停的周期所占比例。"
             "网络计数取自 Pod 自己的网卡（eth0），只统计到达这块网卡或由它发出的包。数据来源：节点 kubelet 的 cAdvisor。")
    return "\n".join(rows) + notes


def list_pods(namespace: str) -> list[dict]:
    proc = subprocess.run(["kubectl", "-n", namespace, "get", "pods", "-o", "json"], capture_output=True, text=True, timeout=30)
    if proc.returncode:
        sys.exit(f"cannot list pods in {namespace}: {proc.stderr.strip()[:200]}")
    pods = []
    for item in json.loads(proc.stdout).get("items", []):
        limits = [(c.get("resources") or {}).get("limits") or {} for c in item["spec"]["containers"]]
        cpu = [cpu_m(l.get("cpu")) for l in limits]
        mem = [mem_mi(l.get("memory")) for l in limits]
        pods.append({"name": item["metadata"]["name"], "host": (item.get("status") or {}).get("hostIP"),
                     "cpu_limit_m": sum(cpu) if cpu and all(c is not None for c in cpu) else None,
                     "mem_limit_mi": sum(mem) if mem and all(m is not None for m in mem) else None})
    return pods


def scrape(host: str, token: str, timeout: float = 20) -> str:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE   # kind's kubelet serves a self-signed certificate
    req = urllib.request.Request(f"https://{host}:10250/metrics/cadvisor", headers={"Authorization": f"Bearer {token}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))   # never through a proxy
    with opener.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--namespace", default="boutique")
    ap.add_argument("--window", type=float, default=15)
    args = ap.parse_args()
    token_file = os.environ.get("METRICS_TOKEN_FILE", "")
    if not token_file or not os.path.exists(token_file):
        print("指标不可用：没有配置读取节点指标的凭证。")
        return 1
    token = open(token_file, encoding="utf-8").read().strip()
    pods = list_pods(args.namespace)
    hosts = sorted({p["host"] for p in pods if p["host"]})
    if not hosts:
        print(f"{args.namespace} 里没有正在运行的 Pod。")
        return 1
    try:
        first: dict = {}
        for h in hosts:
            first.update(parse(scrape(h, token), args.namespace))
        time.sleep(args.window)
        second: dict = {}
        for h in hosts:
            second.update(parse(scrape(h, token), args.namespace))
    except Exception as exc:   # an HTTP 401/403 or an unreachable kubelet: say so, without the token or a traceback
        print(f"指标不可用：读取节点 kubelet 失败（{type(exc).__name__}: {str(exc)[:160]}）。")
        return 1
    print(f"命名空间 {args.namespace} 各 Pod 的资源指标\n")
    print(table(pods, first, second, args.window))
    return 0


if __name__ == "__main__":
    sys.exit(main())
