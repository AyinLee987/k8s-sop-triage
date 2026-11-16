# SOP: pod_crash_or_restart

Symptom: A pod enters CrashLoopBackOff, exits abnormally, or repeatedly restarts.

Signals:
- pod status is CrashLoopBackOff
- pod repeatedly restarts before becoming Ready
- container exits during startup or runtime
- application pod crashes or restarts
- service logs show panic or runtime exception

Start at: locate_crashing_workload

## Steps

### Step: locate_crashing_workload
Goal: Locate the pod and workload showing a crash or abnormal restart, and retain alert or service context without assigning a root-cause category.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
Evidence to collect: failing_pod_identified, failing_pod_name, affected_workload, failing_service, symptom_service, entrypoint_service, pod_status_summary, pod_readiness_status, pod_restart_counts, active_alerts_status
Next: inspect_crash_evidence

### Step: inspect_crash_evidence
Goal: Classify the observable crash mechanism by inspecting the affected pod's last termination state, events, volume and liveness-probe configuration, and crash logs before exposing a root-cause confirmation branch.
Actions:
- DescribeResource(resource_type=pod, name=<failing_pod>, namespace=<namespace>)
- GetRecentLogs(namespace=<namespace>, service_name=<failing_service>)
Evidence to collect: container_exit_code, container_termination_reason, pod_events, pod_restart_counts, volume_mount_details, liveness_probe_config, container_port, probe_failure_events, crash_log_message, panic_message, stack_trace_present, permission_denied_indicator, probe_port_mismatch_suspected, probe_protocol_mismatch_suspected, repeated_probe_timeout, container_memory_limit_too_low
Next: confirm_volume_mount_permission_denied, confirm_probe_port_mismatch, confirm_probe_protocol_mismatch, confirm_liveness_probe_timing, classify_missing_parameter_logs, locate_relevant_source_files

### Step: confirm_volume_mount_permission_denied
Goal: Confirm whether container logs contain a permission denied error on the mounted volume that explains the pod crash.
Actions:
- GetRecentLogs(namespace=the affected namespace, service_name=the backing datastore service)
Evidence to collect: container_error_log, permission_denied_confirmed, volume_permission_denied_error, security_context_uid
Next: (end of this SOP - conclude here)

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

### Step: confirm_liveness_probe_timing
Goal: Confirm whether liveness probe timing misconfiguration causes pod restarts by inspecting pod events, container state, and probe timing configuration.
Actions:
- DescribeResource(resource_type=pod, name=<failing_pod>, namespace=<namespace>)
Evidence to collect: probe_failure_events, effective_probe_configuration, container_termination_reason, pod_events, liveness_probe_timing_configuration, liveness_probe_timing_config_from_yaml, root_cause_confirmation
Next: (end of this SOP - conclude here)

### Step: classify_missing_parameter_logs
Goal: Inspect runtime and error logs of a suspected service to classify error pattern or exclude the service.
Actions:
- GetRecentLogs(namespace=<application_namespace>, service_name=<service_name>)
- GetErrorLogs(namespace=<application_namespace>, service_name=<service_name>)
Evidence to collect: error_log_pattern, error_type, service_logs_clean, failing_service, missing_parameter_hint
Next: locate_relevant_source_files, confirm_missing_parameter_code

### Step: locate_relevant_source_files
Goal: List source files for a localized service to identify candidate files for code-level defect inspection.
Actions:
- ListCodeFiles(app_name=<service_name>)
Evidence to collect: source_file_list, candidate_source_file_identified, primary_source_file, candidate_handler_files, candidate_source_files, source_file_candidates, relevant_source_files_identified
Next: confirm_missing_parameter_code, confirm_wrong_argument_order_code

### Step: confirm_missing_parameter_code
Goal: Confirm code_missing_parameter when required request data is made absent before use: omitted from a request, assigned nil, overwritten with an empty string, or replaced by an empty/default value. This includes code such as currency = "" or productID = "" even when the downstream consequence is an error response. Do not reinterpret that downstream error as code_wrong_return, and require an actual swap of existing meaningful values before considering code_wrong_argument_order.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
Evidence to collect: source_code_evidence, required_parameter, parameter_cleared_or_omitted, code_missing_parameter_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_wrong_argument_order_code
Goal: Confirm code_wrong_argument_order only when two or more existing meaningful values are supplied to the wrong parameter or request-field positions, such as value A being passed where B belongs and value B where A belongs. A value merely cleared, omitted, nil, empty, or defaulted is code_missing_parameter, not wrong argument order.
Actions:
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
Evidence to collect: source_code_evidence, request_or_call_site, swapped_arguments, code_wrong_argument_order_confirmed
Next: (end of this SOP - conclude here)
