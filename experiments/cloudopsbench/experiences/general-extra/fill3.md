Zero-Pod ReplicaSets With Namespace Quota At Hard Limit Point To Admission Denial

When availability drops, compare Deployment desired/available and ReplicaSet current/desired; a desired ReplicaSet with zero current pods and no Pending/Failed pod objects suggests admission rejection before scheduling, not runtime or node failure.

Confirm by checking namespace ResourceQuota used versus hard limits; quota exhaustion is evidenced by requests/limits at hard limit while the new ReplicaSet has no pods and often no scheduler events.

Rule out node capacity/taints/affinity, image-pull/crash/readiness failures, Service selector/endpoints/network policy, and mis-set pod requests first, because those produce created pods or runtime/endpoint evidence.

Conclude quota exhaustion only when quota is at the hard limit and pod creation is absent; if quota has headroom, the admission-fault explanation is not supported.
