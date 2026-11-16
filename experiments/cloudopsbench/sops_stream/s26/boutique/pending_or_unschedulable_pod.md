# SOP: pending_or_unschedulable_pod

Symptom: A pod remains Pending, Unschedulable, Init, ContainerCreating, or otherwise stalled before normal startup.

Signals:
- pod is Pending
- pod is Unschedulable
- scheduler reports no suitable nodes
- workload cannot place a pod on any node
- pod is Pending, Init, or ContainerCreating
- pod is stuck before normal container startup
- pod has no node assignment or is assigned to an unhealthy node

Start at: locate_pending_workload_context

## Steps

### Step: locate_pending_workload_context
Goal: Locate the Pending, Unschedulable, Init, or ContainerCreating pod and collect the minimum node/alert context needed to distinguish scheduler constraints from platform-component failure.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
Evidence to collect: pending_pod_identified, pending_pod_name, affected_workload, failing_service, pod_status_summary, pod_node_assignment, node_status_summary, active_alerts_summary
Next: classify_pending_blocker

### Step: classify_pending_blocker
Goal: Inspect the stalled pod once to classify scheduler constraints, storage dependency, missing scheduler progress, container-runtime failure, or unhealthy-node/platform evidence. Do not attempt to decide selector, affinity, cordon, and taint ownership from the aggregated FailedScheduling sentence alone; route mixed node-placement evidence to the dedicated placement comparison atom.
Actions:
- DescribeResource(resource_type=pod, name=<pending_pod>, namespace=<namespace>)
Evidence to collect: pod_events, pod_event_error_message, scheduler_failure_reason, pod_node_assignment, node_name, pod_resource_requests, pod_node_selector, pod_tolerations, insufficient_cpu_event, insufficient_memory_event, node_affinity_mismatch_indicated, pod_anti_affinity_conflict_suspected, untolerated_taint_event, unschedulable_nodes_event, unbound_pvc_identified, container_runtime_failure_suspected, scheduler_unavailability_suspected, node_level_failure_suspected
Next: confirm_pod_resource_capacity_mismatch, inspect_node_placement_constraints, inspect_pod_anti_affinity_rule, inspect_node_component_health, confirm_scheduler_availability

### Step: confirm_pod_resource_capacity_mismatch
Goal: Confirm CPU or memory capacity mismatch only when the Pod request exceeds the allocatable capacity of every node that is otherwise eligible: label constraints match, the node is schedulable, and relevant taints are tolerated. An insufficient-memory phrase in an aggregated scheduler event is not enough when a cordon, selector, affinity, or taint blocker independently explains the Pod.
Actions:
- GetAppYAML(app_name=<failing_service>)
- GetClusterConfiguration()
Evidence to collect: pod_cpu_request, pod_memory_request, otherwise_eligible_nodes, eligible_node_allocatable_resources, cpu_capacity_mismatch_confirmed, memory_capacity_mismatch_confirmed
Next: (end of this SOP - conclude here)

### Step: inspect_node_placement_constraints
Goal: Inspect the workload placement rules and cluster nodes together to distinguish node selector, required node affinity, node cordon, and ordinary taint/toleration blockers. A selector or affinity rule is not the root cause when at least one node matches it but that matching node is cordoned or has an untolerated taint.
Actions:
- GetAppYAML(app_name=<failing_service>)
- GetClusterConfiguration()
Evidence to collect: workload_node_selector, workload_required_node_affinity, workload_tolerations, node_labels, node_taints, node_spec_unschedulable, selector_matching_nodes, affinity_matching_nodes, matching_node_blocker
Next: confirm_node_selector_mismatch, confirm_node_affinity_mismatch, confirm_node_cordon_status, confirm_taint_toleration_mismatch

### Step: confirm_node_selector_mismatch
Goal: Confirm node_selector_mismatch only when the Pod has an explicit nodeSelector and no cluster node carries the required key/value combination. If a matching node exists but is cordoned or blocked by a taint, selector mismatch is refuted.
Actions:
- GetAppYAML(app_name=<failing_service>)
- GetClusterConfiguration()
Evidence to collect: workload_node_selector, node_labels, selector_matching_nodes, node_selector_mismatch_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_node_affinity_mismatch
Goal: Confirm node_affinity_mismatch only when no node satisfies the workload's requiredDuringScheduling node-affinity expression. If a matching node exists but is cordoned or blocked by a taint, affinity mismatch is refuted.
Actions:
- GetAppYAML(app_name=<failing_service>)
- GetClusterConfiguration()
Evidence to collect: required_node_affinity, node_labels, affinity_matching_nodes, node_affinity_mismatch_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_node_cordon_status
Goal: Confirm node_cordon_mismatch when every node that satisfies the Pod's selector or required affinity is administratively unschedulable through spec.unschedulable=true, SchedulingDisabled, or node.kubernetes.io/unschedulable:NoSchedule. This Kubernetes cordon marker is not an ordinary taint/toleration mismatch.
Actions:
- GetClusterConfiguration()
Evidence to collect: placement_matching_nodes, node_spec_unschedulable, unschedulable_taint_present, node_cordon_confirmed
Next: (end of this SOP - conclude here)

### Step: inspect_pod_anti_affinity_rule
Goal: Inspect required pod anti-affinity, its labelSelector/topologyKey, desired replicas, current matching-Pod distribution, and schedulable topology domains. Merely finding an anti-affinity rule is not confirmation; the rule must leave too few valid placements for the desired replicas.
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
Goal: Confirm taint_toleration_mismatch only when a placement-matching schedulable node carries a non-cordon taint that the Pod does not tolerate. Never classify node.kubernetes.io/unschedulable:NoSchedule or SchedulingDisabled as this root cause; those are node cordon.
Actions:
- GetClusterConfiguration()
- GetAppYAML(app_name=<failing_service>)
Evidence to collect: placement_matching_node, non_cordon_node_taint, pod_tolerations, taint_toleration_mismatch_confirmed
Next: (end of this SOP - conclude here)

### Step: inspect_node_component_health
Goal: Check containerd and kubelet service health once on the affected node to distinguish container-runtime failure from node-agent failure.
Actions:
- CheckNodeServiceStatus(node_name=<affected_node>, service_name=containerd)
- CheckNodeServiceStatus(node_name=<affected_node>, service_name=kubelet)
Evidence to collect: containerd_health_status, kubelet_health_status, node_readiness_status, runtime_log_errors, containerd_unavailable_supported, kubelet_unavailable_supported
Next: confirm_container_runtime_unavailable, confirm_kubelet_unavailable

### Step: confirm_container_runtime_unavailable
Goal: Confirm from existing node-service and pod-event evidence that containerd is unavailable on the affected node.
Actions:
Evidence to collect: root_cause_confirmation, container_runtime_status
Next: (end of this SOP - conclude here)

### Step: confirm_scheduler_availability
Goal: Confirm whether the kube-scheduler is unavailable and explains the Pending pod.
Actions:
- CheckNodeServiceStatus(node_name=master, service_name=kube-scheduler)
Evidence to collect: scheduler_service_status, root_cause_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_kubelet_unavailable
Goal: Confirm from existing node-service and node-readiness evidence that kubelet is unavailable on the affected worker node.
Actions:
Evidence to collect: root_cause_confirmation, kubelet_service_status
Next: (end of this SOP - conclude here)
