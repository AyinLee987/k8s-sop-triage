# CI/CD 流水线

两条 GitHub Actions 流水线，都**不调用模型、不需要 API key**。需要模型的评测仍然手动跑，原因见最后一节。

## ci.yml：每次 push 和 PR

| Job | 做什么 | 失败条件 |
| --- | --- | --- |
| `lint` | ruff，只开 E9/F63/F7/F82（语法错误、未定义名字等） | 有真 bug；风格问题不拦 |
| `test` | 离线测试（假集群 `tests/fake_cluster.py`，不连集群、不调模型） | 任一测试失败 |
| `image` | 构建 `docker/runner.Dockerfile` → Trivy 扫描 → 推到 GHCR | 有已能修复的 CRITICAL 漏洞；HIGH 只出报告 |

- `image` 依赖前两个 job，先过测试才构建。
- HIGH 不拦的原因：修它要换 kubectl 和 HolmesGPT 的 Python 依赖，也就是换实验环境，会影响前后实验的可比性。
  所以高危漏洞在两批实验之间统一升级，并记下从哪次实验起换了环境。2026-11-24 首次扫描：35 个 HIGH（kubectl 29、Python 包 5、zlib 1），0 个 CRITICAL。
- 镜像只在 main 分支 push 时推送，tag 用 commit SHA（可追溯、不可变），同时把 `main` 指向它。PR 只构建和扫描，不推送。
- 先 `load` 到本地再扫描、再推送，保证扫描的就是推送的那一个镜像。

## e2e.yml：真实集群上的冒烟测试

改动了 `triage/`、`scenarios/`、`boutique/` 或注入脚本时触发，也可以在 Actions 页面手动触发并指定场景。

1. `helm/kind-action` 在 runner 上临时建一个 kind 集群
2. 部署 Online Boutique v0.10.3，等 12 个 Deployment 全部就绪，再确认首页能打开
3. `scripts/validate_injection.py B01 B02`：确认健康 → 注入 → 确认故障生效 → 复位 → 确认恢复（和实验用的是同一套 lifecycle 代码）
4. `scripts/ci_check_validation.py`：`validate_injection.py` 遇到注入未生效时只记录、退出码仍是 0，这一步把它变成硬失败
5. 失败时打印 Pod 和事件；无论成败都把 `results/validation/` 上传为 artifact

默认场景：B01（Service targetPort 被改错，首页 500）、B02（checkoutservice 的下游地址被改错，下单失败）。两个都只用 kubectl，不依赖 tc netem，在 GitHub runner 上更稳定。

## 为什么模型评测不进 CI

- **花钱**：一次完整评测要几十到几百次模型调用，每次 push 都跑不现实。
- **结果不确定**：同一个提交跑两次，正确数可能不同。放进每次提交的 CI 会产生“随机红灯”，大家很快就会忽略它。
- **需要密钥**：来自 fork 的 PR 拿不到 Secrets，也不应该拿到。

所以分两层：CI 只测确定性的部分（代码、镜像、故障注入与复位）；模型评测手动触发或定时跑，带预算上限，结果和基线对比。
