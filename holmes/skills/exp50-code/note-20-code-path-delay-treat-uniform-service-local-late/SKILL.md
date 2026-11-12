---
name: note-20-code-path-delay-treat-uniform-service-local-late
description: "Team experience note: Code-Path Delay: Treat uniform service-local latency inflation as application delay only after dependency and infrastructure causes are excluded."
---

# Code-Path Delay: Treat uniform service-local latency inflation as application delay only after dependency and infrastructure causes are excluded.

When one service’s own latency rises by orders of magnitude while pods stay Running/Ready with stable restarts, low error ratio, and normal resource health, suspect an artificial delay in its request handler.
Distinguishing evidence: p50 and p90 inflate together to a plateau-like value, callers show downstream timeout/cancel symptoms, no concurrent dependency latency appears, and source contains an explicit sleep/wait.
Rule out downstream RPC/database latency, network/DNS/load-balancer delay, CPU/memory/GC/thread-pool saturation, pod restarts/readiness failures, and error/retry storms before concluding.
Confirm by correlating onset with code/deployment change and, if safe, bypassing/removing the delay and observing immediate latency return to baseline without infrastructure changes.
