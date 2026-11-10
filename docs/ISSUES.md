# 问题记录

项目过程中遇到的所有问题，按时间倒序记录。每条写清楚：现象、原因、怎么处理的、现在的状态。
新问题加在最上面。

状态说明：**已解决** / **已规避**（绕开了但根因还在）/ **待验证**（要在真集群或真模型上确认）/ **未解决**

---

## 2026-11-10

### I-019 HolmesGPT 自带评测的"裁判"不支持 DeepSeek
- 现象：HolmesGPT 的评测用大模型给答案打分，打分代码（`tests/llm/utils/classifiers.py`）只接 OpenAI、Azure、OpenRouter。
- 影响：不能直接用它的评测框架给 DeepSeek 的结果打分。
- 处理：打分继续用我们自己的确定性评分器，只把 HolmesGPT 当作被测 agent。
- 状态：已规避

### I-018 HolmesGPT 的答案是自由文本，我们的评分器要结构化 JSON
- 现象：HolmesGPT 没有"提交诊断"这类工具，模型不再调工具时，最后那段文字就是答案。
- 处理方案：试 `response_format` 参数，或用 `system_prompt_additions` 追加输出格式要求，实测哪种对 DeepSeek 稳定。
- 状态：待验证

### I-017 HolmesGPT 无法在我们的假集群上离线试跑
- 现象：HolmesGPT 直接调 kubectl（带 jq 过滤等参数），我们的假集群只模拟了自己那 7 个工具。
- 影响：HolmesGPT 这一组从一开始就要在真 kind 集群上跑，不能像现在三组那样先离线试跑。
- 处理：先在笔记本的 tiktok-k8s-lab 上手动跑通一个场景。
- 状态：未解决

### I-016 文档里加粗紧跟中文句号时不渲染
- 现象：在 Docs 里写 `**某某。**后文`，加粗没生效，原样显示星号。
- 原因：Markdown 规定，结尾的 `**` 前面是标点、后面紧跟文字时，不算加粗结束。
- 处理：把句号移到加粗外面，写成 `**某某**。后文`。
- 状态：已解决（以前也遇到过冒号的同一问题，见 I-005）

### I-015 克隆的 HolmesGPT 仓库是不受信任的代码
- 处理：克隆到单独目录 `/home/claude/research/holmesgpt`，只用 grep、sed 等只读命令阅读，不在里面执行 Python 或构建工具。
- 状态：已规避

### I-014 GitHub 仓库名少了一个 k
- 现象：用户新建的仓库叫 `8s-sop-triage`。
- 处理：用户在 GitHub 上改名为 `k8s-sop-triage`。
- 状态：已解决

### I-013 推送失败：会话没有关联 GitHub
- 现象：`add_repo` 报错，要求先关联 GitHub 账号。
- 处理：用户在 claude.ai 设置里连接了 GitHub，之后推送成功（`main` 分支）。
- 状态：已解决

### I-012 Stack Overflow 无法访问
- 现象：调研真实故障时，Stack Overflow 被网络代理拦截（WebFetch 返回 SITE_BLOCKED，限定该站点的搜索也被拒）。
- 影响：拿不到投票数和浏览量。
- 处理：热度改用 GitHub issue 讨论量、k8s.af 收录次数、公开事故复盘代替；没有使用第三方镜像站。
- 状态：已规避

### I-011 规则基线在假集群上几乎满分，保留集失去意义
- 现象：v2 场景写完后，规则基线在 16 个场景里答对 15 个。
- 原因：SOP 把每个保留集场景的机制都写得很具体，而假集群的证据又很干净。
- 处理：保留一个故意的 SOP 盲区：R14（数据卷挂错路径）的排查步骤从 SOP 里删掉，只在基线里保留挂载路径，答案类别改成 `other`。现在规则答对 14/16。
- 遗留：假集群上的高分不代表真集群上的表现，真集群上的噪声和间歇性现象（R03、R13、R15）才是检验点。
- 状态：已规避

### I-010 bad case 的"噪声"条目里混进了对保留集有用的提示
- 现象：N38（"envFrom 注入的环境变量不会自动更新"）和 N39（"探针超时设得太短会误判"）本来归为"与本系统无关"，但它们恰好是保留集 R11、R13 的正确提示。
- 影响：会在保留集上悄悄帮模型，污染实验。
- 处理：换成真正无关的条目，并加了测试 `test_noise_notes_do_not_hint_at_holdout_mechanisms` 防止再出现。
- 状态：已解决

### I-009 一个 Service 机制同时出现在开发集和保留集
- 现象：R04（targetPort 写错）在开发集，selector 写错原本也想放进保留集，违反"同一机制只出现在一个集合里"的规则。
- 处理：selector 写错改为模板 `service_routing` 的一个变体，整个模板放进开发集。
- 状态：已解决

### I-008 Redis 依赖错误时，"endpoints 为空"会被误判成 Service 问题
- 现象：Redis 自己不 Ready 时，Service redis 的 endpoints 也为空，规则会先判成 `service_misconfigured`。
- 处理：只有 Redis Pod 本身就绪时，"endpoints 为空"才算 Service 问题。
- 状态：已解决

### I-007 第一版场景"太 AI 化"
- 现象：用户反馈 v1 的 14 个场景是照着代码倒推的（"把某个变量改成明显错误的值"），和生产问题有差距。
- 处理：调研 ITBench、AIOpsLab、SREGym、k8s-ai-bench 的故障库，以及 k8s.af 和 GitHub issue 里的真实事故，重写为 16 个有出处的场景（`scenarios/CATALOG.md`）。v1 归档在 `scenarios/archive/`。
- 状态：已解决

### I-006 7 个 v2 场景的注入效果还没在真集群上验证
- 场景：R01（写满时 redis-py 抛的异常类型）、R03（Redis 探针失败事件里有没有 WRONGPASS）、R05（24Mi 能否稳定 OOM）、R09（FailedMount 事件文本）、R13（Redis 重启时 api 存活探针是否失败）、R14（改挂载后数据是否为空）、R15（20m CPU 能否造成超时）。
- 处理：每个场景的 `realism.validate_note` 写明要确认什么；Ubuntu 机器到了之后先用 smoke 逐个验证。注入没生效会记为环境失败，不算诊断错。
- 状态：待验证

### I-005 Docs 中加粗紧跟中文冒号不渲染
- 处理：冒号移到加粗外面。
- 状态：已解决

## 之前（11/10 之前）

### I-004 Windows PowerShell 5.1 读 UTF-8 脚本乱码
- 处理：`.ps1` 保存为带 BOM 的 UTF-8、CRLF 换行；Python 侧设 `PYTHONUTF8=1`。
- 状态：已解决

### I-003 远程拒绝提交 `.env.windows.example`
- 现象：以 `.env` 开头的文件名被当成敏感文件拦截。
- 处理：改名为 `windows.env.example`，同步更新引用。
- 状态：已解决

### I-002 PowerShell 里可能没有 `py` 启动器
- 处理：脚本里先试 `py`，失败再回退到 `python`。
- 状态：已解决

### I-001 桌面上残留的旧 SOP 文件会被拼进 SOP
- 现象：桌面目录里旧的 `sop/triage_sop.md` 会被 `sop/*.md` 一起读进去。
- 原因：设备上不允许删除文件。
- 处理：移到 `_to_delete/` 目录，由用户手动删除。
- 状态：已规避

### 开发中修过的小问题
- `find_abnormal` 漏掉"在运行但不就绪、没有重启"的 Pod → 新增"not ready"判断（测试发现）。
- 假模型没有 `.model` 属性导致预检报错 → 改用 `llm.describe()`。
- 测试辅助函数参数名 `name` 和工具参数冲突 → 改名 `_tool`。
- S08 的关键证据 "rec-feature" 出现在症状里 → 从关键证据中去掉。
