# SOP: deployment_scaled_to_zero

Symptom: A TrainTicket service has no running backend pods or no available endpoints.

Signals:
- affected service has empty endpoints or no backend pods
- service logs point to a downstream service with no available instance
- connectivity to a downstream service fails because no backend is available
- pods are missing rather than crashing
- the symptom is visible from Services, Endpoints, Deployments, or pod listings

Start at: runtime.locate_scaled_to_zero_service_scope

## Steps

### Step: runtime.locate_scaled_to_zero_service_scope
Goal: Localize a TrainTicket service with missing backend pods, empty endpoints, or Deployment availability suggesting the workload may be scaled to zero.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>)
- GetResources(resource_type=services, namespace=<namespace>)
- GetResources(resource_type=endpoints, namespace=<namespace>)
- GetAlerts(namespace=<namespace>)
Evidence to collect: affected_service, affected_workload, runtime_pod_status, alert_details, missing_pods, empty_endpoints, service_without_endpoints, service_list, deployment_availability
Next: runtime.trace_missing_backend_dependency, runtime.exclude_service_selector_or_port_issue, runtime.confirm_deployment_scaled_to_zero

### Step: runtime.trace_missing_backend_dependency
Goal: Use service logs, dependency graph, and connectivity checks to determine whether the request failure points to a downstream service whose backend instances are missing.
Actions:
- GetErrorLogs(namespace=<namespace>, service_name=<affected_service>)
- GetServiceDependencies(service_name=<affected_service>)
- CheckServiceConnectivity(service_name=<downstream_service>, port=<service_port>, namespace=<namespace>)
Evidence to collect: error_log_analysis, failure_source_service, downstream_service_identified, downstream_service_instance_missing, downstream_service_candidates, service_dependencies, downstream_connectivity_status, unreachable_downstream, database_healthy
Next: runtime.exclude_service_selector_or_port_issue, runtime.confirm_deployment_scaled_to_zero

### Step: runtime.exclude_service_selector_or_port_issue
Goal: Exclude Service selector, target port, application YAML, and sibling-service configuration issues before confirming that the observed missing backend is due to a scaled-to-zero Deployment.
Actions:
- DescribeResource(resource_type=service, name=<affected_service>, namespace=<namespace>)
- DescribeResource(resource_type=deployment, name=<affected_workload>, namespace=<namespace>)
Evidence to collect: service_selector_and_port_config, selector_mismatch_excluded, service_configuration_correct, service_misconfiguration_excluded, app_configuration_yaml, configuration_validity, other_service_replica_status, cross_service_comparison, deployment_configuration
Next: runtime.confirm_deployment_scaled_to_zero

### Step: runtime.confirm_deployment_scaled_to_zero
Goal: Confirm that the affected Deployment is configured with zero desired replicas and that this explains the missing backend pods or empty endpoints.
Actions:
- GetResources(resource_type=deployments, namespace=<namespace>, name=<affected_workload>, output_yaml=True)
Evidence to collect: affected_deployment, deployment_replica_config, desired_replicas, available_replicas, zero_replicas_evidence, deployment_scaled_to_zero, deployment_events, scaling_timeline, root_cause_confirmation
Next: (end of this SOP - conclude here)
