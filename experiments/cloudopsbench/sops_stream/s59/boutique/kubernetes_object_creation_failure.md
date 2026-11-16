# SOP: kubernetes_object_creation_failure

Symptom: An expected Kubernetes Pod, workload instance, or Service object is not created or has no ready instance. This includes a downstream Service object that is absent while its backing pods are Running and Ready, causing caller DNS lookup failures such as 'lookup <service> ... no such host'. It also includes a Service with no endpoints because its deployment has zero ready replicas and ReplicaSet FailedCreate reports namespace CPU quota exhaustion, such as frontend Service with no endpoints while frontend deployment is unavailable and ReplicaSet events show exceeded quota: cpu-quota requests.cpu=10/10.

Signals:
- workload has missing pods
- deployment has zero ready replicas
- service has empty endpoints because backend pods are absent
- ReplicaSet or deployment reports FailedCreate
- pod creation failed before a pod became Running
- expected Service object is missing
- connectivity probe localizes a missing or unavailable Service object
- downstream DNS lookup fails with no such host for a service name
- target Service object is absent from the namespace while its pods are Running and Ready
- caller error is a transport DNS failure rather than an application-level error
- service object missing despite healthy matching pod labels; do not mistake this for service_dns_resolution_failure, service_env_var_address_mismatch, or service_selector_mismatch
- frontend Service has no endpoints and no frontend pod exists while other backend pods are Running and Ready
- frontend deployment shows 0 updated, 0 total, 0 available, and 1 unavailable replica
- frontend ReplicaSet reports ReplicaFailure True / FailedCreate
- ReplicaSet failure event reports exceeded quota: cpu-quota, requested requests.cpu, used requests.cpu=10, limited requests.cpu=10
- namespace ResourceQuota cpu-quota shows requests.cpu used equal to hard limit such as 10/10
- an initial missing ServiceAccount error may appear, but the persistent pod-creation failure is namespace CPU quota exhaustion
- service empty endpoints with absent backend pods must not be classified as service_selector_mismatch or service_dns_resolution_failure

Start at: locate_creation_failure_scope

## Steps

### Step: locate_creation_failure_scope
Goal: Establish whether the observed creation symptom is missing workload pods, zero-ready deployment, or a missing Service object, and identify the affected workload without repeating namespace resource inventory. When callers report DNS lookup failure with no such host, include the target service name in the service inventory check and record whether the target Service object is absent while its pods are Running and Ready. When a Service has empty endpoints and no matching backend pod is Running, include deployment readiness and ReplicaSet FailedCreate evidence; do not stop at Service endpoints or selector inspection.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
- GetResources(resource_type=services, namespace=<namespace>)
- GetResources(resource_type=deployments, namespace=<namespace>)
- GetErrorLogs(namespace=<namespace>, service_name=<caller_service>) when caller reports downstream DNS failure
Evidence to collect: affected_workload, affected_deployment, pod_existence_status, deployment_readiness, pod_creation_failure_indicator, service_inventory, missing_service_name, missing_service_object_confirmed, empty_endpoints_due_to_absent_backends, service_empty_endpoints_no_backend_pod, deployment_zero_ready_replicas, admission_creation_suspected, dns_resolution_failure_no_such_host, target_service_object_absent, target_service_pods_running_ready, service_dns_resolution_failure_candidate, service_env_var_address_mismatch_candidate, service_selector_mismatch_candidate
Next: classify_pod_creation_admission_failure, locate_unreachable_downstream_service, confirm_missing_service_object

### Step: classify_pod_creation_admission_failure
Goal: Classify the reason for pod creation failure by inspecting deployment conditions, ReplicaSet events, and pod template/admission evidence. When a deployment has zero available replicas and its ReplicaSet reports FailedCreate, inspect the ReplicaSet failure message before selecting missing ServiceAccount or quota. If the failure message names a namespace ResourceQuota such as exceeded quota: cpu-quota with requests.cpu used at the hard limit, route directly to confirm_namespace_pod_resource_quota and treat deployment_zero_replicas as a symptom, not the root cause.
Actions:
- DescribeResource(resource_type=deployment, name=<target_workload>, namespace=<application_namespace>)
- DescribeResource(resource_type=replicaset, name=<associated_replicaset_name>, namespace=<application_namespace>) when deployment has zero available replicas or ReplicaFailure
Evidence to collect: deployment_conditions, deployment_condition_failure, deployment_replica_status, associated_replicaset_name, replicaset_reference, replicaset_creation_errors, admission_failure_reason, admission_error_message, service_account_name, suspected_missing_service_account, quota_exceeded_event, quota_type_exceeded, exceeded_quota_cpu_quota_event, requested_cpu_value, used_cpu_value, limited_cpu_value, pod_quota_limit_reached, creation_failure_reason
Next: confirm_missing_service_account, confirm_namespace_pod_resource_quota

### Step: confirm_missing_service_account
Goal: Confirm whether the workload references a ServiceAccount that does not exist. A missing ServiceAccount is the leading root cause only when it is independently and persistently blocking pod creation and no persistent quota-exceeded failure explains the same ReplicaSet FailedCreate events. An initial missing ServiceAccount event does not outweigh later persistent exceeded quota CPU failures; in that case exclude missing_service_account as the root cause and continue to confirm_namespace_pod_resource_quota.
Actions:
- GetResources(resource_type=serviceaccounts, namespace=<application_namespace>)
- GetResources(resource_type=resourcequotas, namespace=<application_namespace>) when competing quota evidence exists
Evidence to collect: service_account_reference, existing_service_accounts, missing_service_account_confirmed, competing_quota_evidence, persistent_quota_failure_outweighs_initial_serviceaccount_event, missing_service_account_excluded_when_quota_persistent, root_cause_confirmation
Next: (end of this SOP - conclude here) when the ServiceAccount is absent and independently persistent; otherwise continue to confirm_namespace_pod_resource_quota

### Step: confirm_namespace_pod_resource_quota
Goal: Confirm namespace CPU or memory ResourceQuota exhaustion when a ReplicaSet FailedCreate event names a namespace quota and the quota Used value equals its hard limit. This includes cpu-quota with requests.cpu used 10/10 and a failure event such as exceeded quota: cpu-quota, requested requests.cpu=1, used requests.cpu=10, limited requests.cpu=10. Conclude namespace_cpu_quota_exceeded as the single leading root cause for this incident; frontend Service having no endpoints and deployment_zero_replicas are consequences, and missing_service_account is excluded when the quota failure is the persistent blocker.
Actions:
- GetResources(resource_type=resourcequotas, namespace=<application_namespace>)
- DescribeResource(resource_type=resourcequota, name=<resource_quota>, namespace=<application_namespace>)
- DescribeResource(resource_type=replicaset, name=<associated_replicaset_name>, namespace=<application_namespace>) when the FailedCreate message needs confirmation
Evidence to collect: namespace_quota_status, quota_resource_type, quota_used_value, quota_hard_limit, cpu_quota_used_value, cpu_quota_hard_limit, requests_cpu_used_equal_hard, quota_exceeded_event_matches_requested_cpu, exceeded_quota_cpu_quota_event, quota_exceeded_evidence, cpu_quota_exhausted, memory_request_quota_match, pod_quota_exhausted_confirmed, service_empty_endpoints_consequence, frontend_service_no_endpoints_secondary, deployment_zero_available_replicas_symptom, missing_service_account_excluded_without_independent_persistent_failure, namespace_cpu_quota_exceeded_confirmed, root_cause_confirmation
Next: (end of this SOP - conclude here)

### Step: locate_unreachable_downstream_service
Goal: Using service dependencies and connectivity probes, locate which downstream service is unreachable. When DNS lookup fails with no such host for a named service, treat the absent Service object as the primary suspect and do not require connectivity probes against the missing Service.
Actions:
- GetServiceDependencies(service_name=<entrypoint_workload>)
- GetResources(resource_type=services, namespace=<application_namespace>)
- CheckServiceConnectivity(service_name=<target_workload>, port=7070, namespace=<application_namespace>)
- CheckServiceConnectivity(service_name=<target_workload>, port=8080, namespace=<application_namespace>)
- CheckServiceConnectivity(service_name=<target_workload>, port=9555, namespace=<application_namespace>)
Evidence to collect: unreachable_service_name, connectivity_status, dns_resolution_failure_no_such_host, target_service_object_absent
Next: confirm_missing_service_object, confirm_namespace_service_quota

### Step: confirm_missing_service_object
Goal: Confirm missing_service_object when the caller reports DNS lookup failure for a service name and that Service object is absent from the namespace while its backing pods are Running and Ready. Treat this as the root cause even if an application-level error such as failed to charge card is the visible symptom, and do not classify it as service_dns_resolution_failure, service_env_var_address_mismatch, or service_selector_mismatch: DNS no such host is a consequence of the absent Service object, not an independent DNS or selector defect. If the target Service object exists, do not use this branch; inspect its selector, ports, protocols, and endpoints under the service connectivity SOP.
Actions:
- GetResources(resource_type=services, namespace=<application_namespace>)
- DescribeResource(resource_type=service, name=<target_service_name>, namespace=<application_namespace>) when checking existence
- GetResources(resource_type=pods, namespace=<application_namespace>, output_wide=True)
- GetErrorLogs(namespace=<application_namespace>, service_name=<caller_service>)
Evidence to collect: target_service_name, target_service_object_absent, service_not_found_error, target_service_pods_running_ready, caller_dns_no_such_host_error, missing_service_object_confirmed, service_dns_resolution_failure_excluded, service_env_var_address_mismatch_excluded, service_selector_mismatch_excluded, namespace_service_quota_still_possible
Next: confirm_namespace_service_quota

### Step: confirm_namespace_service_quota
Goal: Confirm whether the missing Service object is due to a namespace service quota being exceeded. If no namespace service quota is exceeded, conclude missing_service_object as the leading root cause and do not fall back to service_dns_resolution_failure, service_env_var_address_mismatch, or service_selector_mismatch.
Actions:
- DescribeResource(resource_type=service, name=<target_workload>, namespace=<application_namespace>)
- GetAppYAML(app_name=<target_workload>)
- GetResources(resource_type=services, namespace=<application_namespace>, label_selector=app=<target_workload>)
- GetResources(resource_type=resourcequotas, namespace=<application_namespace>)
Evidence to collect: suspicious_admission_signal, namespace_quota_status, namespace_service_quota_exceeded, missing_service_object_confirmed, service_dns_resolution_failure_excluded, service_env_var_address_mismatch_excluded, service_selector_mismatch_excluded, root_cause_confirmed
Next: (end of this SOP - conclude here)
