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
- connection refused or reset before headers occurs immediately after a pod start or deployment rollout
- caller connection refused targets the downstream Service ClusterIP on a port not exposed by that Service while the Service endpoints and container port are healthy
- caller dials currencyservice:7001 while the currencyservice Service and container listen on 7000/TCP
- caller downstream address environment variable contains the correct service name but a wrong port
- Service port and targetPort match the container listening port, but the caller configured port differs
- historical connection-refused error occurs but current Service endpoints and connectivity are healthy
- callee logs show successful operations after the connection-refused event
- Service selector, ports, targetPort, and endpoints are correct but active connectivity to the endpoint still fails
- pod log retrieval for the endpoint pod fails because the API server cannot connect to the node kubelet on port 10250
- the endpoint pod is Running and Ready on a node whose kubelet service is inactive or dead
- the endpoint pod node status is Unknown or NotReady while Service routing configuration appears correct

Start at: locate_service_connectivity_failure

## Steps

### Step: locate_service_connectivity_failure
Goal: Localize the failed downstream Service and calling path when pods are healthy but DNS, connection, endpoint, or service traffic still fails, including caller-side address or port mismatches. When a connection refusal names a downstream Service ClusterIP and a port that differs from the Service port, capture the caller target address and port for a caller env-var address check.
Actions:
- GetServiceDependencies(service_name=<entrypoint_service>)
- GetErrorLogs(namespace=<namespace>, service_name=<entrypoint_service>)
- GetResources(resource_type=services, namespace=<namespace>, output_wide=True)
- GetAppYAML(app_name=<entrypoint_service>) when a dial error names a downstream Service ClusterIP and a port different from the Service port
Evidence to collect: pod_health_status, service_inventory, entrypoint_service, service_dependency_graph, calling_service, failed_dependency, failing_service, failing_service_port, failing_service_protocol, connectivity_failure_pattern, partial_service_unreachability, healthy_services, dns_resolution_failure_pattern, connection_refused_or_reset_pattern, error_timestamp, endpoint_pod_name, endpoint_node, caller_target_address, caller_target_port, service_exposed_port, service_and_container_port_consistent, caller_env_address_candidate
Next: correlate_connection_refusal_with_pod_lifecycle, confirm_service_address_configuration, inspect_service_routing_context, inspect_endpoint_node_component_health

### Step: correlate_connection_refusal_with_pod_lifecycle
Goal: Distinguish a transient startup/rollout connectivity gap from persistent Service routing misconfiguration, caller address configuration mismatch, or an application code defect when the observed error is connection refused or reset before headers. Compare the error timestamp with the callee pod start time, deployment revision/rollout, current endpoints, current connectivity, and callee logs. A connection refusal that persists with a fixed wrong port from a caller env var is not a transient rollout gap; route to confirm_service_address_configuration.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- DescribeResource(resource_type=deployment, name=<suspected_downstream_service>, namespace=<namespace>)
- DescribeResource(resource_type=service, name=<suspected_downstream_service>, namespace=<namespace>)
- CheckServiceConnectivity(service_name=<suspected_downstream_service>, port=<service_port>, namespace=<namespace>)
- GetRecentLogs(namespace=<namespace>, service_name=<suspected_downstream_service>)
Evidence to collect: connection_error_type, error_timestamp, callee_pod_start_time, callee_pod_age, recent_rollout_or_recreation, deployment_revision, current_service_endpoints, current_connectivity_success, callee_successful_operations_after_error, application_semantic_error_absent, transient_startup_or_rollout_gap_supported, caller_target_port_differs_from_service_port, fixed_wrong_port_not_transient, service_and_container_port_consistent, service_env_address_mismatch_candidate
Next: confirm_transient_rollout_connectivity_gap, confirm_service_address_configuration, inspect_service_routing_context, inspect_endpoint_node_component_health

### Step: confirm_transient_rollout_connectivity_gap
Goal: Confirm transient_startup_or_rollout_connectivity_gap only when a connection-refused or reset error occurred during or immediately after a callee pod start/rollout, current Service endpoints and connectivity are healthy, callee logs show successful operations afterward, and there is no persistent application-level error such as InvalidArgument or NotFound. Do not select this branch when the caller consistently dials a wrong port from its environment variable or when the error includes a port not exposed by the Service while the Service and container ports agree; a fixed wrong-port connection refused is an address configuration mismatch, not a transient rollout gap. Do not select this branch from a code-level missing-parameter symptom; conversely, do not attribute a transient connection refusal to code_missing_parameter merely because source inspection found an unrelated cleared field.
Actions:
Evidence to collect: transient_gap_confirmed, connection_refused_during_startup_or_rollout, current_connectivity_success, callee_success_after_error, no_persistent_application_semantic_error, code_missing_parameter_not_supported, caller_target_port_differs_from_service_port, fixed_wrong_port_not_transient
Next: (end of this SOP - conclude here)

### Step: confirm_service_address_configuration
Goal: Confirm service_env_var_address_mismatch when the caller service owns a downstream address environment variable whose hostname or port does not match the actual downstream Service and container listener. The address may be wrong by invalid hostname or by using a port that the Service does not expose; for example frontend CURRENCY_SERVICE_ADDR=currencyservice:7001 while the currencyservice Service exposes and targets 7000/TCP and the container listens on 7000. Treat the caller as the configuration owner. If the Service targetPort and container port match and endpoints are healthy, do not classify this as service_port_mapping_mismatch or service_selector_mismatch.
Actions:
- GetAppYAML(app_name=<failing_service_name>)
- GetAppYAML(app_name=<downstream_service_name>) when checking Service and container port consistency
- DescribeResource(resource_type=service, name=<downstream_service_name>, namespace=<namespace>) when checking Service port and endpoints
Evidence to collect: configuration_owner_service, downstream_address_env_var, env_var_value, actual_service_name, actual_service_port, caller_target_port, service_port, service_targetPort, container_port, service_and_container_ports_consistent, service_endpoints_healthy, wrong_hostname_or_wrong_port_in_env_var, address_mismatch_confirmed, service_env_var_address_mismatch_confirmed, service_port_mapping_not_root_cause, service_selector_not_root_cause
Next: (end of this SOP - conclude here)

### Step: inspect_service_routing_context
Goal: Inspect the localized Service's selector, endpoints, ports, port protocols, targetPort protocols, and backing Pod labels/container ports/protocols once before selecting a routing confirmation branch. A numeric port match is not enough: separately record whether the Service and targetPort are TCP or UDP and compare with the container listener, probe protocol, and client expectation. If Service selector, ports, targetPort, and endpoints are consistent and the container listens on the Service targetPort, but the caller error dials a different port, route to confirm_service_address_configuration; do not select service_target_port_mapping or service_protocol_mismatch from the caller's wrong port alone. When selector, ports, protocols, and endpoints are consistent and the caller dials the Service-exposed port but active connectivity to the endpoint still fails, route to endpoint node component health instead of assuming network degradation.
Actions:
- DescribeResource(resource_type=service, name=<suspected_downstream_service>, namespace=<namespace>)
- GetResources(resource_type=pods, namespace=<namespace>, show_labels=True)
- GetAppYAML(app_name=<suspected_downstream_service>)
Evidence to collect: service_selector, service_endpoints, service_port, service_port_protocol, service_targetPort, service_targetPort_protocol, service_protocol, candidate_pod_labels, container_port, container_port_protocol, probe_protocol, client_expected_protocol, selector_mismatch_candidate, target_port_number_mismatch_candidate, protocol_mismatch_candidate, udp_service_for_tcp_workload_candidate, routing_configuration_consistent, caller_target_port, caller_target_port_differs_from_service_port, service_and_container_port_consistent, service_address_configuration_candidate, active_connectivity_failed, endpoint_node
Next: confirm_service_address_configuration, confirm_service_target_port_mapping, confirm_service_selector_mapping, confirm_service_protocol_compatibility, inspect_endpoint_node_component_health

### Step: inspect_endpoint_node_component_health
Goal: When Service selector, ports, protocols, and endpoints are correct and the caller dials the Service-exposed port, but active connectivity to the endpoint fails, inspect the endpoint pod's node and kubelet health before attributing the failure to Service routing, pod network delay, or node network packet loss. Do not use this branch when the caller dials a port the Service does not expose; that is service address configuration mismatch. API server log retrieval failing with dial tcp node:10250 connection refused is a strong kubelet reachability signal.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- CheckNodeServiceStatus(node_name=<endpoint_node>, service_name=kubelet)
- GetClusterConfiguration()
Evidence to collect: endpoint_pod_name, endpoint_node, endpoint_pod_running_ready, service_routing_configuration_consistent, caller_target_port_matches_service_port, service_address_configuration_mismatch_excluded, active_connectivity_failed, kubelet_log_retrieval_error, kubelet_api_connection_refused, kubelet_service_status, node_status, node_condition_status, kubelet_unavailable_supported, node_network_packet_loss_not_supported_without_retransmission, node_network_delay_not_supported_without_latency
Next: confirm_kubelet_unavailable_for_endpoint_node, confirm_service_target_port_mapping, confirm_service_selector_mapping, confirm_service_protocol_compatibility

### Step: confirm_kubelet_unavailable_for_endpoint_node
Goal: Confirm kubelet_unavailable as the cause of partial service unreachability when the unreachable Service endpoint pod is Running and Ready on a node whose kubelet service is inactive or dead, or the API server cannot connect to that node on port 10250, and the node status is Unknown or NotReady. Do not classify this as node_network_packet_loss or node_network_delay merely because the endpoint pod is unreachable; packet loss requires node-aligned retransmission evidence, and delay requires latency evidence. Do not use this branch when the caller is dialing a port the Service does not expose; verify the caller target port matches the Service port first.
Actions:
Evidence to collect: endpoint_pod_name, endpoint_node, kubelet_service_status_inactive_or_dead, kubelet_api_connection_refused, node_status_unknown_or_not_ready, service_routing_configuration_consistent, caller_target_port_matches_service_port, service_address_configuration_mismatch_excluded, active_connectivity_failed, kubelet_unavailable_confirmed, node_network_packet_loss_excluded, node_network_delay_excluded
Next: (end of this SOP - conclude here)

### Step: confirm_service_target_port_mapping
Goal: Compare the Service targetPort number with the container's listening port number to confirm a numeric port mapping mismatch. Do not use this branch when the Service targetPort and container port match and the caller dials a different port from an environment variable; that is caller service address configuration mismatch. Service targetPort mapping concerns only the Service-to-container mapping, not the caller's configured port. Do not use this branch when the numbers match but the Service or targetPort protocol differs; that is a protocol mismatch.
Actions:
- GetAppYAML(app_name=suspected_downstream_service)
- GetAppYAML(app_name=<caller_service>) when the caller target port differs from the Service port
Evidence to collect: container_port, container_port_name, service_targetPort_number, service_port_number, port_number_mismatch_verdict, protocols_match, caller_target_port, caller_target_port_differs_from_service_port, service_and_container_ports_match, caller_env_address_mismatch_candidate
Next: (end of this SOP - conclude here)

### Step: confirm_service_protocol_compatibility
Goal: Confirm service_protocol_mismatch only when the Service port or targetPort protocol (TCP/UDP) is incompatible with the container listening protocol or client/probe expectation. A UDP Service exposing a port for a gRPC/HTTP/TCP workload is protocol mismatch even when the numeric port matches the container port. If protocols match and only the numeric targetPort differs, use confirm_service_target_port_mapping instead. If protocols and Service ports match but the caller dials a different port from its environment variable, use confirm_service_address_configuration.
Actions:
- DescribeResource(resource_type=service, name=<suspected_downstream_service>, namespace=<namespace>)
- GetAppYAML(app_name=suspected_downstream_service)
Evidence to collect: service_port_protocol, service_targetPort_protocol, container_port, container_port_protocol, probe_protocol, client_expected_protocol, service_protocol_is_udp, workload_expects_tcp_or_grpc, numeric_ports_match, caller_target_port_differs_from_service_port, service_address_configuration_candidate, protocol_mismatch_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_service_selector_mapping
Goal: Confirm service selector mismatch by proving the Service selector does not match backend pod labels.
Actions:
- DescribeResource(resource_type=service, name=<suspected_downstream_service>, namespace=<application_namespace>)
- GetResources(resource_type=pods, namespace=<application_namespace>, show_labels=True)
- GetResources(resource_type=endpoints, namespace=<application_namespace>)
Evidence to collect: selector_value, pod_label_value, selector_label_mismatch, empty_or_wrong_endpoints, service_ports_consistent, caller_target_port_differs_from_service_port, service_address_configuration_candidate, root_cause_confirmed
Next: (end of this SOP - conclude here)
