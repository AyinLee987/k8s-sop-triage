# SOP: node_network_degradation

Symptom: Network performance or reachability degrades in a node-correlated pattern, such as increased latency or differences between nodes.

Signals:
- performance alert indicates node network degradation
- multiple services degrade on the same node
- traffic quality differs by node placement
- node-level latency or packet-loss symptom is reported
- the same service is reachable from some nodes but not from one node
- cross-node connectivity differs by source or destination node
- node-aligned TCP retransmission spike accompanies service latency
- multiple latency-affected services are colocated on one node

Start at: locate_node_network_scope

## Steps

### Step: locate_node_network_scope
Goal: Identify the node and affected services associated with observable node-network degradation or cross-node traffic-quality differences.
Actions:
- GetAlerts(namespace=<namespace>)
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- GetClusterConfiguration()
Evidence to collect: performance_alert_summary, anomalous_node, affected_services, pod_node_mapping, node_health_status, node_network_evidence
Next: localize_degraded_node

### Step: localize_degraded_node
Goal: Localize a node-correlated network degradation by comparing node health, affected pod placement, and cross-node service connectivity.
Actions:
- GetClusterConfiguration()
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- CheckServiceConnectivity(service_name=<affected_service>, port=<service_port>, namespace=<namespace>)
Evidence to collect: node_health_status, node_taints, pod_node_mapping, affected_services_on_node, node_connectivity_results, suspect_node, cross_node_difference, ordinary_node_failure, node_network_degradation_supported
Next: inspect_node_network_components, confirm_node_network_delay_from_node_status, inspect_service_logs_for_network_packet_loss

### Step: inspect_node_network_components
Goal: Inspect kube-proxy and containerd health once on the localized suspect node to distinguish component unavailability from component-healthy network delay or packet loss.
Actions:
- CheckNodeServiceStatus(node_name=<node_name>, service_name=kube-proxy)
- CheckNodeServiceStatus(node_name=<node_name>, service_name=containerd)
Evidence to collect: kube_proxy_status, containerd_status, node_network_component_status, kube_proxy_unavailable_supported, components_healthy_for_network_attribution
Next: confirm_kube_proxy_unavailable, confirm_node_network_delay_from_node_status, inspect_service_logs_for_network_packet_loss

### Step: confirm_kube_proxy_unavailable
Goal: Confirm kube-proxy unavailability from the component status already collected by the shared node-network component inspection.
Actions:
Evidence to collect: kube_proxy_status, failing_node, kube_proxy_unavailable_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_node_network_delay_from_node_status
Goal: Confirm node_network_delay only when latency or response-delay degradation is correlated with the same suspect node and that node does not show a meaningful TCP Retransmission increase. Small fluctuations alone are insufficient to establish packet loss. If the same suspect node shows a high-magnitude TCP retransmission spike, do not select node_network_delay; continue to packet-loss confirmation.
Actions:
- GetAlerts()
- DescribeResource(resource_type=node, name=<node_name>)
Evidence to collect: suspect_node, node_correlated_latency_degradation, no_high_magnitude_tcp_retransmission_on_suspect_node, node_network_components_healthy, node_network_delay_supported
Next: (end of this SOP - conclude here)

### Step: inspect_service_logs_for_network_packet_loss
Goal: Confirm node_network_packet_loss from a meaningful TCP Retransmission increase on the same suspect node that hosts the affected services. Timeout, Unavailable, DeadlineExceeded, traffic-drop, or latency evidence is supportive but is not sufficient without the node-aligned retransmission signal. A clean application error log does not refute packet loss when the node-aligned retransmission spike and affected-service placement are present; absence of timeout logs is expected in a packet-loss incident.
Actions:
- GetAlerts()
- GetErrorLogs(namespace=<application_namespace>, service_name=<service_name>)
Evidence to collect: suspect_node, high_magnitude_tcp_retransmission, retransmission_node_matches_suspect_node, affected_services_on_suspect_node, packet_loss_supporting_timeouts, clean_application_logs_do_not_refute_retransmission_evidence, node_network_packet_loss_supported
Next: (end of this SOP - conclude here)
