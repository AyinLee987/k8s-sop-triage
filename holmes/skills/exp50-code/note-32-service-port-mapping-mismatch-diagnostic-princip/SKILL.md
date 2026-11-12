---
name: note-32-service-port-mapping-mismatch-diagnostic-princip
description: "Team experience note: Service Port Mapping Mismatch Diagnostic Principle"
---

# Service Port Mapping Mismatch Diagnostic Principle

When clients fail against a Service while DNS and ClusterIP reachability are intact, compare Service port/targetPort, Endpoints/EndpointSlice port, and backend container’s declared/listening port; an endpoint port that differs from the actual backend listen port is the distinguishing evidence.
Confirm affected pods are Ready and selector-matched so endpoints exist, and confirm clients dial the Service port rather than assuming the endpoint is wrong.
Rule out zero endpoints, selector mismatch, DNS failure, NetworkPolicy/firewall drops, and backend crash/overload before concluding a port-mapping fault.
A Running/Ready backend with correct DNS and repeated connection failures to the Service port, plus a Service-vs-Endpoint-vs-container port discrepancy, indicates routing misconfiguration rather than application failure.
