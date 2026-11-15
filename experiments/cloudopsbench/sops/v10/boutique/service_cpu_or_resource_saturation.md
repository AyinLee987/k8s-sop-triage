# SOP: service_cpu_or_resource_saturation

Symptom: An application service shows sustained CPU saturation or throttling with associated performance degradation, including CPU CFS throttling, elevated CPU-core use, and latency while pods remain Running and Ready.

Signals:
- service has CPU throttling alert
- application service has CPU saturation
- pod CPU usage or throttling is abnormally high
- service performance degradation is dominated by CPU pressure
- CPU CFS throttling and CPU-core increase appear with p95 latency degradation while pods are Running and Ready
- application logs are clean but CPU saturation remains
- source contains a CPU-heavy helper or loop but no independent measurement ties that code to the observed CPU or latency magnitude
- source contains a repeated or unbounded file-read loop (for example a data/CSV file read inside a hot request handler) on the failing path that can lead to CPU/core increase, CPU CFS throttling, or the observed latency even while application logs remain clean

Start at: locate_service_cpu_scope

## Steps

### Step: locate_service_cpu_scope
Goal: Identify the service and pods affected by observable CPU throttling or CPU saturation without assigning resource-limit or code ownership.
Actions:
- GetAlerts(namespace=<namespace>)
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
Evidence to collect: performance_alert_summary, affected_services, anomalous_service, pod_health_summary, pod_node_mapping, cpu_throttling_evidence, cpu_cfs_throttling, cpu_core_increase, latency_degradation_evidence
Next: inspect_cpu_service_context

### Step: inspect_cpu_service_context
Goal: Inspect the CPU-affected service's pod health, application logs, and resource configuration once to establish whether the observed CPU saturation is a pod-level CPU overload candidate. A configured CPU limit together with CPU CFS throttling proves quota pressure only; it does not by itself prove either a bad limit configuration or code-driven demand. Conversely, clean application logs and a normal-looking CPU limit do not exclude a code-level mechanism such as a repeated or unbounded file-read loop that consumes CPU on the request path while also producing latency, and such a loop must still be evaluated in source triage rather than displaced by pod_cpu_overload alone.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, label_selector=app=<service_name>, output_wide=True)
- GetErrorLogs(namespace=<namespace>, service_name=<service_name>)
- GetAppYAML(app_name=<service_name>)
Evidence to collect: affected_service, pod_health_status, pod_restart_count, pod_node_placement, error_log_pattern, application_error_present, cpu_saturation, cpu_cfs_throttling, cpu_core_increase, latency_degradation, resource_limits, configuration_normal, code_level_performance_candidate, file_io_code_candidate, local_failure_excluded
Next: triage_performance_source

### Step: triage_performance_source
Goal: Locate the affected service's relevant source files and inspect the primary code once only to test whether an active code-level defect independently explains the measured CPU saturation or latency. Treat any loop, money-multiplication helper, slow-named function, or file read as a candidate only, and record which one is present on the failing path. When the source shows a repeated or unbounded file-read loop on the affected request path, route it to confirm_excessive_file_reads_code instead of dismissing it as 'unmeasured'; a file-read loop can consume CPU and add latency without an error log or spin construct. If the only code candidate is a CPU helper or loop not tied to the observed CPU-core increase, CPU CFS throttling, or latency magnitude, keep pod_cpu_overload as a competing conclusion rather than assuming it.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
Evidence to collect: source_file_list, candidate_source_files, primary_source_file, source_code_pattern, busy_loop_candidate, suspicious_cpu_helper, excessive_file_read_candidate, file_read_loop_present, file_read_on_failing_path, loop_attribution_unmeasured, no_supported_cpu_code_defect
Next: confirm_busy_loop_code, confirm_excessive_file_reads_code, attribute_observed_cpu_overload

### Step: confirm_busy_loop_code
Goal: Confirm code_busy_loop only when an active CPU-intensive loop or busy-wait is executing on the failing path and independently accounts for the observed CPU saturation, CPU throttling, or latency magnitude. Merely finding a CPU-heavy helper such as money.MultiplySlow, a multiplication loop used for business calculations, is not sufficient. Concrete active spin remains a busy loop even if the same path also contains a sleep expression; a genuinely blocking delay without active spinning belongs to an artificial-delay branch. If the code candidate is instead a repeated or unbounded file-read loop, reject busy loop and use confirm_excessive_file_reads_code. If no code defect is independently supported, reject busy loop and continue to attribute_observed_cpu_overload.
Actions:
- GetSourceCode(app_name=<service_name>, file_path=main.go)
- GetAlerts(namespace=<namespace>)
Evidence to collect: source_code_evidence, active_spin_loop, failing_path_execution, independent_cpu_attribution, measured_loop_cost, observed_cpu_saturation_or_throttling, loop_latency_contribution, code_busy_loop_confirmed, busy_loop_rejected_due_to_unmeasured_attribution
Next: confirm_excessive_file_reads_code when a repeated or unbounded file-read loop is present on the failing path; otherwise attribute_observed_cpu_overload when busy loop is not independently supported; otherwise (end of this SOP - conclude here)

### Step: confirm_excessive_file_reads_code
Goal: Confirm code_excessive_file_reads when retrieved source shows a repeated or unbounded file-read path executing on the affected request path (for example, reading and parsing a data or CSV file on every request inside a handler such as PlaceOrder) that can explain the observed CPU consumption, CPU-core increase, CPU CFS throttling, or latency magnitude. Repeated file reads inside a hot handler are a distinct code-level root cause and should be selected over generic pod_cpu_overload when present on the failing path. Do not reject this branch merely because application logs are clean, because there is no panic or error, or because the mechanism is a file read rather than an explicit spin loop; the read loop consumes CPU and adds latency without application-level errors. This differs from code_busy_loop (active spin without file I/O) and from a merely plausible unmeasured helper.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
- GetSourceCode(app_name=<service_name>, file_path=main.go) when the failing service is checkoutservice or another service whose core workflow lives in main.go
Evidence to collect: source_file_list, source_file_content, repeated_or_unbounded_file_read_path, file_read_on_failing_path, file_read_frequency, data_volume_read, cpu_cost_of_file_reads, latency_contribution_of_file_reads, no_application_error_logs, code_excessive_file_reads_confirmed, excessive_file_reads_takes_precedence_over_pod_cpu_overload
Next: (end of this SOP - conclude here)

### Step: attribute_observed_cpu_overload
Goal: Confirm pod_cpu_overload only when the affected service or pod shows sustained CPU saturation, CPU CFS throttling, or elevated CPU-core use with associated latency degradation, the pods are Running and Ready, application logs show no local application error, resource configuration is not independently anomalous, and source review has produced no supported code defect. The presence of an ordinary CPU-heavy helper or loop that is not shown to explain the observed CPU magnitude does not refute pod_cpu_overload, but a demonstrated repeated or unbounded file-read loop on the failing path does: in that case reject pod_cpu_overload and use confirm_excessive_file_reads_code, and likewise do not confirm pod_cpu_overload when an active busy loop is independently supported.
Actions:
- GetAlerts(namespace=<namespace>)
- GetResources(resource_type=pods, namespace=<namespace>, label_selector=app=<service_name>, output_wide=True)
Evidence to collect: observed_cpu_saturation, cpu_cfs_throttling, cpu_core_increase, latency_degradation, pods_running_ready, no_application_error_logs, resource_configuration_not_explanatory, source_code_review_summary, no_independently_supported_cpu_code_defect, repeated_or_unbounded_file_read_loop_absent, active_busy_loop_absent, pod_cpu_overload_attributed
Next: confirm_excessive_file_reads_code when a file-read loop is present on the failing path; confirm_busy_loop_code when an active busy loop is independently supported; otherwise (end of this SOP - conclude here)
