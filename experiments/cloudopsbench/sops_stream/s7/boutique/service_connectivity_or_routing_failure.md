# SOP: service_connectivity_or_routing_failure

Symptom: A caller cannot resolve or connect to a Kubernetes Service, or traffic fails to reach otherwise healthy backends.

Signals:
- downstream Service is unreachable while pods are Running and Ready
- DNS lookup for a downstream service fails
- connection refused or connection timeout identifies a downstream target
- Service has empty or incorrect endpoints
- traffic to an existing Service fails despite healthy backend pods

Start at: locate_service_connectivity_failure

## Steps

### Step: locate_service_connectivity_failure
Goal: Localize the failed downstream Service and calling path when pods are healthy but DNS, connection, endpoint, or service traffic still fails.
Actions:
- GetServiceDependencies(service_name=<entrypoint_service>)
- GetErrorLogs(namespace=<namespace>, service_name=<entrypoint_service>)
Evidence to collect: pod_health_status, service_inventory, entrypoint_service, service_dependency_graph, calling_service, failed_dependency, failing_service, failing_service_port, connectivity_failure_pattern, dns_resolution_failure_pattern
Next: confirm_service_address_configuration, inspect_service_routing_context

### Step: confirm_service_address_configuration
Goal: Confirm that the service owning a downstream address configuration uses an invalid hostname in its environment variables.
Actions:
- GetAppYAML(app_name=<failing_service_name>)
Evidence to collect: configuration_owner_service, downstream_address_env_var, env_var_value, actual_service_name, address_mismatch_confirmed
Next: (end of this SOP - conclude here)

### Step: inspect_service_routing_context
Goal: Inspect the localized Service's selector, endpoints, ports, protocol, and backing Pod labels/container ports once before selecting a routing confirmation branch.
Actions:
- DescribeResource(resource_type=service, name=<suspected_downstream_service>, namespace=<namespace>)
- GetResources(resource_type=pods, namespace=<namespace>, show_labels=True)
- GetAppYAML(app_name=<suspected_downstream_service>)
Evidence to collect: service_selector, service_endpoints, service_port, service_targetPort, service_protocol, candidate_pod_labels, container_port, selector_mismatch_candidate, target_port_mismatch_candidate, protocol_mismatch_candidate, routing_configuration_consistent
Next: confirm_service_target_port_mapping, confirm_service_selector_mapping, confirm_service_protocol_compatibility

### Step: confirm_service_target_port_mapping
Goal: Compare the Service targetPort with the container's listening port to confirm a port mapping mismatch.
Actions:
- GetAppYAML(app_name=suspected_downstream_service)
Evidence to collect: container_port, container_port_name, service_targetPort, service_port, port_mismatch_verdict
Next: (end of this SOP - conclude here)

### Step: confirm_service_protocol_compatibility
Goal: Confirm that a service protocol mismatch (UDP vs TCP/gRPC) is the root cause.
Actions:
- GetAppYAML(app_name=suspected_downstream_service)
Evidence to collect: service_protocol, container_protocol, client_expected_protocol, connectivity_failure, protocol_mismatch_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_service_selector_mapping
Goal: Confirm service selector mismatch by proving the Service selector does not match backend pod labels.
Actions:
- DescribeResource(resource_type=service, name=<suspected_downstream_service>, namespace=<application_namespace>)
- GetResources(resource_type=pods, namespace=<application_namespace>, show_labels=True)
- GetResources(resource_type=endpoints, namespace=<application_namespace>)
Evidence to collect: selector_value, pod_label_value, selector_label_mismatch, empty_or_wrong_endpoints, root_cause_confirmed
Next: (end of this SOP - conclude here)
