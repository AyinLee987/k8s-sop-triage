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
- pod_network_delay and code_artificial_delay can both leave pods healthy with no application errors, so handler-duration and outbound-call latency evidence is required to distinguish them
- alerts may include TRAFFIC_ANOMALY such as an inbound SURGE; this can be a primary load driver or retry amplification from an already slow service
- alerts may include RESOURCE_SATURATION such as CPU CFS throttle on the dominant service or a downstream dependency; a fast server-side handler log does not exclude CPU scheduling or queueing delay
- pod_network_delay, node_network_delay, pod_cpu_overload, code_artificial_delay, and downstream dependency delay can all leave pods healthy with no application errors, so handler-duration, outbound-call latency, and CPU-throttle evidence are required to distinguish them

Start at: performance.interpret_latency_degradation_alerts

## Steps

### Step: performance.interpret_latency_degradation_alerts
Goal: Inspect performance alerts to identify whether latency degradation has a dominant anomalous service or a widespread propagation scope, and select a root candidate using service dependency direction rather than relative latency magnitude alone. When several services alert, upstream callers of a delayed downstream service are propagated symptoms; prefer the deepest downstream candidate that can explain the caller alerts. Record which alerted services are outside the selected candidate's call chain, because those are the only independent evidence for a node-wide fault. Also record any TRAFFIC_ANOMALY inbound surge and any RESOURCE_SATURATION/CPU CFS throttle in the selected service's call chain; keep a CPU-throttled downstream dependency as a competing root candidate until outbound-call latency is checked.
Actions:
- GetAlerts(namespace=<namespace>)
- GetServiceDependencies(service_name=<candidate_latency_root_service>)
Evidence to collect: performance_alert_summary, latency_delta_ranked_services, throughput_drop_signals, dominant_latency_service, candidate_latency_root_service, upstream_alerted_services, downstream_dependency_direction, downstream_root_candidate, widespread_degradation_scope, downstream_root_candidate_preferred_when_widespread, dominant_latency_service_identified, widespread_degradation_observed, alerted_services_outside_call_chain, call_chain_only_alert_pattern, traffic_anomaly_alerts, inbound_surge_on_dominant_service, retry_amplification_possible, cpu_cfs_throttle_alerts, cpu_throttled_services_in_call_chain, downstream_cpu_overload_candidate
Next: performance.verify_latency_pod_surface_health, performance.inspect_cpu_saturation_and_traffic_surge_candidates

### Step: performance.verify_latency_pod_surface_health
Goal: Verify pod surface health to exclude pod crashes, restarts, readiness failures, or obvious missing backend symptoms before attributing latency degradation to network delay, and capture pod-to-node placement for both alerted services and co-located services outside the alerted call chain so node-level network delay can be tested against independent workloads.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- GetAlerts(namespace=<namespace>)
Evidence to collect: pod_surface_health, no_recent_restarts, pod_surface_health_confirmed, affected_pod_node_placement, co_located_affected_pods, co_located_services_outside_call_chain, shared_node_suspicion, node_level_correlation, call_chain_colocation_only_possible
Next: performance.exclude_node_network_delay, performance.inspect_cpu_saturation_and_traffic_surge_candidates

### Step: performance.inspect_cpu_saturation_and_traffic_surge_candidates
Goal: Resolve whether load-driven CPU saturation or a CPU-throttled downstream dependency explains the latency before attributing to network delay. Inspect CPU CFS throttle alerts and pod placement for the dominant service and its downstream dependencies. If a downstream dependency is CPU-throttled, use the dominant service's recent logs to extract outbound call duration to that dependency and compare it with the dependency's own handler duration. Treat an inbound surge on the dominant service as possible retry amplification, not automatic proof of primary load. Do not confirm pod_network_delay while CPU saturation on the call path or an unexplained inbound surge remains.
Actions:
- GetAlerts(namespace=<namespace>)
- GetRecentLogs(namespace=<namespace>, service_name=<dominant_latency_service>)
- GetRecentLogs(namespace=<namespace>, service_name=<cpu_throttled_dependency_service>)
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
Evidence to collect: cpu_cfs_throttle_services, cpu_throttled_dependency_on_call_path, dominant_service_outbound_call_duration_to_dependency, dependency_server_side_handler_duration, scheduling_or_queue_delay_evidence, inbound_surge_scope, retry_amplification_suspected, primary_load_surge_suspected, load_driven_latency_candidate, cpu_saturation_on_call_path_unresolved, pod_network_delay_attribution_blocked
Next: performance.confirm_pod_cpu_overload_candidate, performance.exclude_node_network_delay, performance.attribute_pod_network_delay_by_exclusion

### Step: performance.confirm_pod_cpu_overload_candidate
Goal: Confirm pod_cpu_overload when a CPU CFS throttle alert exists on a service in the latency call path and the caller's outbound calls to that service are slow or show scheduling or queue delay, even if the throttled service's own handler logs show fast processing once running. Do not dismiss CPU throttle solely because downstream handler logs are short.
Actions:
- GetAlerts(namespace=<namespace>)
- GetRecentLogs(namespace=<namespace>, service_name=<cpu_throttled_service>)
- GetRecentLogs(namespace=<namespace>, service_name=<caller_service>)
Evidence to collect: cpu_cfs_throttle_confirmed, caller_observed_latency_to_throttled_service, outbound_call_duration_to_throttled_service, throttled_service_handler_duration, cpu_scheduling_delay_evidence, pod_cpu_overload_confirmed, root_cause_confirmation
Next: (end of this SOP - conclude here)

### Step: performance.exclude_node_network_delay
Goal: Decide whether the latency pattern is consistent with node-level network delay. If affected pods are spread across nodes, pod-local network delay remains more likely. If they share a node, node_network_delay is only a candidate until an unrelated co-located workload is also shown degraded; co-location of the dominant service with its own callers is not sufficient because callers inherit the callee's latency. Also exclude CPU saturation on the shared node as the explanation.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- GetAlerts(namespace=<namespace>)
Evidence to collect: affected_pod_nodes, shared_node, co_located_latency_services, co_located_services_outside_call_chain, independent_node_wide_impact_present, independent_node_wide_impact_absent, call_chain_colocation_only, node_network_delay_candidate, node_network_delay_excluded, node_network_delay_confirmed, node_cpu_saturation_excluded
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
Goal: Exclude application-level errors by inspecting error logs for the anomalous downstream service selected from latency alerts. Also inspect recent logs to establish preliminary handler-duration and outbound-dependency-latency context, without yet making the final pod_network_delay attribution. If recent logs for the dominant service cannot be retrieved, do not treat the failed retrieval as evidence that there is no application-internal delay; retry with the current container logs or use an alternative log/metric source, and record the uncertainty.
Actions:
- GetErrorLogs(namespace=<namespace>, service_name=<dominant_latency_service>)
- GetRecentLogs(namespace=<namespace>, service_name=<dominant_latency_service>)
Evidence to collect: application_error_status, error_logs_empty, application_errors_excluded, recent_log_retrieval_status, recent_log_retrieval_failure, log_uncertainty_recorded, preliminary_handler_duration_context, preliminary_outbound_dependency_latency_context
Next: performance.attribute_pod_network_delay_by_exclusion, performance.inspect_cpu_saturation_and_traffic_surge_candidates

### Step: performance.attribute_pod_network_delay_by_exclusion
Goal: Confirm root cause pod_network_delay only after the CPU-saturation and traffic-surge step has excluded a load-driven or CPU-throttled downstream candidate, and after comparing three latency views for the dominant service: caller-observed latency, server-side request-handler duration/internal processing gaps, and duration of the pod's outbound calls to its dependencies. Pod_network_delay is supported when caller-observed latency is high while server-side handler duration is short, outbound calls to dependencies are not materially slower than normal, pods are healthy, application errors are excluded, independent node-wide impact is absent, and no unresolved CPU CFS throttle on the call path or inbound surge explains the latency. Prefer code_artificial_delay when server-side handler duration or internal gaps are long despite fast dependencies. Prefer a downstream dependency or downstream network delay when outbound calls from the dominant service are slow or dependency logs show slowness. If recent logs are unavailable, state the limitation and keep pod_network_delay conditional rather than overclaiming; do not use failed log retrieval to confirm node_network_delay or pod_network_delay. The terminal root cause label emitted here is pod_network_delay only when the comparison supports it.
Actions:
- GetRecentLogs(namespace=<namespace>, service_name=<dominant_latency_service>)
- GetRecentLogs(namespace=<namespace>, service_name=<downstream_dependency_service>)
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
Evidence to collect: recent_log_analysis, recent_logs_unavailable, caller_observed_latency, server_side_handler_duration, internal_processing_vs_external_latency_discrepancy, controller_to_service_timestamp_gap, request_arrival_to_handler_completion_gap, internal_sleep_or_delay_evidence, outbound_dependency_call_duration, outbound_dependency_latency_normal, downstream_dependency_server_side_fast, downstream_dependency_delay_candidate, code_artificial_delay_candidate, pod_surface_health_confirmed, application_errors_excluded, node_network_delay_excluded, independent_node_wide_impact_absent, no_direct_delay_artifact_expected, exclusion_based_network_delay_attribution, pod_network_delay_attribution, root_cause_pod_network_delay, root_cause_confirmation, cpu_saturation_on_call_path_excluded, traffic_surge_explained, outbound_dependency_call_duration_normal, cpu_throttle_on_call_path_unresolved, inbound_surge_primary_load_unresolved
Next: (end of this SOP - conclude here)

### Step: performance.attribute_node_network_delay_by_correlation
Goal: Confirm root cause node_network_delay only when the latency alerts and pod placement show multiple affected services co-located on the same node, at least one affected service is outside the dominant service's call path or there is independent node-wide network/latency evidence, application logs do not show internal errors or application-internal delays, and CPU saturation on the shared node or a CPU-throttled call-chain dependency has been excluded. Co-location of a service with its own callers alone is insufficient. If recent logs cannot be retrieved, do not treat that as evidence of no internal delay; either obtain alternative log/metric evidence or keep the node attribution conditional.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- GetAlerts(namespace=<namespace>)
- GetErrorLogs(namespace=<namespace>, service_name=<dominant_latency_service>)
- GetRecentLogs(namespace=<namespace>, service_name=<dominant_latency_service>)
Evidence to collect: affected_pod_nodes, shared_node, co_located_latency_services, co_located_non_call_path_service_degraded, independent_node_wide_impact_confirmed, application_errors_excluded, recent_log_analysis, node_network_delay_candidate, node_network_delay_confirmed, root_cause_node_network_delay, root_cause_confirmation, node_cpu_saturation_excluded, cpu_throttled_call_chain_dependency_excluded
Next: (end of this SOP - conclude here)
