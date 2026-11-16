# SOP: service_latency_degradation

Symptom: One or more TrainTicket services show sustained latency degradation or throughput drop.

Signals:
- alerts report LATENCY_DEGRADATION for TrainTicket services
- p50 or p90 latency increases sharply compared with normal baseline
- throughput drops for one latency candidate service
- multiple services may show degraded latency at the same time; these may be propagation from a downstream root rather than independent faults
- affected pods may still appear Running and Ready
- node-level network delay may affect several pods on the same node
- co-location of a delayed service with its callers is propagation evidence, not by itself proof of node-level network delay

Start at: performance.interpret_latency_degradation_alerts

## Steps

### Step: performance.interpret_latency_degradation_alerts
Goal: Inspect performance alerts to identify whether latency degradation has a dominant anomalous service or a widespread propagation scope, and select a root candidate using service dependency direction rather than relative latency magnitude alone. When several services alert, upstream callers of a delayed downstream service are propagated symptoms; prefer the deepest downstream candidate that can explain the caller alerts. Record which alerted services are outside the selected candidate's call chain, because those are the only independent evidence for a node-wide fault.
Actions:
- GetAlerts(namespace=<namespace>)
- GetServiceDependencies(service_name=<candidate_latency_root_service>)
Evidence to collect: performance_alert_summary, latency_delta_ranked_services, throughput_drop_signals, dominant_latency_service, candidate_latency_root_service, upstream_alerted_services, downstream_dependency_direction, downstream_root_candidate, widespread_degradation_scope, downstream_root_candidate_preferred_when_widespread, dominant_latency_service_identified, widespread_degradation_observed, alerted_services_outside_call_chain, call_chain_only_alert_pattern
Next: performance.verify_latency_pod_surface_health

### Step: performance.verify_latency_pod_surface_health
Goal: Verify pod surface health to exclude pod crashes, restarts, readiness failures, or obvious missing backend symptoms before attributing latency degradation to network delay, and capture pod-to-node placement for both alerted services and co-located services outside the alerted call chain so node-level network delay can be tested against independent workloads.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- GetAlerts(namespace=<namespace>)
Evidence to collect: pod_surface_health, no_recent_restarts, pod_surface_health_confirmed, affected_pod_node_placement, co_located_affected_pods, co_located_services_outside_call_chain, shared_node_suspicion, node_level_correlation, call_chain_colocation_only_possible
Next: performance.exclude_node_network_delay

### Step: performance.exclude_node_network_delay
Goal: Decide whether the latency pattern is consistent with node-level network delay. If affected pods are spread across nodes, pod-local network delay remains more likely. If they share a node, node_network_delay is only a candidate until an unrelated co-located workload is also shown degraded; co-location of the dominant service with its own callers is not sufficient because callers inherit the callee's latency.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- GetAlerts(namespace=<namespace>)
Evidence to collect: affected_pod_nodes, shared_node, co_located_latency_services, co_located_services_outside_call_chain, independent_node_wide_impact_present, independent_node_wide_impact_absent, call_chain_colocation_only, node_network_delay_candidate, node_network_delay_excluded, node_network_delay_confirmed
Next: performance.verify_independent_node_wide_impact, performance.exclude_latency_application_errors

### Step: performance.verify_independent_node_wide_impact
Goal: Distinguish node_network_delay from pod_network_delay by checking whether at least one service on the shared node that is not in the dominant service's call path also shows unexplained latency or network degradation. Co-location of the dominant service with its direct callers is propagation evidence, not independent node-wide evidence. CPU saturation or other non-network anomalies on the node may be recorded but do not by themselves confirm node network delay.
Actions:
- GetAlerts(namespace=<namespace>)
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- GetRecentLogs(namespace=<namespace>, service_name=<co_located_non_call_path_service>)
Evidence to collect: co_located_services_outside_call_chain, independent_service_latency_or_network_symptom, independent_node_wide_impact_confirmed, independent_node_wide_impact_absent, call_chain_colocation_only, node_network_delay_confirmed, node_network_delay_excluded, pod_network_delay_preferred
Next: performance.attribute_node_network_delay_by_correlation, performance.exclude_latency_application_errors

### Step: performance.exclude_latency_application_errors
Goal: Exclude application-level errors by inspecting error logs for the anomalous downstream service selected from latency alerts. If recent logs for the dominant service cannot be retrieved, do not treat the failed retrieval as evidence that there is no application-internal delay; retry with the current container logs or use an alternative log/metric source, and record the uncertainty.
Actions:
- GetErrorLogs(namespace=<namespace>, service_name=<dominant_latency_service>)
- GetRecentLogs(namespace=<namespace>, service_name=<dominant_latency_service>)
Evidence to collect: application_error_status, error_logs_empty, application_errors_excluded, recent_log_retrieval_status, recent_log_retrieval_failure, log_uncertainty_recorded
Next: performance.attribute_pod_network_delay_by_exclusion

### Step: performance.attribute_pod_network_delay_by_exclusion
Goal: Confirm root cause pod_network_delay by inspecting recent application logs for normal internal processing despite externally observed latency degradation, after pod health and application errors have been excluded and node-level correlation has not explained the pattern. This is exclusion-based attribution because chaosblade-injected pod network delay leaves no durable configuration artifact. If logs show tight controller-to-service timestamps with no internal delay, network delay is supported; if logs show long application-internal gaps, prefer an application delay hypothesis. If recent logs are unavailable, state the limitation and prefer pod_network_delay only when independent node-wide impact is absent; do not use failed log retrieval to confirm node_network_delay. The terminal root cause label emitted here is pod_network_delay.
Actions:
- GetRecentLogs(namespace=<namespace>, service_name=<dominant_latency_service>)
Evidence to collect: recent_log_analysis, recent_logs_unavailable, internal_processing_vs_external_latency_discrepancy, controller_to_service_timestamp_gap, pod_surface_health_confirmed, application_errors_excluded, node_network_delay_excluded, independent_node_wide_impact_absent, no_direct_delay_artifact_expected, exclusion_based_network_delay_attribution, pod_network_delay_attribution, root_cause_pod_network_delay, root_cause_confirmation
Next: (end of this SOP - conclude here)

### Step: performance.attribute_node_network_delay_by_correlation
Goal: Confirm root cause node_network_delay only when the latency alerts and pod placement show multiple affected services co-located on the same node, at least one affected service is outside the dominant service's call path or there is independent node-wide network/latency evidence, and application logs do not show internal errors or application-internal delays. Co-location of a service with its own callers alone is insufficient. If recent logs cannot be retrieved, do not treat that as evidence of no internal delay; either obtain alternative log/metric evidence or keep the node attribution conditional.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- GetAlerts(namespace=<namespace>)
- GetErrorLogs(namespace=<namespace>, service_name=<dominant_latency_service>)
- GetRecentLogs(namespace=<namespace>, service_name=<dominant_latency_service>)
Evidence to collect: affected_pod_nodes, shared_node, co_located_latency_services, co_located_non_call_path_service_degraded, independent_node_wide_impact_confirmed, application_errors_excluded, recent_log_analysis, node_network_delay_candidate, node_network_delay_confirmed, root_cause_node_network_delay, root_cause_confirmation
Next: (end of this SOP - conclude here)
