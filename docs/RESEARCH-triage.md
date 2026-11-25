# 排障 Agent 怎么优化：近期工作调研（2026-11-25）

起因：v4 两次小规模试跑 18 次通过 8 次（`docs/V4.md` 第 7 节），用户想讨论排障流程怎么优化，先调研 arXiv 和 GitHub 上最近的做法。

## 结论先行

1. **文献里最常见、最有效的一招是"下结论前再核对一遍"**。DiagGuard 在结论前加了"先盘点有哪些证据"和"对照证据审一遍结论"两步，Acc@1 从 43.5% 提到 52.5%，token 约 1.8 倍。我们试跑没通过的 10 次里，有 5 次唯一的问题是多报（真实原因都找对了），正是这一步要管的。
2. **调用链上的延迟算错位置，是文献里最常见的遗漏**（DiagGuard 的失败分析里"调用边没验证"占失败运行的 54% 到 68%）。我们有 2 次是这个。对策有两种：给 Agent 一个算好的"页面耗时拆分 + 调用图"工具（EviRCA、GALA+ 收益最大的部分），或在 SOP 里补调用顺序和次数（不改工具）。
3. **"规定死的排查步骤"会帮倒忙**。Free to Look 一文里，模型自己决定看什么的简单框架（65% 到 77%）全面超过按 SOP 或固定流程走的 RCA Agent（16% 到 53%），原因是程序替模型决定看什么时，会跳过决定性的证据。所以往 SOP 里加的应该是知识和核对，不是更多固定步骤。
4. **我们的主线（经验先过准入检查再入库）有了直接的同类证据**。OpsHarness（港中文 + 字节）让 Agent 从对错运行里自动总结经验，每条经验都要过两道门（原题不变差、另一批题不退步），37% 被拒；去掉门槛后效果比不总结还差。
5. 公开基准里，SREGym 也发现 Agent "贪心"（把第一个看起来可疑的异常当原因，包括噪声），多个故障同时发生时只得出部分结论（端到端成功率从 63.7% 掉到 17.9%）。和我们 FL08、FV08 的现象一致。我没搜到用"同一次发布里的无害改动"当干扰项的公开基准，这是我们题库设计上可以讲的特点。

## 1. 我们的失败，在文献里叫什么

试跑没通过的 10 次（定稿判分 `results/grading/v4-pilot/grades_final.json`）：

| 我们的失败 | 次数 | 文献里的对应 | 对应的做法 |
| --- | --- | --- | --- |
| 唯一的问题是多报：Agent 自己都说影响很小的改动（adservice 缩容每页只多 1 到 2 毫秒）、已经结束的短暂中断、低把握的 sidecar | 5 | SREGym："贪心"，把第一个可疑异常当原因；DiagGuard："显眼偏差"（Salience bias，失败运行的 33% 到 42%） | 结论前核对：反事实地问"它要是正常，哪些慢请求会消失"（2.2 节）；把"正常"也当一个候选（AgentRCA） |
| 沿调用链算错延迟归属（FV02 算给 paymentservice，FV07 第 3 遍算给 cartservice） | 2 | DiagGuard："调用边没验证"（Edge untested，54% 到 68%，最常见） | 页面耗时拆分 + 调用图工具（2.3 节）；或 SOP 补调用顺序 |
| 第二个原因漏掉或被盖住（FL08 的 CPU 原因 2 次被排除，FV07 网络原因 1 次判给别处） | 3 | SREGym：多故障时"得出部分结论"；DiagGuard："锚定"（Anchor lock，28% 到 35%） | 残差核对：每个慢页面、慢的一跳都要有归属。FL08 要先排除题目本身症状太弱的可能 |
| 相似的无害改动（FL08 把延迟归到 frontend 地址改成完整域名） | 1 | DiagGuard："没和正常时段比"（Baseline gap，26% 到 52%） | 改动核对：每个改动都和改动前、和同类服务比，看得出效果才算 |
| 网络原因没说是哪一端（FV07 第 2 遍） | 1 | 无 | 回答格式里要求写"哪个 Pod 的入向" |

## 2. 几类优化做法

### 2.1 下结论前先盘点证据、再审一遍结论

- **DiagGuard**（arXiv 2608.21310，港中文（深圳），2026-08-21）。先分析 3500 条 Agent 排查轨迹，把错误归成三类：漏了决定性证据、读错了证据、没证据硬推。然后在 Agent 外面包两步：Grounder 在定位前先盘点有哪些观测可用；Verifier 在提交前按挖出来的失败类型自查（例如同一个对象、同一个指标，异常时段和正常时段比过没有；调用边有没有逐跳的证据）。AIOps 2025 数据集，Acc@1 43.5% → 52.5%（5 次运行），只加 Grounder 48.0%，只加 Verifier 47.6%；每个事件的 token 17.7 万 → 31.9 万。提示词说会公开，目前没看到。
- **Stalled, Biased, and Confused**（arXiv 2601.22208，滑铁卢大学，FORGE '26 杰出论文）。4.8 万个模拟故障、6 个模型，整理出 16 种推理失败。锚定、原地打转、随意挑证据、不随新证据更新判断，这几种和答错强相关。只考单一原因的故障。
- **我们自己的锚定试验**（`docs/ANCHORING-pilot.md`）：要求先列症状、再逐条核对（m2）以后，漏掉第二个原因的次数从约三分之二降到约一成，已经放进 v3/v4 提示词。但当时还发现"再查一遍"会换锚、把第一个原因丢掉。

### 2.2 显式的假设表和反事实核对

- **AgentRCA**（arXiv 2607.22385，EPFL，2026-07-24）。领域是工业过程，不是微服务，但做法通用：Agent 维护一张排好序的假设表，"一切正常"也是其中一个候选；工具给出和正常基准比较的方向性统计。假设表比不加结构只高几个点（83.2% → 87.6%）；去掉"和正常基准比"这一项，掉到 43.8%。更弱的小模型用假设表格式反而变差（75.5% → 71.7%）。
- **图增强多 Agent RCA**（ScienceDirect，2026，只读到搜索摘要）：Verifier 用反事实检查，问"如果怀疑的服务是健康的，下游的故障还会不会出现"；验证不通过时可以回退到假设栈里的上一个候选。这正是我们要的"贡献核对"。
- **SpecRCA**（arXiv 2601.02736，ICSE-NIER '26）：先用非 LLM 的方法一次列出很多候选，再逐个并行验证，最后汇总。初步结果，没有消融。

### 2.3 把证据整理好再给模型（改工具）

- **EviRCA**（arXiv 2609.19825，浙大 + 天翼云，2026-09-17）：确定性的程序先把指标、调用链、日志整理成一张张"证据卡"，模型只通过几个只读工具看卡片，其中 `get_call_graph` 给出按延迟和失败率排序的服务和调用边。OpenRCA 上，deepseek-v4-pro 的全对率从 13.1%（让模型自己写代码读原始数据的 RCA-Agent）提到 43.9%。消融里调用链数据最关键（在一个数据集上去掉它掉 45 个点）。
- **GALA+**（arXiv 2608.08968，ASE '26）：给模型的是每条调用边的延迟统计（p50、p95、p99、调用量），并用 p99/p50 算"不稳定度"给服务打分。Online Boutique 上 AC@1 74.4%，按 SOP 走的 Flow-of-Action 只有 48.4%。去掉这个打分掉得最多（这个消融数字来自摘要，未核实）。只考单一故障。
- **SREGym 里的 Stratus**：先过滤观测数据，每次约 53 万 token（Claude Code 约 159 万），花的 token 多少和成败无关。
- 和我们的关系：`docs/NEXT.md` 里"页面耗时统计 + 调用关系"工具、"每跳延迟指标"两条旧想法，就是这一类。文献里收益最大，但它改的是"Agent 能拿到什么证据"，基线要重测，而且要和流程改动分开测，不然分不清是哪一个起的作用。

### 2.4 少规定步骤，多给知识

- **Free to Look, Free to Reason**（arXiv 2610.35767，北大 + 北邮，2026-10-26）：90 个验证过的案例，中档模型（含 DeepSeek V4.1 Flash）。不规定调用顺序的简单框架（一次性看证据包、SQL Agent、Codex）PR@1 65% 到 77%；已发表的 RCA Agent 16% 到 53%，其中按 SOP 走的 Flow-of-Action 最好（53%）。作者的解释：程序决定读什么时，会漏掉决定性证据（例如 Flow-of-Action 匹配到的 SOP 没有看日志这一步）。例外：四种数据齐全的 17 个案例上，Flow-of-Action 和 Codex 打平。
- 对我们：基础 SOP 是 HolmesGPT 按需读取的 skill，不是固定流程，方向是对的。往里加东西时，加"事实"（调用顺序和次数）和"核对"，不要加"先做 A 再做 B"。

### 2.5 经验先过门槛再入库（对应主线）

- **OpsHarness**（arXiv 2608.25661，港中文 + 字节，2026-08-26）：不训练模型，在通用 Agent（Codex、Claude Code）外面加一个会自我进化的外壳：分层的运维知识（系统概况、从对的轨迹里挖出的流程、正反两类 if-then 规则）。每次排查后，从对的运行里挖重复步骤，从错的运行里找"从哪一步开始走偏"，提成一条条修改提案；每条提案要过两道门：内门要求原题准确率不降、成本不超 5%，外门要求在另一批重新抽样的题上不退步，不过的最多改三轮。4 个基座模型（含 DeepSeek-V4）平均：直接用 36.1%，只加初始知识 41.4%，进化后 59.0%。37% 的提案被拒；去掉门槛后比不进化还差（后两个数来自摘要，未核实）。
- 对我们：这和"经验先过准入检查再入库"是同一个问题，结论也支持门槛。它"从对错运行的分歧点总结"也对应 `docs/NEXT.md` 里"针对多次运行的分歧点写经验"的旧想法。区别是它的门槛用的是大量自动判分的题，我们每题只能跑几遍、人工判分，分辨率低得多。

### 2.6 HolmesGPT 自己的变化（GitHub）

- 我们固定在 0.46.0（`docker/runner.Dockerfile` 里的提交 27c623e）。最新正式版 0.47.0（2026-11-22）。
- 和排查方式有关的改动只看到发布说明的标题：0.47.0"下结论前先看告警历史"（ROB-664）；0.46.0 把自带评测的判分换成另一个 LLM 判分人，并加了"判分人基准"。文档说 0.26 以后用 skills 取代 runbooks（未在发布页核实）。
- 一篇独立博客转述的团队经验：七份 markdown runbook，写明作用范围、可用工具、注意事项，以及"这里没有 Loki、Tempo、Istio"这类排除规则，无效的工具调用从每次 16 次降到 2 次。只是一个两人团队的自述，没有对照实验。
- 建议：实验中途不升级 HolmesGPT（会改变基线）。"先看告警历史""写明不用查什么"的思路可以借鉴，但写进我们的 SOP 时要写成方法（例如"Pod 级限流要拆到容器看""已经结束的短暂中断解释不了持续变慢"），不能写成和题目答案对应的结论。

## 3. 公开基准（对照我们的题库，面试可讲）

| 基准 | 环境 | 和我们相关的点 |
| --- | --- | --- |
| SREGym（arXiv 2605.07161，[GitHub](https://github.com/SREGym/SREGym)） | 真实 Kubernetes，DeathStarBench、Train Ticket、Astronomy Shop 等，含 AIOpsLab、ITBench 的全部题 | 每 5 分钟随机两个短暂噪声（重启无关 Pod、加延迟、压节点）；有多个独立故障同时发生的题；LLM 判分人按 9 个是/否问题打分（定位、定性、范围各三问，至少 7/9 才算过，和人工的 kappa 0.90），多报和漏报在"范围"里扣分。说明 AIOpsLab、ITBench 的注入器是 Agent 能看到并关掉的 Pod，存在钻空子的可能 |
| Incident-Arena（arXiv 2610.00648） | 每题一个临时 Kubernetes 集群，20 道人工出的题 | 每次运行平均约 400 万 token、68 步（我们约 195 万 token） |
| SRE-Marathon（arXiv 2609.33023） | 连续运行，故障来自注入和代码/配置改动，含 Online Boutique | 端到端几乎都做不到（Online Boutique 上 1/57） |
| RCAtlas-Core（Free to Look 一文） | 8 个公开来源的 90 个离线案例 | 离线数据，不是活集群 |

我们和它们的区别：活集群 + 只读权限；干扰项是同一次发布里的无害改动、被限流的 sidecar、一直在报错的后台服务（SREGym 的噪声是短暂扰动，不是"看起来像原因的改动"）；位置和机制分开判，网络原因标了"能不能分清"。

## 4. 建议（按代价排序）

1. **结论前核对（只改提示词）**。在现有"逐条核对症状"之后，对每个候选原因回答三个问题：
   - 反事实贡献：它要是正常的，哪些慢请求会消失，大约占多少？说不出、或者占比接近 0 的，移到 not_causes。
   - 残差：还有哪些慢页面、慢的一跳没有归属？
   - 改动核对：每个改动和改动前、和同类服务比，效果看得出来吗？看不出来的不算原因。

   依据：DiagGuard（+9 个点）、图增强 RCA 的反事实 Verifier、AgentRCA 把"正常"当候选、我们自己的 m2。针对 5 次只因多报不通过、1 次相似改动、部分第二个原因。
2. **SOP 补调用顺序和次数（只改 SOP 的事实部分）**：每个页面调哪些服务、各几次，下单时的先后顺序（扣款前还有一次换算、发邮件前先清空购物车）。写成知识，不写成步骤。针对 2 次调用链归属错误。
3. **页面耗时拆分 + 调用图工具（改工具，基线重测）**：文献里收益最大，但应当在 1、2 测完后单独测。
4. **主线：经验 + 准入检查**：OpsHarness 支持门槛的必要性。等 1、2 定下来作为新的基础流程，再用学习题写经验、验证题做准入、检验题做最终评估。
5. 不升级 HolmesGPT。

测法：1 和 2 合起来当"新流程"，和现在的流程在验证题（FV01 到 FV08）上各跑 3 遍，共 48 次，按第二次试跑每次约 0.41 元算约 20 元。每组 24 次，只有 25 个百分点左右以上的差距才分得出来。试跑过的 FV01、02、07、08 的结果不算基线，两组都要新跑。检验题（FT）不碰。

## 5. 可信度说明

- 论文是用网页读取工具读的 arXiv HTML 版：每篇前约 4 万字是原文，后面是模型摘要。上面标了"摘要""未核实"的数字来自后一部分。
- 2026 年的论文大多只有 v1，没有同行评审（GALA+ 是 ASE '26，SpecRCA 是 ICSE-NIER '26，Stalled 是 FORGE '26）。各篇的基准、模型都不同，数字不能横向比。
- GitHub 上的 HolmesGPT 只看了发布页的标题，没看代码改动。博客和排行榜多是自述。

## 6. 第二轮：不靠提示词约束的方法（2026-11-25，用户问）

用户的问题：有没有不在提示词里加规定、也能让 Agent 少犯错的办法。区分的标准是：约束由代码和数据执行（工具算出来的数、程序检查、多次运行合并、训练），还是要模型自己记得遵守一段文字。注意：DiagGuard 的 Verifier 本身也是"通过提示词和工具"接进去的，只是由程序在"写出结论草稿"那一刻触发，不是一开始就写在提示词里。

| 做法 | 怎么起作用 | 文献里的证据 | 对应我们的哪类失败 | 在我们这里的代价和问题 |
| --- | --- | --- | --- | --- |
| 1. 换工具、给算力 | 让模型自己写查询或代码去算（每个操作的耗时、调用次数之比），或者由工具直接给出调用图和每一跳的耗时 | Free to Look：只给数据和代码执行的 Codex 77.2%、SQL agent 72.8%，规定步骤的 FoA 53.3%、mABC 15.6%（3 个模型、90 个案例、PR@1）；EviRCA 的调用图工具；GALA+ 的每一跳耗时；Stalled 用窄而确定的工具 | 停在被拖累的上游、漏掉第二个原因、沿调用链算错归属、机制判断（现在 Agent 是从日志里手算比例） | 要做工具（把日志和指标导出到 DuckDB 或 Python 沙箱，或者做调用图工具），基线要重测；Agent 仍须只读；有些工具会直接拆掉题目里的陷阱（比如按容器给限流就没有 sidecar 陷阱了），选工具时不能照着题目设计来 |
| 2. 程序检查后打回 | 答案出来后，程序拿数据逐条查每个原因，查不过的连同数据一起退回让它重写；退回的是数据，不是新规定 | 思路源头是 CRITIC（微软亚洲研究院、清华，2023-05，ICLR 2024）：用外部工具核对答案，把核对结果交回去改；不用外部工具的自我纠错收益很小，甚至变差。工程上是护栏框架的输出校验加重试（Pydantic AI 等）。RCA 里没找到在运行时这样做的论文：OpsHarness（港中文、字节，2026-08）的双重门槛管的是 harness 的改动，不是诊断结论（37% 的改动被拦下，不设门槛反而变差）；AgentFixer（IBM，2026-02）是离线分析运行记录，把修改建议交给开发者 | 多报（原因的时间和症状窗口对不上、只有一瞬间的事件）、JSON 写坏 | 每次多一轮；检查项必须是一般 SRE 会做的通用检查，不能从我们的题目设计里抄（那就是泄题）；通用检查能抓到的多报有限 |
| 3. 独立复核（第二次调用） | 新开一个上下文，把第一次的答案和证据交给它，让它带只读工具逐条反驳 | DiagGuard 只加 Verifier：43.5% → 47.6%（Doubao，AIOps 2025，400 个故障），token 从每个故障 17.7 万变成 34.4 万（约 2 倍，摘要，需核对）；TopoEvo 的评审按"时间先后、传播路径、故障类型模式"三项核对，定位 +3.44 个点、故障类型 +4.39 到 16.81 个点；多智能体辩论的研究提醒：大家共有同一个错误假设时会形成假共识 | 多报、停在被拖累的上游 | token 约 1.5 到 2 倍；要在 `holmes_batch` 里加第二次 `holmes ask`；同一个模型复核，盲点也一样 |
| 4. 多次运行再合并 | 同一题跑几遍，只保留多数遍都报的原因 | RCAgent 在结论阶段采样 10 次做一致性筛选；LATS-RCA 每一步按候选动作的出现频率打分；mABC 用投票（但整体只有 15.6%）。没有找到对整次运行的最终根因做多数投票的论文。Free to Look 发现单次运行噪声大（同一模型三遍 62%、69%、76%） | 多报：学习集的 5 个多报每个都只在 3 遍里出现 1 次 | 每次诊断 3 倍 token；只出现 1 次的真实第二原因也会被删掉（FL08 的 cartservice CPU 3 遍只找到 1 次）。可以用现有的运行离线算，不花钱 |
| 5. 训练模型 | 微调、强化学习、偏好优化 | ThinkFL（强化微调）；2611.02416 用 DPO 训练 4B 小模型，一次调用在 Telecom、Bank、AIOps 2025 上精确匹配最高；5G 网络 RCA 用 SFT + GRPO | 全部 | 不适用：DeepSeek 只能调 API，学习集只有 30 次运行，也不是本项目要回答的问题 |

另外两条：

- **强制结构化输出**（JSON schema，HolmesGPT 的 HTTP 接口有 `response_format`）：只解决 JSON 写坏（学习集 2/24 次、试跑 1/18 次），不影响对错。
- **由代码规定步骤**（状态机、FoA 这类）：Free to Look 统计了各个 Agent 有多少次模型调用是程序规定的（FoA 51%、DecompRCA 100%），规定得越多越差，理由是"流程不合适这个案例时，写死在控制流里就有害"。这是反面证据。

**建议（按代价排序）**：

1. **不花钱**：用现有的运行（学习集 30 次、试跑 18 次）离线算"3 遍投票"的结果并按规则判分。能看出多报是随机的（投票就能去掉）还是系统性的，也能看出会丢掉多少只找到一次的真实原因。
2. **独立复核**（第二次调用，新上下文，带只读工具）：证据最直接（DiagGuard 只加 Verifier +4.1 个点），代价约 2 倍 token；主 Agent 的提示词不动。程序检查打回只适合做 JSON 和明显的时间对不上这类通用检查。
3. **换工具**：文献里收益最大，但改动也最大、要重测基线，并且要避免工具本身把题目的陷阱拆掉。放在 2 之后单独测。

## 来源

- [Beyond Fault Localization（DiagGuard），arXiv 2608.21310](https://arxiv.org/html/2608.21310v1)
- [Free to Look, Free to Reason，arXiv 2610.35767](https://arxiv.org/html/2610.35767)
- [EviRCA，arXiv 2609.19825](https://arxiv.org/abs/2609.19825)
- [GALA+，arXiv 2608.08968](https://arxiv.org/abs/2608.08968)
- [OpsHarness（From General Agents to RCA Experts），arXiv 2608.25661](https://arxiv.org/html/2608.25661)
- [SpecRCA（Hypothesize-Then-Verify），arXiv 2601.02736](https://arxiv.org/abs/2601.02736)
- [AgentRCA（Evidence-Grounded Reasoning），arXiv 2607.22385](https://arxiv.org/abs/2607.22385)
- [Stalled, Biased, and Confused，arXiv 2601.22208](https://arxiv.org/abs/2601.22208)
- [Graph-Augmented Multi-Agent Robust RCA in AIOps，ScienceDirect](https://www.sciencedirect.com/org/science/article/pii/S1546221826005011)
- [SREGym，arXiv 2605.07161](https://arxiv.org/html/2605.07161v2) / [GitHub](https://github.com/SREGym/SREGym)
- [Incident-Arena，arXiv 2610.00648](https://arxiv.org/html/2610.00648v1)
- [SRE-Marathon，arXiv 2609.33023](https://arxiv.org/html/2609.33023)
- [HolmesGPT 发布页](https://github.com/HolmesGPT/holmesgpt/releases)
- [HolmesGPT runbook 经验（博客）](https://www.matthewswong.com/en/blog/holmesgpt-runbooks-prometheus-slack-investigations/)
- [Open Source SRE Agents in 2026（Aurora 厂商博客，排名仅供参考）](https://www.aurorasre.ai/blog/top-ai-sre-tools-2026)
- [TopoEvo，arXiv 2605.15611](https://arxiv.org/html/2605.15611)
- [RCAgent，arXiv 2310.16340](https://arxiv.org/pdf/2310.16340)
- [LATS-RCA / Multi-Agent Systems for RCA in Microservices，arXiv 2605.03505](https://arxiv.org/html/2605.03505)
- [mABC，arXiv 2404.12135](https://arxiv.org/abs/2404.12135)
- [多智能体辩论在编程任务上的实证研究，arXiv 2503.12029](https://arxiv.org/pdf/2503.12029)
- [ThinkFL，arXiv 2504.18776](https://arxiv.org/pdf/2504.18776)
- [Fault-Free Profiles and Decision-Level Training（小模型 DPO），arXiv 2611.02416](https://arxiv.org/html/2611.02416)
- [Reasoning Language Models for RCA in 5G，arXiv 2507.21974](https://arxiv.org/pdf/2507.21974)
- [AgentFixer，arXiv 2603.29848](https://arxiv.org/html/2603.29848)
- [CRITIC，arXiv 2305.11738](https://arxiv.org/abs/2305.11738)
- [Code as Agent Harness（综述），arXiv 2605.18747](https://arxiv.org/pdf/2605.18747)
- [Simplifying RCA in Kubernetes with StateGraph and LLM，arXiv 2506.02490](https://arxiv.org/html/2506.02490v1)
- [sre-harness（GitHub）](https://github.com/ajinb/sre-harness)
- [Pydantic AI 的输入、输出和工具护栏](https://pydantic.dev/docs/ai/harness/guardrails/)
- [HolmesGPT 结构化输出（DeepWiki，自动生成）](https://deepwiki.com/HolmesGPT/holmesgpt/3.9-structured-output-processing)
