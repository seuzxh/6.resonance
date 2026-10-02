# qlib 路线 A 扩展轨实施报告（T1–T7 全流程验证）

> 2026-09-29 夜实施（用户指令"开始实施，执行完成后进行 qlib 全流程验证"）。
> 规格：[docs/research/qlib-validation-plan.md](../../docs/research/qlib-validation-plan.md) §十；
> 实施计划：[docs/superpowers/plans/2026-09-29-qlib-routeA-5min-factors.md](../../docs/superpowers/plans/2026-09-29-qlib-routeA-5min-factors.md)。
> 本报告所有结论的措辞边界（L3）：**样本内上界口径**；训练/评估窗为
> 2025-09-22~2026-09-23 的单一 regime（成长牛）；概念目录为 2026-09-19
> 幸存者快照；概念指数不可直接交易，成本（10bp 单边）是统一压力假设。

## 一、一句话总结

**链路全通、结论为负**：ParquetProvider 免 bin 数据层 + 表达式因子管道 +
LGBModel 训练 + F8 替换对照全部跑通（每步有合成测试与真数据产物），
但学习化模型分替换分钟共振重排的预注册判据落判 **HARMFUL（反效，中位
差 −12.30pp、0/5 相位）**——与模型分负 IC（−0.037）一致；单因子层面
**尾盘动量 TAIL_MOM24 的 Rank IC +0.156（t=+2.41，显著为正）** 是本轮
唯一过线的新信号，值得后续单独预注册验证。

## 二、交付物清单（全部落盘并提交）

| 工件 | 内容 | 验证 |
|---|---|---|
| `research/qlib_route_a/qlib_provider.py` | ParquetData + 三接口 provider + init_qlib_parquet（十契约实现，含新踩实的契约⑩右边界钳制） | 合成测试 6 项 + 真数据冒烟复核双绿 |
| `research/qlib_route_a/qlib_factor_export.py` | F1–F7 日线因子导出（V3Signals 零重写） | 合成测试 2 项；真数据 182,607 行 / 654 信号日 / 457 概念 |
| `research/qlib_route_a/qlib_pipeline.py` | 8 条冻结 5min 表达式 + 特征构建 + 单标签训练 + IC 报告 | 合成测试 3 项；真数据特征 94,527 行 × 243 日 |
| `research/qlib_route_a/qlib_f8_compare.py` | post_rank 钩子（镜像 F8' 降级链）+ 5 相位预注册对照 | 合成测试 3 项；真数据判决 HARMFUL |
| `tests/qlib_route_a/` | 上述四者的合成数据测试（qlib 侧 importorskip） | 14 项全绿（qlib env） |
| `outputs/qlib_ml/` | features / pred / coverage.md / ic_report.md / x3_phases.csv / v44_anchor_recheck.csv | — |

## 三、V4.4 开盘成交口径（前置引擎工作）

- 引擎：`V3Params.exec_price`（默认 "close" 零回归，全量 49 测试绿；
  "open" 需 open_all 宽表）。语义：T 收盘信号 → T+1 **开盘**成交、成交
  当日 open→close 计收益、旧仓隔夜段、止损判定基准 = 入场日收盘价。
- **收盘口径对照组复现冻结锚点分毫不差**（+165.4%/−16.2%/1.98 与
  +87.6%/−13.0%/2.07），证明口径迁移对比的基线正确。
- 迁移影响（10bp、5 相位中位）：完整周期 **+165.4% → +220.2%
  （+54.8pp，回撤改善至 −12.8%，夏普 2.37）**；分钟子窗 +87.6% →
  +85.4%（−2.2pp，持平）。相位梯队连贯，无孤峰。待补：0/30bp 矩阵、
  九池对照、2022-24 外推、单锚（spec §三已登记）。
- 样本外验证 runner（OOS，out-of-sample）未动（spec §四：未获指示前
  维持收盘口径，处置待用户决策）。

## 四、IC 诊断（本项目首次分因子信号诊断）

口径：test 段（2026-08-03~09-23）日频 Spearman Rank IC 对 T+1 收盘对
收盘收益；样本内、单一 regime。因子名构成：前缀 TAIL=尾盘 24bar / 
FULL=全天 48bar，MOM=动量、VOL=波动、VRATIO=量能占比、
DAY_POS=收盘价日内位置、HI_PUMP=日内冲高、ACC=加速度：

| 口径 | Rank IC | t 值 | 天数 |
|---|---:|---:|---:|
| **TAIL_MOM24（尾盘 24bar 动量）** | **+0.1560** | **+2.41** | 37 |
| TAIL_VRATIO（尾盘量能占比） | +0.0771 | +1.78 | 37 |
| FULL_MOM48（全天动量） | +0.0991 | +1.35 | 37 |
| DAY_POS（收盘日内位置） | +0.0696 | +1.21 | 37 |
| TAIL_VOL24 / FULL_VOL48 | −0.063 / −0.057 | −0.92 / −1.06 | 37 |
| HI_PUMP / TAIL_ACC | −0.008 / −0.017 | −0.15 / −0.44 | 37 |
| F6 score / F4 sync / F5 capture（日线共振） | +0.012 / +0.004 / −0.000 | ≤0.19 | 22 |
| LGBModel 模型分 | −0.0372 | −1.29 | 36 |

三点解读（完整句子、口径齐全）：

1. 尾盘动量因子在不计成本、样本内、test 段 37 日的口径下日均 Rank IC
   为 +0.156，t 值 +2.41 刚过显著线——是本轮唯一过线因子，但未过
   多重比较校正（8 因子族），采纳前须按 playbook 另行预注册（含
   5 相位稳健性与孤峰检查）。
2. 日线共振分（score）对次日概念横截面收益几乎无预测力（+0.012，
   t=0.19）——现行策略的排序键更接近" regime 内动量选择器"而非
   逐日截面预测器，这与策略设计意图（跟随强势概念）一致，但值得
   登记为信号诊断基线。
3. LGB 模型分 IC 为负（−0.037）：8 特征 × ~7 万样本、单一 regime 下
   早停第 8 轮，模型把噪声学进了排序——**模型并非因子信息的上界**，
   单因子 TAIL_MOM24 显著而模型分反向，是典型的"弱信号被合并稀释"。

## 五、X3 预注册判决：HARMFUL

配置（两组方案同窗、同 10bp、同 V4.4 开盘口径、test 段 5 相位中位）：

| 配置 | 总收益中位 | 回撤中位 | 夏普中位 | 相位胜出 |
|---|---:|---:|---:|---:|
| A：现行 F8 分钟重排栈（daily_top=5） | +4.60% | −8.42% | 1.20 | — |
| B：模型分替换 F8（daily_top=0 + post_rank） | −7.70% | −14.10% | −1.92 | 0/5 |

判决：**反效（中位差 −12.30pp ≤ −2pp 判据线，且 0/5 相位）**。降级链
零触发（当日 Top5 概念全部有模型分，model_excluded=0、
model_fallback_sparse=0），即对照是"分钟共振重排 vs 学习模型重排"的
纯净比较，结论不掺杂覆盖降级噪声。

登记：负结论入 experiment-playbook §三；模型化重排方向在"换特征集/
换目标/加 walk-forward"的新条件下方可重开（L5 交互警示）。

## 六、新踩实的契约（并入十契约清单）

- **契约⑩（右边界钳制）**：负向 Ref（标签）的扩展窗会把 end_index 推到
  日历末端之外，provider 的 feature() 切片后必须同步截短索引，否则
  数据/索引长度不齐直接 ValueError（真数据管道实际踩过，已修）。
- **日期边界语义**：`end="YYYY-MM-DD"` 是当日 00:00，会把当日 5min bar
  全部切掉——build_features 内部统一推至 23:59。
- **Wrapper 挂点**：init 后注入数据用 `qdd.Cal.register(实例)`（Wrapper
  持有 _provider 并做属性委托），不是 `.provider.data =`。
- **单标签**：LightGBM 不支持多标签列，LABEL2/5 只作评估口径。

## 七、残留与建议

1. **样本外（OOS）四轨处置待用户决策**（spec v44 §四：A=旧口径跑完+V4.4 另起，
   B=即日切换重启）——runner 目前维持收盘口径。
2. V4.4 锚点重算待补批次（0/30bp、九池、2022-24 外推、单锚）。
3. TAIL_MOM24 如需立项：新预注册设计（对照=现行栈，判据 playbook §一），
   不在本轮范围内。
4. 全量测试现状：resonance env 51 项全绿（49 旧 + 2 项 F1–F7 导出
   合成 + …以最终回归输出为准）；qlib env 14 项全绿。

## 八、验证轨 P1–P3（2026-09-29 追加，同日完成）：三段等价链闭合

自研引擎在 qlib 独立框架上**逐笔复算一致**（V4.4 开盘口径、3 窗 × 5 相位
× {0,10,30}bp 全矩阵 45 配置）：

| 等价段 | 门 | 结果 |
|---|---|---|
| ① V3Backtester ≡ ReplayBacktester（信号桥驱动，resonance env） | G2 逐笔门 | **45/45，nav 相对差全为 0（逐位一致）**——信号抽取零缺陷 |
| ② ≡ ResonanceStrategy（qlib BaseStrategy + Exchange 记账，qlib env） | E-Gate 逐笔门 | **45/45 逐笔一致**（日期/类型/标的/价格 float32 相等） |

组件：`qlib_harness.py`（P1 适配层，$open 成交+双边成本+账户自洽，探针
测试 2 项）、`qlib_bridge_export.py`（P2 桥 3,268 行/654 信号日 + 重放
引擎）、`qlib_equivalence.py`（P3 移植）。实施中修掉两处移植缺陷（冷却
期映射 off-by-one、价格 CSV 往返按 float32 比较）与一处 E-8 口径澄清
（组合列重建不作准，对拍锚 = 逐笔成交 + 账户终值）。报告：
outputs/qlib_bridge/{g2_report.md, e_gate_report.md}。

**含义**：本项目全部历史锚点所依赖的自研事件循环（含 V4.4 开盘成交
语义），经 qlib Exchange/Account 独立记账复算逐笔验证无实现 bug——
Q1（引擎正确性）以最严判据通过。
