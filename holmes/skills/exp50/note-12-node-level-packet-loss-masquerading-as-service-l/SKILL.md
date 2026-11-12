---
name: note-12-node-level-packet-loss-masquerading-as-service-l
description: "Team experience note: Node-Level Packet Loss Masquerading as Service Latency"
---

# Node-Level Packet Loss Masquerading as Service Latency

When latency rises across multiple unrelated pods sharing a node, pods stay Running without restarts, and inbound/outbound packet rates drop simultaneously, suspect node-level packet loss over per-service code or dependency latency.
Confirm with node NIC drop/error/retransmit metrics and by comparing co-located pods against the same workloads on unaffected nodes.
Rule out pod CPU/memory throttling or OOM, application error spikes, downstream/database latency, node CPU/disk pressure, and legitimate traffic reduction before concluding packet loss.
The distinguishing pattern is multi-service co-located degradation plus bidirectional packet-rate drop with healthy pod status, not isolated single-service slowness or global traffic change.
