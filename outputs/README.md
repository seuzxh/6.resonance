# outputs

交付物目录。大文件（csv/json 等产物）gitignored；报告类 md 已随实验收口
清理（40a9532，"结论均已在 docs/ 留档"）——锚终选与 2022-24 历史外推结论见
[docs/spec/v43-best-plan.md](../docs/spec/v43-best-plan.md) §一/§六，资金流
结论见 [docs/research/moneyflow-gate-plan.md](../docs/research/moneyflow-gate-plan.md) §八，
原始报告 git 历史（≤40a9532^）可溯。

- `exp_anchor/`：锚点终选实验数据表（13 锚全流程 + 9 池拆解）。
- `exp_hist_2022/`：2022-2024 历史扩展验证数据表（时间外推 + 数据体检 +
  防御层熊市补测）。
- `exp_moneyflow/`：资金流四轮实验数据表。
- `anchor_selection/`：动态选锚预注册矩阵、纯日线诊断、相位收敛核验与净值图；
  两项主规则不采纳，结论见 [实验记录](../docs/research/anchor-selection.md)。
- `minute_resonance/`：分钟共振设计验证（report.md 在库）。
- `index_backtest_framework/`：回测框架复算（report.md 在库）。
- `oos/`：样本外验证逐日信号 / 净值 / 审计（四轨，运行中）。
- `v4/`：backtest_v41 现行产物目录（nav/trades/phase_table）。

旧版本交付 v3 已于 2026-09-27 删除（冻结参数快照录
[docs/ops/version-catalog.md](../docs/ops/version-catalog.md)）；
`archive/` 为已归档实验保留；版本目录见同上链接。

本轮成分研究的三个目录分别为 `constituent_breadth/`（原特征独立复验及成员数据）、`constituent_shrinkage/`（中性收缩初始诊断）、`constituent_shrinkage_clean/`（上市前成员记录隔离后的正式评价）。原始查询、模型和账单留本地；结论见[成分研究](../docs/research/constituent-breadth.md)。
