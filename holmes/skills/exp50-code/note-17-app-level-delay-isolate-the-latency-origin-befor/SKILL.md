---
name: note-17-app-level-delay-isolate-the-latency-origin-befor
description: "Team experience note: App-Level Delay: Isolate the Latency Origin Before Blaming Infrastructure"
---

# App-Level Delay: Isolate the Latency Origin Before Blaming Infrastructure

Confirm the symptom is latency-only: healthy pods, no restarts/OOM, low error ratio, and no CPU/memory/network saturation.
Trace upstream/downstream dependencies: the slowest span should originate at a service whose own call path adds delay, while its downstream dependencies remain fast and upstream callers inherit the same latency.
Inspect application code/config for explicit sleeps, artificial delays, or blocking logic in the request path; this distinguishes code defect from infrastructure, dependency, traffic-surge, retry, or GC causes.
Before concluding, rule out network latency, resource exhaustion, pod lifecycle events, downstream database/service slowness, load spikes, and recent deploy/config changes.
