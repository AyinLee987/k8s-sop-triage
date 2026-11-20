---
name: boutique-recommendationservice
description: Online Boutique 的 recommendationservice 排查 SOP：症状涉及 recommendationservice，或从调用方（frontend）查到它时读。下游：productcatalogservice。
---

# recommendationservice 排查 SOP

## 经验条目

以下条目来自过去的排查记录，括号里是来源。

- [E1]（BH03）【已证实】页面变慢、/product/* 与 /cart 页稳定 ~400ms，而首页、POST /cart、/_healthz 仍是 0–47ms 时，把范围锁到 recommendationservice：对齐前端日志，可见请求发出后 +399~435ms 才出现 [Recv ListRecommendations]，而其处理器返回后 4ms 前端即完成，说明延迟全部在入方向。下一步到该 Pod 所在节点执行 `tc qdisc show dev <veth>`，检查 netem 延时/丢包规则，清除后恢复默认队列；本次清除后中位数 407ms→8ms、>180ms 占比 67.4%→0%，500 和下单失败保持 0。 【已排除】该服务处理慢或 CPU 200m 限额：处理段仅 4ms、limit 与 last-applied 一致、全程未改配额延迟即消失。 【未解决】“重建 Pod 清除延迟”从未被验证——本次是清 tc 规则治好的，别把重启 Pod 当已验证手段。
- [E2]（BH04）【已排除】ListRecommendations 变慢不等于源头在本服务：它内部还会调 productcatalogservice.ListProducts，上游 productcatalogservice 的 veth 被注入 ~300ms 延时会让它的耗时同步抬升。判别方法：看 productcatalogservice 的 GetProduct/ListProducts 是否也是 ~300ms/次、其调用方是否同样呈台阶；是则先清 productcatalogservice 的 tc 规则再复测。本次只清 productcatalogservice、redis-cart 的 veth、完全没动本服务，商品页即从 ~2400ms 回落到 10ms 级、0% 请求超 180ms；只有上游正常而本服务自身仍慢时，才按 E1 查自己的 veth。
- [E3]（BH07、BH14）[E3]（BH07、本次）【已排除】商品页/购物车页仍 200、但每个页面都打 `failed to get product recommendations ... dial tcp <ClusterIP>:8080: connect: connection refused` 时，查 Deployment replicas（可能被缩到 0）与 `kubectl get endpoints recommendationservice`（subsets 为空），扩回 1 可消除该报错、恢复推荐位。但它是独立问题：上次扩回后 500 仍 8、下单仍全失败、中位数 8ms 不变；本次扩回后尾部延迟不降反升（>180ms 7.5%→15.3%、p90 217ms、p99 436ms、下单失败仍 0），随后清 cartservice 的 veth 才归零。判断恢复要看推荐请求是否成功/该 warning 是否消失，不看前端延迟。

<!-- 经验条目结束 -->

适用症状：frontend 推荐内容报错或为空，或 recommendationservice 的 Pod 不 Ready、重启、Service 无可用 endpoint。

## 信号 → 步骤

| 信号 | 动作 |
|---|---|
| `Deployment/recommendationservice` AVAILABLE 为 0/1，或 Pod 不存在/非 Running | 第 1 步 |
| Pod `Pending` | 第 1 步，查事件和 ResourceQuota |
| Pod `ImagePullBackOff` / `ErrImagePull` | 第 2 步 |
| Pod `CrashLoopBackOff`、频繁重启、`OOMKilled` | 第 1、3 步，查事件和日志 |
| Pod Ready=false，事件中有 probe failed | 第 4 步 |
| `Service/recommendationservice` 对应 EndpointSlice 无 ready endpoint | 第 5 步 |
| recommendationservice 日志出现连接 `productcatalogservice:3550` 失败/超时等实际错误 | 第 6 步；下游异常跳 skill `boutique-productcatalogservice` |
| recommendationservice 自身 Ready、EndpointSlice 有 ready endpoint，但 frontend 报调用失败 | 第 7 步；跳 skill `boutique-frontend` |

## 步骤

1. 先确认影响面：查看 `Deployment/recommendationservice`、其 ReplicaSet、Pod 列表、Pod 状态、重启次数、Ready 条件和事件；查看命名空间内 ResourceQuota。该 Deployment 只有 1 个副本，任何 Pod 不 Ready 都会导致无可用实例。Pod `Pending` 时先看事件中的调度失败/配额不足原因；不要归因节点，因为当前权限不能看节点。若 Deployment/Pod 状态正常，继续第 5、6 步；若异常，按事件进入对应步骤。

2. 查镜像和启动失败：确认实际 Deployment 的镜像是否为 `us-central1-docker.pkg.dev/google-samples/microservices-demo/recommendationservice:v0.10.3`。看 Pod 事件是否有 `ImagePullBackOff`、`ErrImagePull`，看当前及上一次容器日志中的实际启动错误；不要预设日志格式。镜像不一致属发布配置问题；镜像拉取失败时记录事件，并由有权限的人确认镜像是否存在及拉取权限。

3. 查资源限制：对照清单确认 requests 为 cpu `100m`、memory `220Mi`，limits 为 cpu `200m`、memory `450Mi`。查看 Pod 事件是否有 `OOMKilled`、`FailedScheduling`、ResourceQuota 限制相关事件。没有监控指标，不查 CPU/内存使用率；若没有 OOMKilled、调度失败或配额证据，不要仅凭延迟/慢归因资源不足。

4. 查探针和端口配置：确认实际 Deployment 容器端口为 `8080`，env `PORT=8080`，readinessProbe/livenessProbe 为 grpc `8080` 且 `periodSeconds: 5`。查看 Pod Ready 条件和事件中的 probe failed。配置与清单不一致属本服务配置问题。不能进容器执行命令，不能 netstat；不要臆断进程监听状态，先以配置、事件和日志实际内容为准。

5. 查本服务 Service 和 EndpointSlice：查看 `Service/recommendationservice` 的 selector 是否为 `app: recommendationservice`、端口是否为 grpc `8080`、targetPort 是否为 `8080`；查看对应 EndpointSlice 是否有 ready endpoint 指向 Pod IP:8080。无 ready endpoint 时，先确认 Pod 是否 Ready、Pod 实际 labels 是否匹配 Service selector、端口是否匹配。selector、端口或 targetPort 不一致属本服务 Service/Deployment 配置问题。

6. 查下游 productcatalogservice：确认 env `PRODUCT_CATALOG_SERVICE_ADDR` 实际值为 `productcatalogservice:3550`。查看 recommendationservice 日志中与调用 productcatalogservice 相关的实际错误，不预设日志格式。只读权限下可看 `Service/productcatalogservice`、其 EndpointSlice 和相关 Pod 状态/事件/日志，确认下游是否有 ready endpoint。地址配置错误属本服务配置问题；若地址正确但下游无 ready endpoint，或 recommendationservice 日志持续显示连接下游失败，跳 skill `boutique-productcatalogservice`。

7. 查调用方 frontend：若 recommendationservice 的 Pod Ready、Service EndpointSlice 有 ready endpoint、日志无自身启动/探针/下游连接错误，但 frontend 日志或事件显示调用 recommendationservice 失败，查看 frontend 侧实际错误和其调用的服务地址配置。此时问题可能在调用方或调用路径，跳 skill `boutique-frontend`。恢复验证只看 Pod Ready、EndpointSlice ready、相关事件和日志，不使用监控指标。

## 什么时候跳到别的服务

- recommendationservice 日志显示连接 `productcatalogservice:3550` 失败/超时/不可用，且 `Service/productcatalogservice` 或其 EndpointSlice 无 ready endpoint、或 productcatalogservice Pod 不 Ready → 跳 skill `boutique-productcatalogservice`，结论写在 productcatalogservice。
- `PRODUCT_CATALOG_SERVICE_ADDR` 实际值不是 `productcatalogservice:3550`，或 recommendationservice 自身镜像、资源、探针、Service selector/端口异常 → 不跳，结论写在 recommendationservice。
- recommendationservice 自身 Ready、EndpointSlice 有 ready endpoint、日志无自身和下游错误，但 frontend 报调用 recommendationservice 失败 → 回到调用方，跳 skill `boutique-frontend`，结论写在 frontend/调用路径，并附 recommendationservice 正常证据。
- frontend 报错同时 recommendationservice 自身 Pod 不 Ready 或 EndpointSlice 无 ready endpoint → 先按本服务步骤定位，结论写在 recommendationservice，不要直接归因 frontend。

## 结论怎么写

结论必须包含具体对象：`Deployment/recommendationservice`、具体 Pod、`Service/recommendationservice`、对应 EndpointSlice，或下游 `Service/productcatalogservice`/其 Pod；调用方问题写 frontend 侧对象。  
原因写可验证项：镜像或配置字段不一致、资源限制导致 OOMKilled/FailedScheduling、探针失败、selector/端口不匹配、下游无 ready endpoint 等。  
证据写具体事件、日志实际错误或字段值，例如 `OOMKilled`、probe failed 事件、EndpointSlice 无 ready endpoint、`PRODUCT_CATALOG_SERVICE_ADDR` 实际值；无证据不要猜。
