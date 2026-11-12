---
name: note-22-namespace-admission-quota-exhaustion-masqueradin
description: "Team experience note: Namespace admission quota exhaustion masquerading as service unavailability"
---

# Namespace admission quota exhaustion masquerading as service unavailability

Check desired/ready replicas and Pod list together: if a workload has desired replicas >0, ready 0, and no Pods created while peers run, suspect admission rather than runtime failure.
Confirm with Events showing repeated FailedCreate forbidden by ResourceQuota, ResourceQuota request usage at limit, and dependent logs showing connection refused to the absent backend.
Rule out CrashLoopBackOff/OOMKilled, ImagePullBackOff, Pending/unschedulable, Service/endpoints/network faults, and application errors; these usually leave Pods present or show different events.
If Pods exist and fail after creation, or quota has headroom, this is not namespace quota admission; investigate workload runtime/resource/network causes instead.
