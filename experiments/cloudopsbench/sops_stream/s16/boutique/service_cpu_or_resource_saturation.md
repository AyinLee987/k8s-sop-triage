# SOP: service_cpu_or_resource_saturation

Symptom: An application service shows sustained CPU saturation or throttling with associated performance degradation.

Signals:
- service has CPU throttling alert
- application service has CPU saturation
- pod CPU usage or throttling is abnormally high
- service performance degradation is dominated by CPU pressure
- source code repeatedly reads or parses files on the request path and produces CPU-heavy work
- request-path code repeatedly opens and reads the same file per request despite healthy pods and a normal CPU limit

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
Goal: Inspect the CPU-affected service's pod health, application logs, and resource configuration once to distinguish CPU-limit pressure, code-driven CPU usage, and ordinary application failure. A configured CPU limit together with cpu_cfs throttling proves quota pressure only; it does not establish that the limit configuration or pod_cpu_overload is the root cause, because code-driven demand such as a busy loop or repeated file reads can produce the same evidence. A simultaneous CPU rise plus latency degradation with a normal limit and no application errors is a strong signal to inspect source. A fixed count of repeated file reads, such as opening and ReadAll-ing the same file ten times per request, is sufficient source-level demand evidence; do not require an unbounded read or an extremely small CPU limit. Continue to source triage before attributing pod_cpu_overload.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, label_selector=app=<service_name>, output_wide=True)
- GetErrorLogs(namespace=<namespace>, service_name=<service_name>)
- GetAppYAML(app_name=<service_name>)
Evidence to collect: affected_service, pod_health_status, pod_restart_count, pod_node_placement, error_log_pattern, application_error_present, cpu_saturation, resource_limits, configuration_normal, code_level_performance_candidate, repeated_request_path_file_read_candidate, local_failure_excluded
Next: triage_performance_source

### Step: triage_performance_source
Goal: Locate the affected service's relevant source files and inspect the primary candidate code once before selecting a specific busy-loop, excessive-read, or observed-overload conclusion. When CPU saturation and latency co-occur, look specifically for CPU-consuming constructs on the request path, including active-spin busy-waits that check an elapsed-time condition as well as repeated or unbounded file reads. A handler that opens and reads the same file a fixed number of times per request is a repeated-file-read candidate even when the count is not unbounded.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
Evidence to collect: source_file_list, candidate_source_files, primary_source_file, source_code_pattern, busy_loop_candidate, elapsed_time_spin_loop_candidate, excessive_file_read_candidate, repeated_file_read_count_per_request, same_file_reopened_per_request, file_read_on_failing_request_path, no_obvious_code_defect
Next: confirm_busy_loop_code, confirm_excessive_file_reads_code, attribute_observed_cpu_overload

### Step: confirm_busy_loop_code
Goal: Confirm code_busy_loop when retrieved source shows an active CPU-consuming loop or busy-wait on the request path — including a loop that repeatedly spins/checks an elapsed-time condition (for example spinning until ~500ms have passed) instead of blocking — that explains the observed CPU saturation, CFS throttling, or latency. Active spinning consumes CPU and remains a busy loop even when it is expressed via a duration/timeout or accompanied by a sleep expression. A genuinely blocking wait (such as a plain time.Sleep) that does not consume CPU is not a CPU-saturation cause and must not be classified here; treat that as an artificial delay under the latency/IO degradation SOP instead. When the concrete CPU-consuming work is repeated file reads or parsing, use confirm_excessive_file_reads_code rather than this branch. Corroborate the confirmation with the observed CPU rise and CPU-CFS throttling and with the loop sitting on the failing execution path.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=main.go)
Evidence to collect: source_code_evidence, busy_loop_found, loop_spins_on_elapsed_condition, active_cpu_consumption_correlates_with_loop, loop_on_failing_request_path, non_busy_loop_code_pattern, file_read_mechanism_excluded, root_cause_confirmation, code_defect_pattern
Next: (end of this SOP - conclude here)

### Step: confirm_excessive_file_reads_code
Goal: Confirm code_excessive_file_reads when retrieved source shows a repeated or unbounded file-read path whose allocated, parsed, or retained data explains the observed CPU saturation, CFS throttling, or latency after resource configuration is not independently explanatory. A fixed large or repeated per-request read is sufficient when the source and CPU/latency evidence align; do not require unbounded growth. This is distinct from code_busy_loop: when file I/O and CPU-heavy parsing are the concrete mechanism, use this branch rather than busy-loop attribution. This also applies when a normal CPU limit, healthy pods, and no application errors coexist with repeated request-path reads such as opening and ReadAll-ing the same file many times per request.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<relevant_source_file>)
Evidence to collect: source_code_evidence, repeated_or_unbounded_file_read_path, repeated_file_read_count_per_request, same_file_reopened_per_request, file_read_on_failing_request_path, cpu_heavy_file_parsing, memory_or_cpu_growth_from_file_reads, normal_cpu_limit_not_explanatory, code_excessive_file_reads_confirmed
Next: (end of this SOP - conclude here)

### Step: attribute_observed_cpu_overload
Goal: Attribute the observed CPU saturation to pod_cpu_overload only after source inspection finds no busy-loop, excessive-file-read including fixed repeated request-path reads, or other supported code-level CPU defect.
Actions:
Evidence to collect: observed_cpu_saturation, source_code_review_summary, no_supported_cpu_code_defect, excessive_file_read_excluded, cpu_limit_context, pod_cpu_overload_attributed
Next: (end of this SOP - conclude here)
