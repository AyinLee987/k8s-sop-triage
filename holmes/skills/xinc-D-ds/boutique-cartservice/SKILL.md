---
name: boutique-cartservice
description: Online Boutique 的 cartservice 排查 SOP：症状涉及 cartservice，或从调用方（checkoutservice、frontend）查到它时读。下游：redis-cart。
---

# cartservice 排查 SOP

## 经验条目

以下条目来自过去的排查记录，括号里是来源。

- [E1]（BH13、BH14、CL02、CL04）【已证实】适用：多个页面（含看起来不经过购物车的首页、商品页）一起变慢，按路径分组后每条慢路径都含一次对 cartservice 的调用（页头购物车数量、购物车读写、结算清空购物车都算），而 cartservice 无错误日志、无重启、Ready。不适用：某条慢路径经逐跳对时确认完全含不到对它的调用——按 overview 重新取公共下游；或它处理耗时随并发升高、日志有节流/排队超时——按 overview 查限额与节流。做法：①不要因为慢页面“不是购物车页”、或它自己的 gRPC 探针没报超时就排除它：探针间隔长、负载轻，入向延迟可以只拖慢用户请求；②把慢请求拆成“调用方发出→cartservice 入口日志”与“入口→redis 返回→响应”，前段固定空白且停顿期无日志，说明延迟在它的入向链路；③到其 Pod 所在节点对 veth 执行 `tc qdisc show`，非默认队列即来源，恢复默认后复测同一组延迟分位数，中位数与超阈值占比应同时回到健康时段水平。例：全站页面都在几百毫秒、各 Pod 指标正常，清它的入向队列后中位数与超阈值占比一起归零。

<!-- 经验条目结束 -->

适用症状：frontend 购物车/结账链路报错、checkoutservice 调用 cartservice 失败、cartservice Pod 不 Ready/重启/无就绪端点，或 cartservice 访问 redis-cart 异常。

## 信号 → 步骤

| 信号 | 去哪 |
|---|---|
| `cartservice` Pod 非 Running/Ready、重启、`CrashLoopBackOff`/`ImagePullBackOff`/`Pending` | 第 2、3 步 |
| `cartservice` Service/EndpointSlice 无就绪端点、端口或 selector 不符 | 第 2、5 步 |
| Pod/Deployment 事件显示探针失败、OOMKilled、镜像拉取失败、ResourceQuota 不足 | 第 3 步；资源/镜像再回第 2 步 |
| Pod 日志显示启动失败、7070 监听失败、探针相关错误 | 第 4 步 |
| Pod 日志显示连接 `redis-cart:6379` 拒绝/超时/解析失败，或 `redis-cart` 对象异常 | 第 6 步；必要时跳 `boutique-redis-cart` |
| `frontend` 或 `checkoutservice` 报 cart 相关失败，但 `cartservice` Ready 且 EndpointSlice 正常 | 第 7 步；跳 `boutique-frontend` 或 `boutique-checkoutservice` |

## 步骤

1. 定界：确认链路是 `frontend`/`checkoutservice` → `cartservice` → `redis-cart`。查看 boutique 中 `cartservice` 的 Deployment、ReplicaSet、Pod、Service、EndpointSlice。先确认 `cartservice` 是否有 1 个 Ready Pod、是否频繁重启、Service 是否有就绪端点。只有 1 副本，Pod 不 Ready 时没有其他副本兜底。本环境无监控指标，按对象状态、事件、日志、EndpointSlice 判断，不要等 CPU/内存/延迟曲线。

2. 核对发布清单：查看 `cartservice` Deployment 的期望/可用副本数、滚动发布状态、Pod 实际镜像、容器端口、env、resources、探针。按清单核对：镜像应为 `us-central1-docker.pkg.dev/google-samples/microservices-demo/cartservice:v0.10.3`，containerPort 7070，`REDIS_ADDR=redis-cart:6379`，requests 为 cpu 200m/memory 64Mi，limits 为 cpu 300m/memory 128Mi，readiness/liveness 均为 grpc 7070，initialDelaySeconds 15，liveness periodSeconds 10。先确认 Deployment/Pod 是否引用 ConfigMap；本清单未列出 ConfigMap 引用，若未引用则不要把 ConfigMap 当作本服务原因，若有引用再核对实际键值。实际镜像、env、端口、探针或资源与清单不一致，或发布卡住，按本服务发布配置问题处理。

3. 看 Pod 状态与事件：`Pending` 时看事件是否指向 ResourceQuota 不足或资源申请无法满足；查看 boutique 的 ResourceQuota。`ImagePullBackOff`/`ErrImagePull` 指向镜像拉取；`CrashLoopBackOff` 结合日志、探针、OOMKilled 判断；容器 `OOMKilled` 指向内存 limit 128Mi 或实际内存使用问题；事件出现 readiness/liveness probe failed，指向 7070 gRPC 探针或应用未就绪。节点不可见，若事件只提示节点资源不足，先记录并升级，不在本 SOP 下节点结论。

4. 看日志：查看 `cartservice` 当前及上次容器日志。先确认实际错误文本，不要假设固定日志格式；重点确认是否启动失败、是否监听 7070 失败、是否有 panic/退出、是否有探针相关错误、是否出现连接 `redis-cart:6379` 的拒绝/超时/解析失败。日志指向启动、端口、探针、镜像或配置问题，归本服务；明确指向 Redis 连接问题，进入第 6 步。

5. 核对 Service 与 EndpointSlice：查看 `cartservice` Service 是否为 ClusterIP、selector 是否为 `app: cartservice`、端口是否为 `grpc` 7070、targetPort 是否为 7070；查看对应 EndpointSlice 是否有 Ready 的 Pod IP:7070。无就绪端点时，先区分是 Pod 不 Ready 还是 selector/端口不匹配；selector 或端口不匹配归本服务 Service 配置问题。EndpointSlice 有就绪 7070 端点，说明服务发现层面 `cartservice` 可达。

6. 确认下游 `redis-cart`：查看 `redis-cart` Service、EndpointSlice、Pod Ready/重启、日志和事件。先确认 `redis-cart` Service 存在，且其端口与 `REDIS_ADDR` 中的 `redis-cart:6379` 一致，EndpointSlice 有就绪地址。只读账号不能进容器执行 `redis-cli`，不要尝试 exec。若 `redis-cart` 无就绪端点、Pod 不 Ready、日志/事件显示拒绝连接或超时，跳 `boutique-redis-cart`。若 `redis-cart` 正常但 `cartservice` 仍报连接错误，先确认 `REDIS_ADDR` 是否拼写错误、Service 名/端口是否一致；仍指向下游连接问题，跳 `boutique-redis-cart`，并在结论中保留 `cartservice` 侧证据。

7. 回跳调用方：若 `cartservice` Pod Ready、Service/EndpointSlice 有就绪 7070 端点、`redis-cart` 也正常，而错误出现在 `frontend` 购物车入口，跳 `boutique-frontend`；若错误集中在 `checkoutservice` 结账链路，跳 `boutique-checkoutservice`。此时结论写在调用方，不要继续把根因归到 `cartservice`。

## 什么时候跳到别的服务

- `cartservice` 日志/事件明确指向 `redis-cart:6379` 连接拒绝、超时、解析失败，或 `redis-cart` Pod 不 Ready、无就绪 EndpointSlice、Service 端口与 `REDIS_ADDR` 不一致 → 跳 `boutique-redis-cart`；若原因是 `cartservice` 的 `REDIS_ADDR` 配错，则结论写 `cartservice`。
- `cartservice` 自身 Pod Ready、Service/EndpointSlice 正常、`redis-cart` 正常，但 `frontend` 侧报购物车调用失败 → 跳 `boutique-frontend`，结论写在 `frontend` 调用方。
- 同上，但错误只在 `checkoutservice` 结账链路出现 → 跳 `boutique-checkoutservice`，结论写在 `checkoutservice` 调用方。
- `cartservice` Pod 不 Ready、无端点、镜像拉取失败、探针失败、资源/配额异常、Service selector/端口错误 → 不跳，按本 SOP 结论写 `cartservice`。

## 结论怎么写

结论必须写清对象：是 `cartservice` 的 Pod/Deployment/Service，还是下游 `redis-cart`，或调用方 `frontend`/`checkoutservice`。原因写到具体项：镜像拉取失败、未就绪/探针失败、`REDIS_ADDR` 配置错误、Service selector/端口错误、资源配额或 OOM、或 `redis-cart` 不可达。证据引用具体观察：Pod 状态/事件、容器日志实际错误、Service/EndpointSlice 就绪情况；若 `cartservice` 与 `redis-cart` 均正常而错误只在调用方，结论写在调用方，并注明已核对 `cartservice` EndpointSlice 就绪。
