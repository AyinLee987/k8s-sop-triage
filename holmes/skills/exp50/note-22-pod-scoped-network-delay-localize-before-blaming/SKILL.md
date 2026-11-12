---
name: note-22-pod-scoped-network-delay-localize-before-blaming
description: "Team experience note: Pod-Scoped Network Delay: Localize Before Blaming Application or Dependency"
---

# Pod-Scoped Network Delay: Localize Before Blaming Application or Dependency

Suspect pod-network delay when a single pod shows extreme latency inflation, throughput collapse, and success-rate drop while remaining Running with no restarts and healthy sibling pods/services.
Distinguish it from application faults and resource saturation by confirming normal CPU/memory/disk, no restart/OOM, no matching application exceptions, and latency present on network path but not in local processing.
Rule out node-wide network impairment, service-wide deploy/config changes, downstream dependency/database slowness, and client-side or load-balancer effects before concluding pod-network delay.
If multiple pods on the same node degrade, treat as node/network-plane fault; if only application error logs or resource metrics worsen, treat as application or capacity fault instead.
