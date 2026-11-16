# SOP: pending_or_unschedulable_pod

Symptom: A pod remains Pending, Unschedulable, Init, ContainerCreating, or otherwise stalled before normal startup. This includes multiple pods on one node stuck at Init:0/1 because pod sandbox creation fails with FailedCreatePodSandBox and a missing /run/containerd/containerd.sock, while pods on other nodes are Running.

Signals:
- pod is Pending
- pod is Unschedulable
- scheduler reports no suitable nodes
- workload cannot place a pod on any node
- pod is Pending, Init, or ContainerCreating
- pod status is Init:0/1 or ContainerCreating on a node
- pod is stuck before normal container startup
- multiple pods across services colocated on one node are stuck Init:0/1 while pods on other nodes are Running
- pod events show FailedCreatePodSandBox
- pod events show dial unix /run/containerd/containerd.sock: no such file or directory
- node containerd.service is inactive or dead
- pod has no node assignment or is assigned to an unhealthy node
- Pending pod is constrained by nodeSelector or required affinity to a specific node that is Unknown or NotReady
- scheduler reports an untolerated node.kubernetes.io/unreachable or unschedulable taint on the node matching the Pod placement rule
- kubelet service on the selected node is inactive or dead while the Pod remains Pending

Start at: locate_pending_workload_context

## Steps

### Step: locate_pending_workload_context
Goal: Locate the Pending, Unschedulable, Init, or ContainerCreating pod and collect the minimum node/alert context needed to distinguish scheduler constraints from platform-component failure. When multiple pods on one node are stuck at Init:0/1 while pods on other nodes are Running, record the node colocation and proceed to inspect container-runtime and node-component health before placement constraints.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
Evidence to collect: pending_pod_identified, pending_pod_name, affected_workload, failing_service, pod_status_summary, pod_node_assignment, node_status_summary, active_alerts_summary, init_or_container_creating_pods, init_stuck_pods_on_same_node, pods_on_other_nodes_running
Next: classify_pending_blocker

### Step: classify_pending_blocker
Goal: Inspect the stalled pod once to classify scheduler constraints, storage dependency, missing scheduler progress, container-runtime failure, unhealthy-node/platform evidence, or kubelet failure. When pod events show FailedCreatePodSandBox or a containerd socket error such as dial unix /run/containerd/containerd.sock: no such file or directory, route directly to inspect_node_component_health and confirm_container_runtime_unavailable rather than selector, affinity, cordon, or taint ownership. When the pod is constrained to a matching node that is Unknown or NotReady and that node kubelet is inactive or dead, route to inspect_node_component_health and confirm_kubelet_unavailable rather than treating the unreachable or unschedulable taint as the root cause.
Actions:
- DescribeResource(resource_type=pod, name=<pending_pod>, namespace=<namespace>)
Evidence to collect: pod_events, pod_event_error_message, scheduler_failure_reason, pod_node_assignment, node_name, pod_resource_requests, pod_node_selector, pod_tolerations, insufficient_cpu_event, insufficient_memory_event, node_affinity_mismatch_indicated, pod_anti_affinity_conflict_suspected, untolerated_taint_event, unschedulable_nodes_event, unbound_pvc_identified, container_runtime_failure_suspected, failed_create_pod_sandbox, containerd_socket_dial_error, containerd_socket_missing, init_stuck_pods_on_same_node, scheduler_unavailability_suspected, node_level_failure_suspected, matching_node_unknown_or_not_ready, matching_node_kubelet_inactive_candidate
Next: confirm_pod_resource_capacity_mismatch, inspect_node_placement_constraints, inspect_pod_anti_affinity_rule, inspect_node_component_health, confirm_container_runtime_unavailable when pod events directly show containerd socket missing, confirm_scheduler_availability

### Step: confirm_pod_resource_capacity_mismatch
Goal: Confirm CPU or memory capacity mismatch only when the Pod request exceeds the allocatable capacity of every node that is otherwise eligible: label constraints match, the node is schedulable and healthy, and relevant taints are tolerated. An insufficient-memory phrase in an aggregated scheduler event is not enough when a cordon, selector, affinity, taint, unhealthy-node blocker, or container-runtime failure independently explains the Pod.
Actions:
- GetAppYAML(app_name=<failing_service>)
- GetClusterConfiguration()
Evidence to collect: pod_cpu_request, pod_memory_request, otherwise_eligible_nodes, eligible_node_allocatable_resources, eligible_nodes_healthy, cpu_capacity_mismatch_confirmed, memory_capacity_mismatch_confirmed
Next: (end of this SOP - conclude here)

### Step: inspect_node_placement_constraints
Goal: Inspect the workload placement rules and cluster nodes together to distinguish node selector, required node affinity, node cordon, ordinary taint/toleration blockers, and an unhealthy selected node. A selector or affinity rule is not the root cause when at least one node matches it but that matching node is cordoned, has an untolerated non-unreachable taint, or is Unknown/NotReady due to kubelet failure. Do not use this branch when pod events show FailedCreatePodSandBox or a containerd socket failure on a node hosting multiple Init:0/1 pods; route to inspect_node_component_health and confirm_container_runtime_unavailable first. If the matching node is Unknown/NotReady and its kubelet is inactive or dead, route to inspect_node_component_health and confirm_kubelet_unavailable rather than classifying taint_toleration_mismatch or node_network_packet_loss.
Actions:
- GetAppYAML(app_name=<failing_service>)
- GetClusterConfiguration()
Evidence to collect: workload_node_selector, workload_required_node_affinity, workload_tolerations, node_labels, node_taints, node_spec_unschedulable, selector_matching_nodes, affinity_matching_nodes, matching_node_status, matching_node_unknown_or_not_ready, matching_node_kubelet_unavailable_candidate, unreachable_taint_is_consequence, matching_node_blocker
Next: confirm_node_selector_mismatch, confirm_node_affinity_mismatch, confirm_node_cordon_status, confirm_taint_toleration_mismatch, inspect_node_component_health

### Step: confirm_node_selector_mismatch
Goal: Confirm node_selector_mismatch only when the Pod has an explicit nodeSelector and no cluster node carries the required key/value combination. If a matching node exists but is cordoned, blocked by a taint, Unknown/NotReady due to inactive kubelet, or has a failed container runtime causing Init:0/1 sandbox errors, selector mismatch is refuted and the appropriate blocker or component-health branch applies.
Actions:
- GetAppYAML(app_name=<failing_service>)
- GetClusterConfiguration()
Evidence to collect: workload_node_selector, node_labels, selector_matching_nodes, matching_node_health_status, matching_node_kubelet_status, node_selector_mismatch_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_node_affinity_mismatch
Goal: Confirm node_affinity_mismatch only when no node satisfies the workload requiredDuringScheduling node-affinity expression. If a matching node exists but is cordoned, blocked by a taint, Unknown/NotReady due to inactive kubelet, or has a failed container runtime causing Init:0/1 sandbox errors, affinity mismatch is refuted and the appropriate blocker or component-health branch applies.
Actions:
- GetAppYAML(app_name=<failing_service>)
- GetClusterConfiguration()
Evidence to collect: required_node_affinity, node_labels, affinity_matching_nodes, matching_node_health_status, matching_node_kubelet_status, node_affinity_mismatch_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_node_cordon_status
Goal: Confirm node_cordon_mismatch when every node that satisfies the Pod selector or required affinity is administratively unschedulable through spec.unschedulable=true, SchedulingDisabled, or node.kubernetes.io/unschedulable:NoSchedule. This Kubernetes cordon marker is not an ordinary taint/toleration mismatch and is not the same as a node becoming Unknown because kubelet is inactive or dead, nor is it a container-runtime failure that leaves pods Init:0/1.
Actions:
- GetClusterConfiguration()
Evidence to collect: placement_matching_nodes, node_spec_unschedulable, unschedulable_taint_present, node_health_status, node_cordon_confirmed, kubelet_unavailable_not_cordon, containerd_unavailable_not_cordon
Next: (end of this SOP - conclude here)

### Step: inspect_pod_anti_affinity_rule
Goal: Inspect required pod anti-affinity, its labelSelector/topologyKey, desired replicas, current matching-Pod distribution, and schedulable topology domains. Merely finding an anti-affinity rule is not confirmation; the rule must leave too few valid placements for the desired replicas. Do not use this branch when pods are already assigned to a node but remain Init:0/1 because their sandbox cannot be created.
Actions:
- GetAppYAML(app_name=<failing_service>)
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- GetClusterConfiguration()
Evidence to collect: required_pod_anti_affinity, anti_affinity_label_selector, topology_key, desired_replicas, matching_pod_distribution, schedulable_topology_domains
Next: confirm_pod_anti_affinity_capacity

### Step: confirm_pod_anti_affinity_capacity
Goal: Confirm pod_anti_affinity_conflict only when required anti-affinity and the current matching-Pod distribution consume every schedulable topology domain, leaving no valid placement for the remaining desired replica.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- GetClusterConfiguration()
Evidence to collect: desired_replicas, schedulable_topology_domains, occupied_topology_domains, pending_replica_without_valid_domain, pod_anti_affinity_conflict_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_taint_toleration_mismatch
Goal: Confirm taint_toleration_mismatch only when a placement-matching schedulable and healthy node carries a non-cordon, non-unreachable taint that the Pod does not tolerate. Never classify node.kubernetes.io/unschedulable:NoSchedule or SchedulingDisabled as this root cause; those are node cordon. Never classify node.kubernetes.io/unreachable or an Unknown/NotReady node caused by inactive or dead kubelet as taint_toleration_mismatch; that unreachable taint is a consequence of node/kubelet unavailability. If the matching node is Unknown/NotReady and kubelet is inactive or dead, use confirm_kubelet_unavailable and exclude taint_toleration_mismatch and node_network_packet_loss from the top predictions. If pods on a node are assigned but stuck Init:0/1 with FailedCreatePodSandBox or a missing containerd socket, use confirm_container_runtime_unavailable and exclude taint_toleration_mismatch.
Actions:
- GetClusterConfiguration()
- GetAppYAML(app_name=<failing_service>)
- CheckNodeServiceStatus(node_name=<placement_matching_node>, service_name=kubelet) when the matching node is Unknown or NotReady
Evidence to collect: placement_matching_node, placement_matching_node_health, non_cordon_node_taint, unreachable_taint_present, matching_node_unknown_or_not_ready, matching_node_kubelet_inactive_or_dead, unreachable_taint_consequence_not_root_cause, taint_toleration_mismatch_excluded, node_network_packet_loss_excluded_without_retransmission, pod_tolerations, taint_toleration_mismatch_confirmed
Next: (end of this SOP - conclude here) when a genuine non-cordon non-unreachable taint mismatch is confirmed; otherwise continue to inspect_node_component_health, confirm_container_runtime_unavailable, or confirm_kubelet_unavailable

### Step: inspect_node_component_health
Goal: Check containerd and kubelet service health once on the affected node to distinguish container-runtime failure, kubelet failure, and node-agent failure. When multiple pods on the same node are stuck Init:0/1 with FailedCreatePodSandBox and a dial error for /run/containerd/containerd.sock, treat containerd as the primary suspect and check containerd first. If containerd.service is inactive or dead, route to confirm_container_runtime_unavailable and do not attribute this to node_network_packet_loss, node_network_delay, taint_toleration_mismatch, or kubelet_unavailable absent separate kubelet evidence. If containerd is healthy and kubelet is inactive or dead with the node Unknown or NotReady, route to confirm_kubelet_unavailable. Do not attribute either case to node_network_packet_loss without node-aligned TCP retransmission evidence, and do not attribute it to taint_toleration_mismatch when an unreachable taint is a consequence of node/kubelet or runtime failure.
Actions:
- CheckNodeServiceStatus(node_name=<affected_node>, service_name=containerd)
- CheckNodeServiceStatus(node_name=<affected_node>, service_name=kubelet)
Evidence to collect: containerd_health_status, containerd_service_inactive_or_dead, kubelet_health_status, kubelet_inactive_or_dead, node_readiness_status, node_status_unknown_or_not_ready, node_taints, unreachable_taint_consequence, pods_stuck_init_on_affected_node, failed_create_pod_sandbox, containerd_socket_dial_error, containerd_socket_missing, pods_on_other_nodes_running, runtime_log_errors, containerd_unavailable_supported, kubelet_unavailable_supported, taint_toleration_mismatch_excluded, node_network_packet_loss_excluded_without_retransmission, node_network_delay_excluded_without_latency
Next: confirm_container_runtime_unavailable, confirm_kubelet_unavailable

### Step: confirm_container_runtime_unavailable
Goal: Confirm container_runtime_unavailable when pod events show FailedCreatePodSandBox because the containerd socket is missing or cannot be dialed, and the node containerd.service is inactive or dead. This is the single leading root cause for multiple pods stuck Init:0/1 on that node, even when the reported symptom is generic service availability disruption. Do not rank kubelet_unavailable as a competing top prediction when kubelet is not separately shown inactive or dead and containerd is directly confirmed down; do not rank node_network_delay or node_network_packet_loss without node-aligned latency or retransmission evidence. If containerd is healthy and kubelet is inactive or dead, use confirm_kubelet_unavailable instead.
Actions:
- (No additional actions; use pod events and node service status already collected.)
Evidence to collect: pod_events_failed_create_pod_sandbox, containerd_socket_dial_error, containerd_socket_missing, containerd_service_inactive_or_dead, pods_stuck_init_on_affected_node, pods_on_other_nodes_running, container_runtime_unavailable_confirmed, kubelet_unavailable_excluded_without_independent_kubelet_failure, node_network_delay_excluded_without_node_aligned_latency, node_network_packet_loss_excluded_without_retransmission
Next: (end of this SOP - conclude here)

### Step: confirm_scheduler_availability
Goal: Confirm whether the kube-scheduler is unavailable and explains the Pending pod. Do not use this branch when pods are assigned to a node and stuck Init:0/1 because of a containerd or kubelet failure.
Actions:
- CheckNodeServiceStatus(node_name=master, service_name=kube-scheduler)
Evidence to collect: scheduler_service_status, root_cause_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_kubelet_unavailable
Goal: Confirm kubelet_unavailable as the cause of a Pending pod when the pod is constrained by selector or affinity to a node that is Unknown or NotReady and the kubelet service on that node is inactive or dead. The scheduler-visible node.kubernetes.io/unreachable or unschedulable taint is a consequence of the node/kubelet failure, not a separate taint_toleration_mismatch root cause. Exclude node_network_packet_loss unless there is separate node-aligned TCP retransmission evidence. If pod events directly show FailedCreatePodSandBox with a missing containerd socket and containerd.service is inactive or dead, conclude container_runtime_unavailable instead and do not list kubelet_unavailable as a competing top prediction absent separate kubelet failure evidence. Conclude kubelet_unavailable as the single leading root cause for this incident only when the matching node is Unknown/NotReady and kubelet is inactive or dead and containerd is not the directly confirmed failing runtime.
Actions:
Evidence to collect: root_cause_confirmation, pending_pod_name, pending_pod_node_selector_or_affinity, selected_node, selected_node_status_unknown_or_not_ready, selected_node_kubelet_inactive_or_dead, pod_pending_due_to_node_unavailable, unreachable_taint_consequence, taint_toleration_mismatch_excluded, node_network_packet_loss_excluded_without_retransmission, containerd_unavailable_exclusion_from_kubelet, kubelet_unavailable_confirmed
Next: (end of this SOP - conclude here)
