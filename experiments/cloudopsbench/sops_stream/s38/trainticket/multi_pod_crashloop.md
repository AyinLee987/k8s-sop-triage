# SOP: multi_pod_crashloop

Symptom: Multiple TrainTicket service pods show crash, error, or repeated restart states at the same time.

Signals:
- multiple TrainTicket service pods are in CrashLoopBackOff, Error, or repeated startup failure
- many pods have elevated restart counts
- alerts affect several unrelated business services at once
- abnormal pods belong to more than one TrainTicket service
- the issue is visible from pod listings before inspecting service-specific configuration

Start at: runtime.locate_multi_service_crashloop_scope

## Steps

### Step: runtime.locate_multi_service_crashloop_scope
Goal: Detect a wide CrashLoopBackOff pattern across multiple TrainTicket services, which suggests a shared backend dependency rather than an isolated application configuration error.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>)
Evidence to collect: pod_health_baseline, affected_workloads, crash_loop_backoff_pods, multi_service_crashloop_detected, shared_dependency_suspected
Next: runtime.inspect_representative_db_connection_crash

### Step: runtime.inspect_representative_db_connection_crash
Goal: Inspect a representative crashing service log to classify whether the shared crash pattern is caused by database connection exhaustion.
Actions:
- GetErrorLogs(namespace=<namespace>, service_name=<affected_service>)
Evidence to collect: pod_state_details, application_error_logs, crash_reason, db_connection_exhaustion, connection_pool_exhausted, too_many_connections, pod_exit_error_code_1_no_root_cause
Next: runtime.verify_mysql_backend_for_connection_exhaustion

### Step: runtime.verify_mysql_backend_for_connection_exhaustion
Goal: Check MySQL pod health and database logs after many services crash with connection pool failures, confirming that the symptom is caused by connection exhaustion rather than a missing database.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, name=<mysql_workload>)
Evidence to collect: database_pod_status, database_log_status, database_operational, connection_limit_exceeded, root_cause_confirmation
Next: (end of this SOP - conclude here)
