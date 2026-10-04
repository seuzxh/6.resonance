# 文档导航：按「寻优研究 → 冻结规格 → 每日运行」归类

> 2026-09-26 按架构分层归类。本项目是「参数即模型」形态的量化研究项目：
> **寻优（训练）与每日预测（推理）是两个独立过程**，通过不可变的
> **冻结规格**（工件）解耦——研究轨只写结论与规格，运行轨只读规格，
> 互不调用、失败域隔离。工件本体在代码（`config.V43_ANCHOR_POOL` +
> `ops/signal_daily.py FROZEN`），文档是规格的叙述面，git commit 即版本号；
> 「评价期禁改参」= 晋升门，新版本只能整体替换、不能原地修改。

## 当前入口

- [当前详细方案](spec/current-plan.md)：生产三锚、研究双锚、候选边界与完整持仓规则。
- [当前流程图](diagrams/current-plan-20261003/current-workflow.html)：已按源码核对的每日运行、数据降级与研究停止流程。

## spec/ 冻结规格（产物层——两过程的唯一接口）

- [v43-best-plan.md](spec/v43-best-plan.md) — V4.3 三锚动选历史收盘口径：
  冻结参数（§三）、组件来源表、领先分布与 2022-24 时间外推警示。
- [v44-open-exec-plan.md](spec/v44-open-exec-plan.md) — V4.4 成交时点
  切换 T+1 开盘（2026-09-29 已实施）：语义定义、引擎
  改动记录、锚点重算与四轨评价重启。

## ops/ 每日运行（推理过程）

- [oos-validation-design.md](ops/oos-validation-design.md) — OOS 四轨 runner
  预注册设计：判据、运行纪律与三道数据防线。runner 为
  `ops/signal_daily.py`（15:05 后运行、OOS 起点无状态重放、幂等）。
- [version-catalog.md](ops/version-catalog.md) — 版本目录：tag、口径要点、
  结论位置与归档记录（版本生命周期规则见 CLAUDE.md）。

## research/ 寻优研究（历史过程——写侧）

- [exposure-roadmap.md](research/exposure-roadmap.md) — 下一轮开仓、空仓与仓位恢复方向：既有负结论、机制差异、账户前置要求和待冻结判据；阶段一归因已执行，阶段二未开跑。
- [exposure-attribution-plan.md](research/exposure-attribution-plan.md) — 2026-10-04风险暴露环节归因阶段一：只读双锚连续账单，定位入场来源与退出原因中的亏损集中环节；不产生采纳结论。

- [constituent-breadth.md](research/constituent-breadth.md) — 双锚独立学习、历史成员核验、中性收缩与上市边界隔离复验；包含数据资格及实际学习覆盖限制。

- [qlib-autonomous-summary.md](research/qlib-autonomous-summary.md) — 2026-10-03
  自主探索总账：前五轮5115次，加后续5670次，共10785次账户回放，尚无稳健采纳方案，含观察点与净值图。

- [qlib-label-recheck.md](research/qlib-label-recheck.md) — 标签纠错后受控
  重训75次回放，正向模型未胜过原分钟重排。
- [qlib-decision-learning.md](research/qlib-decision-learning.md) — 正向可成交
  目标的滚动入场学习，1125次回放未同时通过胜率与跨阶段门槛。
- [qlib-candidate-rerank.md](research/qlib-candidate-rerank.md) — 1395次候选
  学习重排有单点改善，但邻域未通过，保留观察。
- [qlib-episode-learning.md](research/qlib-episode-learning.md) — 按原退出
  规则定义完整交易目标，1125次回放仍未改善完整交易胜率。
- [qlib-volume-rerank.md](research/qlib-volume-rerank.md) — 量价信息增强及
  同覆盖对照，1395次回放未通过新增信息与邻域要求。

- [resonance-alternatives.md](research/resonance-alternatives.md) — 风险调整动量、
  多锚共同排序与锚占用硬上限研究（2026-10-03）：两轮1710次回测完成，
  9个中心均不采纳；含固定池交互复验、有限观察与净值图。

- [anchor-selection.md](research/anchor-selection.md) — 单锚概念策略表现选锚与
  软占用惩罚实验（2026-10-03）：预注册主矩阵和纯日线诊断均完成，两项主规则
  不采纳；含相位收敛、分钟陈旧窗口审计及图表入口。
- [experiment-playbook.md](research/experiment-playbook.md) — 方法论手册：
  预注册协议、七条定律、负结论登记表（新实验必读 §五检查清单）。
- [qlib-validation-plan.md](research/qlib-validation-plan.md) — qlib 独立引擎
  复算验证（parquet 直接驱动、无需转 bin；预注册设计，P0 冒烟已过，
  待过 §九决策项；附 §十 5min 因子+训练扩展轨草案）。
- [moneyflow-gate-plan.md](research/moneyflow-gate-plan.md) — 资金流闸门探索
  （预注册 → 四轮实验 → 全线收官存档）。
- [anchor830-plan.md](research/anchor830-plan.md) — 830000 平均股价锚验证
  （预注册 → 执行 → HARMFUL 收口：等权宽基锚方向关闭，2026-10-02）。
- [minute-resonance-design.md](research/minute-resonance-design.md) — 分钟层
  设计演化存档（dyn5→v4；现行口径已由 V4.1+ 取代，其成本工程章节仍为
  5min 采集需求矩阵的依据）。
- [gpt-session-summary.md](research/gpt-session-summary.md) — 项目起源纪要。
- 收口实验报告（锚点终选 / 2022-24 历史外推 / 资金流四轮）已随实验收口
  清理删除：结论分别并入 [v43-best-plan](spec/v43-best-plan.md) §一/§六 与
  [moneyflow-gate-plan](research/moneyflow-gate-plan.md) §八；原始报告
  git 历史（≤40a9532^）可溯，[outputs/](../outputs/README.md) 保留各实验的
  数据表（CSV）。

## data/ 数据层（研究写侧与运行读侧共用）

- [data-inventory.md](data/data-inventory.md) — 数据资产清单、双链路采集与
  灾备口径（主仓 `data/cache/` 为唯一生产副本，行情无 git 恢复渠道）。

## diagrams/ 图示

- 当前流程以本页新图为准；以下五张为历史架构记录，含旧执行口径，不能替代当前方案。
- 五张历史交互式 HTML：系统架构 / OOS 每日运行流程 / 信号生命周期时序 /
  数据流 / 持仓状态机（`src/` 为 JSON 规格，archify 生成，站点图库同步展示）。

---

旧版本文档（V3/V4.1/V4.2 方案与报告、dyn5 归档）已于 2026-09-23 清理，
git 历史（≤0790071）完整可溯。
