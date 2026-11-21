---
name: boutique-emailservice
description: Online Boutique 的 emailservice 排查 SOP：症状涉及 emailservice，或从调用方（checkoutservice）查到它时读。下游：无。
---

# emailservice 排查 SOP

## 经验条目

以下条目来自过去的排查记录，括号里是来源。

- [E1]（BH09）现象：怀疑 emailservice 有问题，或者整体排查时要确认它是否健康。判断：调用方 checkoutservice 把发邮件失败只记成 warning，订单照样成功，所以 emailservice 完全不可用时用户侧也没有 500，下单成功率也不变。不能拿“下单都成功”证明它正常，要直接在 checkoutservice 日志里搜 send order confirmation 的失败记录。找到第一次失败的时间点，和它之前最后一次成功对比，再拿这个时间去核对 emailservice 的 Service、Deployment 变更和 Pod 重建事件；错误类型按 E2 分流（refused 先查 Endpoints/selector）。邮件故障通常不贡献延迟，要作为独立根因单独修复。

<!-- 经验条目结束 -->

适用症状：checkoutservice 调用 emailservice 失败/超时，或 emailservice Pod 不 Ready、反复重启、Service 无就绪端点。

## 信号 → 步骤

| 信号 | 动作 |
|---|---|
| emailservice Pod 非 Running/Ready、重启、CrashLoopBackOff、ImagePullBackOff、OOMKilled、Pending | 第 1、2 步 |
| Deployment 可用副本小于 1、ReplicaSet 异常、rollout 未完成 | 第 1 步 |
| 事件出现 FailedScheduling、FailedCreate、Unhealthy、OOMKilling、BackOff | 第 3 步 |
| emailservice 日志出现启动失败、端口绑定失败、panic、gRPC 服务端错误 | 第 4 步 |
| Pod Running 但 READY 0/1，或 liveness 导致重启 | 第 5 步 |
| Service 端口/selector 异常，EndpointSlice 为空或端点未 ready | 第 6 步 |
| Pod Pending/FailedCreate，或容器 OOMKilled | 第 7 步 |
| checkoutservice 报调用 emailservice 失败/超时，但 emailservice Pod Ready、EndpointSlice 正常 | 第 8 步；无本服务证据则跳 skill `boutique-checkoutservice` |

## 步骤

1. 对齐 Deployment 与清单。执行 `kubectl -n boutique get deploy emailservice -o yaml`、`kubectl -n boutique get rs -l app=emailservice`、`kubectl -n boutique get pods -l app=emailservice -o wide`。核对副本数 1、镜像 `v0.10.3`、containerPort 8080、env `PORT=8080`、`DISABLE_PROFILER=1`、resources requests `100m/64Mi`、limits `200m/128Mi`、readiness/liveness 均为 grpc 8080、periodSeconds 5。实际不符时记录差异为原因候选；若可用副本为 0 或 ReplicaSet 新旧异常，继续看 Pod 和事件。本清单未引用 ConfigMap，不要按不存在的配置项排查；若实际 YAML 被改动引用了 ConfigMap，先确认对应 ConfigMap 存在且键值正确。

2. 看 Pod 和容器状态。执行 `kubectl -n boutique describe pod <emailservice-pod>`，看 STATUS、READY、RESTARTS、Containers State、Last State。Running 且 READY 1/1 才说明探针通过。`ImagePullBackOff/ErrImagePull` 指向镜像拉取；`CrashLoopBackOff/Error` 指向启动或运行失败；`OOMKilled` 指向内存 limit 128Mi；`Pending` 指向调度或配额，继续第 3、7 步。不能进容器，不推断进程内部状态。

3. 看事件。执行 `kubectl -n boutique get events --sort-by=.lastTimestamp`，过滤 emailservice 的 Pod、ReplicaSet、Deployment。`FailedScheduling/FailedCreate` 看调度或 ResourceQuota；`Unhealthy` 看探针失败；`OOMKilling` 看内存 limit；`BackOff` 看镜像或启动失败。若事件只提示节点资源不足，因不能看节点，只能记录“调度失败，先确认节点/配额”，不要编造节点指标。

4. 看日志。执行 `kubectl -n boutique logs deploy/emailservice --tail=200`，必要时加 `--previous` 看上次容器。先确认实际日志格式，不要套用不存在的日志字段。重点看：启动是否失败、是否按 `PORT=8080` 监听、是否 panic 退出、gRPC 服务端是否报错、探针端口是否不一致。无日志且容器未启动时回到第 2、3 步；日志明确显示端口绑定失败、启动 panic 属于本服务配置或镜像问题。

5. 确认探针与就绪。readiness 和 liveness 都是 grpc 8080、periodSeconds 5。若 Pod Running 但 READY 0/1，且事件有 Readiness probe failed，说明 8080 的 gRPC 健康检查未通过：先核对实际 Deployment 探针端口是否仍为 8080，以及日志中应用是否按 `PORT=8080` 启动。liveness 失败会导致重启；readiness 失败会导致 EndpointSlice 无 ready 端点。没有指标，不能判断探针延迟是否由 CPU 限流导致，只能写“先确认”。

6. 确认 Service 与 EndpointSlice。执行 `kubectl -n boutique get svc emailservice -o yaml`、`kubectl -n boutique get endpointslice -l kubernetes.io/service-name=emailservice -o yaml`。核对 Service type ClusterIP、selector `app=emailservice`、port name `grpc`、port 5000、targetPort 8080。EndpointSlice 应有 1 个 ready 端点，地址为 Pod IP，端口对应 targetPort 8080。selector 不匹配或无端点：查 Service selector 与 Pod 标签；端点 not ready：回到第 5 步；端口不对：调用方访问 Service 的 5000 端口会失败。调用方应使用 Service 的 5000 端口，若实际地址端口不同，记录为调用方配置候选。

7. 看 ResourceQuota 和资源限制。执行 `kubectl -n boutique get resourcequota -o yaml`，结合 requests `100m/64Mi`、limits `200m/128Mi`。若事件 FailedCreate 或 Pod Pending 且配额不足，说明本服务无法按清单创建/更新。若容器 Last State 为 OOMKilled，说明内存 limit 128Mi 被打满。没有 CPU/内存指标，不能直接证明 CPU 限流；只能根据重启、事件、超时现象谨慎写“疑似”，并注明无法用监控确认。

8. 关联 checkoutservice。查看 checkoutservice 的 Pod 日志和事件中调用 emailservice 的报错（只读）。若 emailservice Pod Ready、EndpointSlice 有 ready 端点、Service selector/端口正确、日志无启动/探针错误，而 checkoutservice 仍报调用 emailservice 失败/超时，问题更可能在调用方配置或调用链，跳到 `boutique-checkoutservice`。若 emailservice 日志明确显示自身启动、端口、gRPC 错误，结论留在 emailservice。不要只因 checkoutservice 报错就把结论写成本服务，必须有 emailservice 侧证据。

## 什么时候跳到别的服务

- emailservice 自身无下游服务，不能跳到其他下游。
- 看到 checkoutservice 日志/事件明确报调用 emailservice 失败/超时，但 emailservice Pod Ready、EndpointSlice 有 ready 端点、Service 5000→8080 与 selector 正常、日志无启动/探针错误 → 跳 skill `boutique-checkoutservice`，核对调用地址/端口、超时、重试和错误处理。
- 看到 checkoutservice 使用的 emailservice 地址或端口与 Service 不一致，或调用方超时配置明显导致失败 → 结论写在 checkoutservice，回 `boutique-checkoutservice`。
- 若 emailservice 侧有镜像、环境变量、资源 limit、探针、Service selector/targetPort、EndpointSlice 端点未就绪的证据 → 不跳，结论写 emailservice。

## 结论怎么写

结论必须写清：对象是 emailservice 的 Deployment/Pod/ReplicaSet/Service/EndpointSlice，还是 checkoutservice 的调用配置；原因是什么，例如镜像不符、环境变量错误、内存 limit 导致 OOMKilled、探针端口/就绪失败、Service selector 或 targetPort 错误、EndpointSlice 无 ready 端点、调用方地址/端口/超时错误；证据是什么，例如具体 Pod 状态、Last State、事件、EndpointSlice 输出、Deployment YAML 差异或日志片段。证据不足时写“先确认……”并列出已排除项，不要把推测写成确定原因。
