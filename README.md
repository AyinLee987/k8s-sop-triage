# k8s-sop-triage

**核心问题（来自 11/5 面试官的真实痛点）**：把 bad case 复盘出来的"经验"直接写进 SOP / skill，会不会让原来能查对的场景反而查错（过拟合）？加得越多越严重吗？

**配套问题**：确定的排查步骤写成代码，和让模型读着 SOP 自己执行相比，哪个更准、更快、更省？现象出在 A、根因在 B 的跨组件故障里，谁更容易"停在症状层"？

两部分共用同一套环境、场景、工具和评分。结果可能是取舍，而不是全面更好，两种结论都写进报告。

## 核心实验：bad case 过拟合剂量实验（`triage/experiments/overfit.py`）

- **经验条目库**（`experiments/badcases.yaml`，40 条）：模仿真实复盘写法，分六类：准确的窄经验、触发条件过宽的经验（最像真实过拟合）、让结论停在最先报错组件的经验、让模型跳过取证的经验、与真实原因矛盾的经验、与本系统无关的经验。条目的来源是开发集场景或通用经验，不包含保留测试集的答案；但"矛盾"类条目会和部分场景（包括保留测试集）的真实原因相冲突，这是有意设计的干扰。分析时可以用 `--kinds` 按类别单独跑，区分"条目变多本身的影响"和"错误经验的影响"。
- **剂量**：往 SOP 末尾追加 0 / 5 / 10 / 20 / 40 条，也就是"写进 skill"。剂量是嵌套的（10 条包含前 5 条），各类条目交错排列。
- **指标**：回归集（开发集场景，也就是经验来源的场景）正确率、泛化集（保留测试集）正确率、"停在症状层"次数、token 成本，以及"剂量 0 时每次都对、加经验后出错"的回退明细。
- **判断复现**：回归集正确率随剂量增加稳定下降，并且在多次重复里都出现。

```bash
python -m triage.experiments.overfit show --dose 10                     # 看追加了什么
python -m triage.experiments.overfit run --backend fake --doses 0,10,40 --reps 2   # 试点：真实模型 + 假集群
python -m triage.experiments.overfit run --backend fake --kinds specific           # 消融：只加准确经验
python -m triage.experiments.overfit run --backend real ...                        # 新电脑上用真集群
```

试点复现成功后，下一步比较三种吸收 bad case 的方式：写进提示词、存进案例库按需检索、写成代码分支。

## 三组对照

三组共用同一套 SOP（`sop/` 目录：总则、通用 Pod 步骤、api、worker、redis 五份，互相引用，按文件名顺序拼接；总则写明组件依赖关系和跳转原则："现象所在的组件不一定是根因所在"）和同一个只读工具层（`triage/tools.py`），区别只在"谁来决定下一步"。

| 配置 | 代码 | 执行方式 |
| --- | --- | --- |
| `rule` 规则基线 | `triage/agents/rule_baseline.py` | 按 SOP 确定性采集，再用固定规则判断；不调用模型 |
| `text` 文本 SOP 加 LLM | `triage/agents/text_sop.py` | 模型读 SOP，自己选择并调用工具，最后提交诊断 |
| `code` 代码 SOP 加 LLM | `triage/agents/code_sop.py` | 代码执行 SOP 步骤并行采集证据；模型只在三个判断点出场；没有匹配分支时进入有预算上限的自由排查 |

`rule` 和 `code` 的采集逻辑都在 `triage/agents/sop_steps.py`，两组拿到的证据完全一样。

## 场景（`scenarios/scenarios.yaml`）

被注入故障的业务是 `tiktok-k8s-lab`（api 2 副本、worker 1 副本、redis StatefulSet）。

| 场景 | 集合 | 类型 | 故障 |
| --- | --- | --- | --- |
| S01 | dev | 单证据 | api 镜像标签不存在 |
| S02 | dev | 运行但异常 | worker 的 REDIS_HOST 写错 |
| S03 | dev | 同症状不同根因 | worker 配置值超范围，启动校验失败 |
| S04 | dev | 同症状不同根因 | worker 内存限制 16Mi，OOMKilled |
| S05 | holdout | 同症状不同根因 | worker 启动模块名写错 |
| S06 | holdout | 同症状不同根因 | api 存活探针路径写错 |
| S07 | dev | 正常 | 无故障 |
| S08 | holdout | 证据不足 | 症状在诊断器无权读取的命名空间 |
| S09 | holdout | SOP 未覆盖 | Service selector 选不中 Pod |
| S10 | holdout | 运行但异常 | worker 的 Redis 密码被改成错误的字面值 |
| S11 | dev | 跨组件 | 接口返回 503（现象在 api），根因是 redis-config 的 maxmemory 被改成 512kb |
| S12 | dev | 跨组件 | api 全部不 Ready、worker 停止（现象在 api / worker），根因是 redis 副本数为 0 |
| S13 | holdout | 跨组件 | 与 S12 症状相同，根因是 redis-config 多了一行 requirepass，Redis 自己不就绪 |
| S14 | holdout | 跨组件 | 点击数涨得很慢（现象在用户侧），根因是 worker 的处理延迟被配成 10 秒 |

跨组件场景的评分会单独标出"停在症状层"：诊断器把根因写成了最先出现现象的组件（例如 api），而不是真正的根因（例如 redis）。

### 参数化生成的场景（`scenarios/templates.yaml`）

一个模板对应一种故障机制，每组参数展开成一个场景，目前从 9 个模板生成 25 个场景。参数只改位置和取值（比如换一个环境变量、换一个工作负载、换一个取值），不改机制。

| 机制 | 集合 | 生成数 | 例子 |
| --- | --- | --- | --- |
| invalid_config_value | dev | 6 | worker 的 READ_BLOCK_MS 超过 Redis 超时；api 的 REDIS_PORT=abc |
| image_unavailable | dev | 4 | api 镜像 v2-rc1 / latest；worker 镜像 v1.1 |
| dependency_unreachable | dev | 3 | worker 的 REDIS_PORT=6380；api 的 REDIS_HOST 写错导致不 Ready |
| memory_limit_too_low | dev | 2 | worker 24Mi；api 32Mi |
| wrong_command | holdout | 3 | 可执行文件不存在（容器根本起不来，没有日志）；uvicorn 入口写错 |
| probe_misconfigured | holdout | 3 | 存活探针路径或端口写错；就绪探针路径写错 |
| dependency_auth_failed | holdout | 2 | worker 密码为空；api 密码写错导致不 Ready |
| other（SOP 未覆盖） | holdout | 2 | Service selector 选错；targetPort 写错 |

- 生成的场景继承模板的集合划分。测试会检查：把手写和生成的场景合在一起，同一种机制也只出现在一个集合里。
- 同一句症状对应多种机制。例如"worker 一直在重启"对应 3 种以上原因，"api 发布后新 Pod 一直不 Ready"对应依赖地址错、密码错、就绪探针写错三种。
- `python -m triage.generator` 列出所有生成的场景；`--dump` 输出完整规格。
- 生成的注入能不能真正产生故障，要在真集群上用 `smoke` 验证。没生效会记为环境失败，不会算成诊断错误。最没把握的是两个内存限制场景（能不能真的触发 OOMKilled）和 `REDIS_PORT=6380`（报连接被拒还是超时）。

### 划分规则

- 按故障机制划分：同一种机制只出现在一个集合里。`dev` 用于开发和回归；`holdout` 要等 SOP、提示词、代码都定稿后，才用 `--finalize` 跑一次。
- 诊断器只拿到症状文本、默认命名空间和只读工具。场景 ID、注入方式和答案只有执行器和评分器能读；`tests/test_harness.py` 会检查 Agent 代码有没有引用它们。
- 每个场景重复 3 次只用来看结果稳不稳。报告按场景列"成功次数 / 有效次数"，不把 3 次运行当成 3 个独立样本。

## 执行约束

- 每轮六步：验证健康 → 注入 → 确认生效 → 诊断 → 评分 → 复位并确认恢复。串行执行。复位失败就停止整批运行，避免污染后面的结果。
- 诊断器用 `k8s/rbac-diag.yaml` 里的 ServiceAccount，只有 get / list / watch 权限，不能读 Secret。注入和复位用管理员凭据。
- 每次调用有单独的超时，整次诊断有总截止时间，超时的 kubectl 子进程会被直接终止。工具和模型调用都有次数上限；连续发出参数完全相同的调用会被当作"没有进展"，超过阈值就停止。
- 事件按注入时间过滤，上一轮留下的事件不会混进本轮。
- 环境失败（故障没生效、复位失败）单独记录、单独公开，不计入诊断准确率。

## 已在离线环境验证的部分

`pytest` 共 80 个测试，用假集群（`tests/fake_cluster.py`）和按脚本回复的假模型跑通了全流程：

- 规则基线在 10 个场景里答对 8 个。S08 和 S09 答成"无故障"，这符合预期：它们本来就是测规则覆盖不到的情况。
- 文本 SOP 的工具循环；代码 SOP 在匹配到分支时只调用一次模型；没有匹配分支时进入自由排查（S09、S08）。
- 预算耗尽、重复调用、截止时间；评分器把对象定位和原因判断分开打分；执行器的阶段顺序和环境失败处理。
- 生成场景的结构、划分纯度、症状不泄漏答案、JSON patch 合法；规则基线在"容器起不来""依赖故障导致不 Ready""就绪探针写错"三类新分支上答对。
- 完整实验循环（`run_matrix`）：记录写入、评分、报告；故障没生效会记为环境失败并照常复位；复位失败或开始前环境不健康会立即停止。
- 跨组件场景：规则基线和代码 SOP 都会按跳转原则先查客户端覆盖、再查 Redis；"停在症状层"会被单独标出；ConfigMap 可读、Secret 不可读。
- 过拟合剂量实验：条目嵌套与混合、追加内容确实进入提示词、能检测出"剂量 0 对、加经验后错"的回退。
- 真实模型试跑脚本的流程（模型用假的代替）；模型不支持工具调用时会提前停下。工具层在每个故障状态下都经得起 16 种异常参数（不存在的资源、错误的类型、未知工具、多余参数），不会崩。

**还没在真集群上验证**：注入的故障能不能稳定生效、复位是否干净、真实模型的表现、内存峰值。这些是机器到了之后第 1 周要做的事。

## 现在就能在 Windows 笔记本上做的

在项目目录打开 PowerShell（如果提示禁止运行脚本，先执行 `Set-ExecutionPolicy -Scope Process Bypass`）：

```powershell
.\scripts\Run-LocalChecks.ps1 -Stage Setup      # 建虚拟环境、装依赖，生成 .env
#   打开 .env，填 LLM_API_KEY
.\scripts\Run-LocalChecks.ps1 -Stage Test       # 离线测试，应全部通过
.\scripts\Run-LocalChecks.ps1 -Stage DryRun     # 真实模型 + 假集群试跑，预计 1 美元以内，脚本到 2 美元会自动停
.\scripts\Run-LocalChecks.ps1 -Stage Overfit    # 过拟合试点：剂量 0/10/40 各 2 次，预计 2–4 美元，上限 5 美元
```

故障注入的冒烟测试要用到你原来的 `tiktok-k8s-lab` 集群：

```powershell
# 在 tiktok-k8s-lab 目录：确认 Docker Desktop 已启动，然后部署业务（原项目的脚本）
.\scripts\Deploy.ps1
# 回到本项目目录
.\scripts\Run-LocalChecks.ps1 -Stage DiagCreds  # 生成诊断器只读凭据
.\scripts\Run-LocalChecks.ps1 -Stage CheckEnv   # 环境健康，且诊断器确实只读
.\scripts\Run-LocalChecks.ps1 -Stage Smoke      # 默认 S01,S03,S07 各 3 轮；-Scenario S04 -Rounds 2 可以单测 OOM
```

冒烟测试会真的修改并重启 api、worker，每轮结束都会复位并确认恢复。复位失败会立即停止，并打印卡在哪一步。

试跑用的是假集群，结果只用来检查提示词和流程能不能跑通，不能当作实验结果写进报告。

## 机器到了之后（Ubuntu）

```bash
# 0. 依赖：Docker Engine（官方仓库）、kind、kubectl、Python 3.12
sudo usermod -aG docker $USER   # 重新登录后生效

# 1. 部署业务环境（把 tiktok-k8s-lab 拷到新机器）
bash scripts/setup_lab_linux.sh ~/tiktok-k8s-lab

# 2. 本项目
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env            # 填 LAB_K8S_DIR、LLM_API_KEY
python scripts/make_diag_kubeconfig.py
python -m triage.runner check-env

# 3. 第 1 周检查点：不跑 Agent，只验证"健康—注入—确认—复位"能稳定重复
python -m triage.runner smoke --scenario S01,S03,S07 --rounds 3
#    同时另开一个终端记录资源峰值：docker stats --no-stream ；df -h

# 4. 开发集对照（检查点通过后）
python -m triage.runner run --split dev --arms rule --reps 1        # 先只跑规则基线，不花钱
python -m triage.runner run --split dev --arms rule,text,code --reps 3
python -m triage.runner report results/<文件>.jsonl

# 4b. 加上生成的场景：先 smoke 每个模板的第一个变体，确认注入可靠
python -m triage.runner smoke --scenario G-env_invalid-01,G-memory_limit-01,G-dependency_address-02 --rounds 2
python -m triage.runner run --split dev --source all --arms rule,text,code --reps 3
#    场景多时可以先每个模板只取 1 个：--max-per-template 1

# 5. 定稿后才跑保留测试集，只跑一次
python -m triage.runner run --split holdout --finalize
```

每条结果记录在 `results/*.jsonl`，内容包括：诊断、评分、墙钟时间、工具和模型调用次数、重复调用次数、token、费用、完整执行轨迹，以及 SOP、场景、代码文件的哈希（用来固定版本）。

## 目录

```
sop/                       三组共用的 SOP（总则 + 通用 Pod 步骤 + api / worker / redis，互相引用）
scenarios/scenarios.yaml   14 个手写场景，含 4 个跨组件场景（只有执行器和评分器读取）
experiments/badcases.yaml  40 条 bad case 经验条目（过拟合剂量实验用）
triage/experiments/        过拟合剂量实验
scenarios/templates.yaml   9 个参数化故障模板，展开成 25 个场景
triage/generator.py        模板展开与校验
triage/tools.py            只读工具层（预算、去重、超时、轨迹）
triage/agents/             三组对照
triage/lifecycle.py        注入、确认、复位、健康检查（管理员凭据）
triage/grader.py           评分
triage/runner.py           命令行
triage/report.py           结果汇总
k8s/rbac-diag.yaml         诊断器只读权限
k8s/restricted-ns.yaml     S08 用的无权限命名空间
scripts/                   Ubuntu 部署脚本、诊断器 kubeconfig 生成、真实模型试跑、Windows 分阶段脚本
tests/                     离线测试（假集群 + 假模型）
```
