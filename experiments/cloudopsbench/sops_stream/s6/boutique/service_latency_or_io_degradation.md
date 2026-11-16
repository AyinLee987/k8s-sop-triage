# SOP: service_latency_or_io_degradation

Symptom: An application service shows sustained response latency, throughput degradation, or abnormal filesystem-I/O activity.

Signals:
- application service has severe latency or slow response
- service latency degradation is reported
- downstream timeout appears in service logs
- FILESYSTEM_WRITE_SURGE or abnormal fs_write_bytes is reported

Start at: locate_service_latency_io_scope

## Steps

### Step: locate_service_latency_io_scope
Goal: Identify the service and pods affected by observable latency degradation or filesystem-I/O anomaly without assigning network or code ownership.
Actions:
- GetAlerts(namespace=<namespace>)
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
Evidence to collect: performance_alert_summary, affected_services, anomalous_service, pod_health_summary, pod_node_mapping, latency_degradation_evidence, filesystem_io_evidence
Next: inspect_latency_io_service_context

### Step: inspect_latency_io_service_context
Goal: Inspect the latency- or I/O-affected service's pod health, logs, dependency context, and relevant configuration once to distinguish pod-network delay, artificial delay, file I/O code patterns, and ordinary application failure.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, label_selector=app=<service_name>, output_wide=True)
- GetErrorLogs(namespace=<namespace>, service_name=<service_name>)
- GetRecentLogs(namespace=<namespace>, service_name=<service_name>)
Evidence to collect: affected_service, pod_health_status, pod_restart_count, pod_node_placement, error_log_pattern, recent_log_pattern, application_error_present, downstream_timeout_found, fixed_latency_pattern, filesystem_write_surge, connectivity_failure_pattern, code_level_performance_candidate, local_failure_excluded
Next: triage_performance_source

### Step: triage_performance_source
Goal: Locate the affected service's relevant source files and inspect the primary candidate code once before selecting a specific artificial-delay, busy-loop, excessive-read, or excessive-write confirmation.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
Evidence to collect: source_file_list, candidate_source_files, primary_source_file, source_code_pattern, artificial_delay_candidate, busy_loop_candidate, excessive_file_read_candidate, excessive_file_write_candidate, no_obvious_code_defect
Next: confirm_artificial_delay_code, confirm_busy_loop_code, confirm_excessive_file_writes_code, attribute_pod_network_delay_after_code_exclusion

### Step: confirm_artificial_delay_code
Goal: Confirm code_artificial_delay only when an explicit delay operation is active with a meaningful non-zero duration on the failing execution path. A dormant, zero-valued, or unreachable delay hook is insufficient. Active spin or busy-wait code remains code_busy_loop even if a sleep expression also exists.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=main.go)
Evidence to collect: source_file_list, target_source_file, artificial_delay_present, non_delay_code_pattern, root_cause_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_busy_loop_code
Goal: Confirm code_busy_loop when an active CPU-intensive loop or busy-wait explains the observed CPU saturation or latency. Concrete active-spin evidence remains a busy loop even if the same path also contains a sleep expression; a genuinely blocking delay without active spinning belongs to code_artificial_delay.
Actions:
- GetSourceCode(app_name=<service_name>, file_path=main.go)
Evidence to collect: source_code_evidence, busy_loop_found, non_busy_loop_code_pattern, root_cause_confirmation, code_defect_pattern
Next: (end of this SOP - conclude here)

### Step: confirm_excessive_file_writes_code
Goal: Confirm whether repeated or unbounded file writes in the affected service's source code explain FILESYSTEM_WRITE_SURGE / abnormal fs_write_bytes.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<relevant_source_file>)
Evidence to collect: source_code_evidence, repeated_or_unbounded_file_write_path, code_excessive_file_writes_confirmed
Next: (end of this SOP - conclude here)

### Step: attribute_pod_network_delay_after_code_exclusion
Goal: Attribute remaining service latency to pod_network_delay after supported code-level and dependency explanations are inspected and not found.
Actions:
- GetServiceDependencies(service_name=<service_name>)
Evidence to collect: observed_latency_degradation, source_code_review_summary, no_supported_latency_code_defect, service_dependency_topology, no_visible_downstream_bottleneck, pod_network_delay_attributed
Next: (end of this SOP - conclude here)
