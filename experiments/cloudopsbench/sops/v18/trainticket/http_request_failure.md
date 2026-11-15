# SOP: http_request_failure
Symptom: One or more TrainTicket business requests fail with HTTP error responses, or a TrainTicket service is partially unreachable from callers even when its pods and Deployment are Running and Ready.
Signals:
- alerts report HTTP 4xx or 5xx responses for TrainTicket business actions, or a user or caller reports partial service unreachability
- a user-facing or internal business API returns an HTTP error, or a downstream service cannot be reached despite running pods
- alert data includes an entry service, request path, business action, failed URL, or affected downstream service
- request failure is observed at the service-call or business-operation level
- pod status alone does not fully explain the request failure
- pods may all be Running and Ready and most Services and Endpoints may be healthy while one dependency is reported unreachable
- a running service may appear absent from Service or Endpoint listings, or a gateway dependency may look missing; this is a candidate symptom only and must not be accepted as root cause before gateway route comparison
- downstream service logs may show application exceptions, SQL/query anomalies, NonUniqueResultException, IncorrectResultSizeDataAccessException, or a downstream HTTP 500, but these can be secondary effects of a gateway route misroute and must not be treated as root cause until the gateway route is checked
- when pods, Services, and Endpoints are broadly healthy, a business-action HTTP 500 with downstream SQL/query/cardinality exceptions must trigger gateway route comparison before any code-bug conclusion
- when a specific downstream service appears missing or unreachable, a gateway route target/path mismatch must be checked before any DNS, Service-selector, missing-Service, or port-mapping conclusion
- a clean GetAlerts result does not exclude gateway misroute when partial service unreachability is observed

Start at: service.check_service_route_baseline

## Steps

### Step: service.check_service_route_baseline
Goal: Establish whether TrainTicket pods, Services, and Endpoints are broadly healthy and capture alert or caller context for a failing business request or partial service unreachability without attributing the failure to downstream application code, missing Service, or DNS.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>)
- GetResources(resource_type=services, namespace=<namespace>)
- GetResources(resource_type=endpoints, namespace=<namespace>)
- GetAlerts(namespace=<namespace>)
Evidence to collect: pod_health_baseline, service_discovery_baseline, endpoint_baseline, alert_summary, alert_count_zero_allowed, failed_business_route, affected_entry_service, route_http_status, failing_request_path, unreachable_service_symptom, affected_downstream_service, gateway_service, route_failure_candidates, backend_pods_ready, service_endpoints_present, service_appears_missing_from_listing
Next: shared.trace_http_5xx_downstream_source

### Step: shared.trace_http_5xx_downstream_source
Goal: Inspect entry-service logs and service dependencies only to identify the downstream service and the exact downstream request path for an HTTP 4xx/5xx business-action failure or partial service unreachability. Do not inspect downstream service application logs or treat downstream SQL/query/cardinality exceptions, missing Service listings, or DNS errors as root cause yet; those can be produced by gateway misrouting.
Actions:
- GetErrorLogs(namespace=<namespace>, service_name=<entry_service>)
- GetServiceDependencies(service_name=<gateway_service>)
Evidence to collect: entry_service_error_logs, downstream_failure_source, downstream_service_identified, suspected_downstream_service, downstream_request_path, entry_request_path, service_instance_null, downstream_http_500, downstream_http_503, failure_source, downstream_application_exception, downstream_sql_or_query_exception, downstream_non_unique_result_exception, downstream_exception_candidate_only, gateway_service, gateway_downstream_dependencies, service_dependency_graph
Next: service.check_gateway_route_for_http_5xx

### Step: service.check_gateway_route_for_http_5xx
Goal: Mandatory gateway route comparison for both the alert request path and any downstream request path, and also for any downstream service reported missing or unreachable, before inspecting downstream service application logs or accepting downstream SQL/code exceptions, DNS errors, missing Service listings, or selector/port issues as root cause. Compare the failing request path, the suspected downstream service name, gateway route rules, configured backend targets, backend endpoints, and gateway logs. A downstream HTTP 500, NonUniqueResultException, IncorrectResultSizeDataAccessException, SQL/query anomaly, missing Service object, or unreachable service is not root-cause confirmation until gateway route mismatch has been checked.
Actions:
- GetAppYAML(app_name=<gateway_service>)
- GetRecentLogs(namespace=<namespace>, service_name=<gateway_service>)
- GetResources(resource_type=endpoints, namespace=<namespace>)
- GetServiceDependencies(service_name=<gateway_service>)
Evidence to collect: gateway_service, failed_business_route, failing_request_path, downstream_request_path, suspected_downstream_service, gateway_route_path, expected_backend, configured_backend, route_target_mismatch, gateway_route_log_evidence, gateway_upstream_routing_errors, backend_endpoints_present, downstream_application_exception, downstream_sql_or_query_exception, downstream_non_unique_result_exception, possible_secondary_app_exception, service_appears_missing_from_listing, missing_service_observation_candidate_only, missing_service_root_cause_excluded, gateway_route_misroute_suspected, gateway_route_misroute_excluded, root_cause_gateway_misrouted
Next: service.confirm_gateway_route_misroute (if route target/path mismatch or gateway routing error evidence exists); service.exclude_backend_route_owner_failure (only after gateway route mismatch is excluded)

### Step: service.confirm_gateway_route_misroute
Goal: Confirm gateway route-to-backend mismatch by comparing the failing request path, downstream request path, and any suspected missing or unreachable downstream service name with the gateway route rule and configured backend target. The HTTP status code and downstream exception are evidence context, not a precondition. If a route sends the request to the wrong backend or handler, or the route target/name does not match the actual reachable service, emit root cause gateway_misrouted on the gateway service.
Actions:
- GetAppYAML(app_name=<gateway_service>)
- GetRecentLogs(namespace=<namespace>, service_name=<gateway_service>)
- GetServiceDependencies(service_name=<gateway_service>)
Evidence to collect: gateway_route_path, expected_backend, configured_backend, route_target_mismatch, gateway_route_log_evidence, downstream_application_exception, downstream_sql_or_query_exception, downstream_non_unique_result_exception, possible_secondary_app_exception, service_appears_missing_from_listing, missing_service_observation_candidate_only, root_cause_gateway_misrouted, root_cause_confirmation
Next: (end of this SOP - conclude here)

### Step: service.exclude_backend_route_owner_failure
Goal: Only after gateway route mismatch has been excluded, check whether the backend associated with the failing route or HTTP error is itself unhealthy, misconfigured, or unreachable before attributing the symptom to downstream application code. If the suspected problem is a missing or unreachable Service object, keep it as a candidate only and verify the gateway route target and name before concluding DNS, missing Service, selector, or port root cause.
Actions:
- GetResources(resource_type=deployments, namespace=<namespace>, name=<affected_backend>)
- GetResources(resource_type=services, namespace=<namespace>, name=<affected_backend>, output_yaml=True)
- GetErrorLogs(namespace=<namespace>, service_name=<affected_backend>)
Evidence to collect: backend_deployment_health, backend_service_configuration, backend_pod_health, backend_connectivity, backend_error_logs, backend_failure_excluded, gateway_route_misroute_excluded, service_appears_missing_from_listing, missing_service_observation_candidate_only
Next: runtime.inspect_downstream_db_write_error, service.classify_dns_resolution_failure_logs, runtime.classify_downstream_code_or_data_exception

### Step: runtime.inspect_downstream_db_write_error
Goal: Inspect the downstream service that emits HTTP 500 to determine whether the failure is caused by database write rejection or read-only database behavior, only after gateway route mismatch has been excluded.
Actions:
- GetErrorLogs(namespace=<namespace>, service_name=<downstream_service>)
Evidence to collect: downstream_error_logs, database_write_error, sql_error_code, read_only_database_error, root_cause_evidence, root_cause_classified, gateway_route_misroute_excluded
Next: runtime.confirm_database_readonly_from_logs

### Step: runtime.confirm_database_readonly_from_logs
Goal: Confirm database read-only mode by retrieving database-side logs after downstream service logs show write statements being rejected.
Actions:
- GetErrorLogs(namespace=<namespace>, service_name=<mysql_service>)
Evidence to collect: database_error_logs_check_result, database_readonly_mode, database_write_rejected, root_cause_confirmation
Next: (end of this SOP - conclude here)

### Step: runtime.classify_downstream_code_or_data_exception
Goal: Only after gateway route mismatch, backend failure, database read-only behavior, missing or unreachable Service verification, and DNS resolution failure have been excluded, classify residual downstream SQL/query/cardinality exceptions as application code or data-access defects. Do not use this step while an unchecked gateway route could be sending the request to the wrong backend or handler.
Actions:
- GetRecentLogs(namespace=<namespace>, service_name=<downstream_service>)
Evidence to collect: downstream_non_unique_result_exception, incorrect_result_size_data_access_exception, code_missing_parameter_suspected, code_wrong_argument_order_suspected, code_wrong_return_suspected, gateway_route_misroute_excluded, backend_failure_excluded, database_readonly_excluded, missing_service_root_cause_excluded, dns_resolution_failure_excluded, root_cause_confirmation
Next: (end of this SOP - conclude here)

### Step: service.classify_dns_resolution_failure_logs
Goal: Inspect the failing or restarting service logs for DNS lookup errors, timeout evidence, service instance null messages, or name-resolution failures, preserving whether the same service is partially ready, restarting, or in startup failure. Use this step only after gateway route mismatch is excluded; do not classify a service as a DNS resolution failure solely because a Service name appears absent from a listing.
Actions:
- GetErrorLogs(namespace=<namespace>, service_name=<failing_service>)
Evidence to collect: dns_error_logs, dns_resolution_failure_evidence, dns_timeout_evidence, service_instance_null, crash_cause_classification, failing_service_identified, pod_restart_context, repeated_startup_failure, pod_partial_ready_or_crashloop_context, gateway_route_misroute_excluded, missing_service_observation_candidate_only
Next: service.verify_dns_target_service_exists

### Step: service.verify_dns_target_service_exists
Goal: Verify that the Service or database target named in DNS errors exists and is reachable, so the diagnosis can distinguish pod-local DNS configuration from a genuinely missing target. Confirm that gateway route mismatch has already been excluded and that a missing Service listing is not merely a route-target naming mismatch.
Actions:
- GetResources(resource_type=services, namespace=<namespace>)
Evidence to collect: target_service_exists, target_service_endpoints, service_reachable, missing_service_excluded, network_unavailability_excluded, gateway_route_misroute_excluded, missing_service_observation_candidate_only
Next: service.confirm_pod_dns_misconfiguration

### Step: service.confirm_pod_dns_misconfiguration
Goal: Confirm pod-local DNS misconfiguration by inspecting the failing workload YAML for dnsPolicy and custom nameserver configuration after DNS errors are observed, including cases where the misconfiguration causes repeated service restarts or partial readiness. Only use this step after gateway route mismatch and missing target checks have been excluded.
Actions:
- GetAppYAML(app_name=<failing_service>)
Evidence to collect: deployment_dns_config, dns_policy, custom_nameserver_configured, pod_dns_policy_none, unreachable_nameserver, dns_failure_causes_service_restart, gateway_route_misroute_excluded, missing_service_excluded, root_cause_service_dns_resolution_failure, root_cause_confirmation, root_cause_confirmed
Next: (end of this SOP - conclude here)
