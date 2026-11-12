---
name: note-04-diagnose-path-specific-503s-as-application-code
description: "Team experience note: Diagnose Path-Specific 503s as Application Code Defects"
---

# Diagnose Path-Specific 503s as Application Code Defects

When availability disruption is confined to specific endpoints/actions while pods stay Ready with stable restarts, services have valid endpoints, and downstream dependencies are healthy, suspect an application-level defect in the entry handler.
Distinguishing evidence: low overall error ratio with failures concentrated on one handler/business action, no broad connection/timeout/restart pattern, and source inspection showing swapped positional arguments or a call-signature mismatch.
Before concluding, rule out pod crash/OOM/resource pressure, service/endpoint/port misconfiguration, DNS/network/quota limits, and downstream/database latency or failure.
Confirm by matching the caller's argument order/types to the callee's contract and verifying the path-specific failures disappear after correction.
