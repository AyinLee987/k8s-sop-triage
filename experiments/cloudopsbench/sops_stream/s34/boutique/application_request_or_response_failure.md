# SOP: application_request_or_response_failure

Symptom: An application request reaches a service but returns an HTTP, RPC, or business-level error while basic service connectivity remains available. This includes intermittent HTTP 503 responses on user journeys while all pods remain Running and Ready.

Signals:
- HTTP 400, HTTP 500, or HTTP 503 is observed while downstream connectivity succeeds
- RPC request returns an application or business-semantics error
- downstream Service is reachable but the operation still fails
- logs show nil, missing, misplaced, or semantically invalid request values
- application returns an incorrect response despite healthy pods and routing
- downstream service logs show the operation succeeded while the caller still reports an error
- caller returns a fabricated or unconditional error after a successful downstream call
- downstream callee is reachable but returns NotFound or InvalidArgument naming a missing or empty required parameter
- caller request contains a valid parameter value while the callee reports that same parameter as missing or empty
- a local HTTP handler returns 400 for a request whose path or query contains a valid required value and logs that the value is missing
- request handler extracts a path parameter and then explicitly clears or overwrites it with an empty or default value before validation
- frontend returns HTTP 503 for product-detail or cart journeys while all pods are Running and Ready
- downstream cartservice logs show AddItemAsync or GetCartAsync receiving a product ID in the userId field and a session UUID in the productId field
- caller cart handler explicitly swaps userID and productID before calling insertCart or AddItem

Start at: locate_request_failure_owner

## Steps

### Step: locate_request_failure_owner
Goal: Localize the service, operation, and caller/callee ownership of HTTP, RPC, or business request errors after basic service connectivity remains available, including whether the named downstream callee actually succeeded, whether the caller supplied a valid request value, whether the error was emitted locally by a handler that claims a required request value is missing, and whether downstream logs show semantically swapped identifiers that indicate a caller argument-order fault.
Actions:
- GetErrorLogs(namespace=<namespace>, service_name=<symptom_service>)
- GetRecentLogs(namespace=<namespace>, service_name=<symptom_service>)
- GetServiceDependencies(service_name=<symptom_service>)
- GetRecentLogs(namespace=<namespace>, service_name=<suspected_downstream_service>)
- GetErrorLogs(namespace=<namespace>, service_name=<suspected_downstream_service>)
Evidence to collect: symptom_service, affected_operation, error_pattern, request_failure_type, current_service_role, upstream_callers, downstream_callee, code_owner_candidate, connectivity_available, downstream_operation_successful, caller_reported_error_after_downstream_success, caller_request_value_present, caller_request_value_valid, local_handler_missing_parameter_error, downstream_error_type, downstream_semantic_missing_parameter, downstream_callee_reachable, error_origin_caller_or_callee, healthy_pods_during_503, downstream_argument_identifiers, product_id_in_user_id_field, session_uuid_in_product_id_field, identifier_semantics_swapped, caller_argument_order_candidate
Next: triage_request_source

### Step: triage_request_source
Goal: Locate and inspect the likely code owner's relevant request-handling source files once before selecting missing-parameter, wrong-argument-order, or wrong-return confirmation. When a downstream service logs a successful operation but the caller still reports that operation failed, prioritize the caller's response or return handling as the code owner rather than treating the downstream service as defective. When the caller supplies a valid request value but the reachable downstream callee returns a semantic NotFound or InvalidArgument error naming a missing or empty required parameter, inspect the callee's request-handling source for a cleared or omitted field before attributing the defect to the caller. When the local service's own handler returns HTTP 400 or an equivalent invalid-argument error saying a required parameter is missing even though the original request path or query contains a valid value, treat that local handler as the code owner and inspect its parameter extraction and validation site before investigating downstream services. When multiple frontend journeys return HTTP 503 while pods are healthy and a common downstream callee logs identifiers in semantically wrong fields, prioritize inspecting the caller's cart or insert call site for swapped userID and productID before treating the callee as defective, missing-parameter, or wrong-return.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
- ListCodeFiles(app_name=<downstream_callee_service>)
- GetSourceCode(app_name=<downstream_callee_service>, file_path=<callee_candidate_source_file>)
- GetRecentLogs(namespace=<namespace>, service_name=<downstream_callee_service>)
Evidence to collect: source_file_list, primary_source_file, request_handler_or_helper_file, request_construction_site, caller_argument_valid, local_handler_emitted_missing_parameter, request_path_parameter_present, handler_parameter_extraction_site, handler_parameter_validation_site, downstream_success_observed, downstream_semantic_input_error, callee_input_handling_site, callee_field_cleared_or_omitted_candidate, caller_return_or_error_construction_site, missing_parameter_candidate, wrong_argument_order_candidate, wrong_return_candidate, no_supported_request_code_defect, downstream_argument_order_anomaly, callee_received_product_id_as_user_id, callee_received_session_uuid_as_product_id, caller_insert_cart_call_site
Next: confirm_missing_parameter_code, confirm_wrong_argument_order_code, confirm_wrong_return_code

### Step: confirm_missing_parameter_code
Goal: Confirm code_missing_parameter when required request data is made absent before use: omitted from a request, assigned nil, overwritten with an empty string, or replaced by an empty or default value. This includes a callee that receives a valid caller value and then clears it, such as productID := req.Id followed by productID = empty string before lookup, even when the downstream consequence is a NotFound error response. It also includes a local request handler that extracts a valid path or query parameter and then explicitly clears it, for example extracting productID from mux.Vars and then assigning productID = empty string before validation, producing HTTP 400 or an invalid-argument error that says the required product ID was not specified. Do not reinterpret that downstream or local validation error as code_wrong_return, and require an actual swap of existing meaningful values before considering code_wrong_argument_order. If callee logs show a product ID in userId and a session UUID in productId, use confirm_wrong_argument_order_code rather than this missing-parameter branch.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
Evidence to collect: source_code_evidence, required_parameter, parameter_cleared_or_omitted, caller_supplied_value_valid, request_path_or_query_parameter_present, handler_extracted_value_then_cleared, handler_cleared_required_parameter_before_validation, local_missing_parameter_error_after_clear, callee_cleared_required_parameter, code_missing_parameter_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_wrong_argument_order_code
Goal: Confirm code_wrong_argument_order only when two or more existing meaningful values are supplied to the wrong parameter or request-field positions, such as value A being passed where B belongs and value B where A belongs. This includes frontend addToCartHandler passing userID where productID belongs and productID where userID belongs to cartservice insertCart or AddItem, producing downstream logs where userId contains a product ID and productId contains a session UUID and causing cart operations or frontend requests to fail with HTTP 503 while pods are healthy. A value merely cleared, omitted, nil, empty, or defaulted is code_missing_parameter, not wrong argument order.
Actions:
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
- GetRecentLogs(namespace=<namespace>, service_name=<downstream_callee_service>)
Evidence to collect: source_code_evidence, request_or_call_site, swapped_arguments, caller_argument_order_site, callee_log_userid_productid_swapped, product_id_in_user_id_field, session_uuid_in_product_id_field, healthy_pods_during_503, code_wrong_argument_order_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_wrong_return_code
Goal: Confirm code_wrong_return when the defect is at the return statement, response construction, or status-code selection itself: valid inputs reach the function, but it unconditionally or incorrectly returns an error, wrong status, or wrong response object. This includes a caller that successfully completes a downstream call and then fabricates or unconditionally returns an error such as a charge card error; when downstream logs show success and the caller still reports failure, inspect the caller's return path before attributing the defect to the downstream callee. An error caused by input data being cleared, omitted, nil, empty, defaulted, or passed to the wrong position does not by itself confirm code_wrong_return. A local handler that rejects a valid request because it cleared or overwrote the extracted required parameter before validation is code_missing_parameter, not code_wrong_return. Swapped userID/productID arguments or downstream logs showing identifiers in semantically wrong fields are code_wrong_argument_order, not code_wrong_return.
Actions:
- ListCodeFiles(app_name=<service_name>)
- GetSourceCode(app_name=<service_name>, file_path=<candidate_source_file>)
Evidence to collect: source_code_evidence, valid_inputs_reach_return_path, downstream_success_observed, caller_error_constructed_after_success, unconditional_error_after_successful_downstream_call, incorrect_return_statement_or_response, code_wrong_return_confirmed
Next: (end of this SOP - conclude here)
