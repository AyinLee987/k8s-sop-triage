# SOP: service_cpu_or_resource_saturation

Symptom: An application service shows sustained CPU saturation or throttling with associated performance degradation.

Signals:
- service has CPU throttling alert
- application service has CPU saturation
- pod CPU usage or throttling is abnormally high
- service performance degradation is dominated by CPU pressure

Start at: locate_service_cpu_scope

## Steps

### Step: locate_service_cpu_scope
Goal: Identify the service and pods affected by observable CPU throttling or CPU saturation without assigning resource-limit or code ownership.
Actions:
- GetAlerts(namespace=<namespace>)
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
Evidence to collect: performance_alert_summary, affected_services, anomalous_service, pod_health_summary, pod_node_mapping, cpu_throttling_evidence
Next: inspect_cpu_service_context

### Step: inspect_cpu_service_context
Goal: Inspect the CPU-affected service's pod health, application logs, and resource configuration once to distinguish CPU-limit pressure, code-driven CPU usage, and ordinary application failure. A configured CPU limit together with cpu_cfs throttling proves quota pressure only; it does not establish that the limit configuration or pod_cpu_overload is the root cause, because code-driven demand such as a busy loop can produce the same evidence. Continue to source triage before attributing pod_cpu_overload.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, label_selector=app=<service_name>, output_wide=True)
- GetErrorLogs(namespace=<namespace>, service_name=<service_name>)
- GetAppYAML(app_name=<service_name>)
Evidence to collect: affected_service, pod_health_status, pod_restart_count, pod_node_placement, error_log_pattern, application_error_present, cpu_saturation, resource_limits, configuration_normal, code_level_performance_candidate, local_failure_excluded
Next: triage_performance_source

### Step: triage_performance_source
Goal: Locate the affected service's relevant source files and inspect the primary candidate code once before selecting a specific artificial-delay, busy-loop, excessive-read, or excessive-write confirmation.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
Evidence to collect: source_file_list, candidate_source_files, primary_source_file, source_code_pattern, artificial_delay_candidate, busy_loop_candidate, excessive_file_read_candidate, excessive_file_write_candidate, no_obvious_code_defect
Next: confirm_busy_loop_code, attribute_observed_cpu_overload

### Step: confirm_busy_loop_code
Goal: Confirm code_busy_loop when an active CPU-intensive loop or busy-wait explains the observed CPU saturation or latency. Concrete active-spin evidence remains a busy loop even if the same path also contains a sleep expression; a genuinely blocking delay without active spinning belongs to code_artificial_delay.
Actions:
- GetSourceCode(app_name=<service_name>, file_path=main.go)
Evidence to collect: source_code_evidence, busy_loop_found, non_busy_loop_code_pattern, root_cause_confirmation, code_defect_pattern
Next: (end of this SOP - conclude here)

### Step: attribute_observed_cpu_overload
Goal: Attribute the observed CPU saturation to pod_cpu_overload only after source inspection finds no busy-loop or other supported code-level CPU defect.
Actions:
Evidence to collect: observed_cpu_saturation, source_code_review_summary, no_supported_cpu_code_defect, cpu_limit_context, pod_cpu_overload_attributed
Next: (end of this SOP - conclude here)
