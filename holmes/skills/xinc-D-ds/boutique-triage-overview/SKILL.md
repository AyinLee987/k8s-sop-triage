---
name: boutique-triage-overview
description: Online Boutique（命名空间 boutique）排查总则：服务调用关系、按症状选入口 SOP、跨服务跳转原则、怎么判断故障源头。排查这个命名空间的任何问题时先读。
---

# Online Boutique 排查总则

## 经验条目

以下条目来自过去的排查记录，括号里是来源。

- [E1]（BH01、BH06、BH07、BH14、BH15、CL03）【已证实】适用：用户同时报错和/或报变慢，且错误数、关键业务成功/失败数、延迟分位数与超阈值占比能分别读出。不适用：所有指标同一时刻同向突变——更像单一根因，直接沿调用链查下游。做法：①一次只改一处，改后重采同一窗口的同一组指标（500 数、下单成功/失败数、延迟中位数、90/99 分位、超阈值占比），逐个记“变了/没变”；②错误数归零但延迟不动，说明只解释了报错；延迟归零仍有 5xx 继续查错误；③延迟是否修好，以超阈值占比与中位数是否回到健康时段水平为准：只恢复一条被降级、仍返回 200 的路径（如可选下游副本为 0）时，高端分位数可能明显下降，但超阈值占比若仍高于健康时段，就说明它至多解释部分长尾，不能结案，继续按“慢的都调、快的不调”取公共下游；窗口重采后单个分位数也会自行波动；④只修降级路径时错误数与延迟也可能完全不动——它只是另一个独立问题。
- [E2]（BH02、BH03、BH04、BH05、BH08、BH09、BH11、BH12、BH13、BH15、CL01）【已证实】适用：用户报变慢、无报错，能拿到逐请求耗时；按下游拆开后多出的耗时固定落在某一跳，或一次请求内调用该下游次数越多台阶越多（间歇尾巴）。不适用：不经过任何共同下游的路径一起变慢——改查 frontend 自身与共享基础设施。做法：①按“慢的都调、快的不调”取公共下游；②挑一次只调用一次的请求，比较“调用方发出→下游处理入口”与下游自身处理耗时，前者固定偏移、后者正常即延迟在该 Pod 自身，限额与同类一致、探针通过都不能排除；③读该 Pod 的 CFS 限流周期占比与 CPU 用量/limit，与同命名空间其他 Pod 同窗口对比：若只有它被大幅限流而用量远低于 limit，把它 Deployment 的 limits/requests 与上一版 ReplicaSet 或 last-applied-configuration 逐字段 diff，改回原值后复测同一组延迟分位数；④限流占比与同类一样接近 0 时，才到该 Pod 所在节点对 veth 执行 `tc qdisc show`，非默认队列即来源，恢复默认后复测。判据是限流占比与用量/limit 的对比；探针偶发 `i/o timeout` 在两种来源下都会出现，不能单独区分；提高 limit 只在确认存在限流时才有效，不能当作通用手段。
- [E3]（BH10、BH15、CL06、CL07）【已证实】适用：大量 500/关键操作失败，调用方日志对某下游报连接错误（refused/i/o timeout）或 gRPC `Unimplemented`/`Method not found!`，失败毫秒级且集中在一个调用步。不适用：下游普通 5xx 且耗时正常，或失败随并发升高/日志排队——按该服务 CPU/网络 SOP。做法：①从日志取地址+端口确认目标 Service；②比对 Service 的 targetPort 与容器 containerPort、env 端口、selector 与 Pod 标签；③Endpoints 有地址只说明 selector 匹配，必须读 EndpointSlice 实际指向哪个 Pod：若指向另一个 app 的 Pod（地址与端口都对也会中招），就是该 Service 的 selector 写错，调用方 `*_SERVICE_ADDR` 正确也救不了；④`Unimplemented` 表示请求已到健康 gRPC 服务端但无此方法：先查调用方地址是否指向别的服务，地址正确再查③；⑤修正后复测 500 与关键成功/失败数，未降的原因见 E5。
- [E4]（CL02、CL05）【已证实】适用：用户报变慢、多条路径都慢，候选服务里有自身指标显著异常者（唯一被 CPU 限流、唯一有探针超时或 Warning 事件，或 ReplicaSet 显示其 CPU 限额最近被改小且时间点与故障起点重合），其余候选指标正常、无日志错误。不适用：只有单一候选，或候选日志有连接错误、处理耗时随并发升高——直接按该服务 SOP 查。做法：①别按“谁看起来最异常”定责，先按“慢的都调、快的不调”取公共下游；②对候选一次只改一处（限额、入向队列等），改后重采同一组用户侧延迟指标与改前逐项比：超阈值占比与中位数完全不动，说明该候选与这条慢路径无关，必须继续查其它跳，包括指标全正常、处在慢路径中段或末尾的服务的入向队列；配置改动时间与故障起点重合、且确实产生了限流，都只是“看起来像”，不是定责证据；只有高端分位数下降而中位数与占比不动，才说明改动只解释长尾离散；③只有超阈值占比与中位数一起回到基线，才算该候选被证实。例：唯一被限流的入口服务恢复限额后台阶不变，真台阶在别处。
- [E5]（CL07）【已证实】适用：改完 Service 的 selector、`*_SERVICE_ADDR`、targetPort 这类路由配置后重采指标，500 或关键失败数完全没降、或只降一部分。不适用：改的是 CPU limit、入向 tc 队列这类资源/链路配置——指标不动按 E1/E2/E4 判为该候选无关。做法：①把错误按调用方拆开（页面 500 与下单失败分别统计）；②长连接客户端（gRPC channel、连接池、进程内解析缓存）在连接建立后不会因 Service 改动而改目标，修复只对新连接生效；③逐个重启/回收调用方 Pod，每做一个重采同一组指标，只有该调用方自己的错误清零才说明它已连到新端点；④改动与重建期间的分位数尖峰可能只是一次性重连，判据仍是超阈值占比与中位数是否回到健康时段。例：改回 selector 后失败数不变，重建下单服务后下单失败归零但页面 500 仍在，重建前端后 500 才归零。

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
