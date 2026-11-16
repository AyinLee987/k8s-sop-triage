# SOP: oom_or_memory_exhaustion
Symptom: A container terminates or restarts with observable out-of-memory evidence such as OOMKilled or exit code 137, including checkoutservice CrashLoopBackOff after PlaceOrder allocates a very large in-memory slice. This includes checkout order HTTP 500s while checkoutservice is OOMKilled and paymentservice or emailservice success_rate vanishes because checkoutservice crashes before downstream calls.

Signals:
- pod or container is OOMKilled
- container exit code is 137
- pod restarts after memory exhaustion
- memory exhaustion is visible in pod status, events, or alerts
- service restarts after memory pressure
- upstream callers report no healthy upstream while a downstream pod is in CrashLoopBackOff with OOMKilled
- a normal finite memory limit such as 128Mi coexists with repeated OOMKilled restarts
- checkoutservice pod is OOMKilled or CrashLoopBackOff while checkout order returns HTTP 500 and payment or email success rate vanishes
- checkoutservice main.go PlaceOrder contains a bounded but huge per-request allocation loop, such as appending up to 8M ints followed by runtime.GC()
- checkoutservice PlaceOrder allocates a large in-memory slice before downstream payment or email calls
- productcatalogservice pod is OOMKilled or restarting while ListProducts latency rises and its deployment has an ordinary 128Mi memory limit
- productcatalogservice ListProducts opens and ReadAll-s /src/log.csv 15 times per request
- repeated request-path file reads co-occur with OOMKilled and latency degradation

Start at: locate_oom_affected_workload

## Steps

### Step: locate_oom_affected_workload
Goal: Locate the pod, workload, and service affected by an observed OOM or memory-exhaustion restart without assigning configuration or code ownership. For checkoutservice, note checkout order HTTP 500s and payment or email success-rate disappearance as secondary evidence that checkoutservice crashed before downstream calls.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
Evidence to collect: failing_pod_identified, failing_pod_name, affected_workload, failing_service, symptom_service, pod_status_summary, pod_restart_counts, checkoutservice_crashloopbackoff, checkout_order_500s, payment_success_rate_missing, email_success_rate_missing, checkoutservice_crash_before_downstream_calls
Next: inspect_memory_exhaustion_mechanism

### Step: inspect_memory_exhaustion_mechanism
Goal: Establish whether OOMKilled or exit code 137 is the container termination mechanism and collect the observed memory limit without deciding whether configuration or application code caused the exhaustion. For checkoutservice CrashLoopBackOff, capture last termination state and restart count before source-level attribution.
Actions:
- DescribeResource(resource_type=pod, name=<failing_pod>, namespace=<namespace>)
- GetRecentLogs(namespace=<namespace>, service_name=checkoutservice) when checkoutservice is CrashLoopBackOff
Evidence to collect: container_termination_reason, container_last_state_terminated, reason_oomkilled, container_exit_code, exit_code_137, pod_events, container_state, crashloopbackoff, checkoutservice_restart_count, checkoutservice_placeorder_started, container_memory_limit, oom_kill_event, memory_exhaustion_mechanism, resource_configuration_check_needed
Next: evaluate_oom_resource_configuration

### Step: evaluate_oom_resource_configuration
Goal: Read the affected workload YAML once and decide whether an independently anomalous memory limit explains the OOM or whether source-level behavior remains the leading branch. A finite limit plus OOMKilled is not sufficient evidence of misconfiguration; 128Mi is an ordinary finite limit and must not by itself be called too low. Do not use memory_capacity_mismatch for a runtime OOMKilled event; that root cause belongs to scheduling capacity and is not an OOM mechanism. When configuration is not independently explanatory, keep generic allocation or retention, a bounded but huge in-memory allocation loop, and repeated or unbounded file reads as distinct competing source-code hypotheses. When OOMKilled co-occurs with checkoutservice CrashLoopBackOff and source is available, inspect checkoutservice main.go PlaceOrder for a large per-request allocation loop before selecting low limit. When OOMKilled co-occurs with productcatalogservice latency and source is available, inspect product_catalog.go ListProducts for repeated /src/log.csv reads before selecting memory leak or low limit.
Actions:
- GetAppYAML(app_name=<failing_service>)
- GetSourceCode(app_name=checkoutservice, file_path=main.go) when checkoutservice is OOMKilled with CrashLoopBackOff
- GetSourceCode(app_name=productcatalogservice, file_path=product_catalog.go) when productcatalogservice OOMKilled co-occurs with latency
Evidence to collect: deployment_resource_limits, deployment_memory_limit, memory_limit_value, ordinary_finite_limit_128mi_not_too_low, memory_capacity_mismatch_not_oom_branch, configuration_validity, resource_limit_independently_anomalous, resource_configuration_not_explanatory, checkoutservice_placeorder_large_allocation_loop_candidate, large_in_memory_slice_append_candidate, append_up_to_8m_ints_candidate, productcatalogservice_listproducts_read_candidate
Next: confirm_oom_limit_misconfiguration, confirm_memory_leak_code, confirm_excessive_file_reads_code

### Step: confirm_oom_limit_misconfiguration
Goal: Confirm container_memory_limit_too_low as a memory-limit configuration root cause only when the affected application container configured limit is extremely small, for example only a few MiB and clearly unable to support normal startup or basic operation, so that the configuration independently explains the repeated OOM termination. An ordinary finite limit such as 128Mi is not sufficient evidence by itself and must be rejected as a low-limit confirmation; do not stop at 128Mi or rank container_memory_limit_too_low first from that value alone. When source confirms a large per-request allocation loop such as checkoutservice PlaceOrder appending up to 8M ints, or repeated request-path file reads such as productcatalogservice ListProducts reading /src/log.csv 15 times per request, reject container_memory_limit_too_low even with 128Mi and do not rank it as a competing top prediction. If source inspection has not been performed, continue to the still-open memory-leak and excessive-file-read source branches and report the code-level cause as unconfirmed rather than concluding low limit. Do not include memory_capacity_mismatch as an OOM prediction.
Actions:
Evidence to collect: root_cause_confirmation, deployment_memory_limit_insufficient, 128mi_limit_rejected_as_too_low, source_inspection_required_before_low_limit, checkoutservice_large_allocation_excludes_low_limit, repeated_file_read_confirmation_excludes_low_limit, container_memory_limit_too_low_not_competing_after_large_allocation, container_memory_limit_too_low_not_competing_after_repeated_reads, memory_capacity_mismatch_excluded
Next: (end of this SOP - conclude here)

### Step: confirm_memory_leak_code
Goal: Confirm code_memory_leak when retrieved source shows non-file allocation growth, unbounded collection growth, retained references, or a bounded but excessive per-request in-memory allocation loop that explains the OOMKilled event. This includes checkoutservice PlaceOrder appending up to 8M ints followed by runtime.GC(), exhausting a 128Mi limit and causing CrashLoopBackOff. When OOMKilled occurs with an ordinary finite limit such as 128Mi and source has not yet been inspected, this branch remains open and is preferred over container_memory_limit_too_low. Repeated or unbounded file reads are a distinct code_excessive_file_reads root cause; if file I/O is the concrete growth mechanism, reject this branch and load confirm_excessive_file_reads_code. Once source confirms a large allocation loop or other concrete memory-growth mechanism, reject code_excessive_file_reads only when file reads are absent, and conclude code_memory_leak as the single leading root cause. Do not present container_memory_limit_too_low, code_busy_loop, or code_excessive_file_reads as competing top predictions. A large allocation loop is memory exhaustion, not busy-loop attribution; code_busy_loop requires active CPU spin evidence and is not an OOM root cause. Paymentservice or emailservice availability missing is secondary because checkoutservice crashes before downstream calls complete.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<relevant_source_file>)
- GetSourceCode(app_name=checkoutservice, file_path=main.go) when checkoutservice OOMKilled with CrashLoopBackOff
Evidence to collect: source_file_content, memory_allocation_pattern, ordinary_limit_with_uninspected_source, repeated_file_read_mechanism_present, memory_leak_not_competing_after_repeated_reads, checkoutservice_placeorder_allocation_loop, append_up_to_8m_ints, runtime_gc_after_allocation, oomkilled_with_128mi, checkoutservice_code_memory_leak_confirmed, downstream_payment_email_missing_secondary, code_busy_loop_excluded, container_memory_limit_too_low_excluded, code_excessive_file_reads_excluded, root_cause_confirmation
Next: (end of this SOP - conclude here)

### Step: confirm_excessive_file_reads_code
Goal: Confirm code_excessive_file_reads when retrieved source shows a repeated or unbounded file-read path whose allocated or retained data explains the OOM after resource configuration is non-explanatory. This is distinct from code_memory_leak: when file I/O is the concrete growth mechanism, use this branch rather than generic memory-leak attribution. A 128Mi limit is not a substitute for source evidence; if source inspection is not complete, leave this branch open rather than attributing the OOM to a low limit. This includes productcatalogservice ListProducts opening and ReadAll-ing /src/log.csv 15 times per request, which can explain both memory pressure leading to OOMKilled and latency degradation. When that site is confirmed with an ordinary 128Mi limit, conclude code_excessive_file_reads as the single leading root cause and do not present code_memory_leak or container_memory_limit_too_low as competing top predictions; do not list them at rank 2 or rank 3, and make the top prediction list code_excessive_file_reads alone. Do not use this branch for checkoutservice PlaceOrder when the concrete source site is a large in-memory allocation loop and there is no repeated file-read mechanism; use confirm_memory_leak_code for that case.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<relevant_source_file>)
Evidence to collect: source_code_evidence, repeated_or_unbounded_file_read_path, productcatalogservice_listproducts_read_loop, productcatalogservice_log_csv_read_count_per_request, productcatalogservice_log_csv_read_15_times, memory_growth_from_file_reads, ordinary_limit_with_uninspected_source, ordinary_128mi_limit_not_too_low, memory_leak_excluded_after_file_read_confirmation, container_memory_limit_too_low_excluded_after_file_read_confirmation, single_leading_root_cause_code_excessive_file_reads, checkoutservice_allocation_loop_not_file_read_mechanism, code_excessive_file_reads_confirmed
Next: (end of this SOP - conclude here)
