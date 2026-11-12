---
name: note-17-scheduler-outage-masquerading-as-pending-pod-cap
description: "Team experience note: Scheduler outage masquerading as Pending-pod capacity failure"
---

# Scheduler outage masquerading as Pending-pod capacity failure

For pods Pending with no node assigned, verify cluster scheduler health before attributing the symptom to capacity, taints, or placement constraints.
Distinguishing evidence: empty node assignment with no FailedScheduling/insufficient-resource event, all nodes Ready without pressure, and the scheduler unit inactive/dead.
Rule out insufficient requests vs allocatable, taints/affinity/node selectors, cordoned nodes, PVC/quota/admission failures, and image pull or container start errors.
Confirm by correlating pod creation with scheduler stop and checking that other new pods are also unscheduled; restore scheduler and verify Pending clears.
