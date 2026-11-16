# SOP: pod_crash_or_restart

Symptom: A single TrainTicket service pod shows crash, error, partial readiness, or repeated restart behavior.

Signals:
- exactly one TrainTicket service pod is abnormal while most other pods remain Running or Ready
- the pod is in CrashLoopBackOff, Error, 0/1 Ready, 1/N Ready, or partial readiness
- restart count increases for the affected service
- the affected service has reduced readiness or availability
- the symptom is visible from pod listings before detailed application inspection

Start at: shared.locate_single_pod_crash_or_restart

## Steps

### Step: shared.locate_single_pod_crash_or_restart
Goal: Locate a single TrainTicket workload with pod lifecycle instability, including CrashLoopBackOff, Error, 0/1 Ready, 1/N Ready, partial readiness, or abnormal restart growth. This atom merges the common GetResources(pods) prefix from the historical single-crash and partial-ready diagnostic paths while preserving service identity for MySQL, sidecar-port, and DNS-resolution diagnosis.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>)
Evidence to collect: failing_pod_identified, failing_pod_name, affected_workload, affected_service, pod_status, pod_readiness_status, pod_restart_count, restart_count, CrashLoopBackOff, partial_ready_pod, sidecar_present, repeated_startup_failure, alert_summary, downstream_failure_indication
Next: shared.inspect_mysql_startup_crash_evidence, runtime.inspect_sidecar_port_conflict_evidence, service.classify_dns_resolution_failure_logs

### Step: shared.inspect_mysql_startup_crash_evidence
Goal: Inspect pod state and application logs for a single CrashLoopBackOff service to determine whether startup failure is caused by MySQL authentication, host, or port configuration.
Actions:
- DescribeResource(resource_type=pod, name=<failing_pod>, namespace=<namespace>)
- GetErrorLogs(namespace=<namespace>, service_name=<affected_service>)
Evidence to collect: pod_exit_code, pod_events, pod_crash_state, environment_variable_sources, mysql_secret_dependency, application_error_logs, mysql_auth_error_message, mysql_access_denied, mysql_connection_refused, crash_loop_reason
Next: runtime.confirm_mysql_invalid_credentials, runtime.inspect_mysql_port_configuration

### Step: runtime.confirm_mysql_invalid_credentials
Goal: Confirm invalid MySQL credentials by inspecting the Secret referenced by the affected workload and correlating it with access-denied log evidence and database reachability.
Actions:
- GetResources(resource_type=deployments, namespace=<namespace>, name=<affected_workload>, output_yaml=True)
- GetResources(resource_type=secrets, namespace=<namespace>, name=<referenced_secret>, output_yaml=True)
Evidence to collect: referenced_secret, secret_credential_configuration, deployment_secret_binding, credential_key_structure, credential_structure_ok, database_connectivity_status, mysql_invalid_credential_suspected, mysql_invalid_credentials_confirmed, root_cause_confirmation
Next: (end of this SOP - conclude here)

### Step: runtime.inspect_mysql_port_configuration
Goal: Inspect the affected application YAML or referenced Secret to retrieve the configured MySQL host and port after logs show connection refused or communications failure.
Actions:
- GetAppYAML(app_name=<affected_service>)
Evidence to collect: configured_mysql_host, configured_mysql_port, secret_configuration, database_configuration, database_port_misconfiguration_suspected, referenced_secret
Next: runtime.confirm_mysql_port_mismatch

### Step: runtime.confirm_mysql_port_mismatch
Goal: Confirm that the application-configured MySQL port differs from the actual MySQL Service port, using Service YAML and optional connectivity checks.
Actions:
- GetResources(resource_type=services, namespace=<namespace>, name=<mysql_service>, output_yaml=True)
Evidence to collect: actual_mysql_service_port, app_configured_db_port, configured_port_connectivity_failure, standard_port_connectivity_success, port_mismatch_confirmed, root_cause_confirmation
Next: (end of this SOP - conclude here)

### Step: runtime.inspect_sidecar_port_conflict_evidence
Goal: Inspect the partially ready pod and application logs to determine whether repeated restarts are caused by an application container port conflicting with the Istio sidecar status port.
Actions:
- DescribeResource(resource_type=pod, name=<failing_pod>, namespace=<namespace>)
- GetErrorLogs(namespace=<namespace>, service_name=<affected_service>)
Evidence to collect: container_ports, sidecar_status_port_annotation, port_conflict_suspicion, application_error_logs, port_in_use_exception, port_conflict_confirmed, sidecar_annotation, conflicting_port
Next: runtime.confirm_sidecar_port_conflict

### Step: runtime.confirm_sidecar_port_conflict
Goal: Confirm sidecar/application port conflict by inspecting deployment YAML, sidecar logs, or service reachability on the conflicting port.
Actions:
- GetAppYAML(app_name=<affected_service>)
- GetRecentLogs(namespace=<namespace>, service_name=<affected_service>, container=istio-proxy)
Evidence to collect: deployment_port_configuration, deployment_container_port_configuration, sidecar_statusport_annotation, istio_sidecar_port_annotation, istio_proxy_port_binding_logs, service_reachability_status, verified_port_conflict, root_cause_confirmed
Next: (end of this SOP - conclude here)

### Step: service.classify_dns_resolution_failure_logs
Goal: Inspect the failing or restarting service logs for DNS lookup errors, timeout evidence, service instance null messages, or name-resolution failures, preserving whether the same service is partially ready, restarting, or in startup failure.
Actions:
- GetErrorLogs(namespace=<namespace>, service_name=<failing_service>)
Evidence to collect: dns_error_logs, dns_resolution_failure_evidence, dns_timeout_evidence, service_instance_null, crash_cause_classification, failing_service_identified, pod_restart_context, repeated_startup_failure, pod_partial_ready_or_crashloop_context
Next: service.verify_dns_target_service_exists

### Step: service.verify_dns_target_service_exists
Goal: Verify that the Service or database target named in DNS errors exists and is reachable, so the diagnosis can distinguish pod-local DNS configuration from a genuinely missing target.
Actions:
- GetResources(resource_type=services, namespace=<namespace>)
Evidence to collect: target_service_exists, target_service_endpoints, service_reachable, missing_service_excluded, network_unavailability_excluded
Next: service.confirm_pod_dns_misconfiguration

### Step: service.confirm_pod_dns_misconfiguration
Goal: Confirm pod-local DNS misconfiguration by inspecting the failing workload YAML for dnsPolicy and custom nameserver configuration after DNS errors are observed, including cases where the misconfiguration causes repeated service restarts or partial readiness.
Actions:
- GetAppYAML(app_name=<failing_service>)
Evidence to collect: deployment_dns_config, dns_policy, custom_nameserver_configured, pod_dns_policy_none, unreachable_nameserver, dns_failure_causes_service_restart, root_cause_service_dns_resolution_failure, root_cause_confirmation, root_cause_confirmed
Next: (end of this SOP - conclude here)
