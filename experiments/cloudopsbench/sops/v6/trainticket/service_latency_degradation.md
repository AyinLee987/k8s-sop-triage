# SOP: service_latency_degradation

Symptom: One or more TrainTicket services show sustained latency degradation or throughput drop.

Signals:
- alerts report LATENCY_DEGRADATION for TrainTicket services
- p50 or p90 latency increases sharply compared with normal baseline
- throughput drops for one latency candidate service
- multiple services may show degraded latency at the same time
- affected pods may still appear Running and Ready

Start at: performance.interpret_latency_degradation_alerts

## Steps

### Step: performance.interpret_latency_degradation_alerts
Goal: Inspect performance alerts to identify whether latency degradation has a dominant anomalous service or a widespread systemic scope, using only alert evidence and service-topology reasoning available at the beginning of the investigation.
Actions:
- GetAlerts(namespace=<namespace>)
Evidence to collect: performance_alert_summary, latency_delta_ranked_services, throughput_drop_signals, dominant_latency_service, candidate_latency_root_service, widespread_degradation_scope, upstream_candidate_preferred_when_widespread, dominant_latency_service_identified, widespread_degradation_observed
Next: performance.verify_latency_pod_surface_health

### Step: performance.verify_latency_pod_surface_health
Goal: Verify pod surface health to exclude pod crashes, restarts, readiness failures, or obvious missing backend symptoms before attributing latency degradation to pod network delay.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
Evidence to collect: pod_surface_health, no_recent_restarts, pod_surface_health_confirmed
Next: performance.exclude_latency_application_errors

### Step: performance.exclude_latency_application_errors
Goal: Exclude application-level errors by inspecting error logs for the anomalous service selected from latency alerts.
Actions:
- GetErrorLogs(namespace=<namespace>, service_name=<dominant_latency_service>)
Evidence to collect: application_error_status, error_logs_empty, application_errors_excluded
Next: performance.attribute_pod_network_delay_by_exclusion

### Step: performance.attribute_pod_network_delay_by_exclusion
Goal: Confirm root cause pod_network_delay by inspecting recent application logs for normal internal processing despite externally observed latency degradation, after pod health and application errors have been excluded. This is an exclusion-based attribution because chaosblade-injected pod network delay leaves no durable configuration artifact. The terminal root cause label emitted by this atom is pod_network_delay.
Actions:
- GetRecentLogs(namespace=<namespace>, service_name=<dominant_latency_service>)
Evidence to collect: recent_log_analysis, internal_processing_vs_external_latency_discrepancy, pod_surface_health_confirmed, application_errors_excluded, no_direct_delay_artifact_expected, exclusion_based_network_delay_attribution, pod_network_delay_attribution, root_cause_pod_network_delay, root_cause_confirmation
Next: (end of this SOP - conclude here)
