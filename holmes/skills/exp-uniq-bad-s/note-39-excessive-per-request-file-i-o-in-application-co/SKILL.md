---
name: note-39-excessive-per-request-file-i-o-in-application-co
description: "Team experience note: Excessive per-request file I/O in application code"
---

# Excessive per-request file I/O in application code

When latency degradation coincides with pod restarts/OOM and no downstream errors, inspect request-handler code for repeated synchronous file reads/parsing in the hot path before blaming infrastructure.
Distinguishing evidence: restart/OOM pattern plus latency spike, normal startup/no application error logs, and source-level file I/O per request; dependency and traffic metrics do not explain the slowdown.
Rule out node pressure, CPU throttling, memory leak/cache growth, downstream latency, traffic surge, and recent config/resource-limit changes.
Conclude only after correlating the code-level I/O path with observed latency/restarts and proving limits, dependencies, and load are insufficient causes.
