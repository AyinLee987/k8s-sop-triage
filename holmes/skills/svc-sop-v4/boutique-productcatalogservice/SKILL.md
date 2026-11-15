---
name: boutique-productcatalogservice
description: Online Boutique 的 productcatalogservice 排查 SOP：症状涉及 productcatalogservice，或从调用方（checkoutservice、recommendationservice、frontend）查到它时读。下游：无。
---

# productcatalogservice 排查 SOP

适用症状：frontend、checkoutservice 或 recommendationservice 出现调用 productcatalogservice（:3550）失败（gRPC 报错/超时/500），或调用方页面/接口明显变慢但无报错，或 productcatalogservice 的 Pod 不 Ready、反复重启。

## 信号 → 步骤

| 能观察到的信号 | 去向 |
| --- | --- |
| 某个调用方日志里出现指向 `productcatalogservice:3550` 的 gRPC 错误 | 第 1 步 |
| Pod 不是 Running 1/1，或 RESTARTS 在增长 | 第 2 步 |
| Pod Pending，或事件里有调度失败、配额信息 | 第 3 步 |
| Pod OOMKilled / CrashLoopBackOff / 探针失败 | 第 3 步 |
| Pod Running 1/1，但调用方持续报错 | 第 4 步 |
| Service 存在，但 EndpointSlice 没有 ready 端点 | 第 4 步 |
| 调用方报“页面/接口变慢”但无报错，本服务 Pod Running 1/1、restartCount 稳定、事件为空 | 第 7 步；事件为空不等于无故障 |
| 多个调用方同时变慢，且慢的都落在“调用本服务”的路径上 | 第 7、8 步 |
| 调用方日志显示每次调用本服务固定多出同一量级耗时（如 ~300ms），而本服务日志 received→completed 仍是毫秒级 | 第 7、8 步，结论写本服务 Pod 网络/链路；不要写成 CPU 限流/节点争抢待确认 |
| 全 Pod Running/Ready、事件为空、无 ResourceQuota 限制，但多个调用方调用本服务的路径都慢 | 第 7 步；必须做跨调用方耗时比对，不要因对象正常而停止 |
| 只有一个调用方慢，其它调用方调用本服务耗时正常 | 第 8 步，跳该调用方 skill |
| 只有一个调用方报错，另外两个正常 | 跳到该调用方的 skill `boutique-frontend` / `boutique-checkoutservice` / `boutique-recommendationservice` |
| 报错指向的是本服务之外的组件（cartservice、adservice、paymentservice 等） | 跳到发起该调用的调用方 skill |

## 步骤

1. **确认错误范围**
   分别看三个调用方的 Pod 日志，找指向 `productcatalogservice:3550` 的错误，或“变慢但无报错”的耗时现象。先确认日志里错误/耗时的具体形态（是连接不上、超时、返回业务错误，还是调用耗时固定增加），不要预设日志格式。三个调用方都报错或都变慢 → 问题大概率在本服务，进第 2 步；只有一个报错或只有它慢 → 见“什么时候跳到别的服务”。

2. **看 Pod 状态**
   `kubectl get pods -n boutique -l app=productcatalogservice`，看 READY、STATUS、RESTARTS、AGE。
   - Pending → 第 3 步。
   - CrashLoopBackOff / RESTARTS 增长 → `kubectl logs --previous` 看上一次启动的日志，再进第 3 步。
   - Running 但 READY 0/1 → 读就绪探针（gRPC 3550）没过，进第 3、5 步。
   - Running 1/1 → 进第 4 步；若调用方只是慢、不报错，做完第 4 步后直接进第 7 步。
   注意 Deployment 只有 1 个副本，Pod 重建期间三个调用方全部失败属预期，不要当成独立故障。

3. **看事件和 describe**
   `kubectl describe pod -n boutique <pod>`，配合 `kubectl get events -n boutique --sort-by=.lastTimestamp`。对照本服务清单逐项排除：
   - `OOMKilled`（内存 limit 128Mi）→ 内存超限，本服务自身原因。
   - 探针失败：liveness/readiness 都是 gRPC 3550，若容器内进程没监听该端口（PORT 被改、启动失败、镜像不对）会表现为反复重启。
   - 镜像拉取失败 → 核对 image 是否为 `productcatalogservice:v0.10.3`。
   - Pending 且事件提到配额 → 查 `kubectl get resourcequota -n boutique`；若事件只说资源不足，因无节点权限，只能记录“节点侧资源不足（先确认）”，不要推断具体节点状况。
   注意：本服务被注入网络延迟这类故障时，Pod 状态、事件、restartCount 全部正常，事件为空。事件为空不等于无故障，慢而不报错时继续第 7 步。

4. **看 Service 和 EndpointSlice**
   `kubectl get svc productcatalogservice -n boutique -o yaml` 和 `kubectl get endpointslice -n boutique -l kubernetes.io/service-name=productcatalogservice -o yaml`。
   - Service 应为 ClusterIP、端口 3550 → targetPort 3550，selector 为 `app: productcatalogservice`。
   - EndpointSlice 无端点：selector 与 Pod 标签不符，或 Pod 未 Ready（回第 2 步）。
   - 端点在但未 ready：Pod 读就绪探针失败（回第 3 步）。
   - 端口/selector 与清单不一致 → 判定为配置漂移。
   - 端点、端口、selector 全部正确但调用方慢/报错 → 进第 7 步，不要因为“服务发现正常”就停在这里。

5. **看容器日志**
   `kubectl logs -n boutique <pod>`。先确认日志里有没有启动失败、端口绑定失败、panic、gRPC 错误等；本服务日志格式不做预设，按实际内容判断。日志干净但调用方仍报错或仍慢 → 回到第 4 步确认 Service/EndpointSlice 链路，再进第 7 步。

6. **核对配置**
   本服务清单没有挂载 ConfigMap，配置全部来自 Deployment 的 env，因此以 `kubectl get deployment productcatalogservice -n boutique -o yaml` 为准：确认 `PORT=3550`、`containerPort=3550`、两个探针的端口均为 3550、镜像 tag 为 `v0.10.3`、requests/limits 未被改动。若发现环境里另有 ConfigMap 注入到该 Pod，先确认其内容再下结论。

7. **无错误型变慢：跨调用方耗时比对（本 SOP 的关键判据）**
   适用：用户反馈变慢但无报错，本服务 Pod Running 1/1、restartCount 未增长、事件为空、EndpointSlice 有 ready 端点、日志无错误。
   - 先确定变慢的拐点时间（例如从某时刻起调用耗时从几毫秒跳到几百毫秒），记录时间窗，日志时间戳格式先确认。
   - 时间戳对齐：在**至少两个调用方**（frontend、recommendationservice、checkoutservice 中）日志里取“调用本服务发出 → 收到响应”的耗时，在本服务日志里取同一时间窗内同一请求的“received → completed”耗时，逐条对齐比较。若现场只有一个调用方日志可用，也要用该调用方调用其它下游的耗时做对照。
   - 判据（满足即说明延迟在本服务 Pod 的网络/链路一侧）：
     a) 本服务自身 received→completed 仍是毫秒级，说明应用处理没变慢；
     b) **多个**调用方调用本服务时，每次调用都固定多出同一量级的耗时（例如都约 300ms），且增量与请求内容、购物车大小无关、呈现固定值；
     c) 与此同时，调用方调用其它下游服务（cartservice、currencyservice、shippingservice 等）的耗时正常，只有落到本服务的调用带固定增量。
   - 若本服务日志的 received→completed 也明显变长 → 不是网络增量，回第 3、4、5、6 步按应用/资源方向查；无 CPU/内存指标时只能写“疑似”，不可编造指标数据。
   - 若只有单个调用方调用本服务慢、其它调用方调用本服务正常 → 不是本服务侧问题，按第 8 步回该调用方。
   - 权限边界：看不到节点、无监控指标、不能 exec/tcpdump/tc，也看不到 NetworkPolicy/CoreDNS。机制层（tc/netem、CNI、DNS、CPU 限流）在本 SOP 权限内无法最终确认，但这**不改变归属**：把结论写在本服务侧并注明“机制未确认及所需权限”，不要因为机制未确认就把结论改成“链路问题/未确认”或推给调用方。
   - 本次 bad case 的强制纠偏：当多个调用方调用本服务固定多出约 300ms、本服务自身处理仍是毫秒级、其它下游正常时，根因必须写“productcatalogservice Pod 网络/链路侧固定延迟”，不得把“CPU 限流、节点争抢、注入延迟”列成根因候选或主结论，也不得以“先取限流指标”替代归属。只有出现直接限流/节点/注入证据时，才把这些机制写入证据补充。

8. **排除串行放大与误判，确定归属**
   - 页面变慢常被放大：frontend 推荐区对每个推荐商品各查一次本服务，recommendationservice 内部又调一次本服务，购物车/下单页每件商品一次查询——**串行调用把单次约 300ms 的增量放大成 1.5–3s**，购物车越大越慢。这是放大效应，不是 frontend/recommendationservice 的应用逻辑缺陷，不要把它们写成根因。
   - recommendationservice 看起来最慢是常见误判：它自身只调本服务，它慢多半是本服务侧慢。先按第 7 步比对，再决定是否跳 `boutique-recommendationservice`。
   - checkoutservice 整单链路里只有落到本服务的单次调用带增量、其它下游（shipping、currency、payment、email、cart）都正常 → 归属本服务；不要因为整单总耗时看着不长就排除本服务。若 checkoutservice 的 `[PlaceOrder]` 内部增量来自逐件 `GetProduct`，同样归本服务。
   - 本服务两端都正常（自处理毫秒级、调用方调用本服务也无固定增量），而变慢出现在调用方自身的渲染/并发/重试逻辑 → 才回调用方 skill。
   - 典型误判（已确认案例）：不要因本服务 Pod 全 Running/Ready、无事件、无 ResourceQuota，就把固定约 300ms 增量归为 CPU 限流或节点争抢。无指标时这只能作为未验证的机制假设，不能写成根因；根因仍写本服务 Pod 网络/链路侧。

## 什么时候跳到别的服务

- 三个调用方（frontend、recommendationservice、checkoutservice）调用本服务的耗时都固定多出同一量级，而本服务自身 received→completed 不慢、Pod Ready、EndpointSlice 有 ready 端点 → **不跳**，结论写本服务 Pod 网络/链路侧。
- 只有 frontend 慢或报错，而 recommendationservice、checkoutservice 调用本服务耗时正常 → 跳到 skill `boutique-frontend`，结论写在 frontend（它自身的超时、连接或重试问题）。
- checkoutservice 报错或变慢，但错误同时涉及 paymentservice、emailservice、currencyservice 等本服务之外的下游，或只有 checkoutservice 侧调用本服务慢、其它调用方正常 → 跳到 skill `boutique-checkoutservice`，结论写在 checkoutservice。
- recommendationservice 报错，且本服务侧无异常（它只调用本服务，报错不等于故障在本服务）→ 跳到 skill `boutique-recommendationservice`，结论写在 recommendationservice。
- 报错指向 cartservice、adservice、paymentservice、currencyservice 等本服务未参与调用的组件 → 不在本 SOP 范围，跳到发起该调用的调用方 skill 判断归属。
- 三个调用方都报错或都变慢、且本服务 Pod 不 Ready 或 EndpointSlice 无端点 → 留在本服务，不要外推。
- 本服务与所有下游均正常，只有入口整体异常（请求未到达、外部访问失败）→ 回 `boutique-frontend`，结论写在入口/用户侧。

## 结论怎么写

写清三要素：对象（具体到 Deployment `productcatalogservice` / Pod 名 / Service / EndpointSlice）、原因、证据。

- 对象异常型：原因如内存超限 OOMKilled、探针端口不符、selector 不匹配、镜像拉取失败；证据写哪条命令的哪段输出，例如 `Last State: OOMKilled`、Events 中的探针失败信息、EndpointSlice 无 ready 端点。
- 无错误型变慢：对象写 `Pod/productcatalogservice`（或对应 Service/EndpointSlice）；原因写“本服务 Pod 网络侧存在固定约 Xms 延迟，应用自身处理耗时未变”；证据必须同时给出三样——(1) 至少两个调用方调用本服务的耗时相对基线的固定增量，(2) 本服务日志 received→completed 仍为毫秒级，(3) 同期调用方对其它下游的调用耗时正常。再补一句放大说明：推荐页对每个推荐商品各查一次、购物车页每件商品一次，串行叠加导致页面 1.5–3s。
- 机制层（tc/netem、CNI、DNS、CPU 限流）在本权限内看不到，末尾写“机制未确认，需节点/网络指标或容器内 `tc qdisc show`、`getent hosts` 计时等权限补充”，但**结论归属仍是本服务**，不要写“未确认”了就不定位，也不要把根因改挂到 frontend/recommendationservice 或笼统的“跨 Pod 链路”。特别禁止在证据已满足上述三样时，把 CPU 限流/节点争抢写成根因或“待确认根因”。
- 未能确认时写“未确认”，并注明缺什么（如指标、节点信息、容器内检查权限），不要把推测写成结论。
