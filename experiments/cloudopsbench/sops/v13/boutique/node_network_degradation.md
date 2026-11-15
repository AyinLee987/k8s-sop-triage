# SOP: node_network_degradation

Symptom: Network performance or reachability degrades in a node-correlated pattern, such as increased latency, throughput drop, or differences between nodes.

Signals:
- performance alert indicates node network degradation
- multiple services degrade on the same node
- traffic quality differs by node placement
- node-level latency or packet-loss symptom is reported
- the same service is reachable from some nodes but not from one node
- cross-node connectivity differs by source or destination node
- TCP retransmission increase appears on the suspect node without explicit packet-drop evidence

Start at: locate_node_network_scope

## Steps

### Step: locate_node_network_scope
Goal: Identify the node and affected services associated with observable node-network degradation or cross-node traffic-quality differences.
Actions:
- GetAlerts(namespace=<namespace>)
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- GetClusterConfiguration()
Evidence to collect: performance_alert_summary, anomalous_node, affected_services, pod_node_mapping, node_health_status, latency_degradation_pattern, traffic_drop_pattern, tcp_retransmission_signal, direct_packet_loss_signal, node_network_evidence
Next: localize_degraded_node

### Step: localize_degraded_node
Goal: Localize a node-correlated network degradation by comparing node health, affected pod placement, and cross-node service connectivity.
Actions:
- GetClusterConfiguration()
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- CheckServiceConnectivity(service_name=<affected_service>, port=<service_port>, namespace=<namespace>)
Evidence to collect: node_health_status, node_taints, pod_node_mapping, affected_services_on_node, node_connectivity_results, suspect_node, cross_node_difference, ordinary_node_failure, node_network_degradation_supported, latency_dominant_symptom, connectivity_failure_dominant_symptom, tcp_retransmission_signal_present, direct_packet_loss_evidence_present
Next: inspect_node_network_components

### Step: inspect_node_network_components
Goal: Inspect kube-proxy and containerd health once on the localized suspect node to distinguish component unavailability from component-healthy network delay or packet loss.
Actions:
- CheckNodeServiceStatus(node_name=<node_name>, service_name=kube-proxy)
- CheckNodeServiceStatus(node_name=<node_name>, service_name=containerd)
Evidence to collect: kube_proxy_status, containerd_status, node_network_component_status, kube_proxy_unavailable_supported, components_healthy_for_network_attribution
Next: confirm_kube_proxy_unavailable, evaluate_node_transport_evidence

### Step: confirm_kube_proxy_unavailable
Goal: Confirm kube-proxy unavailability from the component status already collected by the shared node-network component inspection.
Actions:
Evidence to collect: kube_proxy_status, failing_node, kube_proxy_unavailable_confirmed
Next: (end of this SOP - conclude here)

### Step: evaluate_node_transport_evidence
Goal: Distinguish node_network_delay from node_network_packet_loss using node-aligned transport evidence. A TCP retransmission increase is not by itself proof of packet loss: delayed ACKs and retransmission timeouts caused by node network delay can also raise retransmission counts. Direct packet-loss evidence is required before selecting packet loss, such as sustained elevated retransmission ratio/rate, packet-drop counters, or connection resets/failures correlated with the suspect node. A single new retransmission spike from a zero or low baseline, together with latency/slow responses and otherwise successful connections, is insufficient for packet loss and may support delay.
Actions:
- GetAlerts()
- DescribeResource(resource_type=node, name=<node_name>)
- GetErrorLogs(namespace=<application_namespace>, service_name=<service_name>)
Evidence to collect: suspect_node, node_correlated_latency, latency_magnitude, tcp_retransmission_present, tcp_retransmission_absolute_rate, tcp_retransmission_sustained_high, packet_drop_counters, connection_resets_or_failures, timeout_pattern, successful_connections_to_affected_services, direct_packet_loss_evidence, packet_loss_not_supported, delay_supported
Next: confirm_node_network_delay_from_node_status, inspect_service_logs_for_network_packet_loss

### Step: confirm_node_network_delay_from_node_status
Goal: Confirm node_network_delay when latency or response-delay degradation is correlated with the same suspect node and the node has healthy network components. A TCP retransmission increase on the suspect node does not refute delay when direct packet-loss evidence is absent, because retransmissions can be secondary to delay. Small fluctuations alone are insufficient; require node-correlated latency magnitude or cross-node latency comparison. If direct packet-loss evidence is present, reject delay and use packet-loss confirmation.
Actions:
- GetAlerts()
- DescribeResource(resource_type=node, name=<node_name>)
Evidence to collect: suspect_node, node_correlated_latency_degradation, cross_node_latency_difference, node_network_components_healthy, tcp_retransmission_present_but_nonspecific, direct_packet_loss_evidence_absent, node_network_delay_supported
Next: (end of this SOP - conclude here)

### Step: inspect_service_logs_for_network_packet_loss
Goal: Confirm node_network_packet_loss only from direct packet-loss evidence aligned to the same suspect node, such as sustained elevated retransmission ratio/rate plus packet-drop counters, or repeated connection resets, connection failures, or timeouts attributable to packet loss. A TCP retransmission increase alone, a zero-to-small retransmission spike, traffic drop, latency, or downstream timeouts without direct packet-drop evidence is not sufficient. If evidence is only latency/slow response with a nonspecific retransmission increase, reject packet loss and use confirm_node_network_delay_from_node_status.
Actions:
- GetAlerts()
- DescribeResource(resource_type=node, name=<node_name>)
- GetErrorLogs(namespace=<application_namespace>, service_name=<service_name>)
Evidence to collect: suspect_node, sustained_high_tcp_retransmission, packet_drop_counters, connection_reset_or_failure_pattern, retransmission_node_matches_suspect_node, affected_services_on_suspect_node, timeout_pattern, direct_packet_loss_evidence, node_network_packet_loss_confirmed
Next: (end of this SOP - conclude here)
