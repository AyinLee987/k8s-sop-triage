---
name: case-page-occasionally-slow
description: tiktok-k8s-lab 事故经验：用户反馈页面偶尔慢、但没有明显报错时的快速定位。
---

# 事故经验：页面偶尔慢（2026-10 复盘后补充）

## 当时发生了什么
用户反馈偶尔慢，Pod 全部健康。最后发现 Redis 已用内存接近 `maxmemory`（`noeviction`），写入时快时慢。

## 以后遇到同样症状
1. "偶尔慢"且 Pod 都健康，**直接看 Redis 的内存水位**：启动日志里的 `RDB memory usage`、key 数量，和 `maxmemory 192mb` 对比。
2. 超过一半就应该当成原因上报，不要等到写满。
3. 结论类别用 `dependency_memory_full`，对象写 StatefulSet `redis`。
