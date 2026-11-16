# SOP: pending_pod_storage_dependency

Symptom: A Pending pod reports that a PVC or another persistent-storage dependency is unresolved.

Signals:
- pod is Pending with PVC-related event
- PersistentVolumeClaim is unbound
- scheduler reports unbound immediate PersistentVolumeClaims
- storage-backed workload cannot schedule because volume binding is unresolved
- pod is Pending because a PVC or storage dependency is missing
- expected PVC is absent
- pod events point to a storage claim creation dependency

Start at: locate_pending_storage_workload

## Steps

### Step: locate_pending_storage_workload
Goal: Locate the Pending storage-backed pod and affected workload while retaining only the node/alert context needed to enter PVC dependency classification.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
Evidence to collect: pending_pod_identified, pending_pod_name, affected_workload, failing_service, pod_status_summary, active_alerts_summary, node_status_summary
Next: classify_pod_pvc_dependency

### Step: classify_pod_pvc_dependency
Goal: Inspect the Pending pod once, identify its referenced PVC, and distinguish a missing PVC object from an existing Pending/unbound PVC.
Actions:
- DescribeResource(resource_type=pod, name=<pending_pod>, namespace=<namespace>)
- GetResources(resource_type=persistentvolumeclaims, namespace=<namespace>)
Evidence to collect: pod_events, pod_pending_cause, referenced_pvc_name, pvc_object_exists, pvc_object_missing, pvc_status, pending_pvc_identified, storage_request, missing_pvc_reason, storage_quota_verification_candidate
Next: classify_pvc_pv_compatibility, confirm_namespace_storage_quota

### Step: classify_pvc_pv_compatibility
Goal: Inspect the unbound PVC and available PVs once to classify occupied binding, access-mode, capacity, selector, or storage-class incompatibility. Evaluate a compatible PV's existing claimRef before interpreting the generic event "no persistent volumes available and no storage class is set"; an empty storageClassName is not itself storage-class mismatch.
Actions:
- DescribeResource(resource_type=pvc, name=<pvc_name>, namespace=<namespace>)
- GetResources(resource_type=persistentvolumes)
Evidence to collect: pvc_binding_failure_reason, pvc_status, pvc_storage_request, pvc_access_modes, pvc_selector, pvc_storage_class, candidate_pv_name, available_pv_count, pv_binding_status, pv_capacity, pv_access_modes, pv_labels, pv_storage_class, occupied_pv_suspected, access_mode_mismatch_suspected, capacity_mismatch_suspected, selector_mismatch_suspected, storage_class_mismatch_suspected
Next: confirm_pv_binding_occupied, confirm_pvc_access_mode_mismatch, confirm_pvc_capacity_mismatch, confirm_pvc_selector_mismatch, confirm_pvc_storage_class_mismatch

### Step: confirm_pv_binding_occupied
Goal: Confirm pv_binding_occupied when a PV that otherwise satisfies the pending PVC's capacity, access mode, selector, and storage-class requirements is already Bound to a different PVC. The generic event "no persistent volumes available and no storage class is set" does not imply storage-class mismatch; inspect existing PV claimRef/binding first.
Actions:
- GetResources(resource_type=persistentvolumes)
- DescribeResource(resource_type=pvc, name=<bound_pvc>, namespace=<namespace>)
Evidence to collect: pending_pvc_requirements, compatible_pv_name, pv_phase_bound, pv_bound_claim, pv_binding_occupied_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_pvc_access_mode_mismatch
Goal: Compare the PVC's requested access mode and capacity with available PersistentVolumes to verify the binding mismatch.
Actions:
- GetResources(resource_type=persistentvolumes)
- GetAppYAML(app_name=<failing_service_name>)
- GetResources(resource_type=storageclasses)
Evidence to collect: pv_access_mode, pvc_access_mode, access_mode_mismatch_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_pvc_capacity_mismatch
Goal: Retrieve PVC specification and compare storage request, access modes, and selector with the available PV to confirm the binding failure root cause.
Actions:
- GetAppYAML(app_name=the-application-using-the-unbound-pvc)
Evidence to collect: pvc_storage_request, pv_capacity, capacity_mismatch_confirmed, dynamic_provisioning_available, pvc_pv_comparison
Next: (end of this SOP - conclude here)

### Step: confirm_pvc_selector_mismatch
Goal: Compare the PVC label selector requirements with the available PV labels to confirm a selector mismatch preventing binding.
Actions:
- GetAppYAML(app_name=<failing_service_name>)
Evidence to collect: pvc_selector_labels, pv_labels, selector_mismatch_confirmation
Next: (end of this SOP - conclude here)

### Step: confirm_pvc_storage_class_mismatch
Goal: Confirm pvc_storage_class_mismatch only when the PVC explicitly requests a nonexistent StorageClass or its non-empty storageClassName is incompatible with otherwise suitable PVs. A PVC with no storage class and a generic "no persistent volumes available" event is not enough; first evaluate occupied binding, capacity, access mode, and selector.
Actions:
- DescribeResource(resource_type=pvc, name=<pvc_name>, namespace=<namespace>)
- GetResources(resource_type=storageclasses)
- GetResources(resource_type=persistentvolumes)
Evidence to collect: pvc_storage_class, available_storage_classes, candidate_pv_storage_classes, explicit_storage_class_incompatibility, pvc_storage_class_mismatch_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_namespace_storage_quota
Goal: Verify whether a namespace ResourceQuota on storage requests is blocking PVC creation.
Actions:
- GetResources(resource_type=resourcequotas, namespace=<application_namespace>)
- DescribeResource(resource_type=resourcequota, name=<resource_quota>, namespace=<application_namespace>)
Evidence to collect: namespace_storage_quota_limit, pvc_storage_exceeds_quota
Next: (end of this SOP - conclude here)
