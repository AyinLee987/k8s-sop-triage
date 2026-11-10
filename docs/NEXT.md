# 交接：下一步要做的事

给新对话（或新机器）接手用。读完这一页，加上 README 和 `scenarios/CATALOG.md`，就能接着做。

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
