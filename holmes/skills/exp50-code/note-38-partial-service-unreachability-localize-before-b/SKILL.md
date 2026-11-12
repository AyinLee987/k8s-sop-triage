---
name: note-38-partial-service-unreachability-localize-before-b
description: "Team experience note: Partial Service Unreachability: Localize Before Blaming Workloads"
---

# Partial Service Unreachability: Localize Before Blaming Workloads

When only clients on one node fail to reach otherwise healthy ClusterIP services, compare identical probes from multiple nodes and map failures to node locality.
Evidence for node-local service-proxy failure: pods Ready, Services and Endpoints populated, other nodes connect, one node fails across multiple services with reset/punt-style errors, and the node is Ready without pressure.
Rule out pod readiness or endpoint mismatch, Service selector/port/targetPort errors, DNS, NetworkPolicy, CNI/overlay or node-network failure, and control-plane/API health before concluding the node proxy is down.
Confirm with node proxy process/service state, rules, and logs; a stopped proxy with healthy pods/endpoints and node-local-only failures is diagnostic.
