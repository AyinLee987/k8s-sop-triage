---
name: boutique-shippingservice
description: Online Boutique 的 shippingservice 排查 SOP：症状涉及 shippingservice，或从调用方（checkoutservice、frontend）查到它时读。下游：无。
---

# shippingservice 排查 SOP

## 经验条目

以下条目来自过去的排查记录，括号里是来源。

- [E1]（BH09）（BH09）用户报页面变慢、0 个 500、Pod 全 Ready 时，若前端只有 `GET /cart`（约 300ms）和 `POST /cart/checkout`（约 600ms，同一 RPC 在 PlaceOrder 里走两次）慢，而 `/`、`/product/*` 正常，本服务入向延迟即为首选假设：①前端日志按路径分桶，`POST /cart` 1–2ms 即 cart/redis/currency 全链健康；②时间戳对齐——调用方发出 GetQuote 后 `[GetQuote] received request` 恒定晚 278–331ms，而 completed <0.1ms、响应回路 1–2ms，说明延迟只在请求方向；③最硬佐证在 checkoutservice：Charge 返回后到 ShipOrder 收到之间只隔一次 shipping RPC，同样晚约 300ms。CPU limit 与其他服务一致且 handler 瞬时完成可排除限流；探针超时 1s 大于台阶，所以 Pod 一直 Ready，别因『Pod 正常/无 Unhealthy』排除网络注入。处置：

<!-- 经验条目结束 -->

适用症状：frontend 或 checkoutservice 出现与运费报价/发货相关的 gRPC 错误（Unavailable、DeadlineExceeded、500），或 `app=shippingservice` 的 Pod 不 Ready / 反复重启。

## 信号 → 步骤

| 能观察到的信号 | 去向 |
|---|---|
| 调用方日志报 shippingservice 连接失败/超时，但本服务 Pod 状态未知 | 第 1 步 |
| Pod 状态非 Running、READY 0/1、RESTARTS 上升 | 第 2、3 步 |
| Pod 事件里有 ImagePullBackOff / OOMKilled / 探针失败 / 驱逐 | 第 2、5 步 |
| Pod Running 且日志正常，但仍报调用失败 | 第 6、7 步 |
| `kubectl get endpointslice` 中 shippingservice 无 ready 端点 | 第 6 步 |
| 刚发布/滚动更新后开始报错 | 第 2、8 步 |
| 本服务侧全部正常，只有某个调用方报错 | 跳到 skill `boutique-checkoutservice` 或 `boutique-frontend` |

## 步骤

1. **确认对象存在且规模正确**
   `kubectl -n boutique get deploy,rs,pod -l app=shippingservice -o wide`
   关注期望副本数应为 1、Pod 的 READY / RESTARTS / AGE、是否出现多个 ReplicaSet（判断是否刚发布）。
   Pod 不存在、或被驱逐 → 看第 2 步事件；只有一个副本，任何重启都等于服务短暂全不可用，记录在结论里。

2. **看 Pod 事件与状态原因**
   `kubectl -n boutique describe pod -l app=shippingservice`，或 `kubectl -n boutique get events --sort-by=.lastTimestamp`
   看到 `ImagePullBackOff`/`ErrImagePull` → 确认镜像 `us-central1-docker.pkg.dev/google-samples/microservices-demo/shippingservice:v0.10.3` 是否存在、tag 是否被改；本服务原因。
   看到 `OOMKilled` → 见第 5 步。看到 `Unhealthy`（liveness/readiness failed）→ 见第 4 步。
   看到 `FailedScheduling` / 因 ResourceQuota 被拒 → 用 `kubectl -n boutique get resourcequota` 确认配额是否被打满（只读权限内可看）。

3. **看日志（含上一次容器）**
   `kubectl -n boutique logs -l app=shippingservice --tail=200`
   若 RESTARTS > 0，再执行 `kubectl -n boutique logs <pod> --previous`。
   关注：进程是否启动成功、是否在监听 50051（`PORT=50051`）、是否有 panic / 启动即退出 / 端口占用。
   日志中出现启动失败或崩溃 → 本服务原因，结合第 2 步事件写结论。
   日志为空或没有明显错误 → 继续第 4 步。日志具体文案以实际输出为准，不要预设格式。

4. **核对端口与探针一致性**
   readinessProbe / livenessProbe 都是 gRPC `50051`，`periodSeconds: 5`；容器 `containerPort: 50051`，env `PORT=50051`。
   现象：进程日志显示监听端口不是 50051，或 Deployment 被改过 env/探针端口 → 探针会持续失败、Pod 永远不 Ready，属本服务配置原因。
   现象：日志显示正常监听，但探针一直失败 → 先确认 Pod 是否真的在 50051 上可接受连接（只读权限下不能 exec，只能通过调用方报错和 EndpointSlice ready 状态间接判断）。

5. **核对资源限制**
   requests 100m/64Mi，limits 200m/128Mi。
   事件里有 `OOMKilled` 或容器退出码 137 → 内存超 limits，属本服务资源配置原因。
   没有监控，CPU 是否被限流无法直接看；若调用方报 DeadlineExceeded 且本服务日志显示请求处理慢/排队，先确认是否存在 CPU 限流（无指标，只能标记为“疑似”，不要当结论）。实际内存用量不可见时写“先确认”，不要编数据。

6. **核对 Service 与 EndpointSlice**
   `kubectl -n boutique get svc shippingservice -o yaml`：应为 ClusterIP、`port: 50051`、`targetPort: 50051`、selector `app: shippingservice`。
   `kubectl -n boutique get endpointslice -l kubernetes.io/service-name=shippingservice -o wide`：
   - 无 EndpointSlice 或 endpoints 为空 → selector 与 Pod 标签不匹配，或 Pod 未 Ready（回第 4 步）。
   - 有地址但 ready 条件为 false → Pod readinessProbe 未通过，回第 4 步。
   - 地址 ready 且端口为 50051 → 本服务侧通路正常，转第 8 步或跳到调用方。

7. **确认故障范围**
   用 `kubectl -n boutique get pods` 看 frontend、checkoutservice、loadgenerator 是否也有异常；再看调用方日志中失败的 RPC 是否只涉及 shipping。
   只有 shipping 相关调用失败 → 问题在本服务或本服务到调用方的链路上。
   多个下游同时失败 → 更可能是调用方或集群层问题，跳到调用方 skill。

8. **发布相关性**
   比对 `kubectl -n boutique get rs -l app=shippingservice -o wide` 的镜像 tag 与清单中的 `v0.10.3`，以及新 ReplicaSet 的创建时间是否与故障开始时间吻合。
   不一致或刚滚动更新 → 记录为发布引入，写进结论（只读权限下不改，只给出证据）。

## 什么时候跳到别的服务

- shippingservice 的 Pod Running、READY 1/1、日志无错误、EndpointSlice 有 ready 端点且端口为 50051，但 checkoutservice 的 PlaceOrder 仍报 shipping 相关 RPC 失败 → 跳到 skill `boutique-checkoutservice`，结论写在调用方（连接/超时/地址配置）。
- 上述本服务健康条件成立，但只有 frontend 的运费报价相关请求失败（checkoutservice 侧正常）→ 跳到 skill `boutique-frontend`，结论写在调用方。
- frontend 同时报多个下游（productcatalog、cart、currency、shipping 等）失败 → 不是 shippingservice 单点问题，跳到 skill `boutique-frontend`，结论写在调用方或集群层。
- 故障期间 shippingservice 有重启/不 Ready 记录 → 不要跳走，结论归 shippingservice 自身；调用方的报错只作为症状证据。
- shippingservice 不调用任何下游，不存在向下跳转的情况。

## 结论怎么写

必须写明具体对象 + 原因 + 证据，例如：“Deployment shippingservice（Pod `shippingservice-xxxx`）因 limits 128Mi 不足被 OOMKilled，证据：Pod 事件 `OOMKilled`、`--previous` 日志尾部进程被终止、RESTARTS 上升”。
若本服务侧全部正常：“Service shippingservice 的 EndpointSlice 有 ready 端点 50051、Pod 无重启、日志无错，故障不在 shippingservice”，并注明已跳到哪个调用方 skill 继续。
