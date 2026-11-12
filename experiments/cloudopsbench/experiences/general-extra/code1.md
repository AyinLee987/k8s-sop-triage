APPLICATION-CODE DELAY DIAGNOSIS FOR ISOLATED LATENCY DEGRADATION
When one service shows p90 latency inflation and callers report timeouts/5xx but its pods are Running with stable restarts, endpoints resolve, and it emits no error logs, suspect in-process artificial or blocking delay before infrastructure.
Distinguishing evidence: latency is confined to that service’s own handler path while downstream dependency latency, node CPU/memory, network/DNS, and traffic metrics remain normal; source inspection confirms an explicit sleep/delay.
Rule out first: downstream slowness, resource saturation/eviction, deployment or config change, network/DNS faults, and traffic surge; confirm by comparing upstream vs downstream latency and reproducing the delay in code, not by aggregate latency alone.
