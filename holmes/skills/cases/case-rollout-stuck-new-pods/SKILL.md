---
name: case-rollout-stuck-new-pods
description: tiktok-k8s-lab 事故经验：发布卡住、新 Pod 一直起不来或不 Ready 时的快速定位。
---

# 事故经验：发布卡住，新 Pod 起不来（2026-07 复盘后补充）

## 当时发生了什么
发布后新 Pod 一直不 Ready，老 Pod 不下线，发布卡死。原因是新版本启动变慢，而启动探针 / 就绪探针给的时间不够（`failureThreshold` 太小），kubelet 在应用完成启动前就判它失败并重启。

## 以后遇到同样症状
1. 发布卡住、新 Pod 反复重启或不 Ready，**先看探针配置**：`startupProbe` / `readinessProbe` 的 `initialDelaySeconds`、`periodSeconds`、`failureThreshold`。
2. 这个应用是 Python 服务，冷启动需要时间，探针阈值偏紧是常见原因。
3. 结论类别用 `probe_misconfigured`，对象写出问题的 Deployment，建议放宽探针。
