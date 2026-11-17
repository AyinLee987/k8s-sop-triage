---
name: boutique-redis-cart
description: Online Boutique 的 redis-cart 排查 SOP：症状涉及 redis-cart，或从调用方（cartservice）查到它时读。下游：无。
---

# redis-cart 排查 SOP

## 经验条目

以下条目来自过去的排查记录，括号里是来源。

- [E1]（BH04）调用方（cartservice）报每次 GetCart/AddItem 稳定多出固定台阶时，先自证 redis 无罪：`SLOWLOG GET`、`redis-cli --latency` 应毫秒级，容器 CPU/内存无压力、日志无报错；redis 自身正常而调用方仍慢→台阶在 cartservice↔redis 路径或 cartservice 自身。注意：重启 redis-cart（emptyDir）会清空购物车，/cart 类请求会因此额外变快，复核延迟是否修复时不要用 /cart 作口径，改用首页、商品页、POST /cart。
- [E2]（BH08）调用方（cartservice）报每个 GetCart/AddItem 多出固定台阶，且台阶随 Redis 命令条数线性放大（1 条≈285ms、2 条≈570ms）、与该跳无关的请求仍 0–2ms、各 Service selector/targetPort/Endpoints 正常、各 Pod Running/0 重启、每条命令都 ≥台阶（无一条快，可排除丢包）时：延迟在 cartservice→redis-cart 的每次往返上，且两个候选机制都在 redis-cart Pod 内——数据面/qdisc 被注入固定延迟，或被 CPU 限流（本命名空间最紧的 125m）导致应答不及时。处置：先重建 redis-cart Pod 并把 CPU limit 提到 1，复测 1/2 条命令耗时比例与页面分位数应回到毫秒级；仍慢说明延迟由 Pod 外注入，转运维/chaos。注意：拿不到节点 CPU/throttle 指标时，用“同窗口其他服务毫秒级”排除节点级原因；cartservice 刚 rollout 或 env 与 last-applied 不同（如多出 RELEASE_TAG）时
- [E3]（BH13）（BH11）偶发尾部延迟：多数请求正常、约 4–5% 命中 +200ms、无 slowlog、0 个 500 错误、不含 redis 的请求仍毫秒级，且本 Pod 是命名空间里唯一 Liveness/Readiness `dial tcp :6379: i/o timeout` 的 Pod（kubelet 探针绕过 cartservice，反证问题在本 Pod 内而非节点级丢包）时：本服务 redis:alpine 实为 Redis 8.x，加载 Bloom/RediSearch(16 worker)/TimeSeries/ReJSON 多模块，CPU limit 常仅 125m，突发即被 CFS 冻结产生 100–200ms 停顿（也可能进程被阻塞或瞬时不通）。先提 limit 到 1 并重建 Pod，复测 >180ms 占比应回落；仍偶发则转运维查注入。emptyDir 重建会清空购物车。

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
