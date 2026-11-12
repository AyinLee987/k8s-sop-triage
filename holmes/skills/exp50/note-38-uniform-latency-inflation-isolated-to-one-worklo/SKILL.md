---
name: note-38-uniform-latency-inflation-isolated-to-one-worklo
description: "Team experience note: Uniform Latency Inflation Isolated to One Workload Points to Injected Network Delay"
---

# Uniform Latency Inflation Isolated to One Workload Points to Injected Network Delay

When one workload's latency shifts by a large, roughly constant offset across all percentiles while its restarts, readiness, error rate and CPU/memory stay normal, treat injected network delay on that pod's network path as the leading hypothesis over load, code, or dependency failure.
Distinguishing evidence: the whole latency distribution moves together (p50 and p90 rise by similar absolute amounts, not a heavy tail), the delay is attributable to the pod itself rather than to its downstream dependencies, and callers show the same offset only on paths that traverse that pod.
Rule out first: CPU/memory throttling or node pressure, slow downstream dependencies (DB, cache, broker), GC or thread-pool saturation, pod restarts/evictions, and error/timeout failures; also rule out a node-scoped network fault, which would degrade co-located workloads too.
Confirm by comparing per-replica latency within the same service and verifying that co-located pods and paths not traversing the suspect pod remain normal.
