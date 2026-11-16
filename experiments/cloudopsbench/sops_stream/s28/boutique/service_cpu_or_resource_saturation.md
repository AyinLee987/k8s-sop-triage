# SOP: service_cpu_or_resource_saturation

Symptom: An application service shows sustained CPU saturation or throttling with associated performance degradation, including request-path spin-wait, repeated file reads, or bulk per-request file writes that raise CPU and may also produce a filesystem-write surge.

Signals:
- service has CPU throttling alert
- application service has CPU saturation
- pod CPU usage or throttling is abnormally high
- service performance degradation is dominated by CPU pressure
- reported symptom is a generic service performance degradation while the alert shows CPU saturation plus a filesystem-write surge on the same healthy service
- source code repeatedly reads or parses files on the request path and produces CPU-heavy work
- request-path code repeatedly opens and reads the same file per request despite healthy pods and a normal CPU limit
- request-path helper or ad-selection code spins/busy-waits until a fixed elapsed duration before returning
- request-path order or PlaceOrder code busy-waits until a fixed elapsed duration such as 500ms before returning
- p50 latency rises to roughly the fixed request-path spin duration while CPU CFS throttling also rises
- frontend/home-product latency and downstream DeadlineExceeded logs accompany caller CPU throttling
- FILESYSTEM_WRITE_SURGE or abnormal fs_write_bytes co-occurs with service CPU saturation or throttling
- request-path code creates a temporary file or writes a large batch such as tens of thousands of rows per request
- request-path quote or helper code creates a temporary CSV and writes a large fixed row count such as 30000 rows on every quote request
- CPU saturation and a filesystem-write surge appear on the same healthy service with no application error logs and fast per-request completion logs
- request-path code repeatedly opens and ReadAll-s the same file such as /src/log.csv a fixed number of times per request

Start at: locate_service_cpu_scope

## Steps

### Step: locate_service_cpu_scope
Goal: Identify the service and pods affected by observable CPU throttling or CPU saturation without assigning resource-limit or code ownership.
Actions:
- GetAlerts(namespace=<namespace>)
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
Evidence to collect: performance_alert_summary, affected_services, anomalous_service, pod_health_summary, pod_node_mapping, cpu_throttling_evidence, filesystem_write_surge
Next: inspect_cpu_service_context

### Step: inspect_cpu_service_context
Goal: Inspect the CPU-affected service's pod health, application logs, and resource configuration once to distinguish CPU-limit pressure, code-driven CPU usage, and ordinary application failure. A configured CPU limit together with cpu_cfs throttling proves quota pressure only; it does not establish that the limit configuration or pod_cpu_overload is the root cause, because code-driven demand such as a busy loop, repeated file reads, or bulk per-request file writes can produce the same evidence. A simultaneous CPU rise plus latency degradation with a normal limit and no application errors is a strong signal to inspect source. A fixed-duration request-path busy-wait that raises p50 latency to roughly the spin duration, with healthy pods, no application errors, and a normal CPU limit, is a code-driven CPU-demand signal; proceed to source triage rather than attributing it to pod_cpu_overload. A fixed count of repeated file reads, such as opening and ReadAll-ing the same file ten times per request, is sufficient source-level demand evidence; do not require an unbounded read or an extremely small CPU limit. When CPU saturation co-occurs with a filesystem-write surge, treat request-path file creation or bulk writes as a leading code-level CPU-demand candidate and do not attribute the issue to pod_cpu_overload before source inspection; a generic performance-degradation symptom with fast request-completion logs does not make this a latency or network case. When downstream timeout or DeadlineExceeded logs appear alongside the caller's own CPU throttling and latency, do not attribute the symptom to the downstream callee until the caller's request-path source is inspected for active spin-wait or file work; downstream timeouts can be secondary.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, label_selector=app=<service_name>, output_wide=True)
- GetErrorLogs(namespace=<namespace>, service_name=<service_name>)
- GetAppYAML(app_name=<service_name>)
Evidence to collect: affected_service, pod_health_status, pod_restart_count, pod_node_placement, error_log_pattern, application_error_present, downstream_deadline_or_timeout_logs, downstream_timeout_secondary_candidate, cpu_saturation, resource_limits, configuration_normal, code_level_performance_candidate, repeated_request_path_file_read_candidate, fixed_duration_spin_latency_alignment, filesystem_write_surge, write_surge_with_cpu_saturation, per_request_file_write_candidate, fast_request_logs_do_not_exclude_file_write_defect, local_failure_excluded
Next: triage_performance_source

### Step: triage_performance_source
Goal: Locate the affected service's relevant source files and inspect the primary candidate code once before selecting a specific busy-loop, excessive-read, excessive-write, or observed-overload conclusion. When CPU saturation and latency co-occur, look specifically for CPU-consuming constructs on the request path, including active-spin busy-waits that check an elapsed-time condition and repeated or unbounded file reads. A per-request loop that opens and ReadAll-s the same log file multiple times, such as /src/log.csv ten times in PlaceOrder, is a repeated-file-read candidate even when the count is fixed and not unbounded. When CPU saturation co-occurs with a filesystem-write surge, also inspect request handlers that create temporary files, serialize or export CSV/JSON, or write large batches such as tens of thousands of rows on the request path; inspect quote, pricing, and helper files used by the hot RPC as well as main.go, since a value helper reached from a handler can be the file-writing site. Inspect ad-selection, helper, request-handler, order, and PlaceOrder files as well as main.go; a loop that checks elapsed time until a fixed interval such as 500ms or 800ms is an active spin-wait candidate. A handler that opens and reads the same file a fixed number of times per request is a repeated-file-read candidate even when the count is not unbounded. A handler that creates and writes a large per-request file is an excessive-file-write candidate even when the writes are bounded.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
Evidence to collect: source_file_list, candidate_source_files, primary_source_file, source_code_pattern, busy_loop_candidate, elapsed_time_spin_loop_candidate, fixed_duration_spin_until_ms_candidate, request_path_handler_file, ad_selection_or_helper_spin_candidate, placeorder_elapsed_time_spin_candidate, excessive_file_read_candidate, repeated_file_read_count_per_request, same_file_reopened_per_request, file_read_on_failing_request_path, excessive_file_write_candidate, per_request_file_write_candidate, large_row_count_write_candidate, temp_file_create_write_delete_candidate, quote_or_pricing_helper_file, filesystem_write_surge_correlates_with_source_path, no_obvious_code_defect
Next: confirm_busy_loop_code, confirm_excessive_file_reads_code, confirm_excessive_file_writes_code, attribute_observed_cpu_overload

### Step: confirm_busy_loop_code
Goal: Confirm code_busy_loop when retrieved source shows an active CPU-consuming loop or busy-wait on the request path, including a loop that repeatedly spins or checks an elapsed-time condition (for example chooseAd spinning until about 800ms have passed or checkoutservice PlaceOrder spinning until about 500ms have passed) instead of blocking, and that explains the observed CPU saturation, CFS throttling, or latency. Active spinning consumes CPU and remains a busy loop even when it is expressed via a duration or timeout or accompanied by a sleep expression. A genuinely blocking wait such as a plain time.Sleep that does not consume CPU is not a CPU-saturation cause and must not be classified here; treat that as an artificial delay under the latency or IO degradation SOP instead. A checkoutservice PlaceOrder loop such as for { if time.Since(start) > 500*time.Millisecond { break } } is an active spin-wait and must be confirmed as code_busy_loop when CPU throttling and latency align; do not classify it as code_artificial_delay merely because it waits a fixed duration, and once active spinning is confirmed do not present code_artificial_delay or pod_cpu_overload as competing top predictions for the same incident. When the concrete CPU-consuming work is repeated file reads or parsing, use confirm_excessive_file_reads_code rather than this branch. When the concrete CPU-consuming work is creating or writing a large per-request file, use confirm_excessive_file_writes_code rather than this branch, and do not retain code_busy_loop as a competing top prediction once that write mechanism is confirmed. Inspect the request-handler or helper file identified during source triage when that is where the spin is located; do not assume the busy loop must be in main.go. Downstream DeadlineExceeded or timeout logs are secondary when the caller's source contains an active request-path spin and should not cause reattribution to pod_cpu_overload or code_artificial_delay. Corroborate the confirmation with the observed CPU rise and CPU-CFS throttling and with the loop sitting on the failing execution path.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<relevant_source_file>)
Evidence to collect: source_code_evidence, busy_loop_found, loop_spins_on_elapsed_condition, fixed_duration_spin_until_ms, placeorder_fixed_duration_spin, request_path_handler_or_helper_file, active_cpu_consumption_correlates_with_loop, loop_on_failing_request_path, p50_latency_matches_spin_duration, downstream_deadline_secondary_not_root_cause, non_busy_loop_code_pattern, file_read_mechanism_excluded, file_write_mechanism_excluded, artificial_delay_not_supported_for_active_spin, pod_cpu_overload_not_primary_after_spin_confirmed, root_cause_confirmation, code_defect_pattern
Next: (end of this SOP - conclude here)

### Step: confirm_excessive_file_reads_code
Goal: Confirm code_excessive_file_reads when retrieved source shows a repeated or unbounded file-read path whose allocated, parsed, or retained data explains the observed CPU saturation, CFS throttling, or latency after resource configuration is not independently explanatory. A fixed large or repeated per-request read is sufficient when the source and CPU or latency evidence align; do not require unbounded growth. This is distinct from code_busy_loop: when file I/O and CPU-heavy parsing are the concrete mechanism, use this branch rather than busy-loop attribution. This also applies when a normal CPU limit, healthy pods, and no application errors coexist with repeated request-path reads such as opening and ReadAll-ing the same file many times per request, including /src/log.csv ten times in PlaceOrder. Once source confirms repeated request-path file reads as the concrete CPU-consuming mechanism, conclude code_excessive_file_reads and do not present pod_cpu_overload or code_busy_loop as competing top predictions for the same incident. When the concrete mechanism is creating or writing a file rather than reading it, use confirm_excessive_file_writes_code.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<relevant_source_file>)
Evidence to collect: source_code_evidence, repeated_or_unbounded_file_read_path, repeated_file_read_count_per_request, same_file_reopened_per_request, file_read_on_failing_request_path, cpu_heavy_file_parsing, memory_or_cpu_growth_from_file_reads, normal_cpu_limit_not_explanatory, pod_cpu_overload_not_primary_after_excessive_reads_confirmed, busy_loop_not_supported_after_excessive_reads_confirmed, code_excessive_file_reads_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_excessive_file_writes_code
Goal: Confirm code_excessive_file_writes when retrieved source shows repeated, unbounded, or large per-request file writes that explain the observed CPU saturation, CFS throttling, filesystem-write surge, or latency after resource configuration is not independently explanatory. This includes a request-path handler or quote/pricing helper that creates a temporary file and writes tens of thousands of rows per request before flushing or deleting it, such as shippingservice quote.go where CreateQuoteFromCount -> CreateQuoteFromFloat creates /tmp/tmp.csv and writes 30000 CSV rows on every GetQuote call. Do not require unbounded growth: a fixed large write repeated by request traffic is sufficient when the source and filesystem-write or CPU evidence align. Do not require a latency-dominant or timeout-bearing symptom, and do not treat fast, error-free request-completion logs as evidence against this branch; a fs_write_bytes surge plus CPU elevation on healthy pods is enough once the bulk write site is found. When file creation or bulk serialization is the concrete mechanism, classify code_excessive_file_writes as the single leading root cause and do not retain code_busy_loop or pod_cpu_overload as competing top predictions: the CPU rise and CFS throttling are side effects of the file work, and busy-loop attribution would need its own active-spin evidence. Inspect quote/pricing helpers reachable from the hot RPC, not only main.go.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<relevant_source_file>)
- GetSourceCode(app_name=<service_name>, file_path=<quote_or_pricing_helper_file>)
Evidence to collect: source_code_evidence, repeated_or_large_file_write_path, per_request_file_creation, large_row_count_or_bulk_write, fixed_row_count_per_request_write, temp_file_create_write_delete_pattern, temp_csv_write_of_tens_of_thousands_of_rows, quote_or_pricing_helper_file_write_site, filesystem_write_surge_correlates_with_source_path, cpu_elevation_explained_by_file_write_work, fast_request_logs_do_not_exclude_file_write_defect, file_read_mechanism_excluded, busy_loop_mechanism_excluded, pod_cpu_overload_not_competing_after_file_write_confirmed, code_excessive_file_writes_confirmed
Next: (end of this SOP - conclude here)

### Step: attribute_observed_cpu_overload
Goal: Attribute the observed CPU saturation to pod_cpu_overload only after source inspection finds no busy-loop, including no duration-bounded spin-wait, no excessive-file-read including fixed repeated request-path reads such as repeated ReadAll of /src/log.csv, no excessive-file-write including fixed repeated or large request-path writes such as a per-request 30000-row temp CSV, or other supported code-level CPU defect. Once a supported code-level file-read, busy-loop, or file-write mechanism is confirmed, do not include pod_cpu_overload as a competing top prediction.
Actions:
Evidence to collect: observed_cpu_saturation, source_code_review_summary, no_supported_cpu_code_defect, busy_loop_excluded, excessive_file_read_excluded, excessive_file_write_excluded, cpu_limit_context, pod_cpu_overload_attributed
Next: (end of this SOP - conclude here)
