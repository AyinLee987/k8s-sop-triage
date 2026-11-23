---
name: boutique-checkoutservice
description: Online Boutique 的 checkoutservice 排查 SOP：症状涉及 checkoutservice，或从调用方（frontend）查到它时读。下游：productcatalogservice、shippingservice、paymentservice、emailservice、currencyservice、cartservice。
---

# checkoutservice 排查 SOP

## 经验条目

以下条目来自过去的排查记录，括号里是来源。

- [E1]（BH01、BH02、BH03、BH07、CL06）【已证实】适用：结账接口集中 5xx、下单几乎全失败，调用方日志指向下单流程某一步（换算价格、运费、扣款等），包括连接错误和 gRPC `Unimplemented`/`Method not found!`。不适用：checkoutservice 自身 panic/重启/未就绪/资源打满——先查自身。做法：①按错误文本定位可疑下游；②连接错误时确认 IP 属哪个 Service，比对 port/targetPort、容器 env PORT 与 containerPort；Service 不转发未暴露端口，selector/Endpoints 可正常；③无论连接错误还是 `Unimplemented`，都比对 Deployment 的 `<下游>_SERVICE_ADDR` 与 last-applied、旧 ReplicaSet：若它指向另一个健康 gRPC 服务，会因方法不存在快速失败；不要因目标地址的 Endpoints/端口健康而排除；④改回真实地址/端口并滚动后复测 5xx 与下单失败数，应归零；延迟通常基本不动。例：扣款地址被改为邮件服务的 5000，返回 `Unimplemented`，改回支付地址后失败归零。

<!-- 经验条目结束 -->

适用症状：用户从 frontend 结账/下单失败，frontend 出现调用 checkoutservice:5050 的错误、超时或 5xx，或 checkoutservice Pod 不 Ready/重启。

## 信号 → 步骤

| 信号 | 动作 |
|---|---|
| frontend 日志/事件显示调用 checkoutservice:5050 失败、超时或 5xx | 第 1 步；若第 2–7 步确认 checkoutservice 侧正常，跳 `boutique-frontend` |
| checkoutservice Pod 非 Running/Ready、重启、CrashLoopBackOff、ImagePullBackOff、OOMKilled、Pending | 第 2 步 |
| Deployment 可用副本为 0，或 ReplicaSet 异常 | 第 2、8 步 |
| checkoutservice 的 EndpointSlice 无 ready 端点，或端口不是 5050 | 第 3 步 |
| Pod 事件出现 Unhealthy、Probe failed | 第 5 步 |
| checkoutservice 日志显示启动失败、端口绑定失败、env/配置异常 | 第 4 步 |
| checkoutservice 日志显示某个下游连接失败/超时 | 第 7 步，并跳对应 `boutique-<下游>` |
| 某个下游 Service/EndpointSlice 无 ready 端点 | 跳对应 `boutique-<下游>` |
| ResourceQuota 限制、事件 FailedCreate/FailedScheduling | 第 6 步 |
| checkoutservice 自身检查均正常，仅 frontend 报 checkout 错误 | 跳 `boutique-frontend` |

## 步骤

1. 定位失败面：看 frontend 日志/事件中与 checkout 相关的错误，记录时间、错误码/超时、目标地址；同时看 checkoutservice Pod 当前状态。目的：区分“frontend → checkoutservice:5050 入口问题”和“checkoutservice → 下游调用问题”。无监控指标，不要依赖 CPU、内存、延迟曲线。

2. 查 Deployment/ReplicaSet/Pod：`kubectl -n boutique get deploy,rs,pods -l app=checkoutservice -o wide`，再 `describe` 异常 Pod。确认副本数为 1、Pod Running 且 Ready、重启次数。ImagePullBackOff/ErrImagePull 查镜像 `v0.10.3` 与拉取事件；CrashLoopBackOff 结合 `kubectl -n boutique logs <pod> --previous`；OOMKilled 对应内存 limit 128Mi；Pending/FailedScheduling 记录事件并转第 6 步；Unhealthy 转第 5 步。清单未列出 ConfigMap 引用；若事件出现 ConfigMap 相关引用，先确认是否有人改动，不按不存在的配置项推断。

3. 查 Service 与 EndpointSlice：`kubectl -n boutique get svc checkoutservice -o yaml`、`kubectl -n boutique get endpointslice -l kubernetes.io/service-name=checkoutservice -o yaml`。确认 Service selector 为 `app: checkoutservice`，port/targetPort 均为 5050；EndpointSlice 应有 ready=true 的 5050 地址。无端点通常是 Pod 未 Ready 或 selector 不匹配；端口不匹配是 Service 配置问题。记录对象和证据。

4. 查 checkoutservice 日志：看当前 Pod 日志和崩溃前日志，查找启动失败、端口绑定失败、探针失败、连接下游失败/超时、异常退出等明确错误。不要假设日志格式；记录原始错误片段和时间。若日志指向某个下游地址，转第 7 步。核对 Deployment env 是否与清单一致：`PORT=5050`，六个下游地址分别为 `productcatalogservice:3550`、`shippingservice:50051`、`paymentservice:50051`、`emailservice:5000`、`currencyservice:7000`、`cartservice:7070`；缺失或错误记为本服务配置问题。

5. 查探针：readinessProbe 和 livenessProbe 都是 gRPC 5050。Pod 事件若出现 Probe failed/Unhealthy，结合日志判断进程是否监听 5050、是否启动即崩溃或阻塞。不能进容器执行探测，结论用事件加日志。readiness 失败会导致 EndpointSlice 无 ready 端点，frontend 调用失败；liveness 失败会导致 Pod 重启。探针端口若被改成非 5050，记录为本服务配置问题。

6. 查资源与配额：Deployment requests 为 100m/64Mi，limits 为 200m/128Mi。看 Pod describe 是否 OOMKilled，查命名空间 ResourceQuota 和 FailedCreate/FailedScheduling 事件。无 CPU/内存指标，不能仅凭现象判断 CPU 饱和；CPU 限流无法直接观测，先确认事件、日志和下游正常性。若频繁重启且 OOMKilled，才归因内存限制。

7. 逐项核对下游入口：对六个下游分别看 Service 端口和 EndpointSlice ready，并核对第 4 步的 env 地址：productcatalogservice:3550、shippingservice:50051、paymentservice:50051、emailservice:5000、currencyservice:7000、cartservice:7070。某下游无 ready 端点、Service 不存在或端口不匹配，跳对应 skill；下游入口正常但 checkout 日志仍报该下游超时/错误，也跳对应 skill 继续确认应用侧。不要直接查 redis-cart；cartservice 的下游由 cartservice skill 处理。

8. 查最近变更和单点风险：看 ReplicaSet 历史、Pod 模板镜像是否为 `v0.10.3`、env/探针/资源是否与清单一致。副本数为 1，Pod 重启或不 Ready 期间没有其他副本承接。若新 ReplicaSet 对应异常 Pod，记录为发布变更嫌疑。ConfigMap 只在有实际引用或事件时确认。

9. 汇总结论：把证据落到具体对象。若 checkoutservice 自身异常，结论写 checkoutservice 的 Deployment/Pod/Service/EndpointSlice 及原因；若下游异常，跳对应 skill 并把结论写在该下游对象；若 checkoutservice 侧正常而 frontend 调用失败，回 frontend 并把结论写在 frontend。

## 什么时候跳到别的服务

- checkoutservice 日志/事件出现 productcatalogservice:3550 连接失败、超时，或 productcatalogservice 的 Service/EndpointSlice 无 ready 端点 → 跳 `boutique-productcatalogservice`；结论写 productcatalogservice 对应对象。
- checkoutservice 日志/事件出现 shippingservice:50051 连接失败、超时，或 shippingservice 的 Service/EndpointSlice 无 ready 端点 → 跳 `boutique-shippingservice`；结论写 shippingservice 对应对象。
- checkoutservice 日志/事件出现 paymentservice:50051 连接失败、超时，或 paymentservice 的 Service/EndpointSlice 无 ready 端点 → 跳 `boutique-paymentservice`；结论写 paymentservice 对应对象。
- checkoutservice 日志/事件出现 emailservice:5000 连接失败、超时，或 emailservice 的 Service/EndpointSlice 无 ready 端点 → 跳 `boutique-emailservice`；结论写 emailservice 对应对象。
- checkoutservice 日志/事件出现 currencyservice:7000 连接失败、超时，或 currencyservice 的 Service/EndpointSlice 无 ready 端点 → 跳 `boutique-currencyservice`；结论写 currencyservice 对应对象。
- checkoutservice 日志/事件出现 cartservice:7070 连接失败、超时，或 cartservice 的 Service/EndpointSlice 无 ready 端点 → 跳 `boutique-cartservice`；结论写 cartservice 对应对象。不要直接跳 redis-cart。
- frontend 日志显示无法连接 checkoutservice:5050、调用超时或 5xx，但第 2–7 步确认 checkoutservice Pod、Service、EndpointSlice、日志、资源、探针均正常 → 跳 `boutique-frontend`；结论写在 frontend 对应对象。若 checkoutservice 侧已有异常证据，不要回 frontend，结论写 checkoutservice。
- 不要跳到未直接调用的服务；loadgenerator 仅作为流量来源，不作为跳转或结论对象。

## 结论怎么写

结论必须写清对象、原因、证据：对象写 checkoutservice 的 Deployment/Pod/Service/EndpointSlice，或具体下游/frontend 的对应对象；原因写如 Pod 未 Ready 导致 EndpointSlice 无端点、OOMKilled 触发内存 limit、env 地址错误、探针失败、Service selector/端口错误、下游不可达；证据写 Pod describe 事件、日志原始片段、EndpointSlice ready 状态、Deployment env/镜像/探针/资源 spec、ResourceQuota 事件。

判到下游时，结论写在该下游对象上并注明“checkoutservice 侧仅表现为调用失败”；判到 frontend 时，结论写在 frontend 对象上。
