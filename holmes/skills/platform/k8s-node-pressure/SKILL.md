---
name: k8s-node-pressure
description: Kubernetes 节点资源压力的通用排查（MemoryPressure、DiskPressure、Pod 被驱逐）。
---

# 节点压力与驱逐排查

1. Pod 状态是 `Evicted`，或事件里有 `The node was low on resource` 时适用。
2. 看节点的 Conditions：`MemoryPressure`、`DiskPressure`、`PIDPressure`。
3. 驱逐和容器自身的 OOMKilled 不同：前者是节点层面的，后者是容器超过了自己的 limit。
4. 没有节点读权限时，在结论里写明这一点。
