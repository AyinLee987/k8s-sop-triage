# 通用 Pod 排查步骤

api、worker、redis 的 SOP 都会引用这里的步骤。

## 第 1 步：总览

列出命名空间内的 Deployment、StatefulSet、Pod 和 Service。记录每个工作负载的期望副本数和就绪副本数，每个 Pod 的阶段、是否就绪、重启次数、等待原因（waiting reason）、上次终止原因（lastState.terminated.reason / exitCode）和创建时间。

如果工具返回无权限（forbidden），停止排查，状态写 `undetermined`，在 `unknowns` 里写明缺少哪个命名空间或资源的读权限。

## 第 2 步：找出异常的工作负载

满足任一条件即视为异常：就绪副本数少于期望值；有 Pod 未就绪；有 Pod 重启次数大于 0；有 Pod 处于等待状态。

**期望副本数本身为 0 时，"就绪数少于期望值"不会成立，要单独看**：

- worker 或 api 的期望副本数是 0 → `scaled_to_zero`，结论写这个工作负载。
- redis 的期望副本数是 0 → 见 `30-redis.md`（`dependency_down`）。

## 第 3 步：按 Pod 状态分支

**3a. 等待原因是 ErrImagePull / ImagePullBackOff / ErrImageNeverPull / InvalidImageName**

查看工作负载的镜像字段和相关事件。结论通常是 `image_unavailable`，证据写镜像名和等待原因。

**3b. 等待原因是 ContainerCreating 且迟迟不变**

看事件。有 `FailedMount` 时，事件消息会写出是哪个 ConfigMap / Secret 的哪个键找不到。对照工作负载的卷（volumes 里的 `keys=[...]`）：引用的键在对象里不存在 → 根因在被引用的 ConfigMap / Secret（redis 的情况见 `30-redis.md`）。

**3c. 等待原因是 CrashLoopBackOff / RunContainerError / StartError / CreateContainerError，或者重启次数大于 0**

1. 看上次终止原因。是 `OOMKilled` 时，对照容器的内存限制，结论通常是 `memory_limit_too_low`。
2. 容器根本没能启动（RunContainerError / StartError / CreateContainerError），这时通常没有日志。看等待状态的消息和事件：有 "executable file not found" 之类的提示 → `wrong_command`，证据写启动命令。
3. 看相关事件。有 "Liveness probe failed" 时，对照存活探针配置：路径、端口错误，或者存活探针指向了会检查下游依赖的接口（例如 `/health/ready`）、阈值过严 → `probe_misconfigured`。下游的短暂故障只是触发条件，结论写探针所在的工作负载。
4. 否则读取上一次运行的日志（previous logs）：
    - 日志里有配置校验错误（例如 "must be"、"ValueError"、取值范围提示）→ `invalid_config_value`，并在工作负载的环境变量里找出对应的配置项。
    - 日志里有找不到模块、找不到命令或入口（例如 "No module named"、"not found"、exit code 127）→ `wrong_command`，证据写启动命令。

**3d. Pod 在运行但未就绪**

看事件里的就绪探针失败信息和当前日志：

- 日志里有依赖连接、认证、超时或内存错误 → 按所在组件的 SOP（`10-api.md` / `20-worker.md`）的"依赖错误"一步处理，必要时转到 `30-redis.md`。
- 探针请求本身失败（返回 404、连接被拒），日志里没有依赖错误 → 对照探针路径和端口，结论通常是 `probe_misconfigured`。

## 第 4 步：工作负载都正常时

按症状选组件 SOP，读应用日志，找错误、重试和异常耗时。同时检查两件"Pod 都健康也可能出问题"的事：

- 工作负载的启动命令、ConfigMap `app-config` 是否和基线一致（基线见 `00-index.md`）；
- Service 的 selector、targetPort 是否和基线一致，endpoints 是否为空。

日志、配置和 Service 都没有问题时，状态写 `no_fault_found`，类别写 `no_fault`，在证据里列出检查过的内容。

## 收尾

- 证据要能直接支持结论；只凭一个症状（例如只看到 CrashLoopBackOff）不能当作根因。
- 不确定的地方写进 `unknowns`，不要猜。
