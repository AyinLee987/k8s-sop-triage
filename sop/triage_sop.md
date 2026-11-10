# K8s 工作负载排查 SOP（v1）

三组对照共用这一份 SOP。规则基线、文本 SOP 加 LLM、代码 SOP 加 LLM 拿到的领域知识完全相同，区别只在"谁来决定下一步"。

范围：单个命名空间内由 Deployment / StatefulSet 管理的 Pod 级故障。只读诊断，不做修复。

## 输出要求

最终给出一份诊断，包含五个字段：

- `object`：故障对象，写到工作负载一级，例如 `{"kind": "Deployment", "name": "worker", "namespace": "..."}`。
- `cause_category`：从下面的原因类别里选一个；都不符合时选 `other`，并在 `cause` 里写清楚。
- `cause`：一句话说明原因。
- `evidence`：支持结论的关键证据，原样引用工具输出里的字段或日志片段，至少一条。
- `unknowns`：还不确定的事项，或者缺少的数据。
- `status`：`root_cause_found`（找到根因）、`no_fault_found`（检查过没有发现故障）、`undetermined`（证据不足或无权限）。

原因类别：

| 类别 | 含义 |
| --- | --- |
| `image_unavailable` | 镜像不存在、拉取失败或标签写错 |
| `invalid_config_value` | 配置值不合法，程序启动时校验失败退出 |
| `wrong_command` | 启动命令、模块名或参数写错 |
| `memory_limit_too_low` | 内存限制过低，容器被 OOMKilled |
| `probe_misconfigured` | 健康检查探针配置错误（路径、端口等），导致容器被重启或不就绪 |
| `dependency_unreachable` | 依赖服务地址错误或不可达（DNS 失败、连接被拒） |
| `dependency_auth_failed` | 连接依赖服务时认证失败（密码错误等） |
| `no_fault` | 没有发现故障 |
| `other` | 以上都不是 |

## 步骤

### 第 1 步：总览

列出命名空间内的 Deployment、StatefulSet 和 Pod。记录每个工作负载的就绪副本数，每个 Pod 的阶段、是否就绪、重启次数、等待原因（waiting reason）和上次终止原因（lastState.terminated.reason / exitCode）。

如果工具返回无权限（forbidden），停止排查，状态写 `undetermined`，在 `unknowns` 里写明缺少哪个命名空间或资源的读权限。

### 第 2 步：找出异常的工作负载

满足任一条件即视为异常：就绪副本数少于期望值；有 Pod 未就绪；有 Pod 重启次数大于 0；有 Pod 处于等待状态。

有异常就进入第 3 步；全部正常就进入第 4 步。

### 第 3 步：按 Pod 状态分支

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

看事件里的就绪探针失败信息和当前日志，区分两种情况：

- 日志里有依赖连接或认证错误（同第 4 步）→ 按依赖类别判断（`dependency_unreachable` / `dependency_auth_failed`），探针只是把依赖故障反映出来。
- 探针请求本身失败（返回 404、连接被拒），日志里没有依赖错误 → 对照探针路径和端口，结论通常是 `probe_misconfigured`。

### 第 4 步：工作负载都正常时，检查应用日志

读取各工作负载最近的日志，找错误或重试记录：

- 有连接失败、DNS 解析失败（例如 ConnectionError、"Name or service not known"）→ `dependency_unreachable`，再看该工作负载环境变量里的依赖地址。
- 有认证失败（例如 AuthenticationError、WRONGPASS、"invalid password"）→ `dependency_auth_failed`，再看依赖密码的来源配置。

日志里也没有错误时，状态写 `no_fault_found`，类别写 `no_fault`，在证据里列出检查过的内容。

### 第 5 步：收尾

- 证据要能直接支持结论；只凭一个症状（例如只看到 CrashLoopBackOff）不能当作根因。
- 不确定的地方写进 `unknowns`，不要猜。
