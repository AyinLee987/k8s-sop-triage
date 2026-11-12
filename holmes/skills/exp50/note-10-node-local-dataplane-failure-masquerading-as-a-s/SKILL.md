---
name: note-10-node-local-dataplane-failure-masquerading-as-a-s
description: "Team experience note: Node-Local Dataplane Failure Masquerading as a Service Outage"
---

# Node-Local Dataplane Failure Masquerading as a Service Outage

Symptom is connectivity failure confined to clients on one node while the same service is reachable from other nodes, with pods Running/Ready and endpoints correctly populated — this points to the node's packet-forwarding agent (kube-proxy/CNI), not the workload.
Confirm with a cross-node differential connectivity test (same service/port from multiple nodes: success elsewhere, failure localized) and then verify that node's networking agent unit shows inactive/dead/killed rather than a config or crash-loop error.
Rule out before concluding: pod crash/readiness failures, service selector/port mismatch, empty or stale endpoints, DNS resolution faults, control-plane/apiserver degradation, node NotReady/taints/resource pressure, and network policy or application-level errors.
Key discriminator: healthy pods + healthy endpoints + healthy control plane isolate the fault to the per-node datapath, since any of those failing would break all nodes, not one.
