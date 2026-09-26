# qlib 回测验证方案（parquet 直驱、免 bin）——设计定稿待评审

> 2026-09-26 设计（同日第二版：应用户要求废弃"parquet→qlib bin"转换路线，
> 改为 **parquet 直接驱动 qlib 回测框架**；技术可行性已由冒烟实证，见 §二）。
> 目标：把现行 V4.3 三锚动选生产口径（含 V4.1 分钟重排层）放到 pyqlib
> 0.9.7 上做**独立引擎回测验证**，用现有 `data/cache/` parquet 数据，
> **零网络、零生产参数改动、零 bin 文件**。本文件为预注册实验设计
> （experiment-playbook §五检查清单逐条回答见 §七）。
>
> 状态：**P0 冒烟已通过（2026-09-26，work/qlib_smoke.py），其余未开跑**；
> 实施前需用户确认 §八 待决策项。

## 一、验证什么：三问分解（qlib 的诚实定位）

本验证**不是**"用 qlib 重写策略找新收益"，而是三层独立复算。qlib 在本项目
中的角色与边界：

| # | 验证问题 | qlib 提供什么 | 抓什么错 |
|---|---|---|---|
| Q1 | **引擎正确性**：自研 `V3Backtester` 事件循环的记账（双边成本、T+1 收盘成交 T+2 起算、5% 收盘止损、冷却期、topk=3 缓冲、min_hold=3）在独立第三方框架复算下是否逐笔一致？ | qlib Exchange/Account/Executor/Report 的成交与记账框架 | 自研回测器的实现 bug（成本漏记/复利次序/日期错位）——本项目所有历史锚点（V4.3 +165.4% 等）都出自该引擎，值得一次独立复核 |
| Q2 | **数据接入正确性**：parquet → qlib 行情框（quote_df）的规范化是否无损？NaN/停牌/日期语义是否保真？ | 回测框架的行情消费通道 | 适配层的口径事故（日期字符串、float32、NaN 停牌语义） |
| Q3 | **分析增量**：本项目从未测过的信号诊断 | qlib 数据通道上的 IC/Rank IC/衰减分析、标准报告 | 盲区补测（不产生新收益结论） |

**明确不做的事**（避免伪等价与范围蔓延）：

1. **不用 `TopkDropoutStrategy` 做等价验证**。该组件是"分数→权重"式调仓，
   无法表达闸门退出、min_hold、止损、cooldown、exec_lag=1 等路径依赖规则；
   强行近似会得到**另一个策略**。等价验证走自定义 `BaseStrategy` 移植
   （§四 P3）；TopkDropout 仅作归因对照（§四 P4，冒烟已证明其可用）。
2. **不转 bin、不建 qlib 数据根、不 qlib.init**。行情以 DataFrame 直接注入
   Exchange（§二 E-3），日历以桩注入（§二 E-2）。qlib 的数据层
   （`D.features`/表达式引擎/Alpha158/workflow）整体不使用——那是 bin
   体系的配套，与本验证无关。
3. **不动生产/OOS**。评价期禁改参（v43-best-plan §四）；本验证零改动
   V4.3 参数与 OOS 四轨。若 Q1 发现引擎 bug 导致历史锚点漂移，**上报
   用户决策**，不自行改参或改历史结论。
4. **5min 不进 qlib 执行框架**。分钟层是"T 收盘时刻的重排打分器"（尾盘
   24 bar 重排日线 Top5），不是盘中成交；且 5min 为 48 槽 bar 结束时刻制，
   与 qlib 高频日历 50 槽制网格不同（data-inventory §六）。分钟层经信号
   桥文件进入（§四 P2）。

## 二、已验证的技术事实（P0 冒烟，2026-09-26）

`work/qlib_smoke.py`（conda env `qlib`）已用**真实 parquet 数据**
（daily_bars 4 码 × 2026-06~09-18）跑通完整回测循环：
TopkDropoutStrategy(topk=1) + SimulatorExecutor + backtest_loop →
79 交易日 portfolio_metrics（return/turnover/cost/bench），基准为
parquet 直出的全A 收益序列。全程无 qlib.init、无 bin、无网络。

冒烟钉死的 8 个集成点（即"适配层"的规格，pyqlib **0.9.7 锁定**）：

| # | 事实 | 处置 |
|---|---|---|
| E-1 | `Exchange.__init__` 急切读全局配置 `C.trade_unit/limit_threshold/deal_price/region`，缺键即 AttributeError（显式传参也拦不住 `limit_threshold=None` 被 nil 判定覆盖） | 适配层统一预注入：`C["trade_unit"]=None`（不取整手）、`C["limit_threshold"]=None`、`C["deal_price"]="$close"`、`C["region"]="cn"` |
| E-2 | `TradeCalendarManager` 经 `qlib.backtest.utils.Cal` 读日历（`calendar()`+`locate_index()`），且 `get_step_time` 末步要读 `calendar[end+1]` | 用 parquet 交易日序列做桩 Cal（np.ndarray of Timestamp，末尾补 1 日哨兵仅作排他上界），模块属性整体替换 |
| E-3 | `Exchange.get_quote_from_qlib()` 是独立可覆写方法（原方法调 `D.features`）；尾部还设 `trade_w_adj_price` 并调 `_update_limit`（**NaN 收盘=停牌**语义在此标记） | 子类覆写：quote_df 从 parquet 构造（`$open/$high/$low/$close/$volume` float32、`$factor`=1.0、`$change`=收盘 pct），尾部照抄原语义（factor 恒 1.0 → `trade_w_adj_price=False`） |
| E-4 | `codes` 传 str 会触发 `D.instruments` | 一律传 list |
| E-5 | `Account` 的 benchmark 默认 CSI300 且查 `D.features`；但 `benchmark_config={"benchmark": <pd.Series>}` 直接吃预计算序列 | 基准=parquet 直出的 883957.TI 全A 日收益序列（与自研口径同源同窗） |
| E-6 | 手动装配：`CommonInfrastructure(trade_account, trade_exchange)` + `strat.reset_common_infra(infra)`（高层 `backtest()` 平时自动做的接线） | 适配层封装成 `run_backtest(quote, signal, strategy, cfg)` 单入口 |
| E-7 | parquet `date` 列是**字符串**，qlib 切 MultiIndex 需 Timestamp（Level type mismatch 崩溃） | 适配层强制 `pd.to_datetime` |
| E-8 | 产出结构：`backtest_loop()` → `{"1day": (portfolio_df, indicator_dict)}`，portfolio_df 列含 return/turnover/cost/bench | 结果解析封装进适配层 |

其余事实：qlib env（pyqlib 0.9.7 / pandas 2.3.3 / numpy 2.4.6 / py3.12）
有 pyarrow 23.0.1，可直接读 parquet；回测循环 ~500 步/秒（79 日窗毫秒级，
全史多相位矩阵可忽略）。

## 三、总体架构：两环境三段桥

核心思想：把"信号"与"执行"解耦成**可独立差分定位**的三段等价链——

```
V3Backtester（信号+执行耦合，resonance env，现状引擎）
   ≡①  ReplayBacktester（榜桥文件驱动，同执行规则，resonance env）
   ≡②  ResonanceStrategy（qlib BaseStrategy + qlib Exchange 记账，qlib env）

①差异 → 信号抽取 bug；②差异 → 执行/记账实现 bug（自研 vs qlib 任一方）。
```

数据与工件流（无 bin、无 qlib 数据根）：

```
data/cache/daily_bars.parquet
  ├─ P2 信号桥（resonance env：V3Signals + MinuteBarProvider 分钟重排）
  │     ├─▶ outputs/qlib_bridge/final_rank.parquet（逐日最终榜+闸门+降级计数）
  │     └─▶ outputs/qlib_bridge/ref_runs/（参考净值/逐笔交易，float32 口径）
  └─ P3 qlib 等价回测（qlib env：适配层 quote_df 注入 + ResonanceStrategy）
        ├─ 引擎门 E-Gate（vs ref_runs 逐笔对比）
        └─ P4 IC/报告 ─▶ outputs/qlib_validation/report.md
```

**环境分工及其硬理由**：

- `resonance` env（pandas 3.0.5，无 pyqlib）：信号计算（`work/qlib_bridge_export.py`）、
  参考引擎重跑、ReplayBacktester。**pyqlib 不装进此环境**。
- `qlib` env（pyqlib 0.9.7，pandas 2.3.3）：适配层 + ResonanceStrategy +
  分析（`work/qlib_equivalence.py`、`work/qlib_analysis.py`）。
- **为什么不用单环境跑通**：pandas 2 与 3 的 `pct_change` 缺省填充语义不同
  （2.x 默认 ffill 填充、3.x 不填充），`resonance/v3.py` 的信号对概念前导
  NaN 敏感——跨版本运行同一信号代码会引入**不可见的数据差**。两环境以
  桥文件为唯一契约，把版本语义差隔离在文件边界之外。
- CLAUDE.md 硬约束 1（"只使用 conda 环境 resonance"）需为此开一个
  **文档化例外**（仅限 qlib 验证层脚本，见 §八决策项 1）。

## 四、阶段设计

### P0 环境与免 bin 链路冒烟 —— ✅ 已完成（2026-09-26）

`work/qlib_smoke.py` 通过（§二）。适配层的全部集成点已钉死并随脚本归档。

### P1 适配层固化 + 规范化门 D-Gate（1 天）

**任务**：把冒烟里的临时拼装固化为 `work/qlib_harness.py`（qlib env）：

- `build_quote(bars_df) -> pd.DataFrame`：parquet → 标准 quote_df
  （E-7 日期转换、float32 cast、$factor=1.0、$change、多码宽→长整形）。
- `StubCal`（E-2）、`ParquetExchange`（E-3/E-4）、config 注入（E-1）、
  `run_backtest(...)`（E-5/E-6/E-8）单入口；pyqlib 版本断言
  （`qlib.__version__ == "0.9.7"`，防升级静默破坏桩）。
- 净值导出：portfolio_df → 与自研 `nav_curve` 同构的 Series（含成本口径
  对照：qlib 的 `return` 是含费口径，对照自研 nav 几何复利）。

**D-Gate 验收（预注册）**：

1. quote_df 三重相等：`$close` ≡ `daily_bars.pivot.astype(np.float32)`
   逐元素（含 NaN 位置）；交易日历 ≡ parquet 日期并集；benchmark 序列 ≡
   883957.TI 收益（同窗）。
2. 停牌语义抽查：抽 5 个激活晚概念（前导 NaN），断言其 NaN 日
   `limit_buy/limit_sell=True`（E-3 `_update_limit` 语义生效）。
3. `tests/test_qlib_harness.py`（resonance env 离线）：不 import qlib 的
   纯函数部分（quote 构造、日期转换、字段补全）单元测试；qlib 端集成
   冒烟沿用 `work/qlib_smoke.py` 模式（离线跑真数据 1 码小窗）。

### P2 信号桥 + 执行解耦（1–2 天）

**任务**（resonance env，`work/qlib_bridge_export.py`）：

- `outputs/qlib_bridge/final_rank.parquet`：逐日全序最终榜（含 V4.1 分钟
  重排层），列 `date, rank, concept, score, leader, gate, half_life,
  minute_layer, minute_fb_leader, minute_fb_sparse, minute_excluded`；
  闸门失败/无候选日 = 该日零行。导出窗 = 数据全史。
- `outputs/qlib_bridge/ref_runs/`：V3Backtester 原样在 **float32 cast 后
  close 矩阵**上重跑的净值与逐笔交易 CSV，配置覆盖 P3 全部窗口×相位×成本。
  两端引擎吃同一份 float32 矩阵，把数据差与引擎差彻底分离。
- **ReplayBacktester**（①段等价）：新写"吃桥文件 + close 矩阵"的重放
  执行器，执行规则逐条照抄 V3Backtester（T+1 收盘成交、T+2 起算、
  topk=3 缓冲、min_hold=3、5% 收盘止损、cooldown=1、成本 10bp/边、
  switch 双边）。

**G2 验收（预注册）**：三窗（§P3 窗口表）× 5 相位 × {0,10,30}bp 全配置，
ReplayBacktester 与 V3Backtester **逐笔交易 100% 一致**（date/type/from/to/
价格 float32 相等），nav 终值相对差 ≤ 1e-9。失败即信号抽取有 bug，修完
再进 P3。

### P3 qlib 等价回测 + 引擎门 E-Gate（2–3 天）

**ResonanceStrategy**（qlib env，继承 `qlib.strategy.base.BaseStrategy`）：

- 输入：桥文件最终榜 + 闸门标志；价格查询走 `trade_exchange.quote`
  （与 quote_df 同源）。决策按 qlib 日频约定在 T 日生成、当日收盘成交
  （`deal_price="$close"`）——桥文件为 T 日收盘信号，因此策略在 T+1 决策
  时点消费 T 日榜，**exec_lag=1 语义等价、无未来数据**（CLAUDE.md 硬约束 3）。
- 路径依赖状态（持仓龄、止损基准价、冷却期、topk 缓冲判定）封闭在
  Strategy 实例内；换仓拆 sell+buy 两笔订单走 Exchange（成本次序与自研
  nav 公式一致：switch 双边）；Exchange 研究模式参数沿 §二 E-1 注入值
  （无涨跌停/无整手/无限量，成本 10bp/边）。

**窗口×相位×成本矩阵（全量复用既有冻结协议，不新设网格）**：

| 窗口 | 相位起点族 | 栈 | 池 |
|---|---|---|---|
| 完整周期 2025-01-02→2026-09-18 | 2024-12-27/12-30/12-31/01-02/01-03（5 相位中位） | 日线栈（topk=3, hl=leader） | V43 三锚 |
| 分钟子窗 2025-09-22→2026-09-18 | 09-22…09-26（5 相位中位） | 全栈（+daily_top=5, 24bar 分钟重排，经桥文件） | V43 三锚 |
| 延伸窗 →2026-09-24（当前数据末） | 同上 5 相位 | 同上 | 同上（信息性，无锚点） |

成本 {0, 10, 30} bp。可选第四窗：V4.1 九池对照复现（backtest_v41.py 口径）。

**E-Gate 验收（预注册）**：

1. 逐笔一致：全配置 qlib 成交记录与 `ref_runs/` 逐笔匹配 100%（日期/
   标的/方向/价格 float32 相等）；nav 终值相对差 ≤ 1e-9，逐日 nav 最大
   绝对差 ≤ 1e-9。
2. 绩效一致：total/max_dd 与自研 `perf_stats` 至 4 位小数相等（夏普声明
   年化因子差异：自研 244 vs qlib 报表默认，报告中两边口径并列）。
3. 锚点对照（方向/量级，沿锚点复现惯例）：完整周期 10bp 中位落在
   +165.4% 邻域（±10% 相对量级，目录快照漂移属已知残差）；分钟子窗对照
   +87.6% 同理。
4. 若 qlib 成本/成交内部次序导致 (1)(2) 无法配平且排查确认属框架差异而
   非 bug：启用**保底方案 B**——成本按自研公式在 Strategy 层记，qlib 只
   担任仓位/成交/报表框架，报告中显式声明降级与原因。

### P4 分析增量（1 天）

1. **IC 家族（本项目首测）**：桥文件日线榜 score 与分钟层最终 score 分别
   对 T+1/T+2/T+5 概念收益做 Rank IC（Spearman）逐日序列 → 均值/ICIR/
   衰减曲线；按 5 相位窗口分窗；t 统计与多重比较措辞沿 playbook §一。
2. **qlib 标准报表**：equivalence run 的收益/回撤/换手分析（基于
   portfolio_df；夏普等年化口径按自研 244 重算并列）。
3. **归因对照（可选一窗）**：TopkDropoutStrategy(topk=1, n_drop=1,
   signal=桥 score, 同成本) vs 等价栈同窗——量化"规则覆盖层"（闸门/止损/
   缓冲/min_hold）的贡献差，报告明确标注**非等价对照**。
4. 全部结论措辞遵守 L3：样本内、幸存者目录、指数不可交易。

### P5 收尾登记（0.5 天）

1. `outputs/qlib_validation/report.md`：三问结论 + D-Gate/G2/E-Gate 证据 +
   IC 诊断 + 锚点对照表 + 方案 B 是否启用的声明。
2. 文档更新：data-inventory（无需新增条目——无新数据资产，仅 outputs/
   qlib_bridge 工件说明）、experiment-playbook §三或验证条目、CLAUDE.md
   常用入口（两 env 命令 + qlib env 例外注记）、README 一段。
3. OOS 纪律复核：确认零生产改动；若 E-Gate 暴露自研引擎 bug → 单独上报
   （含受影响的历史锚点清单），由用户决策处置。

## 五、风险与对策

| # | 风险 | 对策 |
|---|---|---|
| R1 | pyqlib 升级破坏桩/覆写点（Cal 替换、get_quote_from_qlib、C 键） | 适配层版本断言锁 0.9.7；升级需重跑 `work/qlib_smoke.py` 冒烟 |
| R2 | float32 量化使阈值附近决策翻转 | 两端引擎统一吃 float32 cast 矩阵（P2 ref_runs 口径），决策一致性由构造保证；报告注明与 float64 口径终值差（信息性） |
| R3 | qlib Exchange 成本/成交细节不可配平 | E-Gate 兜底 + 保底方案 B（§四 P3.4） |
| R4 | 概念前导 NaN / 激活日语义丢失 | D-Gate 第 1/2 条验收（NaN 位置相等 + 停牌标记）；信号端 eligibility 规则不变 |
| R5 | pandas 2/3 跨 env 语义差 | 唯一契约为文件（桥 parquet + ref_runs CSV）；resonance env 不 import pyqlib，qlib env 不 import resonance |
| R6 | 相位敏感性误读（网格相位敏感性极高，基线复现 §一.3） | 全部结论只取 5 相位中位；单相位数字仅诊断用 |
| R7 | 已退役码混入 | 桥导出脚本入口调 `config.assert_no_retired`；池=V43 三锚+概念目录，天然不含退役码 |
| R8 | qlib env 与 CLAUDE.md 硬约束 1 冲突 | 文档化例外 + 用户确认（§八决策项 1）；例外范围仅限本验证脚本 |

## 六、里程碑总表

| 阶段 | 产出 | 门槛（预注册） | 状态/预估 |
|---|---|---|---|
| P0 | 免 bin 链路冒烟 + 8 集成点钉死 | 真数据回测循环跑通 | ✅ 2026-09-26 |
| P1 | `work/qlib_harness.py` 适配层 + 测试 | D-Gate 三条全过 | 1 天 |
| P2 | 信号桥 + ref_runs + ReplayBacktester | G2 逐笔 100% | 1–2 天 |
| P3 | ResonanceStrategy + 等价矩阵 | E-Gate 四条全过 | 2–3 天 |
| P4 | IC 诊断 + 报表 + 归因对照 | 报告落盘 | 1 天 |
| P5 | 登记 + 文档更新 | 评审通过 | 0.5 天 |

## 七、playbook §五检查清单（预注册回答）

1. 机制一句话：qlib 独立引擎复算自研回测器，抓实现 bug——非新 alpha
   假设，不涉 L1 快慢挤占。
2. 判据已预注册：D-Gate（§四 P1）、G2（§四 P2）、E-Gate（§四 P3）。
3. 单轴：仅新增验证层，生产栈零改动；网格全量复用既有冻结协议
   （V3/V4.1 双 5 相位），无新网格无边缘外探针需求。
4. 相位协议：复用两套 5 相位中位协议；本验证是等价性检验而非收益检验，
   检验力由逐笔一致（确定性）保证。
5. 数据覆盖：日线层 546 码全量；分钟层覆盖与降级计数沿桥文件透传
   （minute_fb_* 列），复现既有 49.8%/389-142 口径。
6. §三登记表：不撞（验证层非策略改动；负结论同样登记——若 qlib 复算
   不一致且定位为自研 bug，将修正并重发历史锚点影响清单）。
7. 措辞：等价验证结论 ≠ 收益结论；IC 诊断为样本内；幸存者目录与指数
   不可交易标注进报告模板。
8. `assert_no_retired`：桥导出入口自检；V43 三锚+概念目录天然不含
   700050/932000，不涉 A9/B13 复现例外。

## 八、待用户决策项（开跑前确认）

1. **qlib env 例外**：CLAUDE.md 硬约束 1 规定只用 resonance env；本验证
   的 qlib 端脚本需用 conda env `qlib`（pyqlib 0.9.7 已就绪，pandas 3.0.5
   与 pyqlib 不兼容是分界硬理由）。是否同意写入例外注记？
2. **TopkDropout 归因对照**（§四 P4.3）：默认做一窗；可裁掉。
3. **第四窗（V4.1 九池对照复现）**：默认可选；可裁掉。
4. **远期项（默认不做）**：若未来引入真正的盘中交易规则，再评估
   NestedExecutor + 5min 高频接入（当前分钟层已定性为重排打分器，
   无此需求）。
