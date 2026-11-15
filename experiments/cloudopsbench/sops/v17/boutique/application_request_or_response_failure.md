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
- a handler extracts a valid path/query/body parameter and then overwrites it with an empty string, nil, or a default value before use, producing a downstream empty-value error, NotFound, or 'no product with ID ' while the request itself contains a valid value
- a downstream RPC receives an empty argument even though the caller's request path contained a valid value
- a handler sets a required value to nil, an empty string, or a default before building an outgoing RPC/HTTP request, so the downstream receives a nil/empty required field even though no request-path value is involved (for example, `money = nil` followed by `From: money`)
- the downstream callee logs a null/undefined field error such as "Cannot read properties of null (reading 'units')" or "Cannot use 'in' operator to search for 'message' in ..." while the caller returns HTTP 500; this is a missing required parameter sent by the caller, not an error-branch defect
- caller and callee both show the same null-field error chain (caller sends nil required field, callee throws TypeError reading that field) for an otherwise healthy request
- source review distinguishes a caller-side nil/empty assignment or omission of a required request field (code_missing_parameter) from an inverted guard or wrong error branch that rejects an otherwise valid, non-emptied value (code_wrong_return)

Start at: locate_request_failure_owner

## Steps

### Step: locate_request_failure_owner
Goal: Localize the service, operation, and caller/callee ownership of HTTP, RPC, or business request errors after basic service connectivity remains available. When an alert names a downstream service but caller logs show valid requests failing at the entry service, keep the caller handler response/status defect as the leading branch and treat the downstream availability alert as a secondary symptom. When a request contains a valid parameter but a downstream call carries an empty value, suspect handler-side parameter clearing as code_missing_parameter rather than assuming wrong_return. When a downstream callee logs a null/undefined read on a required field (for example "Cannot read properties of null (reading 'units')" or "Cannot use 'in' operator ...") and the caller returns HTTP 500, treat this as a caller sending a nil/empty required field and keep code_missing_parameter as the leading branch unless source shows valid, non-emptied inputs reaching a defective return/error branch.
Actions:
- GetAlerts(namespace=<namespace>)
- GetErrorLogs(namespace=<namespace>, service_name=<symptom_service>)
- GetRecentLogs(namespace=<namespace>, service_name=<symptom_service>)
- GetServiceDependencies(service_name=<symptom_service>)
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- GetResources(resource_type=services, namespace=<namespace>)
Evidence to collect: symptom_service, affected_operation, error_pattern, request_failure_type, current_service_role, upstream_callers, downstream_callee, code_owner_candidate, connectivity_available, pods_running_ready, caller_error_paths, handler_received_valid_input, request_path_value_present, downstream_received_empty_parameter, downstream_null_field_error, parameter_clearing_suspected, downstream_logs_clean_or_normal, downstream_alert_is_secondary
Next: triage_request_source

### Step: triage_request_source
Goal: Locate and inspect the likely code owner's relevant request-handling and request-construction source files once before selecting missing-parameter, wrong-argument-order, or wrong-return confirmation. Verify whether the failing handler actually received valid input by correlating the request path, extracted parameters, local variable flow, request-construction sites for outgoing RPC/HTTP calls, and the exact error branch that produced the response. Do not classify from the error message alone; a message saying 'not specified', 'no product with ID', or a downstream empty-ID RPC can be emitted after the handler extracts a present path value and then overwrites it with an empty string, nil, or default. The emptied value need not originate from the request path: when a handler assigns nil, an empty string, or a default to an internal value and then passes that value as a required field of a downstream request (for example `money = nil` followed by `From: money`, producing a downstream null-field TypeError), treat it as code_missing_parameter, not code_wrong_return. If source shows a valid request parameter is extracted and handler-side logic then clears, resets, assigns an empty string/nil, or replaces it with a default before use, keep code_missing_parameter as the leading branch. Keep code_wrong_return only for the case where the extracted parameter (or otherwise valid input) remains non-emptied and a defective return statement, inverted guard, or wrong error branch rejects or misreports it.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
- GetSourceCode(app_name=<service_name>, file_path=rpc.go) when the failing service is frontend or a caller that builds downstream RPC requests
- GetSourceCode(app_name=<service_name>, file_path=handlers.go) when applicable
Evidence to collect: source_file_list, primary_source_file, request_construction_site, handler_entry_site, path_parameter_extraction, request_path_value_present, parameter_extracted_before_handler_mutation, local_variable_assignment_after_extraction, parameter_cleared_by_handler_logic, parameter_assigned_empty_string_nil_or_default, required_field_emptied_before_rpc, emptied_value_not_from_request_path, outgoing_rpc_field_assignment, downstream_empty_value_or_notfound, downstream_null_field_error, error_message_origin, response_return_site, missing_parameter_candidate, wrong_argument_order_candidate, wrong_return_candidate, no_supported_request_code_defect
Next: confirm_missing_parameter_code, confirm_wrong_argument_order_code, confirm_wrong_return_code

### Step: confirm_missing_parameter_code
Goal: Confirm code_missing_parameter when required request data is made absent before use: omitted from the incoming request, extraction returns no value, the value is assigned nil, overwritten with an empty string, replaced by an empty/default value, or cleared by handler-side logic. This includes code such as currency = "", productID = "", or money = nil even when the downstream consequence is an error response, NotFound, or 'no product with ID '. It also includes values not sourced from the request path: a handler that assigns nil/empty/default to an internal value and then passes it into a required field of an outgoing RPC/HTTP request, so the downstream receives a nil/empty required field and returns a null/undefined read error. Do not confirm missing parameter merely because the handler emits an error message such as 'id not specified'; instead, use the source to distinguish genuine request absence from handler-side clearing. If the incoming path, query, or body contains a valid value and the handler extracts it but then empties it, confirm code_missing_parameter and reject code_wrong_return. Likewise, if a downstream callee reports "Cannot read properties of null" / "Cannot use 'in' operator ..." and caller source shows a nil/empty assignment feeding the outgoing request field, confirm code_missing_parameter and reject code_wrong_return. Require an actual swap of existing meaningful values before considering wrong argument order.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
- GetSourceCode(app_name=<service_name>, file_path=rpc.go) when the caller builds downstream RPC requests
Evidence to collect: source_code_evidence, required_parameter, parameter_absent_from_incoming_request, extraction_returned_no_value, request_path_value_present, parameter_extracted_before_handler_mutation, handler_side_clear_or_reset_explains_empty_local, parameter_assigned_empty_string_or_nil, parameter_replaced_with_default_value, caller_request_construction_site, required_field_emptied_before_rpc, emptied_value_not_from_request_path, downstream_empty_value_or_notfound, downstream_null_field_error, error_branch_selection, code_missing_parameter_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_wrong_argument_order_code
Goal: Confirm code_wrong_argument_order only when two or more existing meaningful values are supplied to the wrong parameter or request-field positions, such as value A being passed where B belongs and value B where A belongs. A value merely cleared, omitted, nil, empty, or defaulted is code_missing_parameter, not wrong argument order.
Actions:
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
Evidence to collect: source_code_evidence, request_or_call_site, swapped_arguments, code_wrong_argument_order_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_wrong_return_code
Goal: Confirm code_wrong_return when valid, non-emptied request inputs reach the function or handler but the function returns an error, wrong status, or wrong response object because of a defective return statement, response construction, status or error-branch selection, inverted guard, or unconditional error return. The extracted parameter and all required outgoing-request fields must remain valid and must not be cleared, reset, assigned an empty string/nil, or replaced with a default by handler-side logic. If the handler extracts a parameter and then empties it before use, that is code_missing_parameter, even when the response is NotFound, BadRequest, 500, or a message such as 'no product with ID '. Likewise, an outgoing RPC/HTTP request built with a nil/empty required field (for example `money = nil` then `From: money`) that makes the downstream throw a null/undefined read error is a missing required parameter, not wrong_return, even though the failure surfaces as a failed response; use confirm_missing_parameter_code in that case. If request data is genuinely absent before handler use, use confirm_missing_parameter_code; if existing meaningful values are swapped, use confirm_wrong_argument_order_code.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
- GetErrorLogs(namespace=<namespace>, service_name=<service_name>)
Evidence to collect: source_code_evidence, valid_inputs_reach_return_path, request_path_value_present, parameter_extracted_before_handler_mutation, parameter_value_not_cleared_or_emptied, no_handler_side_clear_or_reset, outgoing_request_field_nil_or_empty_excludes_wrong_return, no_downstream_null_field_error_from_caller_nil, incorrect_return_statement_or_response, wrongly_selected_error_branch, inverted_error_guard, unconditional_error_return_after_extraction, incorrect_status_code, code_wrong_return_confirmed
Next: (end of this SOP - conclude here)
