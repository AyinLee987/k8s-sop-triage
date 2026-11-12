---
name: note-46-title-isolate-partial-unreachability-to-node-lev
description: "Team experience note: Title: Isolate partial unreachability to node-level agent failure"
---

# Title: Isolate partial unreachability to node-level agent failure

When only some service endpoints are unreachable, map missing endpoints to pods and nodes; if Running pods on one node are absent from endpoints while peers on other nodes remain present, suspect node agent/readiness failure before service config.
Confirm the node is Unknown/NotReady with stale lease and unreachable taints, and verify the kubelet unit is inactive/failed; a dead kubelet stops readiness/endpoint updates and can evict that node’s backends.
Distinguish from pod crashes/readiness failures (pods would not be Running/Ready), selector/port misconfig (all replicas affected independent of node), and CNI/DNS/network-policy faults (populated endpoints or cross-node connectivity errors instead).
Rule out control-plane/API-server outage, whole-node power/OS/network partition, and resource-pressure eviction before attributing the symptom to kubelet; direct node/systemd access separates kubelet failure from node unreachability.
Before concluding, verify peer nodes still serve endpoints, kubelet recovery repopulates endpoints, and no workload- or service-level cause remains.
