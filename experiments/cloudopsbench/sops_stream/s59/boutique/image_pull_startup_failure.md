# SOP: image_pull_startup_failure

Symptom: A pod cannot start because its container image cannot be pulled, appearing as ErrImagePull or ImagePullBackOff.

Signals:
- pod status is ErrImagePull
- pod status is ImagePullBackOff
- pod cannot pull container image
- failing pod is non-Running because of image pull

Start at: locate_failing_startup_pod

## Steps

### Step: locate_failing_startup_pod
Goal: Locate the failing pod causing service disruption by inspecting pod readiness and status reasons in the application namespace.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>)
Evidence to collect: active_alerts_status, pod_health_status, pod_status_summary, failing_pod_identified
Next: classify_image_pull_failure

### Step: classify_image_pull_failure
Goal: Classify the image pull failure reason from failing pod events by extracting pull error messages and distinguishing between DNS failure, incorrect image reference, and registry authorization failure.
Actions:
- DescribeResource(resource_type=pod, name=<failing_pod_name>, namespace=<application_namespace>)
Evidence to collect: image_pull_error_message, pull_failure_reason
Next: confirm_registry_dns_failure, confirm_incorrect_image_reference, confirm_missing_image_pull_secret

### Step: confirm_registry_dns_failure
Goal: Verify that the DNS failure is specific to the failing image registry by comparing registries across services.
Actions:
- GetAppYAML(app_name=<failing_service_name>)
- GetAppYAML(app_name=<healthy_service_name>)
Evidence to collect: failing_service_image_registry, healthy_service_image_registry, registry_isolation_confirmed, no_image_pull_secret_issue
Next: (end of this SOP - conclude here)

### Step: confirm_incorrect_image_reference
Goal: Confirm that the failing deployment image tag is incorrect by inspecting deployment YAML, comparing with healthy services, and checking image pull secrets.
Actions:
- GetAppYAML(app_name=<failing-service-name>)
Evidence to collect: deployment_image_tag, healthy_service_tag, image_pull_secrets_status, incorrect_image_reference_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_missing_image_pull_secret
Goal: Confirm whether the deployment lacks imagePullSecrets configuration given registry authorization failure.
Actions:
- GetAppYAML(app_name=<failing-service>)
Evidence to collect: deployment_yaml, image_pull_secrets_presence, image_reference, missing_image_pull_secret_indicator, image_registry_requirement
Next: (end of this SOP - conclude here)
