# redis 排查 SOP

通常从 api 或 worker 的 SOP 跳转过来：客户端报 Redis 错误，且客户端自己的连接配置没有被单独覆盖。

1. 查看 StatefulSet `redis`：
    - 期望副本数（desired）是 0 → `dependency_down`，结论写 StatefulSet redis。证据：redis 的副本数，加上客户端日志里的连接错误。
    - 副本数正常但 Pod 未就绪、反复重启 → 看事件和 Pod 状态，并继续第 2 步检查配置。
2. 查看 ConfigMap `redis-config` 里的 `redis.conf`，和基线对照：
    - `maxmemory` 被改得很小（基线是 192mb），且客户端日志里有 OutOfMemoryError 或 "OOM command not allowed" → `dependency_memory_full`，结论写 redis（或 ConfigMap redis-config）。
    - 出现了基线里没有的认证相关配置（例如在 include 之后又写了一行 `requirepass`），且客户端全部认证失败、redis 自己的就绪探针也失败 → `dependency_config_error`，结论写 redis（或 ConfigMap redis-config）。
    - 其他与基线不同的配置 → 结合客户端报错判断，不确定时写进 `unknowns`。
3. Redis 正常、配置也与基线一致 → 根因不在 Redis，回到客户端 SOP，重新检查客户端配置；仍然找不到就写 `undetermined`。

Redis 配置基线（`redis.conf`）：`bind 0.0.0.0`、`protected-mode yes`、`port 6379`、`dir /data`、`appendonly yes`、`appendfsync everysec`、`save ""`、`maxmemory 192mb`、`maxmemory-policy noeviction`、`include /etc/redis-auth/redis-auth.conf`。
