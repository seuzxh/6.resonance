# 文档导航：按「寻优研究 → 冻结规格 → 每日运行」归类

> 2026-09-26 按架构分层归类。本项目是「参数即模型」形态的量化研究项目：
> **寻优（训练）与每日预测（推理）是两个独立过程**，通过不可变的
> **冻结规格**（工件）解耦——研究轨只写结论与规格，运行轨只读规格，
> 互不调用、失败域隔离。工件本体在代码（`config.V43_ANCHOR_POOL` +
> `work/signal_daily.py FROZEN`），文档是规格的叙述面，git commit 即版本号；
> 「评价期禁改参」= 晋升门，新版本只能整体替换、不能原地修改。

## spec/ 冻结规格（产物层——两过程的唯一接口）

- [v43-best-plan.md](spec/v43-best-plan.md) — V4.3 三锚动选现行口径：
  冻结参数（§三）、组件来源表、领先分布与 2022-24 时间外推警示。

## ops/ 每日运行（推理过程）

- [oos-validation-design.md](ops/oos-validation-design.md) — OOS 四轨 runner
  预注册设计：判据、运行纪律与三道数据防线。runner 为
  `work/signal_daily.py`（15:05 后运行、OOS 起点无状态重放、幂等）。

## research/ 寻优研究（历史过程——写侧）

- [experiment-playbook.md](research/experiment-playbook.md) — 方法论手册：
  预注册协议、七条定律、负结论登记表（新实验必读 §五检查清单）。
- [qlib-validation-plan.md](research/qlib-validation-plan.md) — qlib 独立引擎
  复算验证（parquet 直驱、免 bin；预注册设计，P0 冒烟已过，待过 §八决策项）。
- [moneyflow-gate-plan.md](research/moneyflow-gate-plan.md) — 资金流闸门探索
  （预注册 → 四轮实验 → 全线收官存档）。
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

- 五张交互式 HTML：系统架构 / OOS 每日运行流程 / 信号生命周期时序 /
  数据流 / 持仓状态机（`src/` 为 JSON 规格，archify 生成，站点图库同步展示）。

---

旧版本文档（V3/V4.1/V4.2 方案与报告、dyn5 归档）已于 2026-09-23 清理，
git 历史（≤0790071）完整可溯。
