"""Write holmes/skills/svc-sop-v0-metrics: the base SOPs (svc-sop-v0) with one fact corrected, for the fourth bank.

  python boutique/make_sop_v0_metrics.py

svc-sop-v0 was written (boutique/make_service_sops.py) for an agent without any metrics, and says so in 17 sentences
("没有监控指标", "CPU 限流无法直接观测", "不要找 CPU/内存/延迟指标"). Since the second bank's baseline the agent has had
the boutique_pod_metrics tool (holmes/toolsets-metrics.yaml), so those sentences told it not to use a tool it had. On the
fourth bank (docs/V4.md, 2026-11-25) the base SOP is this copy instead.

What changes: only those sentences, and only the fact. They now say the tool exists and what it shows (one row per pod:
CPU use and limit, share of throttled CFS periods, memory, the pod interface's packet counters; no history, no latency,
no nodes). Nothing is added about how to read it: in particular nothing about a pod's containers being summed, which is
what the fourth bank's sidecar tests. Every other line is svc-sop-v0's, byte for byte. svc-sop-v0 itself is not touched
(the second and third banks were measured with it). The name avoids svc-sop-v1..v4, the bad-case experiment's SOPs.
"""
from __future__ import annotations

import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "holmes" / "skills" / "svc-sop-v0"
OUT = REPO / "holmes" / "skills" / "svc-sop-v0-metrics"
TOOL = "Pod 指标工具 `boutique_pod_metrics`"

# skill -> [(sentence in svc-sop-v0, replacement)]. Each old sentence must occur exactly once in its file.
EDITS = {
    "boutique-triage-overview": [
        ("- 不可用：进入容器执行命令、查看节点、查看监控指标；系统没有 CPU、内存、延迟指标。",
         "- 不可用：进入容器执行命令、查看节点；系统没有延迟指标。\n"
         f"- 可用的指标：{TOOL}，每个 Pod 一行：CPU 用量和上限、被限流的周期占比（最近一段窗口和容器启动以来）、内存用量和上限、"
         "Pod 网卡的收发包和丢包计数；只有当前读数，没有历史曲线。"),
        ("或下一步需要进入容器、查看节点、查看监控指标、访问其他命名空间等无权限操作",
         "或下一步需要进入容器、查看节点、访问其他命名空间等无权限操作"),
    ],
    "boutique-adservice": [
        ("只读账号不能看节点、无监控指标；CPU 限流不能凭指标判断，只能先看日志和事件，不确定时先确认。",
         f"只读账号不能看节点；CPU 用量和被限流的周期占比可以用{TOOL}查看，结合日志和事件判断，不确定时先确认。"),
    ],
    "boutique-cartservice": [
        ("本环境无监控指标，按对象状态、事件、日志、EndpointSlice 判断，不要等 CPU/内存/延迟曲线。",
         f"本环境没有延迟指标，也没有历史曲线；CPU、内存和限流可以用{TOOL}查看，再结合对象状态、事件、日志、EndpointSlice 判断。"),
    ],
    "boutique-checkoutservice": [
        ("无监控指标，不要依赖 CPU、内存、延迟曲线。",
         f"没有延迟指标，也没有历史曲线；CPU、内存和限流可以用{TOOL}查看。"),
        ("无 CPU/内存指标，不能仅凭现象判断 CPU 饱和；CPU 限流无法直接观测，先确认事件、日志和下游正常性。",
         f"CPU、内存用量和被限流的周期占比用{TOOL}查看，不要仅凭现象判断 CPU 饱和；同时确认事件、日志和下游正常性。"),
    ],
    "boutique-currencyservice": [
        ("不要进容器执行命令，不要看节点，不要找 CPU/内存/延迟指标。",
         f"不要进容器执行命令，不要看节点；没有延迟指标，CPU、内存和限流可以用{TOOL}查看。"),
        ("没有监控指标，不能判断实际 CPU/内存使用率或延迟，只能依据状态、事件、配额；不确定处写“先确认/无监控无法确认”。",
         f"实际 CPU/内存用量和限流用{TOOL}查看；没有延迟指标，延迟只能从日志判断；其余依据状态、事件、配额；不确定处写“先确认”。"),
        ("无监控指标时不要写 CPU/内存/延迟推断。",
         "没有读数依据时不要写 CPU/内存/延迟推断（CPU/内存看 `boutique_pod_metrics`，延迟看日志）。"),
    ],
    "boutique-emailservice": [
        ("没有指标，不能判断探针延迟是否由 CPU 限流导致，只能写“先确认”。",
         f"探针变慢是否和 CPU 限流有关，可以用{TOOL}看本服务是否被限流；确认不了就写“先确认”。"),
        ("没有 CPU/内存指标，不能直接证明 CPU 限流；只能根据重启、事件、超时现象谨慎写“疑似”，并注明无法用监控确认。",
         f"CPU 限流用{TOOL}的被限流周期占比确认，结合重启、事件、超时现象判断；没有读数支持时写“疑似”。"),
    ],
    "boutique-frontend": [
        ("没有监控指标，不能靠 CPU/内存曲线下结论。",
         f"内存用量可以用{TOOL}对照上限，但它没有历史曲线，OOMKilled 以事件和容器状态为准。"),
    ],
    "boutique-paymentservice": [
        ("没有监控指标，不能据 CPU/内存使用率或延迟下结论，也不要查节点。",
         f"CPU/内存用量和限流用{TOOL}查看；没有延迟指标；不要查节点。"),
    ],
    "boutique-productcatalogservice": [
        ("并注明缺什么（如指标、节点信息、容器内检查权限）",
         "并注明缺什么（如延迟指标、节点信息、容器内检查权限）"),
    ],
    "boutique-recommendationservice": [
        ("没有监控指标，不查 CPU/内存使用率；若没有 OOMKilled、调度失败或配额证据，不要仅凭延迟/慢归因资源不足。",
         f"CPU/内存用量和限流用{TOOL}查看；若没有 OOMKilled、调度失败、配额或限流的证据，不要仅凭延迟/慢归因资源不足。"),
        ("恢复验证只看 Pod Ready、EndpointSlice ready、相关事件和日志，不使用监控指标。",
         "恢复验证看 Pod Ready、EndpointSlice ready、相关事件和日志，需要时再看 `boutique_pod_metrics` 的读数。"),
    ],
    "boutique-shippingservice": [
        ("没有监控，CPU 是否被限流无法直接看；若调用方报 DeadlineExceeded 且本服务日志显示请求处理慢/排队，先确认是否存在 CPU 限流（无指标，只能标记为“疑似”，不要当结论）。实际内存用量不可见时写“先确认”，不要编数据。",
         f"CPU 是否被限流用{TOOL}查看；若调用方报 DeadlineExceeded 且本服务日志显示请求处理慢/排队，先确认是否存在 CPU 限流（没有读数支持时只能标记为“疑似”，不要当结论）。"
         "内存用量也用这个工具看，看不到时写“先确认”，不要编数据。"),
    ],
}


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(SRC, OUT)
    count = 0
    for skill, edits in EDITS.items():
        path = OUT / skill / "SKILL.md"
        with open(path, encoding="utf-8", newline="") as f:   # keep the files' own line endings (LF) on Windows too
            text = f.read()
        for old, new in edits:
            assert text.count(old) == 1, f"{skill}: expected one occurrence of {old[:40]}…"
            text = text.replace(old, new)
            count += 1
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    print(f"{OUT.relative_to(REPO)}: {count} sentences changed in {len(EDITS)} skills")


if __name__ == "__main__":
    main()
