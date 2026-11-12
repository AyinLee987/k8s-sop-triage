---
name: note-30-healthy-pods-503s-check-application-code-before
description: "Team experience note: Healthy Pods + 503s: Check Application Code Before Infrastructure"
---

# Healthy Pods + 503s: Check Application Code Before Infrastructure

When availability disruption shows all pods Running/Ready with no restarts and Services have endpoints, suspect the application request path before infrastructure.
Distinguishing evidence: 503s clustered on business routes, panic/parameter-mismatch log patterns, and source showing an unset required parameter or missing default.
Rule out pod crash/OOM/restart, endpoint/port mismatch, resource pressure, downstream dependency outage, network/DNS, and ingress/routing faults before concluding.
Confirm by tracing the log panic to the exact handler parameter and verifying the failure follows the code path, then classify as an application code defect.
