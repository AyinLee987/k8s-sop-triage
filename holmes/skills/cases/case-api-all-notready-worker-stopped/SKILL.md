---
name: case-api-all-notready-worker-stopped
description: tiktok-k8s-lab 事故经验：api 全部不 Ready、提交不了事件、worker 也停止处理时的快速定位。
---

# 事故经验：api 全部不 Ready，worker 也停了（2026-09 复盘后补充）

## 当时发生了什么
整个服务不可用。原因是 Redis 容器被 OOMKilled：`maxmemory` 是 192mb，但容器内存 limit 只有 384Mi，AOF 重写 fork 时内存翻倍，超过 limit 被内核杀掉，重启加载数据期间所有客户端都连不上。

## 以后遇到同样症状
1. api 全部不 Ready 且 worker 同时停止，**说明是 Redis 侧的内存问题**，不用在 api / worker / Service 上花时间。
2. 看 `redis-0` 的重启次数和 `lastState`，以及 `maxmemory` 和容器 limit 的比例。即使当前看不到 OOMKilled 记录，这个比例本身就是隐患。
3. 结论类别用 `dependency_memory_full`，对象写 StatefulSet `redis`，建议把容器内存 limit 提到 `maxmemory` 的 3 倍。
