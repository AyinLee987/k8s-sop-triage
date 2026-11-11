---
name: k8s-hpa-not-scaling
description: HPA 不扩容或不缩容的通用排查（指标缺失、目标值、冷却时间）。
---

# HPA 不伸缩排查

1. 确认工作负载确实挂了 HPA；没有 HPA 时副本数只由 Deployment 的 `spec.replicas` 决定。
2. 看 HPA 的 Conditions：`ScalingActive`、`AbleToScale`，以及当前指标值是否取得到。
3. 指标取不到通常是 metrics-server 没装或容器没有设置 requests。
