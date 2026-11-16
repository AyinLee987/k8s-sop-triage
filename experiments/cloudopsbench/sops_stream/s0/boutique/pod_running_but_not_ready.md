# SOP: pod_running_but_not_ready

Symptom: A pod is Running but does not become Ready, such as showing READY 0/1.

Signals:
- pod is Running but NotReady
- pod readiness is 0/1
- workload exists but readiness gate never becomes healthy

Start at: locate_runtime_affected_pod

## Steps

### Step: locate_runtime_affected_pod
Goal: Locate the pod-level runtime anomaly by listing pods and checking readiness, runtime status, and restart counts.
Actions:
- GetResources(resource_type=pods, namespace=<application_namespace>, output_wide=True)
Evidence to collect: pod_runtime_status, pod_readiness_status, pod_restart_counts, failing_pod_identified, abnormal_pod_identified, no_failing_pod_identified, alert_anomaly_status
Next: classify_readiness_probe_failure

### Step: classify_readiness_probe_failure
Goal: Classify readiness probe failure reason by inspecting NotReady pod events and probe configuration — port mismatch or protocol mismatch.
Actions:
- DescribeResource(resource_type=pod, name=<failing_pod>, namespace=<namespace>)
Evidence to collect: readiness_probe_config, container_port, probe_failure_events, suspected_port_mismatch, readiness_probe_protocol_mismatch_suspected, probe_protocol_consistency, readiness_probe_incorrect_protocol
Next: confirm_probe_port_mismatch, confirm_probe_protocol_mismatch

### Step: confirm_probe_port_mismatch
Goal: Confirm liveness or readiness probe port mismatch from deployment or pod configuration.
Actions:
- GetAppYAML(app_name=<failing_app>)
Evidence to collect: deployment_probe_config, readiness_probe_config, container_port, probe_port_consistency, port_mismatch_confirmed, root_cause_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_probe_protocol_mismatch
Goal: Confirm liveness or readiness probe protocol mismatch by inspecting workload YAML probe protocol settings.
Actions:
- GetAppYAML(app_name=<failing_app>)
Evidence to collect: deployment_yaml, liveness_probe_config, readiness_probe_config, protocol_consistency, probe_protocol_mismatch_confirmed, root_cause_confirmed
Next: (end of this SOP - conclude here)
