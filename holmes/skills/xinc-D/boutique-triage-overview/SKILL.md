---
name: boutique-triage-overview
description: Online Boutique（命名空间 boutique）排查总则：服务调用关系、按症状选入口 SOP、跨服务跳转原则、怎么判断故障源头。排查这个命名空间的任何问题时先读。
---

# Online Boutique 排查总则

## 经验条目

以下条目来自过去的排查记录，括号里是来源。

- [E1]（BH01、BH02、BH03、BH04、BH05、BH06、BH11、BH12）【已证实】多个症状不要并成一个根因，按症状分组验证，每修一项重看全部用户侧数字（曾见 500 修好后中位数不变；扩回被缩容服务后慢占比不变）。定位“页面变慢、无 5xx、Pod 健康”：把各 handler 耗时或慢请求占比与它调用某下游的次数对照，两种形态都指向某下游 Pod 的 veth netem：(a) 整体台阶——耗时随调用次数以 200–300ms 台阶增长；(b) 尾部弥散——中位数正常、约 10% 请求 >180ms、p90/p99 明显抬高，慢占比随调用次数上升（0 次 0%、1 次 ~5%、6 次 22–28%），同一请求可叠加两跳。除 productcatalogservice 外，被每页调用 1–12 次的 currencyservice 也是常见源头：本次首页最慢、商品页偶发，清其 veth 后 >180ms 9.6%→0%、p90 121→14ms、p99 426→42ms。别只看中位数，看 p90/p99 与 >180ms 占比；下一步到嫌疑 Pod 所在节点 `tc qdisc show dev <veth>` 查 netem，逐个清除、每次复测。耗时像 100ms 整数倍、偶发探针超时都不等于 CFS 限流，先清 tc 用数字验证，别先改 CPU limit。
- [E2]（BH07、BH10）【已证实】“下单全失败/500 占比高”但延迟完全正常（中位数 6–9ms、几乎无 >180ms）时，不要按“耗时台阶→veth netem”查，这是配置/连接类错误：从 frontend 错误日志定位失败卡在下游哪个调用（`could not retrieve cart`→cartservice、`failed to charge card`→paymentservice），再核对三处：Service selector 是否匹配 Pod 标签、targetPort 是否等于容器实际监听端口、`<SVC>_SERVICE_ADDR` 的地址端口（见 checkoutservice 条目）。注意 `connect: connection refused` 既可能是 Endpoints 为空（selector 错），也可能是 targetPort 指向无人监听端口——后者 Endpoints 非空、端口却是错的，必须把 `kubectl get endpoints` 里的端口与容器 containerPort/Pod 启动日志的监听口逐个比对。判别根因靠逐步修+每次复测：先修无关异常（扩回被缩到 0 的 recommendationservice/adservice），500 与下单失败不变说明它是被降级处理的非根因——本次扩回推荐服务后仍 500 8 个、改对 SHIPPING_SERVICE_ADDR 后 500→0、下单 8/8 成功；另一次改 adservice selector 后 500 仍 169 个、下单仍全失败、中位数 1ms 不变，改对 cartservice targetPort 7071→7070 后 500→0、下单 7/7 成功。
- [E3]（BH13、BH14）【已证实】用户侧只是偶发尾部延迟（中位数正常、~6–8% >180ms、p90 数十 ms、p99 200ms 级、无 5xx、下单成功）而某 Pod 的 Liveness/Readiness 报连接超时（tcpSocket 的 `dial tcp <podIP>:<port>: i/o timeout` 或 grpc 的 `failed to connect service <podIP>:<port> within 1s`）时，把这个「命名空间里唯一探针失败的 Pod」当作嫌疑源头：kubelet 直连该 Pod 探针、绕开调用方，说明问题在该 Pod 的 veth 而非整节点或调用方。下一步到该 Pod 所在节点 `tc qdisc show dev <veth>` 查 netem 并清除恢复默认队列；本次清 redis-cart 的 veth 后探针失败与 >180ms 6.3%→0%、p99 222→38ms 同时消失，同类故障清 cartservice 的 veth 后 >180ms 7.5%→0%。【已排除】CPU/内存不足（升 CPU、重建 Pod 均未执行即恢复）；同时存在的 replicas=0/connection refused 是独立配置问题，修它不会改变延迟数字。【未解决】重启 Pod 能否清除延迟未被验证。

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
