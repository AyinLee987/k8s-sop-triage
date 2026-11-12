---
name: note-14-diagnosing-widespread-latency-with-healthy-pods
description: "Team experience note: Diagnosing Widespread Latency With Healthy Pods"
---

# Diagnosing Widespread Latency With Healthy Pods

When many services show simultaneous RPS drops and p90 latency spikes while all pods are Running/Ready with zero restarts, suspect a shared upstream/entry pod whose network delay slows every call path rather than independent backend faults.
Localize with pod-scoped network latency/RTT: the delay should be confined to one pod while its node, sibling pods, and downstream services remain healthy with no error/exception surge.
Distinguish from code/resource saturation, downstream DB/cache slowdown, node pressure, restarts, DNS failure, packet loss, and broad congestion—those show restarts, errors, node-wide symptoms, or loss rather than isolated RTT inflation.
Before concluding, rule out pod restarts/readiness failures, node CPU/memory/disk/network saturation, service-specific error spikes, downstream dependency latency, and client/ingress issues; confirm with a pod-scoped delay metric or controlled latency test.
