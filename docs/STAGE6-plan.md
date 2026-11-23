# 阶段 6：最终评估的设置（2026-11-23 开跑前定稿，用户已确认；跑完不改）

这是检验题（CT）第一次用于比较。从这次提交起，经验库、写作规则、Agent 的设置、判分规则都不再改。

## 题

CT 的 12 道：CT01、CT02、CT03、CT04、CT05、CT07、CT08、CT09、CT11、CT12、CT15、CT16（阶段 3 筛出，名单见 `docs/STAGE3.md`）。每组每题 5 遍。

## 四组

| 组 | 经验库 | 写经验的模型 | 材料 | 结果目录 |
| --- | --- | --- | --- | --- |
| S0 | 无（`holmes/skills/svc-sop-v0`） | | | `results/holmes_batch/v2-S0`（已有 26 次，补 34 次） |
| B | `holmes/skills/xinc-B-cand`（23 条） | Claude Opus 5.5 | 一次性诊断 | `results/holmes_batch/v2-B` |
| D | `holmes/skills/xinc-D-cand`（11 条） | Claude Opus 5.5 | 诊断 + 工程师修复记录 | `results/holmes_batch/v2-D` |
| Dds | `holmes/skills/xinc-D-ds`（16 条） | deepseek-flash | 同 D | `results/holmes_batch/v2-Dds` |

- 三个库都由 22 道学习题（BH01–15、CL01–07）按同一顺序写成，写作规则都是 `reflect.py --method --scope`，都看不到真实根因。
- B、D 在阶段 5 过了准入检查，没有条目被删，所以用的是整库。Dds 是阶段 5 之后加的对照组，写完直接用，没有过准入检查。
- 每个写作模型只写了一遍；D 和 Dds 的差别里分不出模型和单次写作的运气。
- 排查的 Agent 四组相同：HolmesGPT 0.46.0、deepseek-flash、`-Metrics`、`-MaxSteps 100`、`--frame prod`。

## 运行顺序

四组按遍数轮流：都跑完第 1 遍再跑第 2 遍（S0 已有的遍数跳过）。这样时间先后、接口状态对四组是均摊的。出错没有答案的运行在整批结束后重跑。

## 判分和报告

- 判分规则 `boutique/eval_grades_v2.json`，口径同阶段 3、5（含 `docs/STAGE5.md` 末尾写明的网络题口径）。
  新的 214 次运行混在一起打乱、遮住组别后人工判；S0 已判过的 26 次不重判。
- 每组报三个数：平均通过率、5 遍全通过的题数、每通过一次的 token。另报严格通过率，位置和机制分开，网络 / CPU / 配置 / 应用延迟分开。
- 比较：B、D 对 S0（经验有没有用）；D 对 B（修复记录）；D 对 Dds（写作模型）。
- 另外单独报一项（阶段 5 在验证题上看到的线索，事先写下）：在位置已经找对的网络原因里，机制记为"对或倾向对"的比例，按组比较。
- 分辨率：每组 60 次，两组相差不到约 25 个百分点一律写"分不清"。

## 预算

214 次，估 50 到 65 元，约 28 小时。超过 78 元先停。
