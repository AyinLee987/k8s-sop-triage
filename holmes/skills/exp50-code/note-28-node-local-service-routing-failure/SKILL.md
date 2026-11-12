---
name: note-28-node-local-service-routing-failure
description: "Team experience note: Node-Local Service Routing Failure"
---

# Node-Local Service Routing Failure

When a ClusterIP:port is unreachable from workloads on one node but succeeds from other nodes and endpoints are present/ready, suspect a node-local service proxy/dataplane fault, not a service or backend failure.
Distinguishing evidence: healthy pods/endpoints and correct service port, node Ready, failure isolated to one node, and that node’s kube-proxy or CNI service inactive/repeatedly failing.
Rule out first: empty/missing endpoints or unready backends, service selector/port mismatch, NetworkPolicy/DNS issues, backend pod failure, node NotReady/kubelet failure, and cluster-wide network disruption.
Confirm by checking node-local service-agent status/logs and repeating the same connectivity probe from multiple nodes.
