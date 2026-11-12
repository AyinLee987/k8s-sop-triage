---
name: note-44-app-layer-5xx-with-healthy-infrastructure-verify
description: "Team experience note: App-Layer 5xx With Healthy Infrastructure: Verify Call Contracts Before Blaming Dependencies"
---

# App-Layer 5xx With Healthy Infrastructure: Verify Call Contracts Before Blaming Dependencies

When availability disruption shows as entry-service 5xx while pods are Ready, Services resolve, and resource metrics are normal, suspect an application call-contract defect first.
Distinguishing evidence: failures cluster on specific business actions/routes, downstreams are reachable and healthy, and source review shows a wrong argument/parameter order or mismatched call signature.
Rule out pod restarts/resource saturation, DNS/network/service-endpoint failures, downstream outages or latency, config/env errors, and misleading log-volume anomalies before concluding code defect.
Do not treat the highest downstream error count as causal; correlate the failing request path to the exact code path and verify the call/signature.
