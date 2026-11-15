# SOP: service_latency_degradation

Symptom: One or more TrainTicket services show sustained latency degradation or throughput drop.

Signals:
- alerts report LATENCY_DEGRADATION for TrainTicket services
- p50 or p90 latency increases sharply compared with normal baseline
- throughput drops for one or more latency candidate services
- multiple services may show degraded latency at the same time
- a downstream caller may show more severe absolute latency than the upstream dependency causing it
- affected pods may still appear Running and Ready
- application logs may be clean because injected pod network delay leaves no durable application error
- a service with the largest latency percentage is not automatically the root if it depends on another degraded candidate

Start at: performance.interpret_latency_degradation_alerts

## Steps

### Step: performance.interpret_latency_degradation_alerts
Goal: Inspect performance alerts to identify all latency candidates, their throughput drops, and their basic service topology. When multiple services are degraded, do not select a downstream service merely because it has the largest absolute latency or the largest percentage increase.
Actions:
- GetAlerts(namespace=<namespace>)
- GetResources(resource_type=services, namespace=<namespace>)
- GetResources(resource_type=endpoints, namespace=<namespace>)
Evidence to collect: performance_alert_summary, latency_delta_ranked_services, throughput_drop_signals, all_latency_candidate_services, candidate_service_alert_severity, candidate_latency_root_service, downstream_propagation_candidates, service_topology_baseline, service_without_endpoints, missing_backend_candidate, widespread_degradation_scope, upstream_candidate_preferred_when_widespread, dominant_latency_service_must_not_be_downstream_only
Next: performance.resolve_latency_candidate_dependency_order

### Step: performance.resolve_latency_candidate_dependency_order
Goal: Use service dependencies to distinguish root upstream candidate services from downstream propagation after alerts show widespread or multi-service latency degradation. Select the most upstream service that has its own latency or throughput anomaly as the primary attribution target. Do not attribute root cause to a downstream service whose latency can be explained by waiting on a degraded upstream dependency.
Actions:
- GetServiceDependencies(service_name=<candidate_latency_service>) for each latency candidate
Evidence to collect: service_dependency_graph, latency_candidate_dependencies, upstream_latency_candidate, downstream_latency_candidate, candidate_propagation_path, primary_latency_candidate, downstream_propagation_excluded, upstream_dependency_of_degraded_downstream, primary_candidate_latency_and_throughput_evidence
Next: performance.verify_latency_pod_surface_health

### Step: performance.verify_latency_pod_surface_health
Goal: Verify pod surface health for all latency candidates to exclude pod crashes, restarts, readiness failures, or obvious missing backend symptoms before attributing latency degradation to pod network delay.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- GetResources(resource_type=endpoints, namespace=<namespace>)
Evidence to collect: pod_surface_health, no_recent_restarts, pod_surface_health_confirmed, candidate_pod_health, candidate_endpoint_health, missing_backend_excluded
Next: performance.exclude_latency_application_errors

### Step: performance.exclude_latency_application_errors
Goal: Exclude application-level errors for each latency candidate, especially the primary upstream candidate chosen from dependency analysis, not only the service with the largest observed latency.
Actions:
- GetErrorLogs(namespace=<namespace>, service_name=<primary_latency_candidate>)
- GetErrorLogs(namespace=<namespace>, service_name=<candidate_latency_service>) for other candidates when needed
Evidence to collect: application_error_status, error_logs_empty, application_errors_excluded, per_candidate_error_status, primary_candidate_error_status, downstream_candidate_errors_context_only
Next: performance.attribute_pod_network_delay_by_exclusion

### Step: performance.attribute_pod_network_delay_by_exclusion
Goal: Confirm root cause pod_network_delay by inspecting recent application logs for normal internal processing despite externally observed latency degradation, after pod health and application errors have been excluded. When multiple services are degraded along a dependency path, attribute network delay to the upstream primary candidate whose own latency and throughput anomaly best explains downstream propagation. If the first inspected service is downstream of another degraded candidate, continue checking upstream candidates before concluding. The terminal root cause label emitted by this atom is pod_network_delay.
Actions:
- GetRecentLogs(namespace=<namespace>, service_name=<primary_latency_candidate>)
Evidence to collect: recent_log_analysis, internal_processing_vs_external_latency_discrepancy, primary_latency_candidate_log_analysis, dependency_upstream_checked, downstream_propagation_explained, pod_surface_health_confirmed, application_errors_excluded, no_direct_delay_artifact_expected, exclusion_based_network_delay_attribution, pod_network_delay_attribution, root_cause_pod_network_delay, root_cause_service, root_cause_confirmation
Next: (end of this SOP - conclude here)
