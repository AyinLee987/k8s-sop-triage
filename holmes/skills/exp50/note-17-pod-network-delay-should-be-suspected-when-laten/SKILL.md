---
name: note-17-pod-network-delay-should-be-suspected-when-laten
description: "Team experience note: Pod-network delay should be suspected when latency rises while pods remain healthy."
---

# Pod-network delay should be suspected when latency rises while pods remain healthy.

When multiple callers show simultaneous latency inflation and pps drops while all pods stay Running/Ready with zero restarts, suspect a per-pod network delay on one dependency rather than service-wide compute or code failure.

Distinguishing evidence: caller latency is elevated but the affected pod’s CPU/memory/restart metrics are normal, and packet-rate reduction spans dependent services rather than error spikes localized to one service.

Rule out upstream load surge, application exceptions, resource saturation/OOM, crash loops, downstream database/cache failure, config or mesh policy change, and node-wide network congestion before concluding pod network delay.

Confirm with pod-level network latency or trace span timing showing delay on that pod’s ingress/egress path and recovery when the network fault is removed.
