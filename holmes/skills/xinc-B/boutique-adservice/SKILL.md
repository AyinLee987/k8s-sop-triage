---
name: boutique-adservice
description: Online Boutique 的 adservice 排查 SOP：症状涉及 adservice，或从调用方（frontend）查到它时读。下游：无。
---

# adservice 排查 SOP

## 经验条目

以下条目来自过去的排查记录，括号里是来源。

- [E1]（BH06）（BH06）页面其余功能正常、无 5xx，但 frontend 每个首页/商品页日志反复出现 `failed to retrieve ads: ... dial tcp <adservice ClusterIP>:9555: connect: connection refused`、页面无广告，且这些页面耗时与前缀广告调用无关时：查 adservice Deployment 的 `replicas`（可能被缩容到 0）、Pod 事件（`Killing: Stopping container server`/`SuccessfulDelete`）和 Service 的 Endpoints（应为空）。此类调用毫秒级失败，不贡献页面延迟台阶——不要把它当作变慢原因，也不要为它去调 CPU/网络；把副本数恢复到 1，20–60 秒就绪后报错停止、广告恢复。若仍有延迟台阶，按总则 E2 另行定位真正的慢点。

<!-- 经验条目结束 -->

适用症状：frontend 页面广告缺失或广告相关请求失败，且可能指向 adservice；adservice Pod/Service/Endpoint 异常。

## 信号 → 步骤

| 信号 | 动作 |
|---|---|
| frontend 日志/事件出现指向 adservice 的调用失败、超时或不可用（具体关键字以现场为准） | 先第 1、2 步；若 adservice 对象均正常，跳 skill `boutique-frontend` |
| adservice Pod 非 Running/Ready、重启次数增加、CrashLoopBackOff | 第 1、3、4 步 |
| adservice Deployment 可用副本为 0、ReplicaSet 异常或 Pod 不存在 | 第 1 步；无法创建时转第 5 步 |
| adservice Service 缺失、端口/selector 不匹配，EndpointSlice 无 ready endpoint | 第 2 步 |
| Pod 事件含 ImagePullBackOff/ErrImagePull/OOMKilled/FailedScheduling/探针失败/配额超限 | 第 4、5 步 |
| ResourceQuota 报 exceeded quota | 第 5 步 |
| adservice Pod Ready、Endpoint ready、日志无明显错误，但 frontend 持续报错 | 跳 skill `boutique-frontend` |

## 步骤

1. 看 adservice 总体对象：`kubectl -n boutique get deploy,rs,pod,svc,endpointslice`，再 `kubectl -n boutique describe deploy adservice`、`describe pod <adservice-pod>`。确认 Deployment 副本数是否为 1、Pod 是否 Running/Ready、ReplicaSet 是否正常。Deployment 不存在/副本为 0/可用副本为 0 时看事件：能创建但 Pod 异常转第 3/4/5 步；无法创建转第 5 步。
2. 检查 adservice Service 和 EndpointSlice。确认 Service 为 ClusterIP、selector 为 `app: adservice`、端口 name `grpc`、port `9555`、targetPort `9555`。查看 EndpointSlice 是否有对应 Pod IP 且端口 9555 为 ready。Service 缺失、selector/端口不匹配、无 ready endpoint → 本服务 Service/Endpoint 问题；Endpoint ready 但 frontend 仍报错 → 转第 6 步。
3. 检查探针与端口配置。对照 Deployment：readinessProbe/livenessProbe 均为 grpc 9555，initialDelaySeconds 20、periodSeconds 15；容器 containerPort 9555、env `PORT=9555`。Pod Running 但 Ready=false 或反复重启时，看 Pod 事件中的探针失败信息和日志启动情况；若探针端口不是 9555、env/containerPort 不一致，或日志显示未监听 9555，则本服务配置/启动问题。应用是否实际监听 9555 不能进容器确认，需从日志、探针结果和 Endpoint 状态推断，不确定时先确认。
4. 看 adservice Pod 日志和事件。查看当前日志及 `--previous` 日志中启动失败、端口绑定失败、异常退出、gRPC 服务错误等信息（具体格式以现场为准）；看 events 是否有 ImagePullBackOff/ErrImagePull、OOMKilled、Exit 137、崩溃退出。镜像拉取失败对应发布清单镜像 `us-central1-docker.pkg.dev/google-samples/microservices-demo/adservice:v0.10.3`，属本服务镜像/仓库问题；OOMKilled/Exit 137 转第 5 步；无日志且 Pending 转第 5 步。
5. 检查资源和配额。对照 requests cpu 200m/memory 180Mi、limits cpu 300m/memory 300Mi，查看 Pod 事件是否有 FailedScheduling、OOMKilled、Evicted、exceeded quota，并查看命名空间 ResourceQuota。配额不足、无法调度、OOMKilled 与 limit 相关 → 本服务资源/配额问题并记录具体事件。只读账号不能看节点、无监控指标；CPU 限流不能凭指标判断，只能先看日志和事件，不确定时先确认。
6. 回调用方 frontend。adservice 无下游，若 adservice Deployment/Pod/Service/EndpointSlice 均正常、日志无明显错误，但 frontend 仍报 adservice 调用失败，跳 skill `boutique-frontend`，在 frontend 侧确认调用地址、端口、超时、错误处理或公共网络。

## 什么时候跳到别的服务

- frontend 日志/事件持续出现 adservice 调用失败，但 adservice Pod Ready、EndpointSlice 有 ready endpoint、Service 端口/selector 正确、日志无启动或退出错误 → 跳 skill `boutique-frontend`。
- frontend 同时报多个依赖服务错误，且 adservice 自身对象正常 → 跳 skill `boutique-frontend`，先判断 frontend 自身或公共网络。
- adservice 无 ready endpoint、Pod 未 Ready、Service/selector/端口错误、探针/镜像/资源/配额问题证据明确 → 不跳别的服务，结论写在 adservice。
- adservice 正常但无法确认 frontend 调用配置或网络 → 回调用方 frontend，结论写在 frontend 的调用配置/网络一侧，需 frontend 日志和配置证据。

## 结论怎么写

结论必须点名 adservice 的具体对象（Deployment/Pod/ReplicaSet/Service/EndpointSlice/ResourceQuota）和原因，并附证据，如 Pod Ready=False、Event ImagePullBackOff、EndpointSlice 无 ready endpoint、OOMKilled、配额超限事件。若 adservice 对象均正常，写“adservice Pod Ready、Endpoint ready、日志无明显异常”，并把调用失败归到 frontend，证据为 frontend 日志/事件。
