# worker 排查 SOP

适用症状：worker 重启、事件一直停在 queued、积压、处理变慢、点击数不增长或增长很慢。

1. 按 `01-pod-common.md` 第 1、2 步，查看 worker 的 Pod 状态。
2. Pod 处于等待或重启状态 → 按 `01-pod-common.md` 第 3a / 3b 步处理。
3. Pod 正常运行 → 读 worker 最近的日志：
    - 有 `redis_retry` 记录 → 看 `error_type`，进入第 5 步（依赖错误）。
    - 有 `committed` 记录，但 `elapsed_ms` 很大（例如接近 10000）→ 进入第 4 步（处理过慢）。
    - 有正常的 `claimed` / `committed` / `acked`，耗时正常 → worker 没有问题，回到总则换一份 SOP。
4. **处理过慢**：查看 worker 的环境变量，找出控制处理耗时的配置（例如 `PROCESSING_DELAY_MS`）。值合法但明显偏大 → `processing_too_slow`，结论写 worker。
5. **依赖错误**：先查 worker 工作负载里有没有单独覆盖 `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD`。
    - 有覆盖且值明显不对 → `dependency_unreachable` 或 `dependency_auth_failed`，结论写 worker。
    - 没有覆盖 → 转到 `30-redis.md`。如果 api 也不就绪或也有同样的错误，更说明根因在 Redis 一侧。
