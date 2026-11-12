---
name: note-26-diagnostic-principle-distinguish-cpu-bound-busy
description: "Team experience note: Diagnostic Principle: Distinguish CPU-Bound Busy-Loop Degradation from Shared Resource or Dependency Latency"
---

# Diagnostic Principle: Distinguish CPU-Bound Busy-Loop Degradation from Shared Resource or Dependency Latency

Treat as a code busy-loop when one service shows a sharp latency jump with CPU saturation and CFS throttling while its pod remains Running/Ready and its downstream dependencies are not the primary latency source.
Use dependency topology to separate origin from propagation: callers and sibling dependents may slow, but the common downstream service with CPU throttling and disproportionately inflated latency is the origin.
Confirm with source review for unbounded loops, hot-path spinning, or retry logic without blocking; correlate with recent restart/deploy age and absence of sustained node-wide CPU pressure.
Before concluding, rule out node-level contention/noisy neighbor, traffic surge, memory/GC pressure, network/DNS errors, downstream database or service latency, configuration changes, and readiness/restart churn.
