# SOP: kubernetes_object_creation_failure

Symptom: An expected Kubernetes Pod, workload instance, or Service object is not created or has no ready instance.

Signals:
- workload has missing pods
- deployment has zero ready replicas
- service has empty endpoints because backend pods are absent
- ReplicaSet or deployment reports FailedCreate
- pod creation failed before a pod became Running
- expected Service object is missing
- connectivity probe localizes a missing or unavailable Service object

Start at: locate_creation_failure_scope

## Steps

### Step: locate_creation_failure_scope
Goal: Establish whether the observed creation symptom is missing workload pods, zero-ready deployment, or a missing Service object, and identify the affected workload without repeating namespace resource inventory.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- GetResources(resource_type=services, namespace=<namespace>)
- GetResources(resource_type=deployments, namespace=<namespace>)
Evidence to collect: affected_workload, affected_deployment, pod_existence_status, deployment_readiness, pod_creation_failure_indicator, service_inventory, missing_service_name, missing_service_object_confirmed, empty_endpoints_due_to_absent_backends, admission_creation_suspected
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
Goal: Using service dependencies and connectivity probes, locate which downstream service is unreachable.
Actions:
- GetServiceDependencies(service_name=<entrypoint_workload>)
- CheckServiceConnectivity(service_name=<target_workload>, port=7070, namespace=<application_namespace>)
- CheckServiceConnectivity(service_name=<target_workload>, port=8080, namespace=<application_namespace>)
- CheckServiceConnectivity(service_name=<target_workload>, port=9555, namespace=<application_namespace>)
Evidence to collect: unreachable_service_name, connectivity_status
Next: confirm_namespace_service_quota

### Step: confirm_namespace_service_quota
Goal: Confirm that the missing service is due to a namespace service quota exceeded by inspecting admission signals and verifying the quota.
Actions:
- DescribeResource(resource_type=service, name=<target_workload>, namespace=<application_namespace>)
- GetAppYAML(app_name=<target_workload>)
- GetResources(resource_type=services, namespace=<application_namespace>, label_selector=app=<target_workload>)
Evidence to collect: suspicious_admission_signal, namespace_quota_status, namespace_service_quota_exceeded
Next: (end of this SOP - conclude here)
