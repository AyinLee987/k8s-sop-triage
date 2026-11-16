# SOP: service_connectivity_or_routing_failure

Symptom: A caller cannot resolve or connect to a Kubernetes Service, or traffic fails to reach otherwise healthy backends; this includes partial unreachability where other services remain reachable.

Signals:
- downstream Service is unreachable while pods are Running and Ready
- partial service unreachability: one Service fails while other Services and pods are healthy
- DNS lookup for a downstream service fails
- connection refused or connection timeout identifies a downstream target
- Service has empty or incorrect endpoints
- traffic to an existing Service fails despite healthy backend pods
- Service port or targetPort protocol is UDP while the workload, probes, or clients expect TCP or gRPC
- Service port number matches the container port but the protocol differs

Start at: locate_service_connectivity_failure

## Steps

### Step: locate_service_connectivity_failure
Goal: Localize the failed downstream Service and calling path when pods are healthy but DNS, connection, endpoint, or service traffic still fails.
Actions:
- GetServiceDependencies(service_name=<entrypoint_service>)
- GetErrorLogs(namespace=<namespace>, service_name=<entrypoint_service>)
- GetResources(resource_type=services, namespace=<namespace>, output_wide=True)
Evidence to collect: pod_health_status, service_inventory, entrypoint_service, service_dependency_graph, calling_service, failed_dependency, failing_service, failing_service_port, failing_service_protocol, connectivity_failure_pattern, partial_service_unreachability, healthy_services, dns_resolution_failure_pattern
Next: confirm_service_address_configuration, inspect_service_routing_context

### Step: confirm_service_address_configuration
Goal: Confirm that the service owning a downstream address configuration uses an invalid hostname in its environment variables.
Actions:
- GetAppYAML(app_name=<failing_service_name>)
Evidence to collect: configuration_owner_service, downstream_address_env_var, env_var_value, actual_service_name, address_mismatch_confirmed
Next: (end of this SOP - conclude here)

### Step: inspect_service_routing_context
Goal: Inspect the localized Service's selector, endpoints, ports, port protocols, targetPort protocols, and backing Pod labels/container ports/protocols once before selecting a routing confirmation branch. A numeric port match is not enough: separately record whether the Service and targetPort are TCP or UDP and compare with the container listener, probe protocol, and client expectation.
Actions:
- DescribeResource(resource_type=service, name=<suspected_downstream_service>, namespace=<namespace>)
- GetResources(resource_type=pods, namespace=<namespace>, show_labels=True)
- GetAppYAML(app_name=<suspected_downstream_service>)
Evidence to collect: service_selector, service_endpoints, service_port, service_port_protocol, service_targetPort, service_targetPort_protocol, service_protocol, candidate_pod_labels, container_port, container_port_protocol, probe_protocol, client_expected_protocol, selector_mismatch_candidate, target_port_number_mismatch_candidate, protocol_mismatch_candidate, udp_service_for_tcp_workload_candidate, routing_configuration_consistent
Next: confirm_service_target_port_mapping, confirm_service_selector_mapping, confirm_service_protocol_compatibility

### Step: confirm_service_target_port_mapping
Goal: Compare the Service targetPort number with the container's listening port number to confirm a numeric port mapping mismatch. Do not use this branch when the numbers match but the Service or targetPort protocol differs; that is a protocol mismatch.
Actions:
- GetAppYAML(app_name=suspected_downstream_service)
Evidence to collect: container_port, container_port_name, service_targetPort_number, service_port_number, port_number_mismatch_verdict, protocols_match
Next: (end of this SOP - conclude here)

### Step: confirm_service_protocol_compatibility
Goal: Confirm service_protocol_mismatch only when the Service port or targetPort protocol (TCP/UDP) is incompatible with the container listening protocol or client/probe expectation. A UDP Service exposing a port for a gRPC/HTTP/TCP workload is protocol mismatch even when the numeric port matches the container port. If protocols match and only the numeric targetPort differs, use confirm_service_target_port_mapping instead.
Actions:
- DescribeResource(resource_type=service, name=<suspected_downstream_service>, namespace=<namespace>)
- GetAppYAML(app_name=suspected_downstream_service)
Evidence to collect: service_port_protocol, service_targetPort_protocol, container_port, container_port_protocol, probe_protocol, client_expected_protocol, service_protocol_is_udp, workload_expects_tcp_or_grpc, numeric_ports_match, protocol_mismatch_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_service_selector_mapping
Goal: Confirm service selector mismatch by proving the Service selector does not match backend pod labels.
Actions:
- DescribeResource(resource_type=service, name=<suspected_downstream_service>, namespace=<application_namespace>)
- GetResources(resource_type=pods, namespace=<application_namespace>, show_labels=True)
- GetResources(resource_type=endpoints, namespace=<application_namespace>)
Evidence to collect: selector_value, pod_label_value, selector_label_mismatch, empty_or_wrong_endpoints, root_cause_confirmed
Next: (end of this SOP - conclude here)
