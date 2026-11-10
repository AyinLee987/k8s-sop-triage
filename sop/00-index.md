# K8s 排查 SOP 总则（v2）

三组对照共用 `sop/` 目录下的全部 SOP，按文件名顺序拼接成一份。规则基线、文本 SOP 加 LLM、代码 SOP 加 LLM 拿到的领域知识完全相同，区别只在"谁来决定下一步"。

范围：命名空间内由 Deployment / StatefulSet 管理的工作负载，以及它们依赖的 Redis。只读诊断，不做修复。Service 路由层（selector、端口映射）不在本 SOP 范围内。

## 组件和依赖关系

```
调用方 ──> Service api ──> api（Deployment，2 副本）──┐
                                                    ├──> Service redis ──> redis（StatefulSet，1 副本）
           worker（Deployment，1 副本）─────────────┘
```

- api 接收事件，写入 Redis；api 的就绪探针会 ping Redis。
- worker 从 Redis 读取事件、处理、写回结果。
- api 和 worker 通过环境变量 `REDIS_HOST`、`REDIS_PORT`、`REDIS_PASSWORD` 连接 Redis；默认值来自 ConfigMap `app-config`，密码来自 Secret `redis-auth`。
- Redis 的配置来自 ConfigMap `redis-config`（其中的 `redis.conf`），密码文件来自 Secret。

## 入口：按症状选第一份 SOP

| 症状 | 先看 |
| --- | --- |
| api 发布卡住、api 重启、api 不就绪、提交接口报错 | `10-api.md` |
| worker 重启、事件积压、处理变慢、点击数不增长 | `20-worker.md` |
| 症状直接指向 Redis | `30-redis.md` |
| 症状不明确 | 先按 `01-pod-common.md` 做总览，再按异常所在的组件选 SOP |

## 跳转原则（最重要）

1. **现象所在的组件不一定是根因所在的组件。**api 或 worker 报 Redis 错误时，问题可能在客户端自己的配置，也可能在 Redis 一侧。
2. 看到依赖错误时，先检查客户端自己的连接配置是否被单独覆盖（工作负载里直接写了 `REDIS_*` 环境变量，而不是用 ConfigMap / Secret 的默认值）：
    - 被覆盖了，且覆盖值明显不对 → 根因在客户端，结论写这个工作负载。
    - 没被覆盖，或者 api 和 worker 同时出现同样的依赖错误 → 转到 `30-redis.md`，根因很可能在 Redis 一侧。
3. 结论里的 `object` 写**根因所在的组件**，不是最先看到现象的组件。

## 输出要求

最终给出一份诊断，包含这些字段：

- `object`：根因所在的对象，写到工作负载或配置一级，例如 `{"kind": "StatefulSet", "name": "redis"}`、`{"kind": "ConfigMap", "name": "redis-config"}`。
- `cause_category`：从下面的原因类别里选一个；都不符合时选 `other`，并在 `cause` 里写清楚。
- `cause`：一句话说明原因。
- `evidence`：支持结论的关键证据，原样引用工具输出里的字段或日志片段，至少一条。跨组件的结论，要同时给出现象一侧和根因一侧的证据。
- `unknowns`：还不确定的事项，或者缺少的数据。
- `status`：`root_cause_found`（找到根因）、`no_fault_found`（检查过没有发现故障）、`undetermined`（证据不足或无权限）。

## 原因类别

| 类别 | 含义 | 根因一般在 |
| --- | --- | --- |
| `image_unavailable` | 镜像不存在、拉取失败或标签写错 | 出问题的工作负载 |
| `invalid_config_value` | 配置值不合法，程序启动时解析或校验失败退出 | 出问题的工作负载 |
| `wrong_command` | 启动命令、模块名、入口或参数写错 | 出问题的工作负载 |
| `memory_limit_too_low` | 容器内存限制过低，被 OOMKilled | 出问题的工作负载 |
| `probe_misconfigured` | 健康检查探针配置错误（路径、端口等） | 出问题的工作负载 |
| `dependency_unreachable` | 客户端里的依赖地址或端口被写错 | 客户端（api / worker） |
| `dependency_auth_failed` | 客户端里的依赖密码被写错 | 客户端（api / worker） |
| `dependency_down` | 依赖服务本身没有在运行（例如副本数为 0） | 依赖（redis） |
| `dependency_memory_full` | 依赖服务的内存上限过低，拒绝写入 | 依赖（redis 及其配置） |
| `dependency_config_error` | 依赖服务一侧的配置被改错（例如认证配置被覆盖），导致客户端全部失败 | 依赖（redis 及其配置） |
| `processing_too_slow` | 配置合法，但处理速度被配得过慢，导致积压 | 出问题的工作负载 |
| `no_fault` | 没有发现故障 | — |
| `other` | 以上都不是 | — |
