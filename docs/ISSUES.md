# 工程问题记录

只记工程问题：设计缺陷、实验方法上的坑、代码 bug、和外部系统集成时的技术限制。
账号权限、网络、文件名、工具渲染这类配置和环境问题不记。

每条写清楚：现象、原因、怎么处理的、现在的状态。新问题加在最上面。

状态说明：**已解决** / **已规避**（绕开了但根因还在）/ **待验证**（要在真集群或真模型上确认）/ **未解决**

---

## E-011 HolmesGPT 自带评测的"裁判"不支持 DeepSeek
- 现象：HolmesGPT 的评测用大模型给答案打分，打分代码（`tests/llm/utils/classifiers.py`）只接 OpenAI、Azure、OpenRouter。
- 影响：不能直接用它的评测框架给 DeepSeek 的结果打分。
- 处理：打分继续用我们自己的确定性评分器，只把 HolmesGPT 当作被测 agent。
- 状态：已规避

## E-010 HolmesGPT 的答案是自由文本，我们的评分器要结构化 JSON
- 现象：HolmesGPT 没有"提交诊断"这类工具，模型不再调工具时，最后那段文字就是答案。
- 处理方案：试 `response_format` 参数，或用 `system_prompt_additions` 追加输出格式要求，实测哪种对 DeepSeek 稳定。
- 状态：待验证

## E-009 HolmesGPT 无法在我们的假集群上离线试跑
- 现象：HolmesGPT 直接调 kubectl（带 jq 过滤等参数），我们的假集群只模拟了自己那 7 个工具。
- 影响：HolmesGPT 这一组从一开始就要在真 kind 集群上跑，不能像现在三组那样先离线试跑。
- 状态：未解决

## E-008 规则基线在假集群上几乎满分，保留集失去意义
- 现象：v2 场景写完后，规则基线在 16 个场景里答对 15 个。
- 原因：SOP 把每个保留集场景的机制都写得很具体，而假集群的证据又很干净。
- 处理：保留一个故意的 SOP 盲区：R14（数据卷挂错路径）的排查步骤从 SOP 里删掉，只在基线里保留挂载路径，答案类别改成 `other`。现在规则答对 14/16。
- 遗留：假集群上的高分不代表真集群上的表现，真集群上的噪声和间歇性现象（R03、R13、R15）才是检验点。
- 状态：已规避

## E-007 bad case 的"噪声"条目里混进了对保留集有用的提示
- 现象：N38（"envFrom 注入的环境变量不会自动更新"）和 N39（"探针超时设得太短会误判"）本来归为"与本系统无关"，但它们恰好是保留集 R11、R13 的正确提示。
- 影响：会在保留集上悄悄帮模型，污染实验。
- 处理：换成真正无关的条目，并加了测试 `test_noise_notes_do_not_hint_at_holdout_mechanisms` 防止再出现。
- 状态：已解决

## E-006 一个 Service 机制同时出现在开发集和保留集
- 现象：R04（targetPort 写错）在开发集，selector 写错原本也想放进保留集，违反"同一机制只出现在一个集合里"的规则。
- 处理：selector 写错改为模板 `service_routing` 的一个变体，整个模板放进开发集。
- 状态：已解决

## E-005 Redis 自己不就绪时，"endpoints 为空"会被误判成 Service 问题
- 现象：Redis 自己不 Ready 时，Service redis 的 endpoints 也为空，规则会先判成 `service_misconfigured`。
- 处理：只有 Redis Pod 本身就绪时，"endpoints 为空"才算 Service 问题。
- 状态：已解决

## E-004 第一版场景"太 AI 化"
- 现象：v1 的 14 个场景是照着代码倒推的（"把某个变量改成明显错误的值"），和生产问题有差距。
- 处理：调研 ITBench、AIOpsLab、SREGym、k8s-ai-bench 的故障库，以及 k8s.af 和 GitHub issue 里的真实事故，重写为 16 个有出处的场景（`scenarios/CATALOG.md`）。v1 归档在 `scenarios/archive/`。
- 状态：已解决

## E-003 7 个 v2 场景的注入效果还没在真集群上验证
- 场景：R01（写满时 redis-py 抛的异常类型）、R03（Redis 探针失败事件里有没有 WRONGPASS）、R05（24Mi 能否稳定 OOM）、R09（FailedMount 事件文本）、R13（Redis 重启时 api 存活探针是否失败）、R14（改挂载后数据是否为空）、R15（20m CPU 能否造成超时）。
- 处理：每个场景的 `realism.validate_note` 写明要确认什么；先用 smoke 逐个验证。注入没生效会记为环境失败，不算诊断错。
- 状态：待验证

## E-002 Windows PowerShell 5.1 读 UTF-8 脚本乱码
- 处理：`.ps1` 保存为带 BOM 的 UTF-8、CRLF 换行；Python 侧设 `PYTHONUTF8=1`；脚本里先试 `py` 启动器，失败再回退到 `python`。
- 状态：已解决

## E-001 开发中修过的小 bug
- `find_abnormal` 漏掉"在运行但不就绪、没有重启"的 Pod → 新增"not ready"判断（测试发现）。
- 假模型没有 `.model` 属性导致预检报错 → 改用 `llm.describe()`。
- 测试辅助函数参数名 `name` 和工具参数冲突 → 改名 `_tool`。
- 场景的关键证据出现在症状文本里（会泄漏答案）→ 从关键证据中去掉，并加了"症状不泄漏答案"的测试。
