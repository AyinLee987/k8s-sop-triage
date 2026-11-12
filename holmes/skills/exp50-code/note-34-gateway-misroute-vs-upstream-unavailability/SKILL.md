---
name: note-34-gateway-misroute-vs-upstream-unavailability
description: "Team experience note: Gateway Misroute vs Upstream Unavailability"
---

# Gateway Misroute vs Upstream Unavailability

When an entry point returns 404 while the gateway process is healthy and the intended backend pods/endpoints are Ready on the expected port, inspect the route predicate and load-balanced target; a 404 emitted before any downstream request log indicates misrouting rather than backend failure.
Distinguish from upstream unavailability by confirming target endpoints exist, are selected, and receive no request for the failing path; rule out pod crash/restart, readiness failure, port mismatch, service-discovery failure, and network policy first.
Also rule out application-level 404, auth/TLS/WAF rejection, stale gateway config/cache, and client URL/base-path error before concluding misroute.
Confirm the corrected route sends traffic to the intended backend and restores availability.
