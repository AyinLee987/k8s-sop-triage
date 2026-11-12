---
name: note-06-gateway-route-mismatch-vs-backend-unavailability
description: "Team experience note: Gateway Route Mismatch vs Backend Unavailability"
---

# Gateway Route Mismatch vs Backend Unavailability

For partial unreachability, verify each layer: pod readiness, Service selector/endpoints, DNS/registration, then gateway/ingress route config and access logs.
A route-specific 404/Not Found while other routes work, with healthy target pods and populated endpoints, points to gateway/ingress misrouting—not backend failure.
Rule out pod crashes/readiness failures, empty or mismatched endpoints, DNS/discovery gaps, network-policy/ingress errors, and backend 5xx/timeouts before concluding misrouting.
Confirm by matching the failing request path to gateway route definitions and comparing successful versus failing routes.
