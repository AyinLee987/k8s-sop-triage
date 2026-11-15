# SOP: service_latency_degradation

Symptom: One or more TrainTicket services show sustained latency degradation or throughput drop.

Signals:
- alerts report LATENCY_DEGRADATION for TrainTicket services
- p50 or p90 latency increases sharply compared with normal baseline
- throughput drops for one or more latency candidate services
- multiple services may show degraded latency at the same time
- a downstream caller may show more severe absolute latency than the upstream dependency causing it
- a downstream leaf service may be the true root even if an upstream caller has higher absolute latency; the upstream caller's latency may be caused by waiting on the downstream service. Do not select the most upstream service automatically.
- affected pods may still appear Running and Ready
- application logs may be clean because injected pod network delay leaves no durable application error
- application logs may also contain SQL/query/cardinality exceptions, NonUniqueResultException, IncorrectResultSizeDataAccessException, downstream HTTP errors, or order-not-found messages during the same latency window; these can be latency-correlated secondary effects and are not root-cause proof by themselves, but their presence on a service does not exclude that service from being the primary latency candidate
- CPU throttling or resource saturation alerts on another service can be a secondary effect or separate candidate; use dependency order and primary latency evidence before attributing root cause
- a service with the largest latency percentage is not automatically the root if it depends on another degraded candidate, but a downstream leaf with severe latency and throughput drop is a strong primary candidate even if an upstream caller also shows latency

Start at: performance.interpret_latency_degradation_alerts

## Steps

### Step: performance.interpret_latency_degradation_alerts
Goal: Inspect performance alerts to identify all latency candidates, their throughput drops, and their basic service topology. When multiple services are degraded, do not select a service merely because it is upstream or has the largest absolute latency or largest percentage increase. Instead, identify the service whose own latency and throughput anomaly best explains the propagation; this may be a downstream leaf. Keep the incident in the latency-degradation path even if the alert set also contains application-error, resource-saturation, or CPU-throttling signals.
Actions:
- GetAlerts(namespace=<namespace>)
- GetResources(resource_type=services, namespace=<namespace>)
- GetResources(resource_type=endpoints, namespace=<namespace>)
Evidence to collect: performance_alert_summary, latency_delta_ranked_services, throughput_drop_signals, all_latency_candidate_services, candidate_service_alert_severity, candidate_latency_root_service, downstream_propagation_candidates, service_topology_baseline, service_without_endpoints, missing_backend_candidate, widespread_degradation_scope, upstream_candidate_preferred_when_widespread, dominant_latency_service_must_not_be_downstream_only, downstream_leaf_latency_candidate, upstream_latency_explained_by_downstream, latency_window_application_exception_candidate, secondary_resource_saturation_candidate, incident_remains_latency_degradation
Next: performance.resolve_latency_candidate_dependency_order

### Step: performance.resolve_latency_candidate_dependency_order
Goal: Use service dependencies to distinguish the origin of latency from downstream propagation after alerts show widespread or multi-service latency degradation. Select the service whose own latency or throughput anomaly best explains the overall degradation. If an upstream service's latency can be explained by waiting on a downstream dependency that itself has severe latency and throughput drop, the downstream service is the primary candidate. Do not automatically select the most upstream service.
Actions:
- GetServiceDependencies(service_name=<candidate_latency_service>) for each latency candidate
Evidence to collect: service_dependency_graph, latency_candidate_dependencies, primary_latency_candidate_origin, downstream_leaf_as_primary, upstream_latency_explained_by_downstream, candidate_propagation_path, dependency_direction_considered, downstream_propagation_excluded, primary_candidate_latency_and_throughput_evidence
Next: performance.verify_latency_pod_surface_health

### Step: performance.verify_latency_pod_surface_health
Goal: Verify pod surface health for all latency candidates to exclude pod crashes, restarts, readiness failures, or obvious missing backend symptoms before attributing latency degradation to pod network delay.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- GetResources(resource_type=endpoints, namespace=<namespace>)
Evidence to collect: pod_surface_health, no_recent_restarts, pod_surface_health_confirmed, candidate_pod_health, candidate_endpoint_health, missing_backend_excluded
Next: performance.classify_latency_window_application_errors

### Step: performance.classify_latency_window_application_errors
Goal: Inspect application logs for the primary latency candidate and other candidates to classify any errors during the latency window. Do not require error logs to be empty and do not reclassify the incident as an HTTP request failure or code defect solely because NonUniqueResultException, IncorrectResultSizeDataAccessException, SQL/query anomalies, order-not-found messages, or downstream HTTP errors are present. However, the presence of such errors on a service does not exclude that service from being the primary latency candidate; they may be secondary effects of network delay on the same service. Determine whether the errors are independent code defects or latency-correlated secondary effects. If the errors appear only during the latency window and lack independent code-defect evidence, treat them as secondary and continue latency attribution. Do not dismiss a service as the primary candidate solely because it has application errors.
Actions:
- GetErrorLogs(namespace=<namespace>, service_name=<primary_latency_candidate>)
- GetRecentLogs(namespace=<namespace>, service_name=<primary_latency_candidate>)
- GetErrorLogs(namespace=<namespace>, service_name=<candidate_latency_service>) for other candidates when needed
Evidence to collect: application_error_status, error_logs_present, error_logs_empty_allowed, error_types_observed, error_timing_relative_to_latency_window, latency_correlated_secondary_error, error_rate_correlation, application_error_as_root_candidate_only, code_defect_independent_evidence, code_defect_not_confirmed_by_latency_window_exception, application_errors_do_not_exclude_primary_candidate, primary_candidate_error_status, downstream_candidate_errors_context_only, incident_not_reclassified_as_http_request_failure
Next: performance.attribute_pod_network_delay_by_exclusion

### Step: performance.attribute_pod_network_delay_by_exclusion
Goal: Confirm root cause pod_network_delay by inspecting recent application logs for normal internal processing despite externally observed latency degradation, after pod health and any application errors have been classified. If application errors are present but are correlated with the latency window and lack independent code-defect evidence, treat them as secondary and still attribute pod_network_delay to the service whose own latency and throughput anomaly best explains the overall degradation. This may be a downstream leaf service; do not attribute network delay to an upstream caller whose latency is explained by waiting on a degraded downstream dependency. The terminal root cause label emitted by this atom is pod_network_delay.
Actions:
- GetRecentLogs(namespace=<namespace>, service_name=<primary_latency_candidate>)
Evidence to collect: recent_log_analysis, internal_processing_vs_external_latency_discrepancy, primary_latency_candidate_log_analysis, dependency_upstream_checked, downstream_leaf_attribution, upstream_caller_latency_explained, downstream_propagation_explained, pod_surface_health_confirmed, latency_window_application_errors_classified, application_errors_secondary_if_correlated, code_defect_independent_evidence_absent, no_direct_delay_artifact_expected, exclusion_based_network_delay_attribution, pod_network_delay_attribution, root_cause_pod_network_delay, root_cause_service, root_cause_confirmation
Next: (end of this SOP - conclude here)
