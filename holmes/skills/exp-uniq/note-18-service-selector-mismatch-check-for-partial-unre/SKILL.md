---
name: note-18-service-selector-mismatch-check-for-partial-unre
description: "Team experience note: Service Selector Mismatch Check for Partial Unreachability"
---

# Service Selector Mismatch Check for Partial Unreachability

Verify Service Endpoints/EndpointSlices are populated; empty endpoints with Running/Ready backing pods indicates routing/selector configuration, not pod failure.
Confirm the Service selector exactly matches pod template labels and that targetPort/port/protocol/name match container ports.
Rule out pod NotReady/crashloop, failed readiness, wrong targetPort/named port, DNS failure, NetworkPolicy/firewall/CNI issues, and kube-proxy routing failure before concluding.
Distinguishing evidence: connection refused to a valid Service ClusterIP with `ENDPOINTS <none>` while healthy pods exist and other services resolve/have endpoints isolates selector mismatch from broader network/DNS faults.
