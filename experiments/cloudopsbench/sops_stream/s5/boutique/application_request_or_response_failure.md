# SOP: application_request_or_response_failure

Symptom: An application request reaches a service but returns an HTTP, RPC, or business-level error while basic service connectivity remains available.

Signals:
- HTTP 400, HTTP 500, or HTTP 503 is observed while downstream connectivity succeeds
- RPC request returns an application or business-semantics error
- downstream Service is reachable but the operation still fails
- logs show nil, missing, misplaced, or semantically invalid request values
- application returns an incorrect response despite healthy pods and routing
- downstream service logs show the operation succeeded while the caller still reports an error
- caller returns a fabricated or unconditional error after a successful downstream call

Start at: locate_request_failure_owner

## Steps

### Step: locate_request_failure_owner
Goal: Localize the service, operation, and caller/callee ownership of HTTP, RPC, or business request errors after basic service connectivity remains available, including whether the named downstream callee actually succeeded.
Actions:
- GetErrorLogs(namespace=<namespace>, service_name=<symptom_service>)
- GetRecentLogs(namespace=<namespace>, service_name=<symptom_service>)
- GetServiceDependencies(service_name=<symptom_service>)
- GetRecentLogs(namespace=<namespace>, service_name=<suspected_downstream_service>)
Evidence to collect: symptom_service, affected_operation, error_pattern, request_failure_type, current_service_role, upstream_callers, downstream_callee, code_owner_candidate, connectivity_available, downstream_operation_successful, caller_reported_error_after_downstream_success, error_origin_caller_or_callee
Next: triage_request_source

### Step: triage_request_source
Goal: Locate and inspect the likely code owner's relevant request-handling source files once before selecting missing-parameter, wrong-argument-order, or wrong-return confirmation. When a downstream service logs a successful operation but the caller still reports that operation failed, prioritize the caller's response/return handling as the code owner rather than treating the downstream service as defective.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
Evidence to collect: source_file_list, primary_source_file, request_construction_site, downstream_success_observed, caller_return_or_error_construction_site, missing_parameter_candidate, wrong_argument_order_candidate, wrong_return_candidate, no_supported_request_code_defect
Next: confirm_missing_parameter_code, confirm_wrong_argument_order_code, confirm_wrong_return_code

### Step: confirm_missing_parameter_code
Goal: Confirm code_missing_parameter when required request data is made absent before use: omitted from a request, assigned nil, overwritten with an empty string, or replaced by an empty/default value. This includes code such as currency = empty string or productID = empty string even when the downstream consequence is an error response. Do not reinterpret that downstream error as code_wrong_return, and require an actual swap of existing meaningful values before considering code_wrong_argument_order.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
Evidence to collect: source_code_evidence, required_parameter, parameter_cleared_or_omitted, code_missing_parameter_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_wrong_argument_order_code
Goal: Confirm code_wrong_argument_order only when two or more existing meaningful values are supplied to the wrong parameter or request-field positions, such as value A being passed where B belongs and value B where A belongs. A value merely cleared, omitted, nil, empty, or defaulted is code_missing_parameter, not wrong argument order.
Actions:
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
Evidence to collect: source_code_evidence, request_or_call_site, swapped_arguments, code_wrong_argument_order_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_wrong_return_code
Goal: Confirm code_wrong_return when the defect is at the return statement, response construction, or status-code selection itself: valid inputs reach the function, but it unconditionally or incorrectly returns an error, wrong status, or wrong response object. This includes a caller that successfully completes a downstream call and then fabricates or unconditionally returns an error such as a `charge card error`; when downstream logs show success and the caller still reports failure, inspect the caller's return path before attributing the defect to the downstream callee. An error caused by input data being cleared, omitted, nil, empty, defaulted, or passed to the wrong position does not by itself confirm code_wrong_return.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
Evidence to collect: source_code_evidence, valid_inputs_reach_return_path, downstream_success_observed, caller_error_constructed_after_success, unconditional_error_after_successful_downstream_call, incorrect_return_statement_or_response, code_wrong_return_confirmed
Next: (end of this SOP - conclude here)
