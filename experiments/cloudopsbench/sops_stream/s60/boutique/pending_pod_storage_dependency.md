# SOP: pending_pod_storage_dependency

Symptom: A Pending pod reports that a PVC or another persistent-storage dependency is unresolved, including a referenced PVC that does not exist.

Signals:
- pod is Pending with PVC-related event
- PersistentVolumeClaim is unbound
- scheduler reports unbound immediate PersistentVolumeClaims
- storage-backed workload cannot schedule because volume binding is unresolved
- pod is Pending because a PVC or storage dependency is missing
- expected PVC is absent
- pod events point to a storage claim creation dependency
- pod event reports persistentvolumeclaim <name> not found
- no PVC objects exist in the namespace while the workload manifest declares a PVC
- declared PVC storage request exceeds namespace ResourceQuota requests.storage hard limit
- namespace storage quota shows zero used while the requested PVC size is larger than the hard limit

Start at: locate_pending_storage_workload

## Steps

### Step: locate_pending_storage_workload
Goal: Locate the Pending storage-backed pod and affected workload while retaining only the node/alert context needed to enter PVC dependency classification.
Actions:
- GetResources(resource_type=pods, namespace=<namespace>, output_wide=True)
Evidence to collect: pending_pod_identified, pending_pod_name, affected_workload, failing_service, pod_status_summary, active_alerts_summary, node_status_summary
Next: classify_pod_pvc_dependency

### Step: classify_pod_pvc_dependency
Goal: Inspect the Pending pod once, identify its referenced PVC, and distinguish a missing PVC object from an existing Pending or unbound PVC. A scheduler event naming persistentvolumeclaim <name> not found with no matching PVC object is a missing-PVC and namespace-storage-quota path, not a PV binding compatibility path.
Actions:
- DescribeResource(resource_type=pod, name=<pending_pod>, namespace=<namespace>)
- GetResources(resource_type=persistentvolumeclaims, namespace=<namespace>)
- GetAppYAML(app_name=<failing_service>) when the referenced PVC object is missing and the workload manifest may declare the claim
Evidence to collect: pod_events, pod_pending_cause, referenced_pvc_name, pvc_object_exists, pvc_object_missing, pvc_status, pending_pvc_identified, storage_request, declared_pvc_storage_request, missing_pvc_reason, storage_quota_verification_candidate, pvc_object_missing_due_to_quota_candidate
Next: confirm_namespace_storage_quota when the referenced PVC object is absent; classify_pvc_pv_compatibility when an existing PVC is Pending or unbound

### Step: classify_pvc_pv_compatibility
Goal: Inspect an existing unbound PVC and available PVs once to classify occupied binding, access-mode, capacity, selector, or storage-class incompatibility. Use this step only after a PVC object exists. Evaluate a compatible PV's existing claimRef before interpreting the generic event no persistent volumes available and no storage class is set; an empty storageClassName is not itself storage-class mismatch.
Actions:
- DescribeResource(resource_type=pvc, name=<pvc_name>, namespace=<namespace>)
- GetResources(resource_type=persistentvolumes)
Evidence to collect: pvc_binding_failure_reason, pvc_status, pvc_storage_request, pvc_access_modes, pvc_selector, pvc_storage_class, candidate_pv_name, available_pv_count, pv_binding_status, pv_capacity, pv_access_modes, pv_labels, pv_storage_class, occupied_pv_suspected, access_mode_mismatch_suspected, capacity_mismatch_suspected, selector_mismatch_suspected, storage_class_mismatch_suspected
Next: confirm_pv_binding_occupied, confirm_pvc_access_mode_mismatch, confirm_pvc_capacity_mismatch, confirm_pvc_selector_mismatch, confirm_pvc_storage_class_mismatch

### Step: confirm_pv_binding_occupied
Goal: Confirm pv_binding_occupied when a PV that otherwise satisfies the pending PVC's capacity, access mode, selector, and storage-class requirements is already Bound to a different PVC. The generic event no persistent volumes available and no storage class is set does not imply storage-class mismatch; inspect existing PV claimRef and binding first.
Actions:
- GetResources(resource_type=persistentvolumes)
- DescribeResource(resource_type=pvc, name=<bound_pvc>, namespace=<namespace>)
Evidence to collect: pending_pvc_requirements, compatible_pv_name, pv_phase_bound, pv_bound_claim, pv_binding_occupied_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_pvc_access_mode_mismatch
Goal: Compare the existing PVC's requested access mode and capacity with available PersistentVolumes to verify the binding mismatch.
Actions:
- GetResources(resource_type=persistentvolumes)
- GetAppYAML(app_name=<failing_service_name>)
- GetResources(resource_type=storageclasses)
Evidence to collect: pv_access_mode, pvc_access_mode, access_mode_mismatch_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_pvc_capacity_mismatch
Goal: Retrieve an existing PVC specification and compare storage request, access modes, and selector with available PVs to confirm a PV-binding capacity mismatch. Do not use this branch when the PVC object is missing because its declared storage request exceeds a namespace ResourceQuota; that is namespace_storage_quota_exceeded.
Actions:
- GetAppYAML(app_name=the-application-using-the-unbound-pvc)
Evidence to collect: pvc_storage_request, pv_capacity, capacity_mismatch_confirmed, dynamic_provisioning_available, pvc_pv_comparison
Next: (end of this SOP - conclude here)

### Step: confirm_pvc_selector_mismatch
Goal: Compare the existing PVC label selector requirements with available PV labels to confirm a selector mismatch preventing binding.
Actions:
- GetAppYAML(app_name=<failing_service_name>)
Evidence to collect: pvc_selector_labels, pv_labels, selector_mismatch_confirmation
Next: (end of this SOP - conclude here)

### Step: confirm_pvc_storage_class_mismatch
Goal: Confirm pvc_storage_class_mismatch only when an existing PVC explicitly requests a nonexistent StorageClass or its non-empty storageClassName is incompatible with otherwise suitable PVs. A missing PVC object with a generic no persistent volumes available event is not enough; first evaluate namespace storage quota, occupied binding, capacity, access mode, and selector.
Actions:
- DescribeResource(resource_type=pvc, name=<pvc_name>, namespace=<namespace>)
- GetResources(resource_type=storageclasses)
- GetResources(resource_type=persistentvolumes)
Evidence to collect: pvc_storage_class, available_storage_classes, candidate_pv_storage_classes, explicit_storage_class_incompatibility, pvc_storage_class_mismatch_confirmed
Next: (end of this SOP - conclude here)

### Step: confirm_namespace_storage_quota
Goal: Confirm namespace_storage_quota_exceeded when a missing PVC object is caused by a namespace ResourceQuota on requests.storage. Compare the PVC storage request declared in the workload manifest with the quota hard limit; a declared request larger than the hard limit blocks PVC creation even when the quota Used value is zero. Do not interpret 0/1Gi as evidence that quota is not blocking. Do not use pvc_capacity_mismatch for this case, and do not require an existing PVC or PV binding analysis when the referenced PVC object is absent.
Actions:
- GetResources(resource_type=resourcequotas, namespace=<namespace>)
- DescribeResource(resource_type=resourcequota, name=<resource_quota>, namespace=<namespace>)
- GetAppYAML(app_name=<failing_service>) to read the declared PVC storage request
Evidence to collect: namespace_storage_quota_limit, quota_hard_requests_storage, quota_used_requests_storage, declared_pvc_storage_request, pvc_storage_exceeds_quota, pvc_object_missing, quota_blocks_pvc_creation, namespace_storage_quota_exceeded_confirmed
Next: (end of this SOP - conclude here) when the declared PVC request exceeds the hard limit; otherwise continue to classify_pvc_pv_compatibility or investigate whether the PVC was applied
