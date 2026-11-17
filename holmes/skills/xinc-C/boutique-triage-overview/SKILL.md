---
name: boutique-triage-overview
description: Online Boutique（命名空间 boutique）排查总则：服务调用关系、按症状选入口 SOP、跨服务跳转原则、怎么判断故障源头。排查这个命名空间的任何问题时先读。
---

# Online Boutique 排查总则

## 经验条目

以下条目来自过去的排查记录，括号里是来源。

- [E1]（BH01、BH02、BH03、BH04、BH05、BH06、BH08）【已证实】多条症状拆成独立链路分别定位；慢依赖看调用方 took_ms 与调用该依赖次数是否线性（斜率≈单次延迟）；协议类下游要按“每条命令”而非每次 RPC 拆分（Redis 1 条≈285ms、2 条≈570ms，而 gRPC 固定开销仅 15–20ms）。判断方向先确认被调方日志打在 handler 入口还是结尾，再配对“请求开始→被调方入口”与“入口→调用方完成”：入口恒晚固定值、入口→完成仅几 ms，则延迟在去程、响应正常。被注入延迟的目的服务名单每案不同，可能同时多个且数值不同，须逐个实测，别沿用上次名单。同端口可一慢一快（payment/shipping 均 50051）→ 排除按端口，指向按目的 Pod/标签的规则。重建目标 Pod 与全部客户端 Pod 后数值完全不变 → 排除 Pod 内状态与 netns、按旧 IP 匹配、DNS/连接缓存；提 CPU 上限仍无变化则再排除 CPU 节流。故障前几十秒的滚动发布/手动 env 改动不是根因（新 Pod Ready 后仍出现过正常快窗口）。探针超时 1s，200–300ms 下 Pod 仍 Ready、0 重启。
- [E2]（BH07、BH10）【已证实】用户侧延迟完全正常（中位个位数 ms、0 个 >180ms）却大量 500、某业务动作 100% 失败时，不查延迟注入（无需 tc/netem、扩缩容、调 CPU）：抽出 500 日志里的 `dial tcp <IP>:<port>: connect: connection refused`，把 Service port/targetPort、Endpoint 地址里的端口、Pod containerPort 与 Pod 启动日志的实际监听端口（如 `Now listening on: http://[::]:7070`）逐一对齐，再查该下游 replicas 是否为 0、endpoints 是否 null——Endpoints 有就绪地址不代表端口对。按“是否必经步骤”分：必经步骤失败→业务 100% 失败、先修；可降级依赖（推荐、广告）失败被调用方 catch、页面仍 200，别当根因。改完 Service 必须取完全落在改动之后的整个用户侧窗口核对：本次改对 targetPort 后下单 2/2 失败→8/8 成功，但 500 仍 79/222、最终用户侧未恢复 → 残
- [E3]（BH11、BH12、BH13、BH14、BH15）【已排除】（BH11–BH14）用户侧中位 ~10ms、下单成功，但数 %–20% 请求 >180ms、慢值成 ~200ms 整数倍簇（200/400/800，TCP RTO 形状）时：不是排队/GC/CPU，别抬 CPU/扩副本/改 env。Pod 级重启全部无效（cartservice、redis-cart、recommendationservice 0→1、productcatalogservice、currencyservice、frontend 新 netns，>180ms 5.8%→5.5%→5.8%→9.3%→23.1%→21.9%，p99 227→404→630→653ms），排除 Pod 内状态、CPU 节流、单个下游、调用方 Pod 出口。两类要分开：①探针超时跟着多个 Deployment 走、不同 hop 的请求在同一微秒级一起恢复 → 节点级同步停顿；②探针失败持续集中在某一个工作负载（跨 Pod/IP 重建后数秒内复现）、其他服务探针与跨 Pod 调用干净、每个慢请求都含该依赖的调用 → 该工作负载网络路径上的丢包，转节点侧 tc qdisc/nstat/s

<!-- 经验条目结束 -->

## 1. 权限与证据边界

- 只在命名空间 `boutique` 内排查，使用只读权限。
- 可查看：Pod、Pod 日志、事件、Service、EndpointSlice、Deployment、ReplicaSet、ConfigMap、ResourceQuota。
- 不可用：进入容器执行命令、查看节点、查看监控指标；系统没有 CPU、内存、延迟指标。
- 结论必须来自上述可见证据。无法验证的内容写成“先确认……”或“现有权限内无法确认”，不要根据服务名、调用关系或常见经验直接下根因。
- 不要编造日志格式、接口、配置项；日志字段含义先确认。

## 2. 调用关系

- 用户从 `frontend` 进入。
- `loadgenerator` 持续产生访问流量；其具体访问目标先确认，不纳入下列服务调用图，也不假设存在 `boutique-loadgenerator`。
- `frontend` -> `productcatalogservice`、`currencyservice`、`cartservice`、`recommendationservice`、`shippingservice`、`checkoutservice`、`adservice`
- `checkoutservice` -> `productcatalogservice`、`shippingservice`、`paymentservice`、`emailservice`、`currencyservice`、`cartservice`
- `recommendationservice` -> `productcatalogservice`
- `cartservice` -> `redis-cart`
- 不调用其他服务：`emailservice`、`paymentservice`、`productcatalogservice`、`currencyservice`、`shippingservice`、`adservice`、`redis-cart`

调用关系只表示依赖方向，不等于故障传播方向。

## 3. 入口 SOP 选择

先固定用户症状：发生时间、频率、影响的功能/操作、是否持续、是否与 `loadgenerator` 流量相关。不要先假定某个服务故障。

默认规则：

- 用户可见问题且无法明确指向某个后端服务：从 `boutique-frontend` 进入。
- 症状或已有证据明确指向某个服务及其对应操作：可直接进入该服务 SOP；仍要按第 4 节确认证据，不要跳过入口症状。
- 只有 `loadgenerator` 报错：先确认其访问目标和错误内容；若指向 `frontend` 或用户访问路径，从 `boutique-frontend` 进入。不要假设存在 `loadgenerator` 的 SOP。
- 不明确时回退到 `boutique-frontend`。

| 症状/已有证据明确指向 | 入口 skill |
|---|---|
| `frontend` 页面、入口、多个功能同时异常，或无法判断后端 | `boutique-frontend` |
| 商品目录、商品信息 | `boutique-productcatalogservice` |
| 币种、换算 | `boutique-currencyservice` |
| 购物车操作 | `boutique-cartservice` |
| 推荐内容 | `boutique-recommendationservice` |
| 运费、配送 | `boutique-shippingservice` |
| 下单、结账编排 | `boutique-checkoutservice` |
| 支付 | `boutique-paymentservice` |
| 邮件通知 | `boutique-emailservice` |
| 广告 | `boutique-adservice` |
| 已有证据指向购物车存储 `redis-cart`，或从 `cartservice` SOP 转入 | `boutique-redis-cart` |

## 4. 跨服务跳转原则

1. 只沿调用方向向下游跳，且必须带当前服务侧证据。可跳的证据包括：当前服务的日志、事件或状态显示对某个下游的调用失败、超时、连接失败、返回异常，并能定位到具体下游和操作；或当前服务自身已排除，请求路径明确只可能落在下游。仅凭“它是下游”“它也可能异常”不跳。
2. 跳转前记录：时间、当前服务对象、症状、指向下游的证据、已排除项。到下游后先核对同一时间窗、同一症状是否出现；对不上则回到当前服务，不能把无关异常归因给下游。
3. 反向查看调用方，只在当前服务有证据表明请求来源异常、请求内容异常或流量异常时进行；否则停在当前服务。
4. 结论归属：根因结论写在直接证据所在的最小服务上。调用方只写“受影响/依赖疑似异常”，不替下游下结论。若根因是调用方发出异常请求导致下游报错，结论写在调用方。多个服务受影响时，在源头服务写根因，在其他服务写影响并引用源头结论。
5. 停止条件：已定位到最小责任服务并有直接证据；或可见证据均不支持继续；或下一步需要进入容器、查看节点、查看监控指标、访问其他命名空间等无权限操作。停止时写明已排除项、未确认项、所需权限或补充信息，不要猜。

## 5. 多个服务同时异常时判断源头

- 先列出受影响服务、症状和最早异常时间，再按调用图找共同下游或共同依赖。共同依赖只是候选，不是根因。
- 看时序和错误形状：下游先异常、多个上游随后报同类依赖错误，优先查下游；上游先异常且下游日志或事件显示来自上游的异常请求/流量，优先查上游；只是同时异常不能定源。日志字段含义先确认。
- 对候选源头查直接证据：Pod 状态、日志、事件、Service、EndpointSlice、Deployment、ReplicaSet、ConfigMap、ResourceQuota。候选源头自身没有直接异常证据时，不把共同依赖写成源头，回到调用方或入口继续。
- 多个互不依赖分支同时异常：考虑共同入口 `frontend`、共同流量源 `loadgenerator`（先确认其目标和错误）、命名空间级限制如 ResourceQuota 等；仍须有可见证据。无法证明共同原因时，按多个独立故障记录，不要强行归并。
- 若所有下游均无直接异常，而入口持续报依赖错误，停止向下游跳转，回到入口检查其自身可见状态、端点和服务配置；仍无法确认则按权限边界交接。
