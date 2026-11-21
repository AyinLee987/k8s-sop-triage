# 交接：下一步要做的事

给新对话（或新机器）接手用。读完这一页，加上 README 和 `scenarios/CATALOG.md`，就能接着做。

阶段性成果汇总（2026-11-15）：https://claude.ai/artifact/MjjL9ouRpQDK8mwdPWwHX6

## 待办：下一版题库和证据来源（2026-11-21 记）

背景：Online Boutique 题库（学习集 BH01–BH15、检验集 BT01–BT12）里，"变慢"的题全部是 Pod 网络延迟或丢包
（学习集 13 道、检验集 7 道），另有 2 道检验题是应用自身被配置了延迟，没有 CPU 限流的题。Agent 在这些题上
有时误判成 CPU，或者写"网络还是 CPU 无法区分"。检验结果和判分见 `boutique/eval_grades.json`。

**决定：这 7 道网络题不删。** 理由：

- 删掉后检验集只剩四个组全对的 5 道题，没有区分度。
- 它们是难，不是无解：带经验的组每遍能答对 4 到 7 道，只读权限下有证据可用（请求晚到而处理只要几毫秒；
  整个命名空间只有一个 Pod 探针失败）。
- 看了结果再删题，等于按 Agent 的表现挑题，结论不可信。

要做的事：

1. **换掉延迟的数值。** 现在注入的是 200 毫秒，恰好是 CPU 限流周期（100 毫秒）的整数倍，无意中让两种解释
   更难分。改成 130 或 270 毫秒这类不成倍数的值（`boutique/make_hard_scenarios.py`）。
2. **判分分成两层。** 分开记"定位对不对（是哪个服务）"和"机制对不对（网络还是别的）"，让"证据不足所以
   不下结论"的回答不和"服务都找错了"混在一起。2026-11-21 的结果已经显示这一点很要紧：D 组领先（86% 对
   62%、57%）主要来自它更敢写"最可能是网络"，按"没说死就不算对"判则三组持平（52%、48%、48%）。
3. **加 CPU 限流引起变慢的题。** 现在变慢的答案全是网络，"变慢就说是网络"的经验可以通吃，D 组的经验有这个
   倾向。加了之后"网络还是 CPU"才是真正被考到的判断。早期实验集的 L03 就是 CPU 限流（见下面 2026-11-12
   的表），做法可以参考；`scripts/sweep_cpu_limit.py` 可用来找合适的上限。
4. **给 Agent 补上监控指标，开一个对照组。** 工具已写好并同步到笔记本（2026-11-21）：`boutique_pod_metrics`，
   用 `-Metrics` 开关启用，默认关闭。还没做的：在真实集群上验证能读到数据并记下健康基线
   （`.\scripts\Run-Laptop.ps1 -Action metrics`）；带 `-Metrics` 试跑 1 道题；再决定对照组怎么跑。
   这条线回答的是"Agent 能拿到什么证据"，和"经验怎么写"是两条线，结论不要混在一起。
5. **新题库要重新跑基线。** 上面 1、3 两项改完就是新一版题库，不和 2026-11-20、21 的结果混着比。

相关的另外两条想法（未定是否做）：

- 做一个固定输出"各页面耗时统计 + 调用关系"的工具，替掉 Agent 用肉眼读日志的那部分（48 次排查里读日志占
  全部工具调用的六成，记错调用关系是 BT01 反复出错的原因）。
- 针对多次运行的分歧点写经验（参考 Closing the Consistency Gap，arXiv 2609.08832）。

## 进度更新（2026-11-12）

真集群上的"变慢但 Pod 都正常"实验（L01–L05，HolmesGPT + DeepSeek，每格 3 次，人工判定）：

| 场景 | 不加经验 | 2 条 | 16 条 | 50 条（网络 9:代码 1） | 50 条（1:1） | 50 条（1:4） |
| --- | --- | --- | --- | --- | --- | --- |
| L01 代码延迟 | 0/3 | 1/3 | 0/3 | 0/3 | 0/3 | 0/3 |
| L02 单 Pod 网络延迟 | 0/3 | 3/3 | 2/3 | 3/3 | 3/3 | 3/3 |
| L03 CPU 限流 | 3/3 | 3/3 | 3/3 | 3/3 | – | – |
| L04 网络延迟 + 无害的 CPU 变更 | 0/3 | – | 3/3 | 3/3 | – | – |
| L05 Redis 的 Pod 网络延迟 | 0/3 | – | 2/3 | 1/3 | 0/3 | 2/3 |

- 结果在用户桌面 `results/holmes_batch/latent-*`（未提交）。自动评分不可用，必须读回答。
- 目前的结论：经验能补"不知道往哪查"，补不了"查不到证据"；有没有那一条对口经验比条数和比例重要。
- L05 受一条真实的 Redis "AOF fsync 慢"告警干扰（笔记本磁盘忙时产生），结果波动大。

补充（2026-11-12 晚，均为 3 次/格）：

| 场景 | 23 条去重（exp-uniq） | + L01 的 bad case，随手写（exp-uniq-bad-s） | + L01 的 bad case，复盘写（exp-uniq-bad） |
| --- | --- | --- | --- |
| L01 | 1/3 | – | – |
| L02 | 3/3 | – | – |
| L04 | 3/3 | 3/3 | 3/3 |
| L05 | 0/3 | 0/3（1 次归因新版本） | 3/3 |

- 随手写的那条标题几乎等于症状描述，6 次全部被第一个打开；复盘写的那条标题抽象，6 次一次都没被打开。
- 因此 exp-uniq 和 exp-uniq-bad 实际读到的经验相同（第 2、7 条），L05 却是 0/3 对 3/3：L05 在 3 次重复下的波动覆盖了整个范围，凡是只靠 L05 一格得出的比较都不可靠。
- 真集群带经验的 81 次里，被不对口经验带偏共 6 次，全部在 L01 和 L05。

### 待做

1. **没见过的问题 + 很多经验**（用户已同意方向，暂缓）：原因不在 50 条经验里、症状又像经验里常见原因的题。
   候选：批处理任务全量扫描拖慢 Redis；Redis 的 CPU limit 过低。条件：不加经验 / 50 条。
   除对错外记录"硬套率"（结论是否等于某条经验的原因）和是否明确写"经验不适用"。约 12 次。
   先在云端实测全量扫描能否把 Redis 拖慢到可测的程度。
2. L05 去掉 AOF 告警残留后重跑（重启 Redis 或清日志），看波动是否来自这条干扰。
3. 关键格子补到 6 次重复。
4. 中等规模（4 节点、节点级故障）和完整规模：等用户的新台式机。

## 进度更新（2026-11-10 晚）

- 云端环境已搭好：k3s 单节点集群 + tiktok-k8s-lab，用 `bash scripts/setup_cloud_k3s.sh /home/claude/tiktok-k8s-lab` 可重建（虚拟机重启后需要重跑；业务源码要先从用户桌面同步到 `/home/claude/tiktok-k8s-lab`）。
- 16 个场景全部在真集群上验证过（`scripts/validate_injection.py`，结果在 `results/validation/` 和各场景的 `realism.validation`），过程中修了 6 个场景问题（`docs/ISSUES.md` E-013）。
- 规则基线在真集群开发集上 9/9（修复了只读一个副本日志的问题，E-014）。保留集还没跑过任何诊断。
- 下一步从下面的第 4 步开始（第 1–3 步已完成）；调用 DeepSeek 需要先在云端环境里配好"网络密钥"（host 填 api.deepseek.com），密钥不进虚拟机、不发在聊天里。

## 现在的状态（2026-11-10）

- 项目：K8s 排障 agent 的过拟合实验。核心问题：把 bad case 不断写进 SOP / skill，原本能查对的场景会不会开始查错。
- 代码：16 个有出处的故障场景（R01–R16）、三组对照 agent（规则 / 文本 SOP / 代码 SOP）、注入—确认—诊断—评分—复位流程、bad case 剂量实验。91 个离线测试全部通过。
- 新决定：被测 agent 改用 **HolmesGPT**（CNCF 开源排障 agent，Python，支持 DeepSeek）。调研报告：https://claude.ai/artifact/J4gsCbxexx1svpWfhEXgGS
- 业务环境：`tiktok-k8s-lab`（api ×2、worker、Redis StatefulSet），在用户笔记本 `Desktop\tiktok-k8s-lab`，部署清单在它的 `k8s/` 目录。

## 下一步（需要能拉镜像、能装包的环境）

1. **搭环境**：装 kind，建单节点集群；用 `tiktok-k8s-lab` 的 Dockerfile 构建镜像 `tiktok-k8s-lab:v1` 并 `kind load`；按 `k8s/` 部署；创建 `redis-auth` Secret（两个键：`password`、`redis-auth.conf`，内容 `requirepass <密码>`）。注意 api/worker 的 `imagePullPolicy: Never`。
2. **生成只读凭据**：`k8s/rbac-diag.yaml` + `scripts/make_diag_kubeconfig.py`；`python -m triage.runner check-env`。
3. **验证注入**：`python -m triage.runner smoke --scenario R06,R02,R07 --rounds 3`，再逐个验证 `validated: false` 的场景（R01,R03,R05,R09,R13,R14,R15），把结果写回各场景的 `realism`。
4. **HolmesGPT 跑通一个场景**：安装锁定版本（调研时用的是 `main` 提交 `27c623e`），模型配 DeepSeek（LiteLLM，`openai/<模型名>` + `api_base`），只启用 `kubernetes/core` 和 `kubernetes/logs`，`KUBECONFIG` 指向只读凭据，对 R06 跑 `holmes ask`。
5. **写适配层**：把 HolmesGPT 接进 `run_matrix`，输出转成我们的诊断 JSON（见 `docs/ISSUES.md` E-010），SOP 改写成 `SKILL.md`。
6. **小规模试跑剂量实验**：2–3 个场景，测出单次成本，再定规模。

## 约束

- DeepSeek 密钥只放在 `.env`，不要发在聊天里，`.env` 和 `.local/` 不提交。
- 保留集只在定稿后用 `--finalize` 跑一次。
- tiktok-k8s-lab 的规则：不打印凭据；不强杀 Docker；没有明确要求不删 PVC、命名空间和集群。
- 工程问题记到 `docs/ISSUES.md`（只记工程问题，不记配置问题）。
