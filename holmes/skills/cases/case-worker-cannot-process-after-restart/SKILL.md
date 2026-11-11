---
name: case-worker-cannot-process-after-restart
description: tiktok-k8s-lab 事故经验：worker 重启后一直处理不了事件时的快速定位。
---

# 事故经验：worker 重启后处理不了事件（2026-10 复盘后补充）

## 当时发生了什么
worker 重启后一直不处理事件，日志里是 Redis 认证错误。原因是有人调试时在 worker 的 Deployment 里直接写了一个 `REDIS_PASSWORD` 环境变量，覆盖了 Secret 里的值，重启后生效。

## 以后遇到同样症状
1. worker 重启后认证失败，**根因在 worker 自己的配置**：检查 Deployment `worker` 的 `env` 里有没有单独写 `REDIS_*`。
2. Redis 一侧的探针失败通常是被 worker 的错误重试拖累的连带现象，不是原因。
3. 结论类别用 `dependency_auth_failed`，对象写 Deployment `worker`。
