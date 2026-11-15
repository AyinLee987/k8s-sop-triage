---
name: boutique-paymentservice
description: Online Boutique 的 paymentservice 排查 SOP：症状涉及 paymentservice，或从调用方（checkoutservice）查到它时读。下游：无。
---

# paymentservice 排查 SOP

适用症状：checkoutservice 结账失败或日志提示调用 paymentservice:50051 失败/超时/连接拒绝，或 paymentservice Pod 不 Ready、重启、无可用端点。

## 信号 → 步骤

| 信号 | 去第几步 / 跳转 |
|---|---|
| checkoutservice 日志或事件明确涉及 paymentservice:50051 调用失败 | 第 1 步 |
| paymentservice Pod 不存在、Pending、非 Ready、反复重启 | 第 2 步 |
| paymentservice 容器日志显示启动/端口绑定/退出异常，或事件显示探针失败 | 第 3 步 |
| paymentservice Service 的 selector/端口异常，或 EndpointSlice 无就绪端点 | 第 4 步 |
| Deployment/ReplicaSet 不可用、副本不足、镜像拉取失败、模板与清单不一致 | 第 5 步 |
| Pod 事件出现 OOMKilled、FailedScheduling、资源受限，或 Pending 事件提到 quota | 第 6、7 步 |
| checkoutservice 报错不指向 paymentservice，或 paymentservice 侧检查全部正常但仍失败 | 跳到 skill `boutique-checkoutservice` |

## 步骤

1. **从调用方确认症状**：查看 checkoutservice Pod 日志和事件，筛选是否出现 paymentservice、50051、调用失败/超时/连接拒绝等信息；先确认实际日志内容，不要套用不存在的日志格式。记录故障时间窗。若错误不涉及 paymentservice，跳 `boutique-checkoutservice`；若涉及，继续。
2. **查 paymentservice Pod 状态**：`kubectl -n boutique get pods -l app=paymentservice`，再对目标 Pod 看 `describe` 和事件。确认 Pod 是否存在、Phase、Ready、重启次数、容器状态。Pending 去第 7 步；ImagePullBackOff/ErrImagePull、CrashLoopBackOff、Error、OOMKilled 去第 3、5、6 步；Running 且 Ready 去第 4 步。
3. **查 paymentservice 日志**：`kubectl -n boutique logs <pod> --tail=200`，如刚重启可看 `--previous`。关注启动失败、端口绑定失败、进程退出、探针失败等通用信息；不要假设固定日志字段。结合 `describe pod` 中 readiness/liveness 事件判断 grpc 50051 探针是否失败。若探针失败，核对第 5 步的端口和探针配置；日志正常且 Pod Ready，去第 4 步。
4. **查 Service 和 EndpointSlice**：`kubectl -n boutique get svc paymentservice -o yaml`，确认 type 为 ClusterIP、selector 为 `app: paymentservice`、端口为 grpc 50051、targetPort 50051。再查 boutique 内 paymentservice 对应 EndpointSlice，确认存在 endpoint 且 ready。无 endpoint 或 endpoint 不 ready：回到 Pod Ready 状态和 Service selector；selector/端口不符：记为本服务 Service 问题。全部正常去第 5、8 步。
5. **查 Deployment/ReplicaSet 与发布清单**：`kubectl -n boutique get deploy paymentservice -o yaml`、`get rs`。确认副本数为 1、可用副本正常；镜像是否为 `.../paymentservice:v0.10.3`；containerPort 50051；env `PORT=50051`、`DISABLE_PROFILER=1`；resources requests/limits 是否为 100m/64Mi、200m/128Mi；readiness/liveness 是否为 grpc 50051。实际 YAML 与清单不一致时，按差异记录为镜像、配置、探针或资源问题。该清单未引用 ConfigMap；若实际 YAML 出现 ConfigMap 引用，先确认引用的 ConfigMap 是否存在及键值可用。
6. **查事件与资源受限**：按 Pod、Deployment、ReplicaSet 查看事件，关注 FailedScheduling、FailedCreate、OOMKilled、Unhealthy 等通用事件。若 OOMKilled，结合 memory limit 128Mi 记录；若 Pending 且事件提到 quota 或资源不足，去第 7 步。没有监控指标，不能据 CPU/内存使用率或延迟下结论，也不要查节点。
7. **查 ResourceQuota**：`kubectl -n boutique get resourcequota -o yaml`。若 Pod 创建失败或 Pending 事件提到 exceeded quota，记录具体 quota 和受限资源；quota 正常则回到第 2、5 步继续判断。
8. **汇总结论**：若 Pod Ready、EndpointSlice 有 ready endpoint、Service/Deployment 配置符合清单、日志无异常，则 paymentservice 侧未发现故障证据，按下一节回调用方。若发现具体对象异常，结论留在 paymentservice。

## 什么时候跳到别的服务

- checkoutservice 日志或事件显示错误不涉及 paymentservice:50051，或问题在 checkoutservice 自身或其他下游 → 跳 skill `boutique-checkoutservice`，结论写在 checkoutservice。
- paymentservice Pod Ready、EndpointSlice 有 ready endpoint、Service/Deployment 配置符合清单、日志无异常，但 checkoutservice 仍报调用 paymentservice 失败 → 回调用方 checkoutservice，跳 skill `boutique-checkoutservice`，结论写“paymentservice 侧未发现故障证据，需在 checkoutservice 侧继续查连接/超时/调用逻辑”。
- paymentservice 没有下游服务；不要跳到本服务不存在的下游。确认是 paymentservice 自身对象异常时，不要跳走，结论留在 paymentservice。

## 结论怎么写

结论必须写明具体对象，如 Pod、Deployment/paymentservice、Service/paymentservice、EndpointSlice、ResourceQuota；写明原因，如镜像拉取失败、探针 50051 失败、env/端口不符、Service selector/端口不符、OOMKilled、quota 限制等；附上证据，如 Pod 状态、事件、日志中的通用错误、EndpointSlice ready 条件。若回到调用方，结论写在 checkoutservice：说明 paymentservice 侧对象检查结果及为何需 checkoutservice 继续排查。
