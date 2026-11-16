# SOP: service_latency_degradation

Symptom: One or more TrainTicket services show sustained latency degradation or throughput drop.

Signals:
- alerts report LATENCY_DEGRADATION for TrainTicket services
- p50 or p90 latency increases sharply compared with normal baseline
- throughput drops for one latency candidate service
- multiple services may show degraded latency at the same time; these may be propagation from a downstream root rather than independent faults
- affected pods may still appear Running and Ready
- node-level network delay may affect several pods on the same node

Start at: performance.interpret_latency_degradation_alerts

## Steps

### Step: performance.interpret_latency_degradation_alerts
Goal: Inspect performance alerts to identify whether latency degradation has a dominant anomalous service or a widespread propagation scope, and select a root candidate using service dependency direction rather than relative latency magnitude alone. When several services alert, upstream callers of a delayed downstream service are propagated symptoms; prefer the deepest downstream candidate that can explain the caller alerts.
Actions:
- GetAlerts(namespace=<namespace>)
- GetServiceDependencies(service_name=<candidate_latency_root_service>)
Evidence to collect: performance_alert_summary, latency_delta_ranked_services, throughput_drop_signals, dominant_latency_service, candidate_latency_root_service, upstream_alerted_services, downstream_dependency_direction, downstream_root_candidate, widespread_degradation_scope, downstream_root_candidate_preferred_when_widespread, dominant_latency_service_identified, widespread_degradation_observed
Next: performance.verify_latency_pod_surface_health

### Step: performance.verify_latency_pod_surface_health
Goal: Verify pod surface health to exclude pod crashes, restarts, readiness failures, or obvious missing backend symptoms before attributing latency degradation to network delay, and capture pod-to-node placement so node-level network delay can be distinguished from pod-local delay.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
Evidence to collect: pod_surface_health, no_recent_restarts, pod_surface_health_confirmed, affected_pod_node_placement, co_located_affected_pods, shared_node_suspicion, node_level_correlation
Next: performance.exclude_node_network_delay

### Step: performance.exclude_node_network_delay
Goal: Decide whether the latency pattern is consistent with node-level network delay. If multiple affected service pods, especially the dominant downstream candidate and its callers, are co-located on one node, treat node_network_delay as a serious alternative; if affected pods are spread across nodes, pod-local network delay remains more likely.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
Evidence to collect: affected_pod_nodes, shared_node, co_located_latency_services, node_network_delay_candidate, node_network_delay_excluded, node_network_delay_confirmed
Next: performance.exclude_latency_application_errors, performance.attribute_node_network_delay_by_correlation

### Step: performance.exclude_latency_application_errors
Goal: Exclude application-level errors by inspecting error logs for the anomalous downstream service selected from latency alerts.
Actions:
- GetErrorLogs(namespace=<namespace>, service_name=<dominant_latency_service>)
Evidence to collect: application_error_status, error_logs_empty, application_errors_excluded
Next: performance.attribute_pod_network_delay_by_exclusion

### Step: performance.attribute_pod_network_delay_by_exclusion
Goal: Confirm root cause pod_network_delay by inspecting recent application logs for normal internal processing despite externally observed latency degradation, after pod health and application errors have been excluded and node-level correlation has not explained the pattern. This is exclusion-based attribution because chaosblade-injected pod network delay leaves no durable configuration artifact. If logs show tight controller-to-service timestamps with no internal delay, network delay is supported; if logs show long application-internal gaps, prefer an application delay hypothesis. The terminal root cause label emitted here is pod_network_delay.
Actions:
- GetRecentLogs(namespace=<namespace>, service_name=<dominant_latency_service>)
Evidence to collect: recent_log_analysis, internal_processing_vs_external_latency_discrepancy, controller_to_service_timestamp_gap, pod_surface_health_confirmed, application_errors_excluded, node_network_delay_excluded, no_direct_delay_artifact_expected, exclusion_based_network_delay_attribution, pod_network_delay_attribution, root_cause_pod_network_delay, root_cause_confirmation
Next: (end of this SOP - conclude here)

### Step: performance.attribute_node_network_delay_by_correlation
Goal: Confirm root cause node_network_delay when the latency alerts and pod placement show multiple affected services co-located on the same node and application logs do not show internal errors or application-internal delays.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- GetErrorLogs(namespace=<namespace>, service_name=<dominant_latency_service>)
- GetRecentLogs(namespace=<namespace>, service_name=<dominant_latency_service>)
Evidence to collect: affected_pod_nodes, shared_node, co_located_latency_services, application_errors_excluded, node_network_delay_candidate, node_network_delay_confirmed, root_cause_node_network_delay, root_cause_confirmation
Next: (end of this SOP - conclude here)
