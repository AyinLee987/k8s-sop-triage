---
name: boutique-productcatalogservice
description: Online Boutique 的 productcatalogservice 排查 SOP：症状涉及 productcatalogservice，或从调用方（checkoutservice、recommendationservice、frontend）查到它时读。下游：无。
---

# productcatalogservice 排查 SOP

## 经验条目

以下条目来自过去的排查记录，括号里是来源。

- [E1]（BH04、BH11）【已证实】首页、商品页、购物车、下单页一起变慢但无 5xx、Pod 无重启且配置正常时，若耗时随对 productcatalogservice 的往返次数增长，到该 Pod 所在节点 `tc qdisc show dev <veth>` 查 netem 延时/丢包并清除恢复默认队列。两种形态：整体台阶（首页≈2×300ms、商品页≈8×300ms），或尾部弥散（中位数正常、仅 ~15% 请求偶发 +200ms、慢占比随调用次数上升）；本次只清它（及 redis-cart）的 veth 即恢复：2405→10ms、>180ms 86.1%→0%；本次 >180ms 15.9%→0%、p90 217→15ms、p99 428→50ms。它的 veth 延时会让调用方（recommendationservice、checkout、frontend）看起来也慢，修好后自动回落，别把它们判成源头。【已排除】CPU/CFS 节流、配置漂移、需重建 Pod（未改 limit、未重启 Pod 即恢复）；一次 Liveness probe >1s 超时不等于 CPU 不足。

<!-- 经验条目结束 -->

适用症状：frontend、checkoutservice 或 recommendationservice 出现调用 productcatalogservice（:3550）失败（gRPC 报错/超时/500），或 productcatalogservice 的 Pod 不 Ready、反复重启。

## 信号 → 步骤

| 能观察到的信号 | 去向 |
| --- | --- |
| 某个调用方日志里出现指向 `productcatalogservice:3550` 的 gRPC 错误 | 第 1 步 |
| Pod 不是 Running 1/1，或 RESTARTS 在增长 | 第 2 步 |
| Pod Pending，或事件里有调度失败、配额信息 | 第 3 步 |
| Pod OOMKilled / CrashLoopBackOff / 探针失败 | 第 3 步 |
| Pod Running 1/1，但调用方持续报错 | 第 4 步 |
| Service 存在，但 EndpointSlice 没有 ready 端点 | 第 4 步 |
| 只有一个调用方报错，另外两个正常 | 跳到该调用方的 skill `boutique-frontend` / `boutique-checkoutservice` / `boutique-recommendationservice` |
| 报错指向的是本服务之外的组件（cartservice、adservice、paymentservice 等） | 跳到发起该调用的调用方 skill |

## 步骤

1. **确认错误范围**
   分别看三个调用方的 Pod 日志，找指向 `productcatalogservice:3550` 的错误。先确认日志里错误的具体形态（是连接不上、超时，还是返回业务错误），不要预设日志格式。三个调用方都报错 → 问题大概率在本服务，进第 2 步；只有一个报错 → 见“什么时候跳到别的服务”。

2. **看 Pod 状态**
   `kubectl get pods -n boutique -l app=productcatalogservice`，看 READY、STATUS、RESTARTS、AGE。
   - Pending → 第 3 步。
   - CrashLoopBackOff / RESTARTS 增长 → `kubectl logs --previous` 看上一次启动的日志，再进第 3 步。
   - Running 但 READY 0/1 → 读就绪探针（gRPC 3550）没过，进第 3、5 步。
   - Running 1/1 → 进第 4 步。
   注意 Deployment 只有 1 个副本，Pod 重建期间三个调用方全部失败属预期，不要当成独立故障。

3. **看事件和 describe**
   `kubectl describe pod -n boutique <pod>`，配合 `kubectl get events -n boutique --sort-by=.lastTimestamp`。对照本服务清单逐项排除：
   - `OOMKilled`（内存 limit 128Mi）→ 内存超限，本服务自身原因。
   - 探针失败：liveness/readiness 都是 gRPC 3550，若容器内进程没监听该端口（PORT 被改、启动失败、镜像不对）会表现为反复重启。
   - 镜像拉取失败 → 核对 image 是否为 `productcatalogservice:v0.10.3`。
   - Pending 且事件提到配额 → 查 `kubectl get resourcequota -n boutique`；若事件只说资源不足，因无节点权限，只能记录“节点侧资源不足（先确认）”，不要推断具体节点状况。

4. **看 Service 和 EndpointSlice**
   `kubectl get svc productcatalogservice -n boutique -o yaml` 和 `kubectl get endpointslice -n boutique -l kubernetes.io/service-name=productcatalogservice -o yaml`。
   - Service 应为 ClusterIP、端口 3550 → targetPort 3550，selector 为 `app: productcatalogservice`。
   - EndpointSlice 无端点：selector 与 Pod 标签不符，或 Pod 未 Ready（回第 2 步）。
   - 端点在但未 ready：Pod 读就绪探针失败（回第 3 步）。
   - 端口/selector 与清单不一致 → 判定为配置漂移。

5. **看容器日志**
   `kubectl logs -n boutique <pod>`。先确认日志里有没有启动失败、端口绑定失败、panic、gRPC 错误等；本服务日志格式不做预设，按实际内容判断。日志干净但调用方仍报错 → 回到第 4 步确认 Service/EndpointSlice 链路。

6. **核对配置**
   本服务清单没有挂载 ConfigMap，配置全部来自 Deployment 的 env，因此以 `kubectl get deployment productcatalogservice -n boutique -o yaml` 为准：确认 `PORT=3550`、`containerPort=3550`、两个探针的端口均为 3550、镜像 tag 为 `v0.10.3`、requests/limits 未被改动。若发现环境里另有 ConfigMap 注入到该 Pod，先确认其内容再下结论。

## 什么时候跳到别的服务

- frontend 报错，但 productcatalogservice 的 Pod Ready、EndpointSlice 有 ready 端点、日志无异常 → 跳到 skill `boutique-frontend`，结论写在 frontend（它自身的超时、连接或重试问题）。
- checkoutservice 报错，但错误同时涉及 paymentservice、emailservice、currencyservice 等本服务之外的下游，或 productcatalogservice 侧完全正常 → 跳到 skill `boutique-checkoutservice`，结论写在 checkoutservice。
- recommendationservice 报错，且 productcatalogservice 侧无异常（它只调用 productcatalogservice，报错不等于故障在下游）→ 跳到 skill `boutique-recommendationservice`，结论写在 recommendationservice。
- 报错指向 cartservice、adservice、paymentservice、currencyservice 等本服务未参与调用的组件 → 不在本 SOP 范围，跳到发起该调用的调用方 skill 判断归属。
- 三个调用方都报错、且本服务 Pod 不 Ready 或 EndpointSlice 无端点 → 留在本服务，不要外推。

## 结论怎么写

写清三要素：对象（具体到 Deployment `productcatalogservice` / Pod 名 / Service / EndpointSlice）、原因（如内存超限 OOMKilled、探针端口不符、selector 不匹配、镜像拉取失败）、证据（哪条命令的哪段输出，例如 `Last State: OOMKilled`、Events 中的探针失败信息、EndpointSlice 无 ready 端点）。未能确认时写“未确认”，并注明缺什么（如指标、节点信息、容器内检查权限），不要把推测写成结论。
