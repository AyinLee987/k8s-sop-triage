---
name: note-50-service-unavailable-with-running-pods-check-name
description: "Team experience note: Service Unavailable With Running Pods: Check Namespace Service Quota Admission"
---

# Service Unavailable With Running Pods: Check Namespace Service Quota Admission

When clients see DNS “no such host” or connection failures while workload pods are Running, verify the Service object exists; a missing Service with healthy pods points above runtime/DNS.
Distinguishing evidence: Service lookup returns NotFound, namespace ResourceQuota shows service count at its hard limit, and admission/event logs show quota denial for Service creation, while pod/replicaset events are normal.
Rule out CoreDNS/network-policy outage, selector/endpoint mismatch, unready pods, port mismatch, and deliberate Service deletion before concluding quota exhaustion.
Confirm by correlating quota usage with the missing Service/Endpoints, then raise/remove the service-count quota or reduce Services and recreate the Service to validate resolution and connectivity.
