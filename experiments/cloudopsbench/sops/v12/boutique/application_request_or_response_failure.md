# SOP: application_request_or_response_failure

Symptom: An application request reaches a service but returns an HTTP, RPC, or business-level error while basic service connectivity remains available.

Signals:
- HTTP 400, HTTP 500, or HTTP 503 is observed while downstream connectivity succeeds
- RPC request returns an application or business-semantics error
- downstream Service is reachable but the operation still fails
- logs show nil, missing, misplaced, semantically invalid request values, or an error response for valid request input
- application returns an incorrect response or status despite healthy pods and routing
- repeated 400 or 5xx responses occur on valid user paths, with handler logs such as 'id not specified' even though the path segment is present
- an entry-service handler logs 'product id not specified' for GET /product/<id> while the same request path contains a valid product ID
- source review shows the handler extracts a valid path/query/body parameter, then a defective local assignment, inverted guard, unconditional clear, or wrong error branch causes the handler to return BadRequest or 'not specified'
- a downstream service such as recommendationservice has an availability alert, but its own logs show normal successful processing and the caller logs localize the failure at the entry service

Start at: locate_request_failure_owner

## Steps

### Step: locate_request_failure_owner
Goal: Localize the service, operation, and caller/callee ownership of HTTP, RPC, or business request errors after basic service connectivity remains available. When an alert names a downstream service but caller logs show valid requests failing at the entry service, keep the caller handler response/status defect as the leading branch and treat the downstream availability alert as a secondary symptom.
Actions:
- GetAlerts(namespace=<namespace>)
- GetErrorLogs(namespace=<namespace>, service_name=<symptom_service>)
- GetRecentLogs(namespace=<namespace>, service_name=<symptom_service>)
- GetServiceDependencies(service_name=<symptom_service>)
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- GetResources(resource_type=services, namespace=<namespace>)
Evidence to collect: symptom_service, affected_operation, error_pattern, request_failure_type, current_service_role, upstream_callers, downstream_callee, code_owner_candidate, connectivity_available, pods_running_ready, caller_error_paths, handler_received_valid_input, downstream_logs_clean_or_normal, downstream_alert_is_secondary
Next: triage_request_source

### Step: triage_request_source
Goal: Locate and inspect the likely code owner's relevant request-handling source files once before selecting missing-parameter, wrong-argument-order, or wrong-return confirmation. Verify whether the failing handler actually received valid input by correlating the request path, extracted parameters, local variable flow, and the exact error branch that produced the response. Do not classify from the error message alone; a message saying 'not specified' can be emitted after the handler extracts a present path value and then clears it or selects a wrong error return. If source shows a valid request parameter is extracted and handler-side logic then clears, resets, short-circuits, or rejects it, keep code_wrong_return as the leading branch and do not treat the resulting empty local value as a missing request parameter.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
- GetSourceCode(app_name=<service_name>, file_path=handlers.go) when applicable
Evidence to collect: source_file_list, primary_source_file, request_construction_site, handler_entry_site, path_parameter_extraction, request_path_value_present, parameter_extracted_before_handler_mutation, local_variable_assignment_after_extraction, parameter_cleared_by_handler_logic, error_message_origin, response_return_site, missing_parameter_candidate, wrong_argument_order_candidate, wrong_return_candidate, no_supported_request_code_defect
Next: confirm_missing_parameter_code, confirm_wrong_argument_order_code, confirm_wrong_return_code

### Step: confirm_missing_parameter_code
Goal: Confirm code_missing_parameter only when required request data is genuinely absent from the incoming request or extraction before the handler has a valid value: path, query, or body field omitted, extraction returns no value because no value was supplied, or the required field is not present in the request. Do not confirm missing parameter merely because the handler returns an error message such as 'id not specified', because a local variable is empty after a handler-side clear or reset, or because a defective error branch selected the missing-parameter response. If the incoming path, query, or body contains a valid value and the handler extracts it, then a later local assignment, inverted guard, unconditional clear, or wrong response/status selection that rejects the request is code_wrong_return; reject missing parameter and continue to confirm_wrong_return_code. Require an actual swap of existing meaningful values before considering wrong argument order.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
Evidence to collect: source_code_evidence, required_parameter, parameter_absent_from_incoming_request, extraction_returned_no_value, request_path_value_present, parameter_extracted_before_handler_mutation, handler_side_clear_or_reset_explains_empty_local, error_branch_selection, code_missing_parameter_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_wrong_argument_order_code
Goal: Confirm code_wrong_argument_order only when two or more existing meaningful values are supplied to the wrong parameter or request-field positions, such as value A being passed where B belongs and value B where A belongs. A value merely cleared, omitted, nil, empty, or defaulted is code_missing_parameter, not wrong argument order.
Actions:
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
Evidence to collect: source_code_evidence, request_or_call_site, swapped_arguments, code_wrong_argument_order_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_wrong_return_code
Goal: Confirm code_wrong_return when valid request inputs reach the function or handler but the function returns an error, wrong status, or wrong response object because of a defective return statement, response construction, status or error-branch selection, inverted guard, unconditional clear or reset after extraction, or unconditional error return. This includes a handler with a non-empty path, query, or body parameter that extracts the value and then through handler-side code clears it, inverts the guard, or otherwise unconditionally or incorrectly returns BadRequest, an 'id not specified' error, a 500, or another wrong response. If the request contains the parameter and the handler extracts it, but local handler logic empties or rejects it, confirm wrong_return rather than missing_parameter. If request data is genuinely absent before handler use, such as omitted from the incoming request or extraction yielding no value, use confirm_missing_parameter_code; if existing meaningful values are swapped, use confirm_wrong_argument_order_code.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
- GetErrorLogs(namespace=<namespace>, service_name=<service_name>)
Evidence to collect: source_code_evidence, valid_inputs_reach_return_path, request_path_value_present, parameter_extracted_before_handler_mutation, handler_side_clear_or_reset, parameter_not_absent_in_request, incorrect_return_statement_or_response, wrongly_selected_error_branch, inverted_error_guard, unconditional_error_return_after_extraction, incorrect_status_code, code_wrong_return_confirmed
Next: (end of this SOP - conclude here)
