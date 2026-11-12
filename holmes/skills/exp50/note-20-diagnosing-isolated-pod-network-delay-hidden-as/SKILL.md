---
name: note-20-diagnosing-isolated-pod-network-delay-hidden-as
description: "Team experience note: Diagnosing isolated pod network delay hidden as service latency"
---

# Diagnosing isolated pod network delay hidden as service latency

When p50/p90 latency jumps by orders of magnitude while pods remain Running/Ready with stable restarts, endpoints stay populated, and there is no CPU/memory saturation, CFS throttling, traffic surge, downstream failure, or deployment churn, suspect pod-level network delay over resource exhaustion.
Distinguishing evidence is latency inflation scoped to one workload/pod at normal request rates, whereas saturation shows resource pressure, traffic causes show pps/connection spikes, and crash/routing causes show restarts, empty endpoints, or DNS/service errors.
Before concluding, rule out pod/node resource pressure, CFS throttling, traffic anomalies, downstream dependency latency, DNS/service routing failure, and readiness/restart loss.
Confirm with pod-scoped network RTT/loss/retransmit or tc/chaos evidence and correlate the same latency from clients on other nodes.
