# 通用 Pod 排查步骤

api、worker、redis 的 SOP 都会引用这里的步骤。

## 第 1 步：总览

列出命名空间内的 Deployment、StatefulSet 和 Pod。记录每个工作负载的期望副本数和就绪副本数，每个 Pod 的阶段、是否就绪、重启次数、等待原因（waiting reason）和上次终止原因（lastState.terminated.reason / exitCode）。

如果工具返回无权限（forbidden），停止排查，状态写 `undetermined`，在 `unknowns` 里写明缺少哪个命名空间或资源的读权限。

## 第 2 步：找出异常的工作负载

满足任一条件即视为异常：就绪副本数少于期望值；有 Pod 未就绪；有 Pod 重启次数大于 0；有 Pod 处于等待状态。

注意：期望副本数本身为 0 时，"就绪数少于期望值"不会成立，要在依赖排查里单独检查（见 `30-redis.md`）。

## 第 3 步：按 Pod 状态分支

**3a. 等待原因是 ErrImagePull / ImagePullBackOff / ErrImageNeverPull / InvalidImageName**

查看工作负载的镜像字段和相关事件。结论通常是 `image_unavailable`，证据写镜像名和等待原因。

**3b. 等待原因是 CrashLoopBackOff / RunContainerError / StartError / CreateContainerError，或者重启次数大于 0**

1. 看上次终止原因。是 `OOMKilled` 时，对照容器的内存限制，结论通常是 `memory_limit_too_low`。
2. 容器根本没能启动（RunContainerError / StartError / CreateContainerError），这时通常没有日志。看等待状态的消息和事件：有 "executable file not found" 之类的提示 → `wrong_command`，证据写启动命令。
3. 否则读取上一次运行的日志（previous logs）：
    - 日志里有配置校验错误（例如 "must be"、"ValueError"、取值范围提示）→ `invalid_config_value`，并在工作负载的环境变量里找出对应的配置项。
    - 日志里有找不到模块、找不到命令或入口（例如 "No module named"、"not found"、exit code 127）→ `wrong_command`，证据写启动命令。
4. 看相关事件。有 "Liveness probe failed" 或 "Readiness probe failed" 时，对照探针配置（路径、端口），结论通常是 `probe_misconfigured`。

**3c. Pod 在运行但未就绪**

看事件里的就绪探针失败信息和当前日志：

- 日志里有依赖连接、认证或内存错误 → 按所在组件的 SOP（`10-api.md` / `20-worker.md`）的"依赖错误"一步处理，必要时转到 `30-redis.md`。
- 探针请求本身失败（返回 404、连接被拒），日志里没有依赖错误 → 对照探针路径和端口，结论通常是 `probe_misconfigured`。

## 第 4 步：工作负载都正常时

按症状选组件 SOP，读应用日志，找错误、重试和异常耗时。日志里也没有任何问题时，状态写 `no_fault_found`，类别写 `no_fault`，在证据里列出检查过的内容。

## 收尾

- 证据要能直接支持结论；只凭一个症状（例如只看到 CrashLoopBackOff）不能当作根因。
- 不确定的地方写进 `unknowns`，不要猜。
