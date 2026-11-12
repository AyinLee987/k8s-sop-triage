---
name: note-13-title-verify-service-protocol-port-before-blamin
description: "Team experience note: Title: Verify Service protocol/port before blaming workload health"
---

# Title: Verify Service protocol/port before blaming workload health

When availability fails but pods are Running and clients get connection refused/timeouts to a ClusterIP, compare Service port/protocol/targetPort with the container listener and probe protocol.
A routing fault is supported when the Service transport differs from the app’s expected transport (e.g., UDP Service for TCP/gRPC) and a direct connectivity check to that Service port fails while backends stay healthy.
Rule out pod crash/readiness failures, empty or misselected endpoints, DNS failure, NetworkPolicy/firewall drops, incorrect targetPort, and an app not listening before concluding protocol mismatch.
Distinguishing evidence is configuration-level transport mismatch plus healthy backend, not client errors alone.
