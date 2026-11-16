# SOP: service_latency_or_io_degradation

Symptom: An application service shows sustained response latency, throughput degradation, or abnormal filesystem-I/O activity.

Signals:
- application service has severe latency or slow response
- service latency degradation is reported
- downstream timeout appears in service logs
- FILESYSTEM_WRITE_SURGE or abnormal fs_write_bytes is reported
- filesystem-write surge co-occurs with CPU elevation or HTTP 503s
- request-path code creates or writes a file per request
- healthy pods with severe fixed-duration latency and no application error logs
- shared downstream service and its callers show latency matching a fixed request-path delay

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
Goal: Inspect the latency- or I/O-affected service's pod health, logs, dependency context, and relevant configuration once to distinguish pod-network delay, artificial delay, file I/O code patterns, and ordinary application failure. When a filesystem-write surge co-occurs with CPU saturation or HTTP 503s, keep request-path file writing as a leading candidate even if pods are healthy and some request logs still show successful responses. When healthy pods show severe fixed-duration latency and a shared downstream service plus its callers are affected, keep a request-path fixed sleep or delay as a leading code candidate.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, label_selector=app=<service_name>, output_wide=True)
- GetErrorLogs(namespace=<namespace>, service_name=<service_name>)
- GetRecentLogs(namespace=<namespace>, service_name=<service_name>)
Evidence to collect: affected_service, pod_health_status, pod_restart_count, pod_node_placement, error_log_pattern, recent_log_pattern, application_error_present, downstream_timeout_found, fixed_latency_pattern, filesystem_write_surge, write_surge_with_cpu_or_503, per_request_file_io_candidate, connectivity_failure_pattern, code_level_performance_candidate, fixed_duration_latency_with_healthy_pods, shared_downstream_latency_candidate, local_failure_excluded
Next: triage_performance_source

### Step: triage_performance_source
Goal: Locate the affected service's relevant source files and inspect the primary candidate code once before selecting a specific artificial-delay, busy-loop, excessive-read, or excessive-write confirmation. When a filesystem-write surge is present, look specifically for request handlers that create temporary files, serialize or export CSV/JSON, or write large batches such as tens of thousands of rows on the request path; this file-write-heavy work can also raise CPU and cause latency or 503 responses. When latency is severe, fixed-duration, and pods are healthy, inspect request-handler and helper files as well as main.go for an explicit blocking sleep or active spin-wait; do not assume main.go contains the delay.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
Evidence to collect: source_file_list, candidate_source_files, primary_source_file, request_handler_or_helper_file, source_code_pattern, artificial_delay_candidate, blocking_sleep_candidate, fixed_duration_sleep_candidate, busy_loop_candidate, excessive_file_read_candidate, excessive_file_write_candidate, per_request_file_generation_candidate, large_row_count_write_candidate, temp_file_create_write_delete_candidate, no_obvious_code_defect
Next: confirm_artificial_delay_code, confirm_busy_loop_code, confirm_excessive_file_writes_code, attribute_pod_network_delay_after_code_exclusion

### Step: confirm_artificial_delay_code
Goal: Confirm code_artificial_delay when an explicit delay operation such as a blocking time.Sleep is active with a meaningful non-zero duration on the failing request path. A dormant, zero-valued, or unreachable delay hook is insufficient. Inspect the request-handler or helper file identified during source triage, not only main.go; a fixed sleep in a handler or helper such as productcatalogservice ListProducts is a valid artificial-delay site. Corroborate that the sleep duration matches the observed p50 or p90 latency and that the delay is on the failing execution path. Active spin or busy-wait code remains code_busy_loop even if a sleep expression also exists; a plain blocking sleep that does not consume CPU is code_artificial_delay.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
- GetSourceCode(app_name=<service_name>, file_path=<request_handler_or_helper_file>)
Evidence to collect: source_file_list, target_source_file, request_handler_or_helper_file, artificial_delay_present, blocking_sleep_call, sleep_duration_ms, fixed_duration_latency_alignment, delay_on_failing_execution_path, active_spin_mechanism_excluded, non_delay_code_pattern, root_cause_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_busy_loop_code
Goal: Confirm code_busy_loop when an active CPU-intensive loop or busy-wait explains the observed CPU saturation or latency. Concrete active-spin evidence remains a busy loop even if the same path also contains a sleep expression; a genuinely blocking delay without active spinning belongs to code_artificial_delay. Do not use this branch merely because CPU is elevated when the concrete CPU-consuming mechanism is writing or serializing a large per-request file.
Actions:
- GetSourceCode(app_name=<service_name>, file_path=main.go)
Evidence to collect: source_code_evidence, busy_loop_found, non_busy_loop_code_pattern, root_cause_confirmation, code_defect_pattern
Next: (end of this SOP - conclude here)

### Step: confirm_excessive_file_writes_code
Goal: Confirm code_excessive_file_writes when retrieved source shows repeated, unbounded, or large per-request file writes that explain the observed FILESYSTEM_WRITE_SURGE / abnormal fs_write_bytes and may also explain CPU elevation, latency, or 503s. This includes request-path code that creates a temporary file and writes tens of thousands of rows per request before flushing or deleting it. Do not require unbounded growth: a fixed large write repeated by request traffic is sufficient when the source and write-surge evidence align. When file creation or bulk serialization is the concrete mechanism, classify code_excessive_file_writes rather than code_busy_loop even if CPU rises as a side effect of the file work.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<relevant_source_file>)
Evidence to collect: source_code_evidence, repeated_or_unbounded_file_write_path, per_request_file_creation, large_row_count_or_bulk_write, temp_file_create_write_delete_pattern, filesystem_write_surge_correlates_with_source_path, cpu_elevation_explained_by_file_write_work, code_excessive_file_writes_confirmed
Next: (end of this SOP - conclude here)

### Step: attribute_pod_network_delay_after_code_exclusion
Goal: Attribute remaining service latency to pod_network_delay after supported code-level and dependency explanations are inspected and not found.
Actions:
- GetServiceDependencies(service_name=<service_name>)
Evidence to collect: observed_latency_degradation, source_code_review_summary, no_supported_latency_code_defect, service_dependency_topology, no_visible_downstream_bottleneck, pod_network_delay_attributed
Next: (end of this SOP - conclude here)
