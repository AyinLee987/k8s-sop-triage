---
name: boutique-triage-overview
description: Online Boutique（命名空间 boutique）排查总则：服务调用关系、按症状选入口 SOP、跨服务跳转原则、怎么判断故障源头。排查这个命名空间的任何问题时先读。
---

# Online Boutique 排查总则

## 经验条目

以下条目来自过去的排查记录，括号里是来源。

- [E1]（BH01、BH02、BH03、BH04）先按症状分类：同时报“慢”和“下单失败”通常为两个独立故障，分别定位。失败类：看错误语义与耗时——首次数秒超时后 2–3ms 快速失败=某下游 `*_SERVICE_ADDR` 端口/地址配错（对比当前与上一版 ReplicaSet env、Service port 与容器实际监听端口）；connection refused=Service 无 Endpoints/selector 不匹配，逐个 `get endpoints`。延迟类：看耗时是否与“调用次数”成比例。若多类页面/多个下游同时呈同一固定台阶（每次调用 +200~300ms），而同一窗口内另一组调用仍 1–2.5ms（如 checkout↔shipping/payment/email），说明台阶附着在一批 Pod 上，属运行期延迟注入，不是节点 CPU、全局网络或单服务 CPU 限流（限流应有 cgroup throttle 证据且随负载波动；Pod 健康、配置未变、无 error 也支持此判）。页面耗时≈该页调用受影响服务的次数×台阶，可据此反推受影响集合。也可用时间戳对齐：下游日志“收到请求”晚于调用方发起、而该下游处理仅毫秒级→延迟在到达前。处置：重建这些 Pod；重建后数字完全不变=集群侧按 label 重放的注入（chaos/sidecar/tc），转运维，不要反复重启。以 Pod 创建/版本切换时间为界对比前后基线。
- [E2]（BH05、BH06、BH09）『整站或部分页面变慢、0 个 500、Pod 全 Running/0 重启、env/资源/镜像未变』时按四步收敛，不要先调 CPU limit：①前端日志按路径分桶耗时，用最快路径反证——如 POST /cart 仅 1–2ms，即 productcatalog/cart/redis 全链健康；②页面耗时≈该页调用嫌疑服务的次数×台阶，据此反推唯一嫌疑服务（只有 /cart、/cart/checkout 慢而 /、/product 正常→shippingservice，checkout 约 2×台阶是同一下游 RPC 被调两次）；③复核该服务 Deployment 的 env、资源、镜像、Service targetPort、Endpoints；④时间戳对齐：调用方发出时刻 vs 被查服务 handler 入口日志恒定晚约一个台阶而处理瞬时完成=入向网络延迟（重建该 Pod，无效则转运维查 veth 上 tc/netem），延迟落在响应返回侧则为出向。注入延迟通常小于探针超时，Pod 仍 Ready、无 Unhealthy 事件不能排除网络注入。同一窗口内毫秒级 connection refused（如副本被缩到 0、selector 写错致 Endpoints 为空）只造成降级，按错误语义单独修，不并入延迟归因。
- [E3]（BH07、BH10）同一秒出现 `Scaled up replica set <新> from 0 to 1` 与旧 RS `Scaled down ... from 1 to 0`，通常就是变更注入时刻：以该时间点为准横向检查所有 Deployment 的 replicas、env、镜像，以及所有 Service 的 selector 与 port→targetPort——同一次注入常同时改多个对象（如一个 Service 端口写错 + 另一个 selector 写错）。connection refused 只有两种来源：selector 不匹配致 Endpoints 为空（subsets: null），或 Endpoints 有地址但 targetPort≠容器实际监听端口（对照 containerPort 与 Pod 启动日志 `Now listening on: ...:7070`）。是否算根因取决于调用方能否降级：cartservice/productcatalog 这类每页必经的挂了→大量 500、下单全失败但响应仅 1–3ms（不是资源/延迟问题）；ad、推荐、邮件可降级→页面仍 200 只内容缺失，只单独恢复，不并入延迟/失败归因。修复后按该时刻前后对比：500 归零、下单成功率恢复、对应报错日志消失，延迟分位数不应变差。
- [E4]（BH08）耗时台阶看似卡在某下游、该服务入向指标又不足以解释时，把口径下沉到“下游操作条数”：同一页 1 条缓存/DB 命令≈X ms、2 条≈2X ms，而无该跳的接口仍 0–2ms，则台阶固定在该跳每次往返上（若 2 条接口只有≈1.5X，才是延迟附着在中间 Pod 的整体网络路径）。同时别被“最近变更”误导：某 Deployment 刚 rollout 或 env 与 last-applied 不一致时看时间线——新 Pod 就绪后若曾连续数十秒正常服务，即可排除该变更，回到台阶定位；节点级 CPU/throttle 指标不可得时，用同窗口其他服务毫秒级间接排除节点问题。
- [E5]（BH11、BH12、BH13、BH14、BH15）尾部延迟呈“概率型”：中位数正常、p90/p99 抬高、约 4–9%（也可能到 20%）请求超阈值、无 5xx、Pod 全 Ready、env/资源/镜像未变。不要按 E2 找“每次调用都加台阶的入向延迟”。先看慢值分布定机制：①慢值是基线+100ms 整数倍、成簇、随机 0–100ms→单线程/多模块且 CPU limit 偏低的 Pod 被 CFS 冻结（查 nr_throttled；无权限就只把嫌疑服务的 limit 提到 1）；②慢值量化在 ~200ms（400/600ms）整数倍、各并发请求起跑不同却同一瞬间返回→TCP 重传，问题在被调 Pod 的网络路径；③看本 Pod 探针，`i/o timeout` 或 `timeout: failed to connect service <podIP>:<port> within 1s` 都指向该 Pod 自身的网络或停顿（探针直连 Pod、绕开调用方；同窗口只有它异常即可排除节点级丢包），据此确定是哪个 Pod。确定后一次只重启/修改这一个对象并复测 >180ms 占比，不要同时重启多个候选服务做“验证”，否则无法判断原因，下游日志里的孤立的数百毫秒空档只能当备选证据。用只含单次下游往返的干净区间做严格隔离。

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
