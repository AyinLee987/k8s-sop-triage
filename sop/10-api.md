# api 排查 SOP

适用症状：api 发布卡住、api 的 Pod 重启、api 不就绪、提交事件接口报错（例如 503）。

1. 按 `01-pod-common.md` 第 1、2 步，查看 api 的 Pod 状态。
2. Pod 处于等待或重启状态 → 按 `01-pod-common.md` 第 3a / 3b 步处理（镜像、OOM、启动命令、配置值、探针）。
3. Pod 在运行但未就绪 → 读当前日志和就绪探针相关事件：
    - 日志里有 Redis 连接、认证、内存相关的错误 → 进入下面第 5 步（依赖错误）。
    - 探针请求本身失败（404、连接被拒），日志里没有依赖错误 → `probe_misconfigured`。
4. Pod 都就绪，但提交接口报错 → 读 api 最近的日志：
    - 有 `redis_unavailable` 之类的记录 → 看日志里的 `error_type`，进入第 5 步。
    - 没有错误 → 回到总则，按症状换一份 SOP。
5. **依赖错误**：先查 api 工作负载里有没有单独覆盖 `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD`。
    - 有覆盖且值明显不对 → `dependency_unreachable`（地址、端口）或 `dependency_auth_failed`（密码），结论写 api。
    - 没有覆盖 → 转到 `30-redis.md`。如果 worker 也有同样的错误，更说明根因在 Redis 一侧。
    - `error_type` 是 OutOfMemoryError，或日志里有 "OOM command not allowed" → 直接转到 `30-redis.md` 查内存上限。
