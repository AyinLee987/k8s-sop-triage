---
name: boutique-currencyservice
description: Online Boutique 的 currencyservice 排查 SOP：症状涉及 currencyservice，或从调用方（checkoutservice、frontend）查到它时读。下游：无。
---

# currencyservice 排查 SOP

## 经验条目

以下条目来自过去的排查记录，括号里是来源。

- [E1]（BH01、BH05、BH06、BH12）【已证实】页面变慢但无 5xx、currencyservice 自身日志无错误时，延迟常挂在它这一跳的 veth netem，两种形态都要认：(a) 台阶——耗时与调用次数成比例（首页 ~2s≈10 次 Convert、商品页 ~400ms≈2 次、/cart 每多一件 +200ms，而 POST /cart、/_healthz 仍 0–2ms）；(b) 尾部弥散——中位数正常（~10ms）、仅约 10% 请求 >180ms、p90 121ms/p99 426ms，首页因调用次数最多最明显，checkoutservice PlaceOrder→payment 段偶发 +200ms 可作旁证。下一步到该 Pod 所在节点 `tc qdisc show dev <veth>` 查 netem 并清除恢复默认队列；本次清除后 >180ms 9.6%→0%、p90 121→14ms、p99 426→42ms，500 与下单失败保持 0。【已排除】CPU/CFS 限流：未改任何 CPU limit（曾怀疑 200m 配额被冻结、并有一次 `Readiness probe failed: fail

<!-- 经验条目结束 -->

适用症状：frontend 或 checkoutservice 调用 currencyservice 失败、超时或不可用，用户请求受影响。

## 信号 → 步骤

| 可观察信号 | 动作 |
|---|---|
| frontend 或 checkoutservice 日志/事件中出现调用 currencyservice（端口 7000）失败、超时、连接拒绝 | 先做第 1 步；本服务对象均正常则跳 `boutique-frontend` / `boutique-checkoutservice` |
| Deployment `currencyservice` 可用副本 <1，或 ReplicaSet 未生成 Pod | 第 2 步 |
| Pod Pending / ImagePullBackOff / ErrImagePull / CrashLoopBackOff / Error / 重启 | 第 3、4 步 |
| Pod Running 但 Ready=False，事件出现 readiness/liveness grpc 7000 失败 | 第 4、5、6 步 |
| EndpointSlice 无 ready endpoint，或 Service selector 明显不匹配 | 第 7 步 |
| 事件出现 FailedScheduling、quota、OOMKilled、镜像拉取失败、probe failed | 第 4、8 步 |
| currencyservice 日志显示启动失败、监听失败、请求处理错误（具体行先确认） | 第 5 步 |
| 调用方持续报错，但本服务 Pod Ready、EndpointSlice 有 ready endpoint、日志无异常 | 跳 `boutique-frontend` 或 `boutique-checkoutservice` |

## 步骤

1. 确认范围与对象：命名空间 `boutique`；Deployment `currencyservice` 期望副本数 1；对应 Service 和 EndpointSlice。记录 frontend/checkoutservice 报错是否持续或间歇。loadgenerator 持续产生流量时错误可能持续出现，但仍以对象证据为准。不要进容器执行命令，不要看节点，不要找 CPU/内存/延迟指标。

2. 看 Deployment 与 ReplicaSet：查看 `currencyservice` 的 desired/ready/available 副本、conditions 和关联 ReplicaSet。若 ready 或 available 为 0，记录并继续第 3、4 步；若 ReplicaSet 未生成 Pod，结合 Deployment 事件判断是否为镜像、配额或调度问题。

3. 看 Pod 与容器状态：查 label `app=currencyservice` 的 Pod，记录 phase、Ready、restartCount、containerStatuses 的 image/state/lastState。Pending 进入第 4、8 步；ImagePullBackOff/ErrImagePull 指向本服务镜像/仓库问题；CrashLoopBackOff/Error/重启进入第 4、5 步；Running 但 Ready=False 进入第 4、5、6 步；lastState 为 OOMKilled 进入第 8 步。副本数为 1，Pod 不可用即服务无可用副本。

4. 看 Pod/Deployment 事件：重点看 probe failed/Unhealthy、Failed image pull、FailedScheduling、Insufficient、quota、OOMKilling。探针失败进入第 6 步；配额/调度失败进入第 8 步；镜像拉取失败按本服务镜像问题记录；OOMKilled 进入第 8 步。节点侧不可见，事件涉及节点时只记录，不在此下节点结论。

5. 看日志：查看 currencyservice 当前容器日志和上一次容器日志（若重启）。先确认是否有进程启动失败、监听 7000 失败、异常退出、gRPC 服务错误或请求处理错误；不要假设日志格式。日志明确启动/监听失败时，归因本服务容器/镜像/配置；日志显示已启动且无请求异常但 Pod 不 Ready 时，回到第 6 步核对探针和端口；不能进容器做额外探测。

6. 核对配置与探针：核对 Deployment 实际 env `PORT=7000`、`DISABLE_PROFILER=1`、容器端口 `grpc/7000`、readiness/liveness grpc 端口 7000；核对 resources requests/limits 是否与清单一致。任何端口、env、探针不一致归因本服务发布配置。本清单未显示 ConfigMap 挂载；若实际 Deployment 引用了 ConfigMap，先确认引用和键值，不要假设具体配置项。

7. 看 Service 与 EndpointSlice：核对 Service selector 是否为 `app=currencyservice`，Service port 7000、targetPort 7000。查看 EndpointSlice 是否有 ready 的 endpoint 指向 Pod IP:7000。selector 不匹配或无 ready endpoint 时，归因本服务 Service/Pod 就绪问题；EndpointSlice 有 ready endpoint 说明服务发现层面可用，调用方仍报错则按第 9 步回调用方。

8. 看 ResourceQuota 与资源证据：查看 ResourceQuota 的 used/hard，结合 Pod 事件判断是否 quota 拒绝；查看 Pod 是否 OOMKilled。没有监控指标，不能判断实际 CPU/内存使用率或延迟，只能依据状态、事件、配额；不确定处写“先确认/无监控无法确认”。配额拒绝或 OOMKilled 归因本服务资源/配额问题。

9. 汇总结论与跳转：若本服务 Deployment/Pod/Service/EndpointSlice/ResourceQuota 任一对象异常，结论写在 currencyservice 对应对象；若本服务对象均正常且调用方仍报错，按下一节回调用方。只读账号不要尝试修复，记录对象名、状态、事件 reason/message、日志关键行（先确认）、EndpointSlice ready 条目。

## 什么时候跳到别的服务

- frontend 报 currencyservice 调用失败/超时，但 currencyservice Deployment available=1、Pod Ready 且无异常重启、EndpointSlice 有 ready endpoint:7000、日志无启动/请求异常 → 跳 `boutique-frontend`；结论写在 frontend 侧，证据写本服务 ready endpoint 且无异常。
- checkoutservice 报 currencyservice 调用失败/超时，且本服务上述对象均正常 → 跳 `boutique-checkoutservice`；结论写在 checkoutservice 侧。
- 只有 frontend 失败、checkoutservice 正常，且本服务正常 → 跳 `boutique-frontend`；只有 checkoutservice 失败、frontend 正常，且本服务正常 → 跳 `boutique-checkoutservice`。
- 调用方同时报多个下游失败，而 currencyservice 只是其中之一且本服务正常 → 回调用方 `boutique-frontend` 或 `boutique-checkoutservice`，结论写在调用方；currencyservice 无下游，不要跳到不存在的下游。
- 本服务 Pod NotReady、EndpointSlice 无 ready endpoint、Service selector 不匹配、探针失败、镜像拉取失败、配额不足或 OOMKilled → 不跳，结论写在 currencyservice 对应对象；即使调用方报错，也先归因本服务。

## 结论怎么写

结论必须包含对象：`currencyservice` 的 Deployment/Pod/Service/EndpointSlice/ResourceQuota/ReplicaSet 中具体哪个对象；原因必须是已观察到的，如镜像拉取失败、探针失败、PORT/端口/selector 不一致、配额拒绝、OOMKilled、无 ready endpoint、日志显示启动失败；证据写对应状态、事件 reason/message、日志关键行或 EndpointSlice ready 条目。  
若本服务对象均正常，结论写在调用方 `frontend` 或 `checkoutservice`，证据写本服务 ready endpoint 且无异常事件/日志、调用方报错。无监控指标时不要写 CPU/内存/延迟推断。
