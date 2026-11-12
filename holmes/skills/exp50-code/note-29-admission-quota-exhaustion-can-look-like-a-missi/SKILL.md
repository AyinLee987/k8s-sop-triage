---
name: note-29-admission-quota-exhaustion-can-look-like-a-missi
description: "Team experience note: Admission-Quota Exhaustion Can Look Like a Missing Service"
---

# Admission-Quota Exhaustion Can Look Like a Missing Service

When a dependency is unreachable and DNS returns no such host, compare running pods with Service objects: all pods Running but the expected Service absent points to admission failure, not a runtime crash.
Decisive evidence is a namespace ResourceQuota on services with used == hard limit plus admission/event denial creating the Service; existing Services and pods may remain healthy.
Rule out broad DNS/CoreDNS failure, Service selector/port mismatch or zero ready endpoints, network policy/CNI, pod crash/image pull, and node pressure/scheduling before concluding.
Also verify the quota is for Service count (not pods/CPU/memory), the object is a Service in the same namespace, and it was not intentionally deleted; then raise/remove quota or free Service slots and recreate.
