# 交接：下一步要做的事

给新对话（或新机器）接手用。读完这一页，加上 README 和 `scenarios/CATALOG.md`，就能接着做。

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
