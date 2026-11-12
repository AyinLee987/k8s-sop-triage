Diagnostic Principle: Uniform Endpoint Latency Across Replicas After a Rollout Implicates Application Code
When an endpoint slows or times out while all pods are Running/Ready with no restarts or warning events, compare latency across every replica and determine whether it is uniform rather than pod- or node-local.
Uniform latency on all replicas, no application errors, healthy and normally fast dependencies/consumers, unchanged resources/config, and onset immediately after an image rollout (reversed by rollback) distinguish an application code regression from infrastructure causes.
Before concluding, rule out dependency latency or backlog, resource limits/throttling, node/network faults, config changes, and traffic spikes; confirm latency follows the deployed image version, not individual pods or nodes.
Treat deployment-time correlation plus rollback reversal as decisive evidence, then inspect the changed request path for synchronous waiting on asynchronous work.
