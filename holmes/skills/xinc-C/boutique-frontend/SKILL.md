---
name: boutique-frontend
description: Online Boutique 的 frontend 排查 SOP：症状涉及 frontend，或从调用方（用户入口）查到它时读。下游：productcatalogservice、currencyservice、cartservice、recommendationservice、shippingservice、checkoutservice、adservice。
---

# frontend 排查 SOP

## 经验条目

以下条目来自过去的排查记录，括号里是来源。

- [E1]（BH09、BH12）【已证实】用户侧中位 9–11ms、0 个 500，但 9–21% 请求 >180ms、p90≈114–318ms、p99≈304–640ms 时：先按 `http.resp.took_ms` 的 URL 分组，再用“每页调用各依赖次数”解释。若慢只在 `GET /cart`≈1×300ms、`POST /cart/checkout`≈2×300ms，转 shippingservice；若首页、商品页、购物车都出现 200–230ms 台阶且首页（调用 currency 约 11 次）最慢，逐请求配对 frontend 开始→currencyservice `Getting supported currencies` 入口，入口晚而 currency 内处理仅几 ms 时转 currencyservice SOP；不要先抬 frontend CPU（200m→500m 实测 >180ms 仍 9.1%→10.5%）。

<!-- 经验条目结束 -->

适用症状：用户从 frontend 入口访问 Online Boutique 出现页面不可用、报错或入口功能异常，或 loadgenerator 访问 frontend 失败/异常。

## 信号 → 步骤

| 能观察到的信号 | 去哪 |
|---|---|
| Deployment 可用副本 < 1；Pod 非 Running/Ready；CrashLoopBackOff、ImagePullBackOff、Pending、FailedCreate | 第 1 步 |
| Pod Running 且 Ready，但 frontend Service 访问异常，或 EndpointSlice 无 ready endpoint | 第 2 步 |
| frontend 日志出现下游服务地址/服务名的连接失败、超时、解析失败或非预期响应 | 第 3 步；单个下游持续异常时按“什么时候跳到别的服务”跳转 |
| 事件或日志显示 `/_healthz` 探针失败、容器重启 | 第 4 步 |
| Pod/Service/EndpointSlice 正常，但用户报入口不可用，或 loadgenerator 无法连接 frontend | 第 5 步 |
| 多个下游同时报错，或所有下游地址解析失败 | 第 3 步先核对 frontend env 和下游 Service/EndpointSlice，不直接跳单个下游 |
| 确认某个下游持续异常，frontend 自身正常 | 跳到 skill `boutique-<下游服务>` |
| frontend 及所有下游均正常，仅入口请求未到达或外部访问失败 | 第 5 步，结论写入口/用户侧或 loadgenerator 侧 |

## 步骤

1. **查 frontend Deployment、ReplicaSet、Pod 和事件，先排除本服务发布/资源问题。**  
   执行 `kubectl -n boutique get deploy,rs,pod -l app=frontend -o wide`，再对异常 Pod 执行 `kubectl -n boutique describe pod <pod>` 和查看命名空间事件。重点看：Deployment 的 desired/ready/available，Pod phase、Ready、restartCount、containerStatuses 的 waiting/terminated reason，事件中的 `ImagePullBackOff`、`ErrImagePull`、`CrashLoopBackOff`、`OOMKilled`、`FailedScheduling`、`FailedCreate`。  
   - 镜像拉取失败：本服务镜像问题，先确认镜像名是否为清单中的 `frontend:v0.10.3`、仓库是否可达、拉取凭据是否可用；只读账号不能看节点，不要归因节点。  
   - `OOMKilled` 或反复重启：结合 limits `memory: 128Mi`、容器日志和事件判断是否为内存限制；没有监控指标，不能靠 CPU/内存曲线下结论。  
   - `FailedCreate`/Pending 且事件提到配额：查看 `kubectl -n boutique get resourcequota`，确认是否 ResourceQuota 不足。  
   - Pod 未 Ready 且事件指向探针：转到第 4 步；Pod 正常则转第 2 步。

2. **查 frontend Service 和 EndpointSlice，确认入口 Service 是否把流量送到 Ready Pod。**  
   执行 `kubectl -n boutique get svc frontend -o yaml` 和 `kubectl -n boutique get endpointslice -l kubernetes.io/service-name=frontend -o yaml`。核对 Service 的 selector 是否为 `app: frontend`，端口是否为 `port: 80`、`targetPort: 8080`，EndpointSlice 是否有 ready addresses。  
   - selector 或端口与清单不一致：本服务 Service 配置问题。  
   - EndpointSlice 为空但 Pod 已 Ready：先确认 readinessProbe 是否通过、Service selector 是否匹配；必要时回第 1、4 步。  
   - EndpointSlice 有 ready endpoint 但用户仍访问失败：继续第 3、5 步，区分 frontend 应用错误与入口访问链路问题。

3. **查 frontend 日志中的下游调用错误，按下游拆分。**  
   执行 `kubectl -n boutique logs deploy/frontend --tail=<合适行数>`；若容器重启过，再看 `--previous`。先确认实际日志格式，不要预设字段。检索清单中的下游地址/服务名：`productcatalogservice:3550`、`currencyservice:7000`、`cartservice:7070`、`recommendationservice:8080`、`shippingservice:50051`、`checkoutservice:5050`、`adservice:9555`。  
   - 只有一个下游地址持续出现连接失败、超时、解析失败或非预期响应，且 frontend Pod/Service/EndpointSlice/探针正常：按“什么时候跳到别的服务”跳对应 skill。  
   - 多个下游同时报错：先核对 frontend 的 env 地址是否与清单一致，并只读查看对应下游 Service/EndpointSlice 是否存在；不要直接归因单个下游。若 env 与清单不一致，按本服务配置问题处理。  
   - 所有下游地址集体解析失败：在确认 frontend env 正确、下游 Service 存在后，结论写集群 DNS/网络侧，不跳单个下游。  
   - 清单中有 `SHOPPING_ASSISTANT_SERVICE_ADDR=shoppingassistantservice:80`，但给定调用关系未列出该下游；先确认实际是否被调用，不要据此跳到未列出的服务。

4. **查探针和 frontend 运行配置。**  
   核对 Deployment 中 `PORT=8080`、containerPort `8080`、readinessProbe/livenessProbe 均为 `httpGet /_healthz:8080`，并带清单中的 Cookie 头；initialDelaySeconds 均为 10。查看 Pod 事件和日志中是否出现探针失败、未监听端口、启动失败。  
   - 应用未监听 8080、PORT/containerPort/探针端口不一致：本服务配置问题。  
   - 探针失败同时伴随下游连接错误：回第 3 步判断是否由下游依赖导致。  
   - 探针失败但无下游错误：结论写 frontend Pod/探针配置或应用启动问题，证据用事件和日志。

5. **确认入口请求是否到达 frontend。**  
   用用户报告、loadgenerator Pod 状态/日志辅助判断，先确认 loadgenerator 实际日志格式。若 frontend 日志有持续请求且错误指向下游，回第 3 步；若 frontend 日志无请求、loadgenerator 日志显示无法连接 frontend，而 frontend Service/EndpointSlice 正常，检查第 2 步并判断为入口访问链路/用户侧或 loadgenerator 侧问题。frontend 是入口，没有上游微服务调用方；不要把它写成某个下游服务的调用方问题。loadgenerator 只作为流量来源，不写成 frontend 的下游服务。

6. **汇总结论并决定跳转。**  
   汇总 frontend Deployment/Pod/Service/EndpointSlice 状态、事件、日志中的下游地址错误，按下一节决定跳下游还是写入口侧结论。不要只写“重启/扩容试试”。

## 什么时候跳到别的服务

仅当 frontend Pod Running/Ready、frontend Service/EndpointSlice 正常、探针无明显失败，且 frontend 日志持续指向单个下游时跳转：

- frontend 日志持续出现 `productcatalogservice:3550` 连接失败、超时、解析失败或非预期响应 → 跳到 skill `boutique-productcatalogservice`。  
- frontend 日志持续出现 `currencyservice:7000` 连接失败、超时、解析失败或非预期响应 → 跳到 skill `boutique-currencyservice`。  
- frontend 日志持续出现 `cartservice:7070` 连接失败、超时、解析失败或非预期响应 → 跳到 skill `boutique-cartservice`。  
- frontend 日志持续出现 `recommendationservice:8080` 连接失败、超时、解析失败或非预期响应 → 跳到 skill `boutique-recommendationservice`。  
- frontend 日志持续出现 `shippingservice:50051` 连接失败、超时、解析失败或非预期响应 → 跳到 skill `boutique-shippingservice`。  
- frontend 日志持续出现 `checkoutservice:5050` 连接失败、超时、解析失败或非预期响应 → 跳到 skill `boutique-checkoutservice`。  
- frontend 日志持续出现 `adservice:9555` 连接失败、超时、解析失败或非预期响应 → 跳到 skill `boutique-adservice`。  

回到调用方/入口侧的情形：

- frontend Deployment/Pod/Service/EndpointSlice 均正常，所有下游也未见持续错误，但用户访问失败或请求未到达 frontend：frontend 无上游微服务调用方，结论写在入口/用户侧或 loadgenerator 侧，证据引用用户侧现象或 loadgenerator 日志。  
- loadgenerator 自身 Pod/日志异常导致无法产生或无法到达 frontend：结论写 loadgenerator 对象及证据，不跳微服务下游 skill。  
- 多个下游同时异常或所有下游地址集体解析失败：先核对 frontend env 和下游 Service/EndpointSlice；若均正确，结论写集群 DNS/网络或平台侧，不直接归因单个下游。

## 结论怎么写

结论必须写明对象、原因、证据：对象是 frontend 的 Deployment/Pod/Service/EndpointSlice，或具体下游 Service；原因是镜像拉取失败、探针失败、资源限制、Service selector/端口错误、下游不可达等；证据引用具体事件、Pod 状态、EndpointSlice 状态或 frontend 日志中的下游地址错误。  
跳下游时写“frontend 本身正常，问题在 `<下游服务>`”，并附 frontend 日志证据；入口侧时写“frontend 及下游均正常，入口请求未到达/外部访问失败”，并附用户侧或 loadgenerator 观察证据。
