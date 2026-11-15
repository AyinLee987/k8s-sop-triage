# SOP: oom_or_memory_exhaustion

Symptom: A container terminates, restarts, or enters CrashLoopBackOff with observable out-of-memory evidence such as OOMKilled or exit code 137.

Signals:
- pod or container is OOMKilled
- container exit code is 137
- pod status is CrashLoopBackOff and the last termination reason is OOMKilled or exit code 137
- pod restarts after memory exhaustion
- memory exhaustion is visible in pod status, events, or alerts
- service restarts after memory pressure
- application logs show normal request handling immediately before termination without panic, stack trace, or error logs
- a finite memory limit such as 128Mi is present, but that alone does not prove the limit is too low
- source files for the service contain allocation, retention, collection growth, or repeated file-read code that can explain memory growth

Start at: locate_oom_affected_workload

## Steps

### Step: locate_oom_affected_workload
Goal: Locate the pod, workload, and service affected by observed OOM or memory-exhaustion restart without assigning configuration or code ownership. When pod status is CrashLoopBackOff, check the last termination reason; if it is OOMKilled or exit code 137, keep this OOM SOP as the primary path and do not classify the incident under pod_crash_or_restart.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- DescribeResource(resource_type=pod, name=<failing_pod>, namespace=<namespace>) when the pod status is CrashLoopBackOff or restart counts are elevated
Evidence to collect: failing_pod_identified, failing_pod_name, affected_workload, failing_service, symptom_service, pod_status_summary, pod_restart_counts, last_termination_reason, oomkilled_or_137_indicator, primary_sop_routing
Next: inspect_memory_exhaustion_mechanism

### Step: inspect_memory_exhaustion_mechanism
Goal: Establish whether OOMKilled or exit code 137 is the container termination mechanism and collect the observed memory limit without deciding whether configuration or application code caused the exhaustion. Normal application logs or the absence of panic and error logs do not exclude a memory leak; gradual allocation or retention can terminate the container without application-level errors.
Actions:
- DescribeResource(resource_type=pod, name=<failing_pod>, namespace=<namespace>)
- GetRecentLogs(namespace=<namespace>, service_name=<failing_service>)
- GetErrorLogs(namespace=<namespace>, service_name=<failing_service>)
Evidence to collect: container_termination_reason, container_exit_code, pod_events, container_state, container_memory_limit, oom_kill_event, memory_exhaustion_mechanism, recent_logs_normal, no_panic_or_error_logs, resource_configuration_check_needed, source_code_review_needed
Next: evaluate_oom_resource_configuration

### Step: evaluate_oom_resource_configuration
Goal: Read the affected workload YAML once and decide whether an independently anomalous memory limit explains the OOM or whether source-level behavior remains the leading branch. A finite limit plus OOMKilled is not sufficient evidence of misconfiguration. An ordinary finite limit such as 128Mi is not independently anomalous. When configuration is not explanatory, keep generic allocation/retention and repeated or unbounded file reads as distinct competing source-code hypotheses and inspect source before selecting a root cause.
Actions:
- GetAppYAML(app_name=<failing_service>)
Evidence to collect: deployment_resource_limits, deployment_memory_limit, memory_limit_value, configuration_validity, resource_limit_independently_anomalous, ordinary_limit_not_explanatory, resource_configuration_not_explanatory, source_code_review_needed
Next: confirm_oom_limit_misconfiguration, confirm_memory_leak_code, confirm_excessive_file_reads_code

### Step: confirm_oom_limit_misconfiguration
Goal: Confirm container_memory_limit_too_low as a memory-limit configuration root cause only when the affected application container's configured limit is extremely small, for example only a few MiB and clearly unable to support normal startup or basic operation, so that the configuration independently explains the repeated OOM termination. An ordinary finite limit such as 128Mi is not sufficient evidence by itself; OOMKilled under 128Mi does not confirm a too-low limit. If the limit is not extremely small or the evidence is uncertain, reject this branch and inspect the still-open memory-leak and excessive-file-read source branches.
Actions:
- GetAppYAML(app_name=<failing_service>)
Evidence to collect: deployment_memory_limit, memory_limit_value, limit_extremely_small, limit_independently_explains_oom, root_cause_confirmation, deployment_memory_limit_insufficient, too_low_limit_rejected
Next: confirm_memory_leak_code, confirm_excessive_file_reads_code when too-low limit is not independently supported; otherwise (end of this SOP - conclude here)

### Step: confirm_memory_leak_code
Goal: Confirm code_memory_leak only when retrieved source shows non-file allocation growth, unbounded collection growth, retained references, or another application-code memory growth mechanism that explains the OOMKilled event. Do not reject this branch because recent logs show normal requests or because error logs are absent; memory leaks commonly terminate the process without application-level errors. Repeated or unbounded file reads are a distinct code_excessive_file_reads root cause; if file I/O is the concrete growth mechanism, reject this branch and load confirm_excessive_file_reads_code.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<relevant_source_file>)
- GetSourceCode(app_name=<service_name>, file_path=handlers.go) when the failing service is frontend
- GetSourceCode(app_name=<service_name>, file_path=main.go) when the failing service is frontend
Evidence to collect: source_file_list, source_file_content, memory_allocation_pattern, unbounded_collection_growth, retained_reference_pattern, memory_growth_mechanism, oom_explained_by_code, normal_logs_do_not_refute_leak, code_memory_leak_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_excessive_file_reads_code
Goal: Confirm code_excessive_file_reads when retrieved source shows a repeated or unbounded file-read path whose allocated or retained data explains the OOM after resource configuration is non-explanatory. This is distinct from code_memory_leak: when file I/O is the concrete growth mechanism, use this branch rather than generic memory-leak attribution.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<relevant_source_file>)
Evidence to collect: source_code_evidence, repeated_or_unbounded_file_read_path, memory_growth_from_file_reads, code_excessive_file_reads_confirmed
Next: (end of this SOP - conclude here)
