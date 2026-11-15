---
name: boutique-redis-cart
description: Online Boutique 的 redis-cart 排查 SOP：症状涉及 redis-cart，或从调用方（cartservice）查到它时读。下游：无。
---

# redis-cart 排查 SOP

适用症状：用户加购/改购物车/结账时失败或报错，或 cartservice 日志中出现连接 redis-cart 失败、超时，指向 `boutique` 命名空间里的 `redis-cart`；也适用于商城页面/购物车/结账明显变慢但无报错、所有 Pod 看似正常、慢点与 Redis 往返次数相关的情况。

## 信号 → 步骤

| 能观察到的信号 | 去哪 |
| --- | --- |
| cartservice 或 frontend 报连接 redis、超时、EOF，但还没看 redis-cart 本体的状态 | 第 1 步 |
| `redis-cart` 的 Pod 不存在、不是 Running、READY 不是 1/1 | 第 2 步 |
| 事件或容器状态里出现 OOMKilled，或 `restartCount` 在增长 | 第 3 步 |
| 事件里出现 `Liveness probe failed` / `Readiness probe failed` | 第 4 步 |
| Service `redis-cart` 的 EndpointSlice 没有端点，或端点 `ready` 全是 false | 第 5 步 |
| Deployment 期望 1 副本但 Pod Pending / 新建 Pod 迟迟不出现 | 第 6 步 |
| 页面/购物车/结账明显变慢但无报错，所有 Pod Running/Ready、restartCount 稳定、事件为空，redis-cart Pod/Endpoint/探针/日志均未见异常 | 第 7 步；事件为空不等于无故障，必须做跨调用方耗时与 Redis 往返次数比对 |
| cartservice 日志无 error/timeout/exception，但调用方页面耗时与 Redis 往返次数成正比（1 次往返约多 300ms，2 次往返约多 600ms），frontend 到 cartservice 这一跳本身不慢，其它下游毫秒级 | 第 7 步，结论写 redis-cart Pod 网络/链路侧固定延迟 |
| 调用方只报慢不报错，redis-cart 对象正常且无 OOM/探针/端点/配额证据，同时有人提出 CPU 限流/节点争抢 | 第 7 步；无直接限流指标时不得把 CPU 限流写成根因或主结论 |
| redis-cart 的 Pod、端点、日志、事件全部正常，只有 cart 相关请求报错，且不存在“慢而不报错/固定增量”现象 | 跳到 skill `boutique-cartservice` |

## 步骤

1. **先确认整体状态，确定下一步方向。** 看 `kubectl -n boutique get deploy/redis-cart`、`get pods -l app=redis-cart -o wide`、`get svc redis-cart`、`get endpointslice -l kubernetes.io/service-name=redis-cart`。确认：Deployment 期望副本是否为 1、可用副本是否为 1；Pod 是否 Running 且 READY 1/1。Deployment 的副本数只有 1，Pod 重启或重建期间 cartservice 对 redis 的访问会全部失败，先把“当前有没有可用 Pod”确认清楚再往下走。若用户只是页面/购物车/结账变慢但无报错，先记录变慢拐点时间窗，并准备第 7 步的跨服务时间戳对齐。

2. **看 Pod 状态、镜像和调度情况。** 用 `kubectl -n boutique get pod -l app=redis-cart -o yaml` 看 `status.phase`、`containerStatuses`、`conditions`，再用 `kubectl -n boutique get events --sort-by=.lastTimestamp` 过滤 `redis-cart` 相关事件。
   - 容器 `waiting.reason` 为 `ImagePullBackOff`/`ErrImagePull`：镜像 `redis:alpine` 拉取失败，属于本服务镜像问题。
   - Pod 处于 Pending：看事件里是否有调度失败原因（本账号看不到节点，只能依据事件描述记录）。
   - 容器 `running` 但 `ready=false`：转到第 3、4、5 步。

3. **看日志与是否被内存限制杀掉。** `kubectl -n boutique logs deploy/redis-cart --tail=200`，有重启时再用 `--previous` 看上一个容器的日志。重点确认：容器 `lastState.terminated.reason` 是否为 `OOMKilled`、`restartCount` 是否在增长。该容器 `limits.memory: 256Mi`、`requests.memory: 200Mi`，可用内存余量很小；如果出现 OOMKilled，就可以判定是内存超限导致进程被杀、Pod 重启。日志中与内存、连接数、持久化（RDB/AOF 写盘）相关的报错，具体措辞以实际日志为准，不要预设格式。注意：本账号不能进容器，无法用 redis 客户端主动连 6379 验证，只能靠日志、探针结果和 cartservice 侧报错间接判断。无 OOM、无探针失败、日志正常不等于无网络链路问题，慢而不报错时继续第 7 步。

4. **确认探针是不是重启/摘流量的直接原因。** readinessProbe 与 livenessProbe 都是对 6379 的 tcpSocket，`periodSeconds: 5`；清单未给出 timeoutSeconds/failureThreshold，先确认实际生效值（按 kubelet 默认理解）。在事件里找 `Liveness probe failed` 与 `Readiness probe failed`：
   - liveness 失败 → kubelet 重启容器，表现为第 3 步看到的 `restartCount` 增长，此时先查是进程真的挂了还是资源紧张导致握手超时。
   - readiness 失败 → Pod 被移出 Service 端点，第 5 步会看到端点 `ready=false`，cartservice 直接连不上。
   若探针失败但日志显示 redis 启动正常，考虑启动慢或 CPU 被限（`limits.cpu: 125m`、`requests.cpu: 70m`）导致的响应延迟，不要凭猜测下结论，先记录探针事件原文。探针正常只说明 6379 可握手，不能排除网络延迟。

5. **确认 Service 与 EndpointSlice 是否把流量导到了 Pod。** `kubectl -n boutique get svc redis-cart -o yaml`：确认 `selector` 为 `app: redis-cart`、端口 `6379` / `targetPort: 6379`。`kubectl -n boutique get endpointslice -l kubernetes.io/service-name=redis-cart -o yaml`：看 `endpoints[].conditions.ready` 与端口是否为 6379。
   - 端点为空：先回第 2 步确认 Pod 是否存在、回第 4 步确认是否 readiness 失败；若 Pod Running 且 READY 1/1 却仍无端点，重点核对 Service selector 与 Pod 标签 `app: redis-cart` 是否一致。
   - 端点存在但 `ready=false`：说明 readiness 没过，结论仍落在第 4 步。端点 ready=true 只说明服务发现正常，慢而不报错时继续第 7 步。

6. **确认配额与副本是否影响重建。** `kubectl -n boutique get resourcequota`，再看 `kubectl -n boutique get rs -l app=redis-cart` 与 Deployment 的 conditions/事件。若 Pod 被删除或重建后一直不出现、事件里出现配额相关拒绝，说明是 ResourceQuota 耗尽导致无法建 Pod；若 ReplicaSet 刚发生换代，说明是发布/重启动作，结合第 2–4 步判断新 Pod 是否已就绪。单副本下单次重建就是一次完整中断。无配额限制、无重建动作时，若调用方只是慢，继续第 7 步。

7. **无错误型变慢：跨调用方耗时与 Redis 往返次数比对（本 SOP 的关键判据）。** 适用：用户反馈页面/购物车/结账明显变慢但无报错；redis-cart Pod Running/Ready、restartCount 稳定、事件为空、EndpointSlice 有 ready 端点、日志无 OOM/持久化/连接错误；cartservice 日志也无 error/timeout/exception。事件为空不等于无故障，不能因对象正常就跳 `boutique-cartservice`。
   - 先固定变慢拐点时间窗，确认日志时间戳格式。
   - 在至少两个独立调用方（frontend、checkoutservice）日志中取“调用 cartservice 的耗时”；在 cartservice 日志中取同一时间窗内同一请求对 Redis 的实际操作耗时/次数。也可用 frontend 页面 `took_ms` 与 checkoutservice `PlaceOrder` 分步耗时对齐。
   - 判据：如果调用方到 cartservice 这一跳本身不慢，而 cartservice 每次访问 Redis 的命令成本与 Redis 往返次数成正比——例如 GetCart/KeyDelete 需 1 次 Redis 往返时每次多出约 300ms，AddItem 需 2 次往返时多出约 600ms；多个独立调用方（frontend、checkoutservice）对 cartservice 都出现同样固定增量；同期调用其它下游（currencyservice、productcatalogservice、shippingservice、adservice 等）均为毫秒级——则根因写“redis-cart Pod 网络/链路侧存在固定约 Xms 延迟”，不要写成 cartservice 应用逻辑问题或 CPU 限流。
   - 若 redis-cart 自身日志的接收→处理完成仍正常、数据量很小、无 OOMKilled、无探针失败、无端点缺失、无 ResourceQuota 限制，仍不能排除网络延迟；这些只排除应用/资源/就绪原因，不改变网络链路归属。
   - 权限边界：看不到节点、无监控指标、不能 exec/tcpdump/tc、看不到 NetworkPolicy/CoreDNS。机制层（tc/netem、CNI、CPU throttling）无法最终确认，但归属不变；结论末尾写“机制未确认，需节点/网络指标或容器内 tc qdisc show、getent hosts 计时等权限补充”。不要因为机制未确认就把结论改成“redis-cart 正常，回 cartservice”或“CPU 限流待确认”。
   - 强制纠偏：当证据满足“固定增量与 Redis 往返次数成正比 + 多调用方一致 + 其它下游正常 + redis 进程日志正常”时，根因必须写 redis-cart Pod 网络/链路侧固定延迟；不得把“CPU 饥饿/CPU 限流/节点争抢/redis 后台模块线程”列成根因候选或主结论，也不得以“先 kubectl top/看 CFS 指标”替代归属。只有出现直接限流/节点/注入证据，才把这些机制写入证据补充。
   - 若同时看到其他服务固定停顿，先确认是否同一时间窗与同一调用链；不要用无关服务（如 emailservice）的停顿替代本服务判据，也不要把无关服务写成 redis-cart 根因。
   - 若只有单个调用方慢、其它调用方访问 cartservice 正常，或 cartservice 自身 Redis 操作耗时正常而增量在调用方渲染/重试/并发，则回该调用方；若 cartservice 日志显示 Redis 连接拒绝/超时/解析失败，按第 3/5 步和跳转处理。

8. **形成结论并决定归属。** 把上面收集到的对象名、状态、事件原文、日志关键行汇总：问题落在镜像、资源（OOM）、探针、Service/端点、配额、发布动作还是第 7 步的无错误型固定延迟。若报错型且 redis-cart 侧六项全部正常，走下一节跳到调用方；若无错误型变慢且第 7 步判据成立，结论写 redis-cart Pod 网络/链路侧，不跳 cartservice；若只有单个调用方慢，回该调用方。

## 什么时候跳到别的服务

- 页面/购物车/结账慢但无报错，redis-cart Pod/端点/探针/日志正常，但第 7 步显示固定增量与 Redis 往返次数成正比、多调用方一致、其它下游毫秒级 → **不跳**，结论写 redis-cart Pod 网络/链路侧。
- 只有单个调用方慢，其它调用方访问 cartservice 正常 → 跳到该调用方 skill（`boutique-frontend` 或 `boutique-checkoutservice`），结论写在调用方。
- 看到 `redis-cart` 的 Pod Ready、EndpointSlice 端点 `ready=true`、容器无重启、日志无内存/持久化报错，而 cartservice 仍报连接失败、超时或 gRPC 错误，且第 7 步的无错误型固定延迟判据不成立 → 跳到 skill `boutique-cartservice`，结论写在 cartservice 侧（连接配置、地址、超时、自身资源）。
- cartservice 日志明确连接 redis-cart 拒绝/超时/解析失败，或 redis-cart 无 ready 端点、Pod 不 Ready、OOMKilled、探针失败、ResourceQuota 拒绝 → 不跳，结论写 redis-cart 对应对象。
- 看到 frontend 报错但 cartservice 侧对 redis-cart 的调用无异常，且第 7 步判据不成立 → 回到调用方链路，先按 `boutique-cartservice` 确认，再由其判断是否继续上跳 `boutique-frontend`。
- 看到多个服务同时报错、跨服务普遍超时，且 redis-cart 自身有直接异常证据（重启、OOM、探针失败、端点缺失） → 留在 redis-cart 定位；若多个服务只是同样固定延迟且本服务第 7 步证据成立，仍把本服务结论写 redis-cart 网络/链路侧，同时把其它服务只列为受影响面。
- 本服务不调用任何下游服务，因此不存在向更下游跳转的分支；报错型下“redis-cart 正常但 cart 功能坏”才回调用方 cartservice，无错误型慢且判据成立时不得回跳。

## 结论怎么写

结论必须写清三件事：具体对象（`deploy/redis-cart`、某个 Pod 名、`svc/redis-cart` 或对应 EndpointSlice）、原因分类（如 OOMKilled 超出 256Mi limit、readiness 探针连续失败、Service selector 不匹配、ResourceQuota 拒绝创建、镜像拉取失败）、支撑证据（事件原文、`restartCount`/`lastState.terminated.reason` 字段值、日志关键行、端点 `ready=false` 或端点为空）。

无错误型变慢的结论必须写：对象 `Pod/redis-cart`（或对应 Service/EndpointSlice）；原因“redis-cart Pod 网络/链路侧存在固定约 Xms 延迟，应用自身处理耗时未变”；证据至少同时给出（1）至少两个调用方调用 cartservice 的耗时相对基线的固定增量，（2）增量与 Redis 往返次数成正比（1 次往返约多 300ms、2 次约多 600ms），（3）frontend 到 cartservice 这一跳本身不慢且同期其它下游为毫秒级，（4）redis-cart 进程日志正常、数据量小、无 OOM/探针/端点/配额异常。末尾写“机制未确认，需节点/网络指标或容器内 tc qdisc show、getent hosts 计时等权限补充”，但结论归属仍是 redis-cart。特别禁止在证据已满足上述条件时，把 CPU 限流/节点争抢/CPU 饥饿写成根因或“待确认根因”，也不要把结论改挂到 cartservice 或 frontend。

若证据表明 redis-cart 侧全部正常且第 7 步判据不成立，结论写“redis-cart 侧无异常（Pod Ready、端点 ready=true、无重启）”，并把责任归属明确写到 cartservice。未能确认时写“未确认”，并注明缺什么（如指标、节点信息、容器内检查权限），不要把推测写成结论。
