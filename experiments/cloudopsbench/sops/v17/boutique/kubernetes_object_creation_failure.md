# SOP: kubernetes_object_creation_failure

Symptom: An expected Kubernetes Pod, workload instance, or Service object is not created or has no ready instance, including partial service unreachability where a caller cannot resolve or connect to a Service that is absent from the namespace while the backing workload pods are Running and Ready.

Signals:
- workload has missing pods
- deployment has zero ready replicas
- service has empty endpoints because backend pods are absent
- ReplicaSet or deployment reports FailedCreate
- pod creation failed before a pod became Running
- expected Service object is missing
- connectivity probe localizes a missing or unavailable Service object
- caller logs show DNS no such host for a Service that does not exist in the namespace
- all backend pods for the expected Service are Running and Ready but the Service object is absent
- namespace service count is at its ResourceQuota hard limit

Start at: locate_creation_failure_scope

## Steps

### Step: locate_creation_failure_scope
Goal: Establish whether the observed creation symptom is missing workload pods, zero-ready deployment, or a missing Service object. When callers cannot reach a Service and that Service is absent while its backing deployment pods are Running and Ready, treat this as an admission/quota candidate and collect evidence for service-quota confirmation instead of stopping at DNS or environment-variable mismatch.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- GetResources(resource_type=services, namespace=<namespace>)
- GetResources(resource_type=deployments, namespace=<namespace>)
Evidence to collect: affected_workload, affected_deployment, pod_existence_status, deployment_readiness, pod_creation_failure_indicator, service_inventory, expected_service_name, missing_service_name, missing_service_object_confirmed, backing_pods_running_ready, caller_dns_failure_pattern, service_count_quota_candidate, empty_endpoints_due_to_absent_backends, admission_creation_suspected
Next: classify_pod_creation_admission_failure, locate_unreachable_downstream_service, confirm_namespace_service_quota

### Step: classify_pod_creation_admission_failure
Goal: Classify the reason for pod creation failure by inspecting deployment conditions, ReplicaSet events, and pod template/admission evidence.
Actions:
- DescribeResource(resource_type=deployment, name=<target_workload>, namespace=<application_namespace>)
Evidence to collect: deployment_conditions, deployment_condition_failure, deployment_replica_status, associated_replicaset_name, replicaset_reference, replicaset_creation_errors, admission_failure_reason, admission_error_message, service_account_name, suspected_missing_service_account, quota_exceeded_event, quota_type_exceeded, pod_quota_limit_reached, creation_failure_reason
Next: confirm_missing_service_account, confirm_namespace_pod_resource_quota

### Step: confirm_missing_service_account
Goal: Confirm whether the workload references a ServiceAccount that does not exist.
Actions:
- GetResources(resource_type=serviceaccounts, namespace=<application_namespace>)
Evidence to collect: service_account_reference, existing_service_accounts, missing_service_account_confirmed, competing_quota_evidence, root_cause_confirmation
Next: (end of this SOP - conclude here)

### Step: confirm_namespace_pod_resource_quota
Goal: Confirm whether a namespace ResourceQuota for pod count, CPU requests, or memory requests is exhausted and blocking pod creation.
Actions:
- GetResources(resource_type=resourcequotas, namespace=<application_namespace>)
Evidence to collect: namespace_quota_status, quota_resource_type, quota_used_value, quota_hard_limit, quota_exceeded_evidence, cpu_quota_exhausted, memory_request_quota_match, pod_quota_exhausted_confirmed, pod_memory_request, quota_limit_match, root_cause_confirmation
Next: (end of this SOP - conclude here)

### Step: locate_unreachable_downstream_service
Goal: Using service dependencies and connectivity probes, locate which downstream service is unreachable. When the downstream Service object is absent and its backend pods are Running and Ready, record the missing Service for quota confirmation rather than attributing the caller DNS error to DNS itself.
Actions:
- GetServiceDependencies(service_name=<entrypoint_workload>)
- CheckServiceConnectivity(service_name=<target_workload>, port=7070, namespace=<application_namespace>)
- CheckServiceConnectivity(service_name=<target_workload>, port=8080, namespace=<application_namespace>)
- CheckServiceConnectivity(service_name=<target_workload>, port=9555, namespace=<application_namespace>)
Evidence to collect: unreachable_service_name, connectivity_status, expected_service_name, service_absent_from_inventory, missing_service_object_confirmed, backing_pods_running_ready, caller_dns_failure_pattern
Next: confirm_namespace_service_quota

### Step: confirm_namespace_service_quota
Goal: Confirm that the missing Service is due to a namespace service-count ResourceQuota being exhausted. A caller DNS no such host error for an absent Service is only a consequence; the root cause is confirmed only when the namespace services used count equals or exceeds the hard limit and the expected Service object is absent.
Actions:
- DescribeResource(resource_type=service, name=<target_workload>, namespace=<application_namespace>)
- GetAppYAML(app_name=<target_workload>)
- GetResources(resource_type=services, namespace=<application_namespace>, label_selector=app=<target_workload>)
- GetResources(resource_type=resourcequotas, namespace=<application_namespace>)
- DescribeResource(resource_type=resourcequota, name=<resource_quota>, namespace=<application_namespace>)
Evidence to collect: suspicious_admission_signal, missing_service_object_confirmed, backing_pods_running_ready, namespace_quota_status, quota_resource_type, quota_used_value, quota_hard_limit, service_count_quota_exhausted, namespace_service_quota_exceeded
Next: (end of this SOP - conclude here)
