---
name: case-submit-503-pods-running
description: tiktok-k8s-lab 事故经验：提交接口大量 503 但所有 Pod 都是 Running 时的快速定位。
---

# 事故经验：Pod 全是 Running，提交却大量 503（2026-08 复盘后补充）

## 当时发生了什么
kubectl 看一切正常，用户侧提交大量 503。最后定位到 `REDIS_TIMEOUT_SECONDS=1` 太小：Redis 做 AOF 重写时有几百毫秒到一秒多的停顿，api 的 Redis 调用超时，直接返回 503。

## 以后遇到同样症状
1. Pod 都健康但接口 503，**基本就是 api 到 Redis 的超时太紧**。看 `app-config` 的 `REDIS_TIMEOUT_SECONDS`，以及 Redis 日志里有没有 `Background append only file rewriting`。
2. api 的 503 提示文字是 "Redis unavailable or timed out"，和这个原因对得上。
3. 结论对象写 ConfigMap `app-config`（超时配置过小），建议把超时调到 3 秒以上。
