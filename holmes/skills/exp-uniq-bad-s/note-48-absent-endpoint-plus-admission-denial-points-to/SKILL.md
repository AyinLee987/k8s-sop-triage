---
name: note-48-absent-endpoint-plus-admission-denial-points-to
description: "Team experience note: Absent endpoint plus admission denial points to quota-blocked workload creation, not runtime failure."
---

# Absent endpoint plus admission denial points to quota-blocked workload creation, not runtime failure.

For partial unreachability, separate “no pod exists/selected” from “pod exists but is unhealthy” by comparing desired replicas, pod inventory, Service selector, and Endpoints.
A stable ClusterIP with Endpoints <none>, a missing expected pod while peers are Ready, and connection refusal to the Service IP indicate the backend was never admitted.
Distinguish from crashloop/image-pull/readiness faults that leave a pod object, selector mismatch that leaves Ready pods unselected, port/DNS/network faults that leave endpoints populated, and node pressure that yields Pending/scheduling events.
Before concluding, verify a FailedCreate/forbidden event citing namespace quota used versus hard limit, and confirm no Pending/Evicted pod or endpoint-selector mismatch.
