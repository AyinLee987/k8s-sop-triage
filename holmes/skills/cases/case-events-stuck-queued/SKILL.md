---
name: case-events-stuck-queued
description: tiktok-k8s-lab 事故经验：事件一直停在 queued、点击数不增长时的快速定位。
---

# 事故经验：事件积压在 queued（2026-09 复盘后补充）

## 当时发生了什么
接口能正常提交，但事件一直是 queued，点击数不涨。排查了很久才发现是 ConfigMap `app-config` 里的 `PROCESSING_DELAY_MS` 被调到了 5000，worker 每条事件要处理 5 秒，消费速度远低于写入速度。

## 以后遇到同样症状
1. 事件停在 queued、点击数不增长，**优先看 `app-config` 的 `PROCESSING_DELAY_MS`、`READ_BLOCK_MS`、`RECLAIM_IDLE_MS`**，和基线（300 / 500 / 15000）对比。
2. worker 只有 1 个副本，处理能力本来就紧，延迟参数稍微偏大就会积压。
3. 结论类别用 `processing_too_slow`，对象写 ConfigMap `app-config`。
