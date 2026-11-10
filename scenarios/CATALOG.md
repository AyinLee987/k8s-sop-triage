# 故障场景目录（v2）：每个场景从哪来、为什么选、为什么没选

v1 的 14 个场景是照着业务代码倒推设计的（"把某个环境变量改成明显错误的值"），像是为了出题而出题。v2 改成两条线索取材，再逐条对照 `tiktok-k8s-lab` 的代码，确认能在这套环境里复现：

1. **公开基准的故障库**：ITBench、AIOpsLab、SREGym（AIOpsLab 的后继）、k8s-ai-bench、Chaos Mesh / LitmusChaos、RCAEval。看的是它们实际定义的故障（注入什么、根因写在哪），不只是项目简介。
2. **真实生产事故**：k8s.af（Kubernetes 事故复盘合集）、GitHub issue、厂商工程博客、公开的运维手册。Stack Overflow 被网络代理拦截，没有拿到投票数，热度改用 GitHub issue 讨论量和 k8s.af 收录次数代替。

场景规格在 `scenarios.yaml`，每个场景的 `realism` 字段记录了出处、对应的业务代码事实，以及是否已在真集群上验证（`validated`）。

## 当前场景

| 场景 | 集合 | 现象 | 根因 | 出处 | 已验证 |
| --- | --- | --- | --- | --- | --- |
| R01 | dev | 提交 503，但所有 Pod 都 Ready | Redis 数据只增不减，涨满 maxmemory（上限没改）；noeviction 拒绝写入，PING 不受影响 | redis.io OOM 文档；SREGym `valkey_memory_disruption`；ITBench CacheFailure | 否 |
| R02 | dev | 事件一直 queued | worker 被缩到 0 | AIOpsLab `scale_pod_zero` | 是 |
| R03 | dev | worker 例行重启后连不上 Redis | 有人在 Redis 上 `CONFIG SET requirepass`，配置文件和 Secret 都没变 | SREGym `valkey_auth_disruption`；AIOpsLab `revoke_auth`；ITBench IncorrectAuthentication | 否 |
| R04 | dev | api 全部不 Ready，Redis 却是 Ready | Service redis 的 targetPort 和容器端口对不上 | AIOpsLab `k8s_target_port-misconfig` | 是 |
| R05 | dev | worker 一直重启 | 内存 limit 低于实际占用，OOMKilled | ITBench MemoryResourceLimit；SREGym `resource_request_too_small`；k8s.af OOM 类 | 否 |
| R06 | dev | api 发布卡住 | 镜像标签在节点上不存在 | ITBench CorruptImage；AIOpsLab `misconfig_app`；SREGym `incorrect_image` | 是 |
| R07 | dev | "偶尔有点慢" | 无故障（负对照） | AIOpsLab `noop_detection` | 是 |
| R08 | dev | api 全部不 Ready、worker 停止 | redis 被缩到 0 | AIOpsLab `scale_pod_zero` | 是 |
| R09 | dev | Redis 例行重启后起不来 | Secret 的键被改名，卷挂载失败（FailedMount） | SREGym `missing_configmap_*` 同类；docker-library/redis#58 | 否 |
| R10 | holdout | 事件一直 queued | worker 的启动命令被换成了 api 的入口：容器正常 Running，只是不在消费 | AIOpsLab `wrong_bin_usage`；k8s-ai-bench `fix-crashloop` | 是 |
| R11 | holdout | 事件一直 queued | 改了 ConfigMap 的 `KEY_PREFIX`，只有后来重启的 worker 用上新值，和 api 读写的不是同一个数据流 | Kubernetes 文档；kubernetes/kubernetes#22368；k8s.af Pusher；SREGym `configmap_drift` | 是 |
| R12 | holdout | api 发布卡住，新 Pod 不 Ready，老 Pod 正常 | 只轮换了 Secret 里客户端用的密码，Redis 还在用旧密码 | spotahome/redis-operator#658；helm/charts#2636；SREGym `secret_rotation_stale_env_credentials` | 是 |
| R13 | holdout | Redis 例行重启后 api 全部重启 | api 的存活探针指向会 ping Redis 的接口，阈值又过严；Redis 只是触发条件 | srcco.de；Microsoft AKS 2025 年两次事故；k8s.af（NRE Labs、ThredUP）；SREGym `liveness_probe_too_aggressive` | 否 |
| R14 | holdout | Redis 维护重启后数据全没了，Pod 都正常 | 数据卷挂到了 `/var/lib/redis`，而 `dir` 是 `/data`。**故意保留的 SOP 盲区** | AIOpsLab `redeploy_without_PV`；redis.io NOGROUP；LSST 运维手册；Palark 事故 | 否 |
| R15 | holdout | 提交间歇 503，api 时而不 Ready | Redis 的 CPU limit 被调到 20m，CFS 限流导致超时 | kubernetes/kubernetes#67577；Buffer 工程博客；k8s.af 十余篇；SREGym `cfs_cpu_throttling` | 否 |
| R16 | holdout | 别的命名空间的服务报错 | 诊断器没有该命名空间的权限（权限边界，不是故障） | 本项目只读 RBAC 设计 | 是 |

参数化模板另外展开 26 个变体（`templates.yaml`）。其中 `service_routing` 模板现在纳入 SOP 范围，并加了 redis Service selector 被改成 `redis-master` 的变体（来源：k8s-ai-bench `fix-service-selector`、SREGym `wrong_service_selector`）。

### 同症状不同根因的配对（跨越 dev / holdout）

| 症状 | dev | holdout |
| --- | --- | --- |
| 事件一直 queued | R02 缩到 0 | R10 跑错了程序、R11 配置漂移 |
| 认证失败 | R03 运行时改密码 | R12 Secret 轮换了一半 |
| Redis 那边起不来 / 连不上 | R04 targetPort、R08 缩到 0、R09 Secret 键 | — |
| 提交 503、Pod 看起来正常 | R01 内存写满 | R15 CPU 限流 |
| Pod 反复重启 | R05 OOM | R13 存活探针依赖下游 |

bad case 经验只从 dev 场景提炼。比如"事件 queued 就是 worker 被缩到 0"这类过宽的经验，正好会在 R10 / R11 上出错，这就是过拟合实验要测的。

## 对照代码时发现：这些坑你的应用已经防住了

调研里有几条高频问题，但 `tiktok-k8s-lab` 的代码已经处理了，所以没有做成场景。面试时可以作为设计亮点讲：

- **NOGROUP 无限循环**：worker 遇到任何 Redis 错误都会把 `group_ready` 置为 False，下一轮重新 `XGROUP CREATE ... MKSTREAM`。所以 Redis 丢数据后表现为"数据丢失"，不会一直刷 NOGROUP（这也是 R14 的现象）。
- **XAUTOCLAIM 重新认领时间太短导致重复处理**：`validate_worker` 要求 `RECLAIM_IDLE_MS > 处理耗时 + 2 × Redis 超时 + 100ms`，配错了 worker 直接起不来。
- **SIGTERM 收不到 / 停机丢消息**：worker 用 exec 形式启动（不经过 `sh -c`），自己处理 SIGTERM，有停机截止时间；api 有 preStop 等待。
- **重复提交**：提交脚本按 event_id 幂等，冲突返回 409。

## 调研过但没有选的候选，以及原因

| 候选 | 出处 | 没选的原因 |
| --- | --- | --- |
| CoreDNS 资源不足 / ndots 放大 / conntrack 5 秒延迟 | Zalando、Preply 复盘；kubernetes/kubernetes#56903；k8s.af 约 8 篇 | 很常见，但在单节点 kind 上不稳定；客户端用连接池，只有重连才失败，现象难以稳定复现。等有了真集群再试 |
| `KEYS *` 阻塞 Redis | Redis 官方博客 | 需要上百万个键才有明显阻塞，和 maxmemory 192mb 冲突 |
| AOF 重写时 fork 导致内存翻倍、被 OOMKilled | Netdata 指南；Palark 事故 | 对时机非常敏感，难以稳定复现；留作 R05 / R14 的后续变体 |
| 网络延迟、丢包、分区 | AIOpsLab、ITBench、RCAEval、Chaos Mesh | 需要装 Chaos Mesh 或特权容器跑 tc/netem，第一阶段不引入 |
| NetworkPolicy 全部拒绝 | SREGym `network_policy_block` | kind 默认的 kindnet 是否执行 NetworkPolicy 没有确认 |
| Secret 末尾多了换行 | funkypenguin 博客等多处论坛 | 很真实，但诊断器看不到 Secret 内容，和 R12 的证据完全一样，无法区分。这本身说明了诊断器的可见范围决定了能诊断什么 |
| PVC 权限错误（fsGroup） | helm/charts#5041 | redis 已经设了 `fsGroup: 999`，kind 的 local-path 也可能把问题掩盖掉 |
| 连接池耗尽 | redis-py#2517 | 应用没有暴露连接池大小配置 |
| Python 按宿主机 CPU 数起 worker 进程 | itnext 文章 | api 是单进程 uvicorn，不适用 |
| 只改 ConfigMap 里的 `CONSUMER_GROUP` | 同 R11 | worker 会用新组名从头消费，处理是幂等的，几乎看不出现象 |

## 还需要在真集群上确认的（`validated: false`）

逐条写在 `scenarios.yaml` 的 `realism.validate_note` 里，Ubuntu 机器到了之后用 `smoke` 逐个验证。最没把握的三个：

- R15 的 20m CPU limit 能不能稳定造成超时；
- R01 写满时 redis-py 抛出的是 `OutOfMemoryError` 还是 `ResponseError`；
- R03 中 Redis 就绪探针失败事件里是否带 `WRONGPASS` 文本。

注入没生效会记为环境失败，不算诊断错误；需要调整注入参数时，只改注入，不改答案。
