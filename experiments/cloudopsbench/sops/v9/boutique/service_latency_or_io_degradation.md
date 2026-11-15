# SOP: service_latency_or_io_degradation

Symptom: An application service shows sustained response latency, throughput degradation, or abnormal filesystem-I/O activity, including latency isolated to one service while its pods remain healthy.

Signals:
- application service has severe latency or slow response
- service latency degradation is reported
- downstream timeout appears in service logs
- FILESYSTEM_WRITE_SURGE or abnormal fs_write_bytes is reported
- latency is isolated to a service whose pods are Running and Ready
- service latency is elevated without a CPU throttling or CPU saturation alert
- logs show successful downstream operations but caller latency remains high
- a slow-named helper or loop is present in source but has no independent CPU or latency evidence

Start at: locate_service_latency_io_scope

## Steps

### Step: locate_service_latency_io_scope
Goal: Identify the service and pods affected by observable latency degradation or filesystem-I/O anomaly without assigning network or code ownership.
Actions:
- GetAlerts(namespace=<namespace>)
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
Evidence to collect: performance_alert_summary, affected_services, anomalous_service, pod_health_summary, pod_node_mapping, latency_degradation_evidence, filesystem_io_evidence, cpu_throttling_alert_present
Next: inspect_latency_io_service_context

### Step: inspect_latency_io_service_context
Goal: Inspect the latency- or I/O-affected service's pod health, logs, dependency context, alerts, and relevant configuration once to distinguish pod-network delay, artificial delay, file I/O code patterns, and ordinary application failure. Do not let a source-code loop or slow-named helper override missing CPU or network evidence.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, label_selector=app=<service_name>, output_wide=True)
- GetErrorLogs(namespace=<namespace>, service_name=<service_name>)
- GetRecentLogs(namespace=<namespace>, service_name=<service_name>)
- GetServiceDependencies(service_name=<service_name>)
- GetAppYAML(app_name=<service_name>)
Evidence to collect: affected_service, pod_health_status, pod_restart_count, pod_node_placement, error_log_pattern, recent_log_pattern, application_error_present, downstream_timeout_found, fixed_latency_pattern, filesystem_write_surge, connectivity_failure_pattern, service_dependency_graph, downstream_dependency_health, successful_downstream_operations, cpu_throttling_alert_present, resource_limits, code_level_performance_candidate, pod_network_delay_candidate, local_failure_excluded
Next: triage_performance_source

### Step: triage_performance_source
Goal: Locate the affected service's relevant source files and inspect the primary candidate code once before selecting a specific artificial-delay, busy-loop, excessive-read, or excessive-write confirmation. Treat a loop or slow-named helper as a candidate only, not confirmation; record whether the candidate is independently tied to CPU saturation or the observed latency magnitude.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
Evidence to collect: source_file_list, candidate_source_files, primary_source_file, source_code_pattern, artificial_delay_candidate, busy_loop_candidate, excessive_file_read_candidate, excessive_file_write_candidate, suspicious_slow_helper, loop_latency_contribution_unknown, no_obvious_code_defect
Next: confirm_artificial_delay_code, confirm_busy_loop_code, confirm_excessive_file_writes_code, confirm_pod_network_delay_after_code_exclusion

### Step: confirm_artificial_delay_code
Goal: Confirm code_artificial_delay only when an explicit delay operation is active with a meaningful non-zero duration on the failing execution path. A dormant, zero-valued, or unreachable delay hook is insufficient. Active spin or busy-wait code remains code_busy_loop even if a sleep expression also exists. A named slow helper or loop that lacks an explicit active delay is not artificial delay.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=main.go)
Evidence to collect: source_file_list, target_source_file, artificial_delay_present, non_delay_code_pattern, root_cause_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_busy_loop_code
Goal: Confirm code_busy_loop only when an active CPU-intensive loop or busy-wait both exists on the failing path and independently explains observed CPU saturation, CPU throttling, or the observed latency magnitude. Finding an active loop alone, such as a money-multiplication helper, is not sufficient when the service shows high latency but no CPU throttling or CPU saturation alert and downstream calls succeed. In that situation reject busy loop and continue to confirm_pod_network_delay_after_code_exclusion.
Actions:
- GetSourceCode(app_name=<service_name>, file_path=main.go)
- GetAlerts(namespace=<namespace>)
Evidence to collect: source_code_evidence, active_spin_loop, observed_cpu_saturation_or_throttling, loop_latency_contribution, downstream_operations_succeed, busy_loop_confirmed, busy_loop_rejected_due_to_missing_cpu_or_latency_attribution
Next: confirm_pod_network_delay_after_code_exclusion when busy loop is not independently supported; otherwise (end of this SOP - conclude here)

### Step: confirm_excessive_file_writes_code
Goal: Confirm whether repeated or unbounded file writes in the affected service's source code explain FILESYSTEM_WRITE_SURGE or abnormal fs_write_bytes.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<relevant_source_file>)
Evidence to collect: source_code_evidence, repeated_or_unbounded_file_write_path, code_excessive_file_writes_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_pod_network_delay_after_code_exclusion
Goal: Confirm pod_network_delay when latency degradation affects a service whose pods are healthy and logs show no application errors or downstream timeouts, service dependencies are reachable, and no supported code-level delay, CPU busy-loop, or excessive I/O defect independently explains the observed latency magnitude. A plausible but unmeasured code loop such as a money-multiplication helper does not refute pod_network_delay when CPU evidence is absent and dependency operations succeed.
Actions:
- GetServiceDependencies(service_name=<service_name>)
- CheckServiceConnectivity(service_name=<service_name>, port=<service_port>, namespace=<namespace>)
- GetAlerts(namespace=<namespace>)
Evidence to collect: observed_latency_degradation, pods_running_ready, no_application_error_logs, successful_downstream_operations, no_downstream_timeout, service_dependency_topology, dependencies_reachable, no_cpu_saturation_or_throttling, source_code_review_summary, no_supported_latency_code_defect, code_loop_not_independently_explanatory, pod_network_delay_confirmed
Next: (end of this SOP - conclude here)
