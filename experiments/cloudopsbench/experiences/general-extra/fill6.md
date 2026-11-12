Node NotReady with Pending Pods: Check Kubelet Before Pod-Level Causes
When pods on one node stay Pending/ContainerCreating with no IP and the node is NotReady with unreachable taints, suspect node agent availability, not application or image faults.
Confirm when the node agent service is inactive/failed and multiple workloads on that node are affected while other nodes remain Ready.
Distinguish from image pull, scheduling/resource pressure, CNI, and app crashes, which usually show pod events/errors on a Ready node with a healthy node agent.
Rule out control-plane/API outage, network partition, and node resource exhaustion before concluding; then restore the agent and verify heartbeats and pod recovery.
