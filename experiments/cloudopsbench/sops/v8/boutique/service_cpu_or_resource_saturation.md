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
Goal: Inspect the CPU-affected service's pod health, application logs, and resource configuration once to establish whether the observed CPU saturation is a pod-level CPU overload candidate. A configured CPU limit together with CPU CFS throttling proves quota pressure only; it does not by itself prove either a bad limit configuration or code-driven demand. Conversely, healthy pods, a normal-looking CPU limit, clean application logs, CPU CFS throttling, elevated CPU-core use, and latency degradation support pod_cpu_overload and must not be displaced by source-code speculation alone.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, label_selector=app=<service_name>, output_wide=True)
- GetErrorLogs(namespace=<namespace>, service_name=<service_name>)
- GetAppYAML(app_name=<service_name>)
Evidence to collect: affected_service, pod_health_status, pod_restart_count, pod_node_placement, error_log_pattern, application_error_present, cpu_saturation, cpu_cfs_throttling, cpu_core_increase, latency_degradation, resource_limits, configuration_normal, code_level_performance_candidate, local_failure_excluded
Next: triage_performance_source

### Step: triage_performance_source
Goal: Locate the affected service's relevant source files and inspect the primary code once only to test whether an active code-level CPU defect independently explains the measured saturation. Treat any loop, money-multiplication helper, or slow-named function as a candidate only. If the code candidate is not directly tied to the observed CPU-core increase, CPU CFS throttling, or latency magnitude, keep pod_cpu_overload as the leading conclusion.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
Evidence to collect: source_file_list, candidate_source_files, primary_source_file, source_code_pattern, busy_loop_candidate, suspicious_cpu_helper, loop_attribution_unmeasured, no_supported_cpu_code_defect
Next: confirm_busy_loop_code, attribute_observed_cpu_overload

### Step: confirm_busy_loop_code
Goal: Confirm code_busy_loop only when an active CPU-intensive loop or busy-wait is executing on the failing path and independently accounts for the observed CPU saturation, CPU throttling, or latency magnitude. Merely finding a CPU-heavy helper such as money.MultiplySlow, a multiplication loop used for business calculations, is not sufficient. A source loop that is present but not shown to be active on the failing path or not measured as the cause of the observed CPU-core increase does not override the service-level CPU overload signal. Reject code_busy_loop in that situation and continue to attribute_observed_cpu_overload. Concrete active spin remains a busy loop even if the same path also contains a sleep expression; a genuinely blocking delay without active spinning belongs to an artificial-delay branch.
Actions:
- GetSourceCode(app_name=<service_name>, file_path=main.go)
- GetAlerts(namespace=<namespace>)
Evidence to collect: source_code_evidence, active_spin_loop, failing_path_execution, independent_cpu_attribution, measured_loop_cost, observed_cpu_saturation_or_throttling, loop_latency_contribution, code_busy_loop_confirmed, busy_loop_rejected_due_to_unmeasured_attribution
Next: attribute_observed_cpu_overload when busy loop is not independently supported; otherwise (end of this SOP - conclude here)

### Step: attribute_observed_cpu_overload
Goal: Confirm pod_cpu_overload when the affected service or pod shows sustained CPU saturation, CPU CFS throttling, or elevated CPU-core use with associated latency degradation, the pods are Running and Ready, application logs show no local application error, resource configuration is not independently anomalous, and source review has not produced a measured or independently attributable busy-loop defect. The presence of an ordinary CPU-heavy helper or loop that is not shown to explain the observed CPU magnitude does not refute pod_cpu_overload.
Actions:
- GetAlerts(namespace=<namespace>)
- GetResources(resource_type=pods, namespace=<namespace>, label_selector=app=<service_name>, output_wide=True)
Evidence to collect: observed_cpu_saturation, cpu_cfs_throttling, cpu_core_increase, latency_degradation, pods_running_ready, no_application_error_logs, resource_configuration_not_explanatory, source_code_review_summary, no_independently_supported_cpu_code_defect, pod_cpu_overload_attributed
Next: (end of this SOP - conclude here)
