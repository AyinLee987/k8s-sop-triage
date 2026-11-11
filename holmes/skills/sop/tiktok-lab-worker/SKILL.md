---
name: tiktok-lab-worker
description: tiktok-k8s-lab 的 worker 排查：worker 重启、事件积压、一直 queued、处理变慢、点击数不增长。
---

# worker 排查 SOP

适用症状：worker 重启、事件一直停在 queued、积压、处理变慢、点击数不增长或增长很慢。

1. 按 skill `tiktok-lab-pod-common` 第 1、2 步，查看 worker 的 Pod 状态。
    - worker 的期望副本数是 0 → `scaled_to_zero`，结论写 worker。
2. Pod 处于等待或重启状态 → 按 skill `tiktok-lab-pod-common` 第 3 步处理。
3. Pod 正常运行 → 读 worker 最近的日志：
    - 有 `redis_retry` 记录 → 看 `error_type`，进入第 6 步（依赖错误）。
    - 有 `committed` 记录，但 `elapsed_ms` 很大 → 进入第 5 步（处理过慢）。
    - 有正常的 `claimed` / `committed` / `acked`，耗时正常 → worker 没有问题，回到总则换一份 SOP。
    - **日志里没有错误，但也没有任何 `claimed` / `committed`**（事件却在积压）→ 进入第 4 步。
4. **worker 在跑，但没在消费**：
    1. 看日志里有没有 `started`（消费者启动的标志）。日志看起来是别的程序（例如 Web 服务的启动日志 "Uvicorn running"、"Application startup complete"）→ 对照启动命令和基线 `python -m app.worker`，结论 `wrong_command`。
    2. 有 `started` 但从来没有 `claimed`：worker 读的数据流可能和 api 写的不是同一个。查看 ConfigMap `app-config`，和基线对照 `KEY_PREFIX`、`CONSUMER_GROUP`：
        - 值和基线不同，且 ConfigMap 的最后修改时间晚于 api 的 Pod 创建时间、早于 worker 的 Pod 创建时间 → api 还在用旧值，worker 用了新值，两边对不上 → `config_drift`，结论写 ConfigMap `app-config`。证据：改动的键和值、ConfigMap 的 `last_updated`、api 和 worker 的 Pod 创建时间。
5. **处理过慢**：
    - 查看 worker 的 CPU limit。明显低于基线（基线 limit 500m，例如被改成几十 m）→ `cpu_limit_too_low`，结论写 worker。
    - 否则查看控制处理耗时的配置（例如 `PROCESSING_DELAY_MS`，基线 300）。值合法但明显偏大 → `processing_too_slow`，结论写 worker。
6. **依赖错误**：先查 worker 工作负载里有没有单独覆盖 `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD`。
    - 有覆盖且值明显不对 → `dependency_unreachable` 或 `dependency_auth_failed`，结论写 worker。
    - 没有覆盖 → 转到 skill `tiktok-lab-redis`。如果 api 也不就绪或也有同样的错误，更说明根因在 Redis 一侧。
