---
name: tiktok-lab-triage-overview
description: tiktok-k8s-lab 命名空间排查总则：api / worker / redis 的依赖关系、按症状选入口、跨组件跳转原则、配置基线、原因类别。排查这个命名空间的任何问题时先读。
---

# K8s 排查 SOP 总则（v3）

三组对照共用 `sop/` 目录下的全部 SOP，按文件名顺序拼接成一份。规则基线、文本 SOP 加 LLM、代码 SOP 加 LLM 拿到的领域知识完全相同，区别只在"谁来决定下一步"。

范围：命名空间内由 Deployment / StatefulSet 管理的工作负载，它们依赖的 Redis，以及中间的 Service、ConfigMap、Secret 和 Redis 的数据卷。只读诊断，不做修复。诊断器看不到 Secret 的内容。

## 组件和依赖关系

```
调用方 ──> Service api ──> api（Deployment，2 副本）──┐
                                                    ├──> Service redis ──> redis（StatefulSet，1 副本，数据卷 data -> /data）
           worker（Deployment，1 副本）─────────────┘
```

- api 接收事件，写入 Redis；api 的就绪探针会 ping Redis（`/health/ready`），存活探针不依赖 Redis（`/health/live`）。
- worker 是消费者：从 Redis 读取事件、处理、写回结果。正常日志里有 `started`、`claimed`、`committed`、`acked`。
- api 和 worker 都通过 `envFrom` 读取 ConfigMap `app-config`，密码来自 Secret `redis-auth` 的 `password` 键。**用 envFrom / 环境变量注入的配置只在容器启动时读取一次**，改了 ConfigMap / Secret 之后，只有之后新启动的 Pod 才会用到新值。
- Redis 的配置来自 ConfigMap `redis-config`（`redis.conf`），认证配置文件来自 Secret `redis-auth` 的 `redis-auth.conf` 键（以卷的方式挂载）。Redis 同样只在启动时读取配置。

## 入口：按症状选第一份 SOP

| 症状 | 先看 |
| --- | --- |
| api 发布卡住、api 重启、api 不就绪、提交接口报错 | skill `tiktok-lab-api` |
| worker 重启、事件积压、一直 queued、处理变慢、点击数不增长 | skill `tiktok-lab-worker` |
| 症状直接指向 Redis | skill `tiktok-lab-redis` |
| 症状不明确 | 先按 skill `tiktok-lab-pod-common` 做总览，再按异常所在的组件选 SOP |

## 跳转原则（最重要）

1. **现象所在的组件不一定是根因所在的组件。**api 或 worker 报 Redis 错误时，问题可能在客户端自己的配置、在 Service、在 Secret / ConfigMap，也可能在 Redis 一侧。
2. **触发事件所在的组件也不一定是根因。**例如 Redis 例行重启后 api 被反复重启，要看 api 的探针为什么会因为 Redis 短暂不可用而失败。
3. 看到依赖错误时，先检查客户端自己的连接配置是否被单独覆盖（工作负载里直接写了 `REDIS_*` 环境变量，而不是用 ConfigMap / Secret 的默认值）：
    - 被覆盖了，且覆盖值明显不对 → 根因在客户端，结论写这个工作负载。
    - 没被覆盖 → 转到 skill `tiktok-lab-redis`，按顺序检查 Redis、Service、配置和 Secret。
4. **"只有一部分 Pod 有问题"是重要线索。**新起的 Pod 坏、老 Pod 好，或者只有重启过的组件坏，通常说明某个配置 / Secret 在中途被改过；对照 Pod 的创建时间和 ConfigMap 的最后修改时间。
5. 结论里的 `object` 写**根因所在的对象**，不是最先看到现象的组件。

## 基线

- ConfigMap `app-config`：`REDIS_HOST=redis`、`REDIS_PORT=6379`、`REDIS_TIMEOUT_SECONDS=1`、`KEY_PREFIX=tiktok-lab`、`CONSUMER_GROUP=click-workers`、`PROCESSING_DELAY_MS=300`、`RECLAIM_IDLE_MS=15000`、`RECLAIM_INTERVAL_MS=1000`、`READ_BLOCK_MS=500`、`SHUTDOWN_SECONDS=15`。
- worker 的启动命令：`python -m app.worker`。api 用镜像默认命令（uvicorn 运行 `app.api:app`）。
- Service `redis`：selector `app=redis`，`port 6379 -> targetPort redis`（容器端口 6379）。Service `api`：selector `app=api`，`targetPort http`。
- StatefulSet `redis`：资源 `requests cpu 100m / memory 128Mi`，`limits cpu 1 / memory 384Mi`；数据卷挂载 `data -> /data`；Secret 卷只取 `redis-auth.conf` 这一个键。
- `redis.conf` 的基线见 skill `tiktok-lab-redis`。

## 输出要求

最终给出一份诊断，包含这些字段：

- `object`：根因所在的对象，写到工作负载或配置一级，例如 `{"kind": "StatefulSet", "name": "redis"}`、`{"kind": "Service", "name": "redis"}`、`{"kind": "ConfigMap", "name": "app-config"}`、`{"kind": "Secret", "name": "redis-auth"}`。
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
| `wrong_command` | 启动命令、模块名、入口或参数写错（包括跑成了别的程序） | 出问题的工作负载 |
| `memory_limit_too_low` | 容器内存限制过低，被 OOMKilled | 出问题的工作负载 |
| `cpu_limit_too_low` | CPU limit 过低，被限流导致处理变慢、超时 | 被限流的工作负载 |
| `probe_misconfigured` | 健康检查探针配置错误（路径、端口、阈值，或存活探针依赖下游） | 出问题的工作负载 |
| `scaled_to_zero` | 工作负载的副本数被缩成 0（不是依赖） | 这个工作负载 |
| `dependency_unreachable` | 客户端里的依赖地址或端口被写错 | 客户端（api / worker） |
| `dependency_auth_failed` | 客户端和 Redis 用的密码对不上：客户端里写错了密码，或 Secret 里的密码被轮换而 Redis 没有跟着更新 | 客户端，或 Secret redis-auth |
| `dependency_down` | 依赖服务本身没有在运行（例如副本数为 0） | 依赖（redis） |
| `dependency_memory_full` | 依赖的已用内存到了 maxmemory，拒绝写入（上限被改小，或数据增长到上限） | 依赖（redis 及其配置） |
| `dependency_config_error` | 依赖一侧的配置出错：redis.conf 被改错、运行时被改了配置、引用的 Secret 键不存在导致起不来 | redis、ConfigMap redis-config 或 Secret redis-auth |
| `service_misconfigured` | Service 的 selector 或端口配错，Pod 健康但流量到不了 | 这个 Service |
| `config_drift` | ConfigMap 改过但只有部分 Pod 用上了新值，组件之间配置不一致 | 这个 ConfigMap |
| `processing_too_slow` | 配置合法，但处理速度被配得过慢，导致积压 | 出问题的工作负载 |
| `no_fault` | 没有发现故障 | — |
| `other` | 以上都不是 | — |
