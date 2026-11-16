# SOP: create_container_config_error

Symptom: A TrainTicket pod is blocked before container startup with CreateContainerConfigError.

Signals:
- pod status is CreateContainerConfigError
- container waiting reason is CreateContainerConfigError
- deployment has unavailable replicas because one pod cannot be configured
- startup failure appears before the application container begins running

Start at: startup.locate_create_container_config_error_pod

## Steps

### Step: startup.locate_create_container_config_error_pod
Goal: Locate the TrainTicket workload pod blocked during startup with CreateContainerConfigError, while retaining deployment and alert context for later configuration inspection.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>)
Evidence to collect: startup_failed_pod, affected_workload, affected_deployment_health, container_waiting_reason, create_container_config_error, required_secret_reference, required_configmap_reference
Next: startup.inspect_startup_config_error_events

### Step: startup.inspect_startup_config_error_events
Goal: Inspect the localized startup-failed pod to classify the CreateContainerConfigError and extract the required Secret or ConfigMap reference from pod configuration and kubelet events.
Actions:
- DescribeResource(resource_type=pod, name=<startup_failed_pod>, namespace=<namespace>)
Evidence to collect: startup_failed_pod, container_waiting_reason, container_configuration_error_detail, pod_event_summary, required_secret_reference, secret_reference_optional_status, required_configmap_reference, secret_not_found_event, configmap_not_found_event, missing_configuration_reference
Next: startup.confirm_required_secret_absent

### Step: startup.confirm_required_secret_absent
Goal: Confirm whether the required Secret referenced by the CreateContainerConfigError pod is absent from the namespace, and distinguish missing Secret binding from other startup configuration dependencies.
Actions:
- GetResources(resource_type=secrets, namespace=<namespace>, name=<required_secret>)
Evidence to collect: required_secret_reference, secret_existence_status, workload_secret_reference_confirmation, secret_name_discrepancy, required_configmap_reference, configmap_existence_status, alternate_configuration_dependency_excluded, root_cause_confirmation
Next: (end of this SOP - conclude here)
