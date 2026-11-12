---
name: note-42-diagnostic-principle-prove-application-internal
description: "Team experience note: Diagnostic Principle: Prove application-internal memory growth before attributing crash-loop availability loss to a code leak."
---

# Diagnostic Principle: Prove application-internal memory growth before attributing crash-loop availability loss to a code leak.

Correlate increasing restarts/CrashLoopBackOff with per-container memory/RSS trend, prior-container termination reason, and clean startup then abrupt stop; sustained growth recurring after restart with no node pressure or dependency errors points to an application leak.
Distinguish from undersized memory limits, node pressure/eviction, probe/startup misconfiguration, bad image/config rollout, downstream dependency failures, and sidecar/proxy resource issues.
Rule those out by checking node memory/eviction events, limit headroom and historical stable usage at the same limit, probe settings, rollout diff, dependency health, and sidecar status.
Confirm with source review or heap/goroutine profiling showing retained allocations on request paths without release.
