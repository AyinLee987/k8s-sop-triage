---
name: boutique-redis-cart
description: Online Boutique 的 redis-cart 排查 SOP：症状涉及 redis-cart，或从调用方（cartservice）查到它时读。下游：无。
---

# redis-cart 排查 SOP

## 经验条目

以下条目来自过去的排查记录，括号里是来源。

- [E1]（BH08）【已证实】页面整体变慢、无 5xx、Pod 全 Running/Ready，且耗时随 cartservice→redis-cart 的往返次数呈固定台阶（GET /cart=1 次 Redis 操作≈285–340ms、POST /cart=2 次≈565–630ms、/cart/checkout=2 次≈580–645ms，而首页其余 12 个 RPC 合计仅 ~30ms、不调 Redis 的 /setCurrency 仍 0ms）时，延时挂在这一跳：到 redis-cart Pod 所在节点 `tc qdisc show dev <veth>` 查 netem 延时/丢包，清除恢复默认队列；本次清除后中位数 313→8ms、>180ms 85.6%→0%，500 与下单失败保持 0。【已排除】cartservice 的额外 env/滚动更新（删 RELEASE_TAG 触发滚动后中位数 313ms、85.6% 不变）与 redis-cart 的 125m CPU 限流（未改配额、未重启 Pod 延迟即消失）；不要按“重启 redis-cart / 加 CPU 限额”处理，先清 veth 上的 tc。
- [E2]（BH13）【已证实】尾部弥散型也可能是 redis-cart 的 veth netem，别只认 E1 的固定台阶：中位数正常（10ms）、仅 ~6% 请求 >180ms、p90 31ms、p99 222ms，慢值集中在「正常耗时 +~200ms」，无 5xx、下单全成功、Pod 全 Ready。因为每个页面渲染都要调 cartservice.GetCart 查购物车数量，慢请求会散布在首页/商品页/购物车/结算各端点（结算里 payment→email 之间空转 ~200ms 就是 EmptyCart）。判别：按端点统计慢请求是否都含至少一次 cartservice→redis 调用；redis-cart 的 Liveness/Readiness 出现 `dial tcp <podIP>:6379: i/o timeout` 是同一 Pod veth 丢包/延时的旁证。下一步到 redis-cart Pod 所在节点 `tc qdisc show dev <veth>` 查 netem 并清除；本次只清 tc 后 >180ms 6.3%→0%、p99 222→38ms、中位数 10→9ms、500 与下单失败保持 0。【已排除】redis-cart 125m CPU 被 CFS 限流、以及需要重建 Pod（升 limit 和重启都没做即恢复）；cartservice 自身 GC/线程池停顿（全程未动 cartservice、其 gRPC 探针零失败）。

<!-- 经验条目结束 -->

适用症状：用户加购/改购物车/结账时失败或报错，或 cartservice 日志中出现连接 redis-cart 失败、超时，指向 `boutique` 命名空间里的 `redis-cart`。

## 信号 → 步骤

| 能观察到的信号 | 去哪 |
| --- | --- |
| cartservice 或 frontend 报连接 redis、超时、EOF，但还没看 redis-cart 本体的状态 | 第 1 步 |
| `redis-cart` 的 Pod 不存在、不是 Running、READY 不是 1/1 | 第 2 步 |
| 事件或容器状态里出现 OOMKilled，或 `restartCount` 在增长 | 第 3 步 |
| 事件里出现 `Liveness probe failed` / `Readiness probe failed` | 第 4 步 |
| Service `redis-cart` 的 EndpointSlice 没有端点，或端点 `ready` 全是 false | 第 5 步 |
| Deployment 期望 1 副本但 Pod Pending / 新建 Pod 迟迟不出现 | 第 6 步 |
| redis-cart 的 Pod、端点、日志、事件全部正常，只有 cart 相关请求报错 | 跳到 skill `boutique-cartservice` |

## 步骤

1. **先确认整体状态，确定下一步方向。**
   看 `kubectl -n boutique get deploy/redis-cart`、`get pods -l app=redis-cart -o wide`、`get svc redis-cart`、`get endpointslice -l kubernetes.io/service-name=redis-cart`。
   确认：Deployment 期望副本是否为 1、可用副本是否为 1；Pod 是否 Running 且 READY 1/1。Deployment 的副本数只有 1，Pod 重启或重建期间 cartservice 对 redis 的访问会全部失败，先把“当前有没有可用 Pod”确认清楚再往下走。

2. **看 Pod 状态、镜像和调度情况。**
   用 `kubectl -n boutique get pod -l app=redis-cart -o yaml` 看 `status.phase`、`containerStatuses`、`conditions`，再用 `kubectl -n boutique get events --sort-by=.lastTimestamp` 过滤 `redis-cart` 相关事件。
   - 容器 `waiting.reason` 为 `ImagePullBackOff`/`ErrImagePull`：镜像 `redis:alpine` 拉取失败，属于本服务镜像问题。
   - Pod 处于 Pending：看事件里是否有调度失败原因（本账号看不到节点，只能依据事件描述记录）。
   - 容器 `running` 但 `ready=false`：转到第 3、4、5 步。

3. **看日志与是否被内存限制杀掉。**
   `kubectl -n boutique logs deploy/redis-cart --tail=200`，有重启时再用 `--previous` 看上一个容器的日志。
   重点确认：容器 `lastState.terminated.reason` 是否为 `OOMKilled`、`restartCount` 是否在增长。该容器 `limits.memory: 256Mi`、`requests.memory: 200Mi`，可用内存余量很小；如果出现 OOMKilled，就可以判定是内存超限导致进程被杀、Pod 重启。日志中与内存、连接数、持久化（RDB/AOF 写盘）相关的报错，具体措辞以实际日志为准，不要预设格式。
   注意：本账号不能进容器，无法用 redis 客户端主动连 6379 验证，只能靠日志、探针结果和 cartservice 侧报错间接判断。

4. **确认探针是不是重启/摘流量的直接原因。**
   readinessProbe 与 livenessProbe 都是对 6379 的 tcpSocket，`periodSeconds: 5`；清单未给出 timeoutSeconds/failureThreshold，先确认实际生效值（按 kubelet 默认理解）。
   在事件里找 `Liveness probe failed` 与 `Readiness probe failed`：
   - liveness 失败 → kubelet 重启容器，表现为第 3 步看到的 `restartCount` 增长，此时先查是进程真的挂了还是资源紧张导致握手超时。
   - readiness 失败 → Pod 被移出 Service 端点，第 5 步会看到端点 `ready=false`，cartservice 直接连不上。
   若探针失败但日志显示 redis 启动正常，考虑启动慢或 CPU 被限（`limits.cpu: 125m`、`requests.cpu: 70m`）导致的响应延迟，不要凭猜测下结论，先记录探针事件原文。

5. **确认 Service 与 EndpointSlice 是否把流量导到了 Pod。**
   `kubectl -n boutique get svc redis-cart -o yaml`：确认 `selector` 为 `app: redis-cart`、端口 `6379` / `targetPort: 6379`。
   `kubectl -n boutique get endpointslice -l kubernetes.io/service-name=redis-cart -o yaml`：看 `endpoints[].conditions.ready` 与端口是否为 6379。
   - 端点为空：先回第 2 步确认 Pod 是否存在、回第 4 步确认是否 readiness 失败；若 Pod Running 且 READY 1/1 却仍无端点，重点核对 Service selector 与 Pod 标签 `app: redis-cart` 是否一致。
   - 端点存在但 `ready=false`：说明 readiness 没过，结论仍落在第 4 步。

6. **确认配额与副本是否影响重建。**
   `kubectl -n boutique get resourcequota`，再看 `kubectl -n boutique get rs -l app=redis-cart` 与 Deployment 的 conditions/事件。
   若 Pod 被删除或重建后一直不出现、事件里出现配额相关拒绝，说明是 ResourceQuota 耗尽导致无法建 Pod；若 ReplicaSet 刚发生换代，说明是发布/重启动作，结合第 2–4 步判断新 Pod 是否已就绪。单副本下单次重建就是一次完整中断。

7. **形成结论并决定归属。**
   把上面收集到的对象名、状态、事件原文、日志关键行汇总：问题落在镜像、资源（OOM）、探针、Service/端点、配额、发布动作中的哪一类。若 redis-cart 侧六项全部正常，走下一节跳到调用方。

## 什么时候跳到别的服务

- 看到 `redis-cart` 的 Pod Ready、EndpointSlice 端点 `ready=true`、容器无重启、日志无内存/持久化报错，而 cartservice 仍报连接失败、超时或 gRPC 错误 → 跳到 skill `boutique-cartservice`，结论写在 cartservice 侧（连接配置、地址、超时、自身资源）。
- 看到 frontend 报错但 cartservice 侧对 redis-cart 的调用无异常 → 回到调用方链路，先按 `boutique-cartservice` 确认，再由其判断是否继续上跳 `boutique-frontend`。
- 看到多个服务同时报错、跨服务普遍超时，且 redis-cart 自身指标性证据（重启、OOM、探针失败、端点缺失）都不成立 → 不要在本服务继续深挖，回到调用方 cartservice 的 skill 里排查其上游依赖；本服务无下游可跳。
- 本服务不调用任何下游服务，因此不存在向更下游跳转的分支；任何“redis-cart 正常但 cart 功能坏”的情况，结论一律回到调用方 cartservice。

## 结论怎么写

结论必须写清三件事：具体对象（`deploy/redis-cart`、某个 Pod 名、`svc/redis-cart` 或对应 EndpointSlice）、原因分类（如 OOMKilled 超出 256Mi limit、readiness 探针连续失败、Service selector 不匹配、ResourceQuota 拒绝创建、镜像拉取失败）、支撑证据（事件原文、`restartCount`/`lastState.terminated.reason` 字段值、日志关键行、端点 `ready=false` 或端点为空）。
若证据表明 redis-cart 侧全部正常，结论写“redis-cart 侧无异常（Pod Ready、端点 ready=true、无重启）”，并把责任归属明确写到 cartservice。
