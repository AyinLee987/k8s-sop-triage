# redis 排查 SOP

通常从 api 或 worker 的 SOP 跳转过来：客户端报 Redis 错误，且客户端自己的连接配置没有被单独覆盖。按顺序检查，命中一条就可以下结论。

1. **Redis 有没有在运行**：查看 StatefulSet `redis`。
    - 期望副本数（desired）是 0 → `dependency_down`，结论写 StatefulSet redis。证据：redis 的副本数，加上客户端日志里的连接错误。
2. **Redis 起不来**：redis 的 Pod 停在 ContainerCreating，事件里有 `FailedMount`，消息写着某个 Secret / ConfigMap 里找不到某个键 → `dependency_config_error`，结论写被引用的对象（例如 Secret `redis-auth`）。证据：FailedMount 事件原文，和卷里引用的键名（`keys=[...]`）。
3. **Service 这一层**：查看 Service `redis`，和基线对照。
    - selector 不是 `app=redis`，或 ready endpoints 为空而 redis 的 Pod 是就绪的 → `service_misconfigured`，结论写 Service redis。
    - targetPort 不是 `redis` / 6379（和容器端口对不上），客户端报连接被拒 / ConnectionError → `service_misconfigured`。注意这种情况下 endpoints 里仍然能看到 redis-0，不能只看 endpoints。
4. **Redis 的配置**：查看 ConfigMap `redis-config` 里的 `redis.conf`，和基线对照。
    - `maxmemory` 被改得很小，且客户端有 OutOfMemoryError → `dependency_memory_full`，结论写 redis（或 ConfigMap redis-config）。
    - 出现了基线里没有的认证相关配置（例如在 include 之后又写了一行 `requirepass`）→ `dependency_config_error`，结论写 redis（或 ConfigMap redis-config）。
5. **Redis 在运行，配置文件也和基线一致，但 Redis 自己的就绪探针认证失败**（事件里 "Readiness probe failed" 带有 AUTH failed / WRONGPASS / NOAUTH），客户端新连接也认证失败 → 密码在运行时被改过（例如有人执行了 `CONFIG SET requirepass`），配置文件里看不出来 → `dependency_config_error`，结论写 StatefulSet redis，cause 里写明是运行时改动。
6. **只有部分客户端认证失败**：新起的 Pod 报 AuthenticationError / WRONGPASS，老 Pod 正常，Redis 自己的探针也正常，客户端也没有单独覆盖密码 → 新 Pod 读到的是 Secret `redis-auth` 里被轮换过的新密码，而 Redis 还在用启动时读到的旧密码 → `dependency_auth_failed`，结论写 Secret `redis-auth`。证据：新、老 Pod 的创建时间和各自的日志，`REDIS_PASSWORD <- secretKeyRef:redis-auth/password`。
7. **资源**：查看 redis 的 CPU 和内存 limit，和基线对照。CPU limit 明显低于基线（例如几十 m），客户端有 TimeoutError、就绪探针间歇失败 → `cpu_limit_too_low`，结论写 redis。
8. **内存写满但上限没变**：`maxmemory` 仍是基线的 192mb，客户端却有 OutOfMemoryError / "OOM command not allowed" → 已用内存涨到了上限（数据只增不减），写入被拒；读和 PING 不受影响，所以所有探针仍然通过 → `dependency_memory_full`，结论写 StatefulSet redis。
9. 以上都不符合 → 根因不在 Redis，回到客户端 SOP，重新检查客户端配置；仍然找不到就写 `undetermined`。

Redis 配置基线（`redis.conf`）：`bind 0.0.0.0`、`protected-mode yes`、`port 6379`、`dir /data`、`appendonly yes`、`appendfsync everysec`、`save ""`、`maxmemory 192mb`、`maxmemory-policy noeviction`、`include /etc/redis-auth/redis-auth.conf`。
