# qlib 回测验证方案（parquet 直接驱动，无需转 bin）——设计定稿待评审

> 2026-09-26 设计，同日出第二版：按用户要求废弃"parquet 先转 qlib bin"
> 的路线，改为 parquet 直接驱动 qlib 回测框架；技术可行性已由冒烟测试
> 实证（§二）。目标是把现行 V4.3 三锚动选生产口径（含 V4.1 分钟重排层）
> 放到 pyqlib 0.9.7 上做**独立引擎回测验证**，数据用现有 `data/cache/`
> 下的 parquet，全程零网络、零生产参数改动、零 bin 文件。本文是预注册的
> 实验设计（experiment-playbook §五检查清单的逐条回答见 §八；因子全量
> 清单见 §四）。
>
> 2026-09-29 口径变更：成交时点全栈切换 T+1 开盘（用户裁决，见
> [spec/v44-open-exec-plan.md](../spec/v44-open-exec-plan.md)）；本文历史锚点数字均为旧收盘口径，
> 重算前仅作参考。
>
> 状态：**P0 冒烟已通过（2026-09-26，research/qlib_route_a/qlib_smoke.py），其余阶段
> 未开跑**；实施前需用户确认 §九 的待决策项。

## 一、验证什么：三个问题（qlib 在本项目中的定位）

本验证不是"用 qlib 重写策略、寻找新收益"，而是三层独立的复算与补测。
qlib 在项目中的角色和边界如下：

| # | 验证问题 | qlib 提供什么 | 能抓出什么问题 |
|---|---|---|---|
| Q1 | **引擎正确性**：自研 `V3Backtester` 事件循环的记账逻辑（双边成本、T+1 开盘成交与当日 open→close 计收益（V4.4 口径）、5% 收盘判定止损、冷却期、topk=3 缓冲、最短持有 3 日）放到独立第三方框架上复算，能否逐笔一致？ | qlib 的 Exchange/Account/Executor/Report 成交与记账框架 | 自研回测器的实现 bug（成本漏记、复利次序、日期错位等）。本项目所有历史锚点（V4.3 +165.4% 等）都出自这一个引擎，值得做一次独立复核 |
| Q2 | **数据接入正确性**：parquet 转成 qlib 行情表（quote_df）的过程是否无损？NaN、停牌、日期的语义是否保真？ | 回测框架的行情消费通道 | 适配层的口径事故（日期字符串、float32 舍入、NaN 停牌语义等） |
| Q3 | **分析增量**：本项目从未做过的信号诊断 | qlib 数据通道上的 IC/Rank IC/衰减分析与标准报表 | 盲区补测（不产生新的收益结论） |

**明确不做的事**（避免伪等价和范围蔓延）：

1. **不用 `TopkDropoutStrategy` 做等价验证**。这个组件是"分数转权重"式
   的调仓器，表达不了闸门退出、最短持有、止损、冷却期、exec_lag=1 这些
   路径依赖规则，强行近似得到的会是另一个策略。等价验证通过自定义
   `BaseStrategy` 移植完成（§五 P3）；TopkDropout 只用作归因对照
   （§五 P4，冒烟已证明它可用）。
2. **不转 bin、不建 qlib 数据根、不调用 qlib.init**。行情数据整理成
   DataFrame 后直接注入 Exchange（§二 E-3）；交易日历则用一个按 parquet
   交易日构造的桩对象替换默认实现（§二 E-2）。qlib 的数据层
   （`D.features`、表达式引擎、Alpha158、workflow）整体不用——那些是
   bin 体系的配套，与本验证无关。
3. **不动生产与 OOS**。评价期禁改参（v43-best-plan §四）；本验证对
   V4.3 参数和 OOS 四轨零改动。如果 Q1 阶段发现自研引擎确有 bug、并
   导致历史锚点数字变动，我们会单独上报、由用户决定处置方式，不自行
   改参数，也不改写已有结论。
4. **5min 数据不进入本验证轨的 qlib 执行框架**（只限执行环节，不是弃用
   5min）。原因分两层：其一，现行分钟层的角色是在 T 日收盘时对日线
   Top5 重新打分排序，并不涉及盘中下单；qlib 高频执行框架
   （NestedExecutor）解决的是盘中如何成交的问题，两者语义对不上。
   其二，本项目 5min 数据是 bar 结束时刻制（48 根/日），与 qlib 内置
   高频日历（50 槽制）网格不同——不过这一差异只有在采用 qlib 内置
   日历时才构成障碍。因此本轨中分钟层的结果经由信号桥文件传入
   （§五 P2）；至于"用 5min 数据计算因子并训练模型"，另立扩展轨处理
   （§十）——在自管日历的前提下，上述网格差异不复存在。

## 二、已验证的技术事实（P0 冒烟，2026-09-26）

`research/qlib_route_a/qlib_smoke.py`（conda 环境 `qlib`）已经用真实 parquet 数据
（daily_bars 4 个代码、2026-06 至 09-18）跑通了完整的回测循环：
TopkDropoutStrategy(topk=1) + SimulatorExecutor + backtest_loop，得到
79 个交易日的 portfolio_metrics（收益/换手/成本/基准），基准是 parquet
直接算出的同花顺全A 收益序列。全程没有 qlib.init、没有 bin、没有网络。

冒烟过程逐项确认了 8 个集成点，它们共同构成"适配层"的规格（pyqlib
版本锁定 0.9.7）：

| # | 事实 | 处置 |
|---|---|---|
| E-1 | `Exchange` 的构造函数在初始化时就会读取全局配置 `C` 里的 trade_unit、limit_threshold、deal_price、region 四个键，缺任何一个都会直接抛 AttributeError。更隐蔽的是 limit_threshold：即使显式传入 None，也会因为"传 None 视为未设置"的判定逻辑回落到全局配置 | 适配层统一预先注入：`C["trade_unit"]=None`（指数研究口径，不取整手）、`C["limit_threshold"]=None`（无涨跌停约束）、`C["deal_price"]="$close"`（全局默认值；V4.4 对拍时显式传 $open）、`C["region"]="cn"` |
| E-2 | `TradeCalendarManager` 通过 `qlib.backtest.utils.Cal` 读取交易日历（`calendar()` 和 `locate_index()` 两个方法），而且 `get_step_time` 在最后一步会去读 `calendar[end+1]` | 用 parquet 的交易日序列构造一个桩对象（np.ndarray，末尾补一个哨兵日、仅作排他上界用），整体替换该模块属性 |
| E-3 | `Exchange.get_quote_from_qlib()` 是一个独立的、可覆写的方法（原实现调用 `D.features`）；它的尾部还会设置 trade_w_adj_price 并调用 `_update_limit`——"NaN 收盘即停牌"的语义正是在这里标记的 | 写一个子类覆写此方法：行情表直接从 parquet 构造（`$open/$high/$low/$close/$volume` 均为 float32、`$factor` 恒 1.0、`$change` 为收盘涨跌幅），尾部逻辑照抄原实现（factor 恒 1.0，因此 trade_w_adj_price=False） |
| E-4 | `codes` 参数传字符串会触发 `D.instruments` | 一律传列表 |
| E-5 | `Account` 的基准默认取 CSI300 且会查询 `D.features`；但 `benchmark_config={"benchmark": <pd.Series>}` 可以直接接受预计算序列 | 基准就用 parquet 直接算出的 883957.TI 全A 日收益（与自研口径同源同窗） |
| E-6 | 手动装配：`CommonInfrastructure(trade_account, trade_exchange)` 加上 `strat.reset_common_infra(infra)`——这两步在高层 `backtest()` 入口里是自动完成的，自己组装时必须补上 | 适配层封装成 `run_backtest(quote, signal, strategy, cfg)` 单入口 |
| E-7 | parquet 的 `date` 列是字符串，而 qlib 切 MultiIndex 时需要 Timestamp（否则报 Level type mismatch） | 适配层强制 `pd.to_datetime` |
| E-8 | 产出结构：`backtest_loop()` 返回 `{"1day": (portfolio_df, indicator_dict)}`，portfolio_df 含 return/turnover/cost/bench 等列 | 结果解析封装进适配层 |

其余事实：qlib 环境（pyqlib 0.9.7 / pandas 2.3.3 / numpy 2.4.6 /
Python 3.12）装有 pyarrow 23.0.1，可以直接读 parquet；回测循环约
500 步/秒（79 日窗口毫秒级），全历史多相位矩阵的耗时可以忽略。

## 三、总体架构：两个环境、三段等价链

核心思路是先把"信号"与"执行"拆开，形成三段可以分别定位差异的等价链：

```
V3Backtester（信号+执行耦合，resonance 环境，现状引擎）
   ≡①  ReplayBacktester（由榜桥文件驱动，同一套执行规则，resonance 环境）
   ≡②  ResonanceStrategy（qlib BaseStrategy + qlib Exchange 记账，qlib 环境）

①出现差异 → 信号抽取环节有 bug；②出现差异 → 执行/记账实现有 bug（自研或 qlib 任一方）。
```

数据与工件的流转（无 bin、无 qlib 数据根）：

```
data/cache/daily_bars.parquet
  ├─ P2 信号桥（resonance 环境：V3Signals + MinuteBarProvider 分钟重排）
  │     ├─▶ outputs/qlib_bridge/final_rank.parquet（逐日最终榜+闸门+降级计数）
  │     └─▶ outputs/qlib_bridge/ref_runs/（参考净值/逐笔交易，float32 口径）
  └─ P3 qlib 等价回测（qlib 环境：适配层行情注入 + ResonanceStrategy）
        ├─ 引擎门 E-Gate（与 ref_runs 逐笔对比）
        └─ P4 IC/报告 ─▶ outputs/qlib_validation/report.md
```

**环境分工及其理由**：

- `resonance` 环境（pandas 3.0.5，无 pyqlib）：负责信号计算
  （`research/qlib_bridge_export.py`）、参考引擎重跑和 ReplayBacktester。
  pyqlib 不装进这个环境。
- `qlib` 环境（pyqlib 0.9.7，pandas 2.3.3）：负责适配层、
  ResonanceStrategy 和分析（`research/qlib_equivalence.py`、
  `research/qlib_analysis.py`）。
- **为什么不合并成一个环境**：pandas 2 与 3 的 `pct_change` 默认填充
  语义不同（2.x 先向前填充再计算，3.x 不填充），而 `resonance/v3.py`
  的信号对概念的前导 NaN 很敏感——跨版本运行同一段信号代码会引入肉眼
  不可见的数据差异。两个环境只通过桥文件交换数据，版本语义差异因此被
  挡在各自环境内，不会互相渗透。
- CLAUDE.md 硬约束 1 规定只使用 resonance 环境，因此需要为本验证开一个
  **文档化例外**（仅限 qlib 验证层脚本，见 §九决策项 1）。

## 四、因子（信号特征）全量清单

策略是规则式的因子栈（没有模型训练、不依赖外部特征库）：因子即信号层
每日从行情派生的特征量。全部因子只依赖 close（日线与 5min 两级；
quote_df 里的 open/high/low/volume 只为 Exchange 成交与停牌通道的完整性
服务，不进任何因子）。参数为 V4.3 冻结值（spec/v43-best-plan §三），
实现语义以 `resonance/v3.py` 为准（下表公式与代码逐条对应）；所有因子
在 T 日收盘即可算得，不含未来数据（成交发生在 T+1 开盘，V4.4）。

### 4.1 日线层（T 日收盘计算；对象 = 三锚 + 529 个概念）

| 编号 | 因子 | 定义（公式） | 参数（冻结值） | 消费方 |
|---|---|---|---|---|
| F1 | 领先指数动量 | `mom_i(T) = close_i(T)/close_i(T−W_l) − 1`，i 取三锚池；`leader(T) = argmax_i mom_i`（窗口收益不完整者不参选；并列时按代码序取先，保证确定性） | W_l=10；池 {399001.SZ 深证成指, 399303.SZ 国证2000, 000688.SH 科创50} | 当日领先指数：F2/F4/F5/F7 的基准 |
| F2 | 入场闸门 | `gate(T) = close_leader(T)/close_leader(T−W_p) − 1 > 0` | W_p=3 | False 时当日无候选：空仓不入场；持仓检查日退出至现金 |
| F3 | 候选资格 | 概念近 W_p 日复合 > 0 **且** 近 W_r 日收益序列完整（窗口内无 NaN；激活较晚或数据缺失的概念出局） | W_p=3, W_r=10 | 进入当日共振评分池 |
| F4 | 同步率 sync | `Σ_t w_t·1[r_L,t>0 ∧ r_c,t>0] / Σ_t w_t·1[r_L,t>0]`，t 取最近 W_r 个交易日，`w_t = 0.5^(age_t/h)`（age 为距今日数；分母只计领先指数上涨日） | W_r=10, h=F7 | score 分项一 |
| F5 | 捕获率 capture | `Σ_t w_t·max(r_c,t,0) / Σ_t w_t·max(r_L,t,0)`（分母为全窗正部加权和；进入 score 前 clip 到 [0,2]） | 同上 | score 分项二 |
| F6 | 上涨共振分 score | `score = sync × sqrt(clip(capture, 0, 2))`；并列按代码升序（确定性排序） | — | 日线榜排序键（选出 Top daily_top=5 进分钟层） |
| F7 | 动态半衰期 h | 当日**领先指数自身**近 W_d 日路径最大回撤 `m = −min(close/close.cummax() − 1)`：m≤2% 时取 5；2%<m≤4% 时取 3；m>4% 时取 2；样本不足按最稳档 5 | W_d=10, dd_tiers=(0.02, 0.04), half_lives=(5,3,2), hl_source="leader" | F4/F5 的 EW 权重衰减 |

### 4.2 分钟重排层（V4.1；T 日收盘计算，输入为 5min close，48 根/日、bar 结束时刻制）

| 编号 | 因子 | 定义 | 参数 | 消费方 |
|---|---|---|---|---|
| F8 | 分钟上涨共振分 | 日线 Top5 概念与领先指数各取当日**最后 24 根** bar 收盘，算 bar 间收益（23 个），套同一公式但**无 EW 权重**：`sync_m = 共同上涨 bar 数 / 领先上涨 bar 数`；`capture_m = Σ max(r_c,0) / Σ max(r_L,0)`；`score_m = sync_m × sqrt(clip(capture_m,0,2))` | minute_bars=24, daily_top=5 | 日线 Top5 的最终重排（topk 缓冲作用于重排后的排名） |
| F8' | 降级链（预注册） | 领先指数 bar 窗缺失 → 回退日线原序（计 minute_fb_leader）；概念 bar 窗缺失 → 该概念剔除（计 minute_excluded）；可评分概念不足 2 个 → 回退日线原序（计 minute_fb_sparse） | — | 把 5min 覆盖缺口显式化（覆盖 389/529，data-inventory §四） |

### 4.3 执行层参数（非因子；E-Gate 逐笔核对的合同面）

topk=3（缓冲：持仓仍在最终榜 Top3 内则续持，跌出才换第 1 名）、
min_hold=3（自执行日起最短持有）、stop_loss=5%（判定仍按收盘口径，相对入场
收盘价、不受最短持有约束；V4.4 下 T+1 开盘执行）、cooldown=1（止损执行日及其
后 1 个交易日阻塞入场）、exec_lag=1（T 收盘信号 → T+1 **开盘**成交、成交当日 open→close 计收益——V4.4 口径，spec/v44-open-exec-plan.md）、cost_bp=10（单边；换仓双边计费）、defense_dd=0
（防御层关闭）。

### 4.4 因子到验证工件的映射

- 桥文件 `final_rank.parquet` 每行携带该概念当日的因子值：`score`
  （分钟层启用且未降级时为 F8，否则为 F6）、`sync`、`capture`（对应
  分项）、`leader`（F1）、`gate`（F2）、`half_life`（F7）、`rank`
  （最终榜名次）以及降级计数列。
- P4 的 IC 诊断按 **F6 score / F4 sync / F5 capture / F8 分钟 score**
  四个口径分别计算 Rank IC 与衰减曲线——把共振的预测力拆到"同步率"
  和"捕获率"上分别归因、并量化分钟重排相对日线的增量，这在项目里
  是第一次。

## 五、阶段设计

### P0 环境与免 bin 链路冒烟 —— ✅ 已完成（2026-09-26）

`research/qlib_route_a/qlib_smoke.py` 通过（见 §二）。适配层需要的全部集成点都已
确认，并随冒烟脚本一并归档。

### P1 适配层固化 + 规范化门 D-Gate（1 天）

**任务**：把冒烟里的临时拼装固化成 `research/qlib_harness.py`
（qlib 环境）：

- `build_quote(bars_df)`：parquet 转标准行情表（E-7 日期转换、float32、
  $factor=1.0、$change、宽表转长表）。
- StubCal（E-2）、ParquetExchange（E-3/E-4）、配置注入（E-1）、
  `run_backtest(...)` 单入口（E-5/E-6/E-8）；带 pyqlib 版本断言
  （`qlib.__version__ == "0.9.7"`，防止升级后悄悄破坏桩）。
- 净值导出：portfolio_df 转成与自研 `nav_curve` 同构的 Series（含成本
  口径对照：qlib 的 return 是含费口径，对照自研净值的几何复利）。

**D-Gate 验收（预注册）**：

1. 行情表三重相等：`$close` 与 `daily_bars.pivot().astype(np.float32)`
   逐元素相等（含 NaN 的位置）；交易日历与 parquet 日期并集一致；
   基准序列与 883957.TI 同窗收益一致。
2. 停牌语义抽查：抽 5 个激活较晚的概念（有前导 NaN），断言其 NaN 日
   `limit_buy/limit_sell=True`（即 E-3 的 `_update_limit` 语义生效）。
3. `tests/test_qlib_harness.py`（resonance 环境离线）：对不依赖 qlib
   的纯函数部分（行情表构造、日期转换、字段补全）做单元测试；qlib 侧
   的集成冒烟沿用 `research/qlib_route_a/qlib_smoke.py` 的模式（离线、真数据、
   小窗口）。

### P2 信号桥与执行解耦（1–2 天）

**任务**（resonance 环境，`research/qlib_bridge_export.py`）：

- `outputs/qlib_bridge/final_rank.parquet`：逐日完整最终榜（含 V4.1
  分钟重排层），列为 `date, rank, concept, score, sync, capture, leader,
  gate, half_life, minute_layer, minute_fb_leader, minute_fb_sparse,
  minute_excluded`（因子列含义见 §四.4）；闸门失败或无候选的日期没有
  任何行。导出窗口为数据全史。
- `outputs/qlib_bridge/ref_runs/`：V3Backtester 原样在 float32 化的
  close 矩阵上重跑，产出净值与逐笔交易 CSV，配置覆盖 P3 的全部
  窗口×相位×成本。两个引擎吃的是同一份 float32 矩阵，数据差与引擎差
  由此彻底分离。
- **ReplayBacktester**（①段等价）：新写一个"吃桥文件 + close 矩阵"
  的重放执行器，执行规则逐条照抄 V3Backtester 的 V4.4 口径（T+1 开盘成交、
  成交当日 open→close 计收益、topk=3 缓冲、最短持有 3 日、5% 收盘判定
  止损、冷却 1 日、单边成本 10bp、换仓双边计费）。

**G2 验收（预注册）**：三窗（P3 窗口表）× 5 相位 × {0,10,30}bp 全配置
下，ReplayBacktester 与 V3Backtester 逐笔交易 100% 一致（日期/类型/
卖出与买入代码/价格按 float32 相等），净值终值相对差 ≤ 1e-9。一旦不
一致，说明信号抽取环节有问题，修复并复核通过后再进 P3。

### P3 qlib 等价回测 + 引擎门 E-Gate（2–3 天）

**ResonanceStrategy**（qlib 环境，继承 `qlib.strategy.base.BaseStrategy`）：

- 输入为桥文件最终榜与闸门标志；价格查询走 `trade_exchange.quote`
  （与行情表同源）。成交价按 V4.4 口径取**开盘价**（`deal_price="$open"`，行情框已注入
  $open）；桥文件是 T 日收盘的信号，策略在 T+1 的决策时点消费 T 日的
  榜、以 T+1 开盘价成交、当日 open→close 计收益——无未来数据
  （CLAUDE.md 硬约束 3）。
- 路径依赖状态（持仓天数、止损基准价、冷却期、topk 缓冲判定）都封装
  在 Strategy 实例内部；换仓拆成一卖一买两笔订单走 Exchange（成本计费
  次序与自研公式一致：换仓双边）；Exchange 用 §二 E-1 的研究模式参数
  （无涨跌停、无整手、无成交量约束，单边成本 10bp）。

**窗口×相位×成本矩阵（全部复用既有冻结协议，不新设网格）**：

| 窗口 | 相位起点族 | 栈 | 池 |
|---|---|---|---|
| 完整周期 2025-01-02→2026-09-18 | 2024-12-27/12-30/12-31/01-02/01-03（5 相位中位） | 全栈=冻结参数全量（topk=3, daily_top=5, hl=leader；2025-09-22 之前无 5min，分钟层自动降级为日线序并计数——与锚点 +165.4% 同配置，exp_anchor_full9.py） | V43 三锚 |
| 分钟子窗 2025-09-22→2026-09-18 | 09-22…09-26（5 相位中位） | 全栈（daily_top=5 + 24bar 分钟重排，经桥文件） | V43 三锚 |
| 延伸窗 →2026-09-24（当前数据末） | 同上 5 相位 | 同上 | 同上（信息性，无锚点） |

成本取 {0, 10, 30} bp。可选第四窗：V4.1 九池对照复现（backtest_v41.py
口径）。

**E-Gate 验收（预注册）**：

1. 逐笔一致：全配置下 qlib 成交记录与 `ref_runs/` 逐笔匹配 100%
   （日期/标的/方向/价格按 float32 相等）；净值终值相对差 ≤ 1e-9，
   逐日净值最大绝对差 ≤ 1e-9。
2. 绩效一致：total 与 max_dd 与自研 `perf_stats` 到 4 位小数相等
   （夏普的年化因子存在口径差异——自研按 244、qlib 报表按其默认——
   报告中两个口径并列展示）。
3. 锚点对照（方向与量级，沿用锚点复现的惯例）：完整周期 10bp 中位落在
   +165.4% 邻域（±10% 相对量级；概念目录快照漂移是已知残差）；分钟
   子窗对照 +87.6% 同理。
4. 如果 qlib 的成本与成交内部次序导致 (1)(2) 无法对齐一致，且排查后
   确认属于框架差异而非 bug，则启用**保底方案 B**——成本改按自研公式
   在 Strategy 层记录，qlib 只承担仓位、成交与报表框架；报告中对降级
   及原因做显式声明。

### P4 分析增量（1 天）

1. **IC 家族（本项目首测）**：桥文件的四个因子口径（§四.4：F6
   score / F4 sync / F5 capture / F8 分钟 score）分别对 T+1/T+2/T+5
   概念收益计算逐日 Rank IC（Spearman），得到均值、ICIR 与衰减曲线；
   按 5 相位窗口分段报告；t 统计与多重比较的措辞遵循 playbook §一。
2. **qlib 标准报表**：等价运行结果的收益、回撤与换手分析（基于
   portfolio_df；夏普等年化指标按自研的 244 重算一份并列）。
3. **归因对照（可选，仅一窗）**：TopkDropoutStrategy(topk=1,
   n_drop=1, signal=桥 score, 同成本) 与等价栈同窗对比——量化"规则
   覆盖层"（闸门/止损/缓冲/最短持有）的贡献差；报告明确标注这是
   近似对照、不是等价验证。
4. 全部结论的措辞遵循 L3：样本内、幸存者目录、指数不可直接交易。

### P5 收尾登记（0.5 天）

1. `outputs/qlib_validation/report.md`：三个问题的结论 + D-Gate/G2/
   E-Gate 证据 + IC 诊断 + 锚点对照表 + 是否启用方案 B 的声明。
2. 文档更新：data-inventory（无需新增条目——没有新的数据资产，只补
   outputs/qlib_bridge 工件说明）、experiment-playbook §三或验证条目、
   CLAUDE.md 常用入口（两环境命令 + qlib 环境例外注记）、README 一段。
3. OOS 纪律复核：确认零生产改动；如果 E-Gate 暴露自研引擎 bug，单独
   上报（附受影响的历史锚点清单），由用户决定处置方式。

## 六、风险与对策

| # | 风险 | 对策 |
|---|---|---|
| R1 | pyqlib 升级可能破坏桩与覆写点（Cal 替换、get_quote_from_qlib、C 注入） | 适配层带版本断言锁定 0.9.7；升级后必须重跑 `research/qlib_route_a/qlib_smoke.py` 冒烟 |
| R2 | float32 舍入可能让阈值附近的决策翻转 | 两个引擎统一使用 float32 化的矩阵（P2 ref_runs 口径），决策一致性由构造保证；报告中注明与 float64 口径的终值差异（仅供参考） |
| R3 | qlib Exchange 的成本与成交细节可能无法对齐一致 | 由 E-Gate 兜底，必要时启用保底方案 B（§五 P3.4） |
| R4 | 概念前导 NaN 与激活日语义可能丢失 | D-Gate 第 1/2 条验收覆盖（NaN 位置相等 + 停牌标记）；信号端资格规则不变 |
| R5 | pandas 2/3 跨环境语义差异 | 两环境之间唯一的契约是文件（桥 parquet + ref_runs CSV）；resonance 环境不 import pyqlib，qlib 环境不 import resonance |
| R6 | 相位敏感性被误读（网格相位敏感性极高，基线复现报告 §一.3） | 全部结论只取 5 相位中位；单相位数字仅作诊断 |
| R7 | 已退役指数混入 | 桥导出脚本入口调用 `config.assert_no_retired`；池为 V43 三锚加概念目录，天然不含退役码 |
| R8 | qlib 环境与 CLAUDE.md 硬约束 1 冲突 | 文档化例外并经用户确认（§九决策项 1）；例外范围仅限本验证脚本 |

## 七、里程碑总表

| 阶段 | 产出 | 门槛（预注册） | 状态/预估 |
|---|---|---|---|
| P0 | 免 bin 链路冒烟 + 8 个集成点确认 | 真数据回测循环跑通 | ✅ 2026-09-26 |
| P1 | `research/qlib_harness.py` 适配层 + 测试 | D-Gate 三条全过 | 1 天 |
| P2 | 信号桥 + ref_runs + ReplayBacktester | G2 逐笔 100% | 1–2 天 |
| P3 | ResonanceStrategy + 等价矩阵 | E-Gate 四条全过 | 2–3 天 |
| P4 | IC 诊断 + 报表 + 归因对照 | 报告落盘 | 1 天 |
| P5 | 登记 + 文档更新 | 评审通过 | 0.5 天 |

## 八、playbook §五检查清单（预注册回答）

1. 机制一句话：用 qlib 独立引擎复算自研回测器，目的是抓实现 bug——
   不是新的 alpha 假设，不涉及 L1 的快慢挤占。
2. 判据已预注册：D-Gate（§五 P1）、G2（§五 P2）、E-Gate（§五 P3）。
3. 单轴：只新增验证层，生产栈零改动；网格全部复用既有冻结协议
   （V3/V4.1 双 5 相位），不设新网格，也不需要边缘外探针。
4. 相位协议：复用两套 5 相位中位协议；本验证是等价性检验而非收益
   检验，检验力由"逐笔一致"这一确定性判据保证。
5. 数据覆盖：日线层 546 码全量；分钟层的覆盖与降级计数经桥文件透传
   （minute_fb_* 列），复现既有 49.8%/389-142 口径。
6. §三登记表：不撞（验证层不是策略改动；负结论同样登记——若 qlib
   复算不一致且定位为自研 bug，将修正并重新发布受影响的历史锚点
   清单）。
7. 措辞：等价验证的结论不等于收益结论；IC 诊断属于样本内；幸存者
   目录与指数不可交易写进报告模板。
8. `assert_no_retired`：桥导出入口自检；V43 三锚与概念目录天然不含
   700050/932000，不涉及 A9/B13 复现例外。

## 九、待用户决策项（开跑前确认）

1. **qlib 环境例外**：CLAUDE.md 硬约束 1 规定只使用 resonance 环境；
   本验证的 qlib 端脚本需要用 conda 环境 `qlib`（pyqlib 0.9.7 已就绪；
   与 resonance 的 pandas 3.0.5 不兼容是分环境的硬理由）。是否同意
   写入例外注记？
2. **TopkDropout 归因对照**（§五 P4.3）：默认做一窗；可裁掉。
3. **第四窗（V4.1 九池对照复现）**：默认可选；可裁掉。
4. **盘中执行（默认不做）**：如果未来引入真正的盘中交易规则，再评估
   NestedExecutor（当前分钟层已定性为重排打分器，无此需求）；
   **5min 因子计算 + 模型训练已于 2026-09-28 立项，选定路线 A
   （ParquetProvider 全 qlib 链），设计定稿见 §十**；本项余下的确认
   只有 qlib 环境例外注记（第 1 项）随扩展轨一并生效。

## 十、扩展轨定稿（2026-09-28 用户选定路线 A）：ParquetProvider 全 qlib 链

> 本轨回答"学习化的 5min 因子能否替代或增强 F8 重排"，切入点是把
> 手工的分钟重排公式学习化。2026-09-28 用户在 A0/A/A/B 四条路线中
> **选定路线 A**：行情经 ParquetProvider 进入 qlib 数据层，因子走
> 表达式引擎，训练、验证、回测从头到尾都是 qlib 规范。数据层注入的
> 技术可行性已于当日冒烟实证（10.1）。本轨与验证轨（§五）解耦；
> 判据按 playbook §一 预注册，评价期纪律同 §八。

### 10.1 已实证的技术事实（三个冒烟，pyqlib 0.9.7，全部 exit=0）

| 冒烟 | 链路 | 日期 |
|---|---|---|
| `research/qlib_route_a/qlib_smoke.py` | parquet 直接驱动回测框架（Exchange/Executor/backtest_loop，不 qlib.init） | 2026-09-26 |
| `research/qlib_route_a/qlib_ml_smoke.py` | 因子 DataFrame 直接驱动训练链（from_df→DatasetH→LGBModel）——A0 路线证据，选定 A 后降为备选 | 2026-09-27 |
| `research/qlib_route_a/qlib_provider_smoke.py` | **路线 A 核心**：ParquetProvider 三接口注入 qlib.init，表达式引擎直读 parquet，day/5min 双频结果与 pandas 逐项核对一致 | 2026-09-28 |

路线 A 的注入机制与契约细节（全部由三个冒烟测试逐一确认）：

1. **注入机制**：`qlib.init` 的 kwargs 进入全局配置后自动触发
   `register_all_wrappers`，provider 三键（calendar_provider /
   feature_provider / instrument_provider）用 `init_instance_by_config`
   实例化，dict 配置支持 `{"class": …, "module_path": …}` 指向自定义
   模块；expression_provider 留默认即可（它只做表达式编排，叶子取数
   经 FeatureD 回到我们的 provider）。`custom_ops` 是官方的自定义算子
   注册入口（init kwargs，F4–F7 需要自定义算子时用）。
2. **三接口契约**：CalendarProvider 只需实现 `load_calendar(freq,
   future)` 返回全量日历数组（基类负责切片）；FeatureProvider 实现
   `feature(instrument, field, start_index, end_index, freq)`，**返回
   以日历下标为索引的 pd.Series**（`Expression.load` 会设置
   `series.name`，传裸 ndarray 会直接报错——这是冒烟中实际遇到的
   问题）；Instrument-
   Provider 的 `list_instruments` 返回 `{标的: [(起, 止)]}` 字典。
3. **表达式语义三发现**：① `Ref(X, N)` = N 根 bar **之前**的值，
   pandas `pct_change(10)` 的等价表达式是 `$close/Ref($close,10)-1`
   （方向写反会在与 pandas 的交叉核对时暴露）；② 表达式引擎自带扩展
   窗回看，窗口头部不会像 pandas 切片口径那样出现前导 NaN；③ 全链路
   统一 float32（与 pandas 的差异约 1e-8，属精度而非语义）。
4. **日历双频与数据现实**：day 日历 = daily_bars 日期并集（1,172 日）；
   5min 日历 = minute5_bars 时刻并集（11,664 bar / **243 天，非连续**
   ——按需矩阵采集决定，部分交易日整日无 bar，如 2026-09-18）。概念
   在缺 bar 时刻由 NaN 填充（与"停牌"语义一致）。
5. **provider_uri 处置**：init 只对它做存在性检查，指向 `data/cache`
   即可（我们的 provider 不读它）；显式传 `expression_cache=None,
   dataset_cache=None` 禁用缓存写入，防止污染数据目录。

### 10.2 组件设计（X1 起实施）

```text
research/qlib_route_a/qlib_provider.py      # 三接口固化（生产版）：池映射（概念目录/三锚/
                               #   各历史池）、5min 九字段、按 (freq,标的,字段) 惰性缓存
research/qlib_route_a/qlib_pipeline.py      # 因子表达式清单 + DatasetH 切分 + LGBModel 训练 + 预测落盘
（回测沿用 §五 harness：预测分数 → ResonanceStrategy / TopkDropout）
```

因子层设计（两轨并行、各带等价门）：

- **5min 因子库（本轨核心增量）**：用官方高频算子
  （`qlib.contrib.ops.high_freq` 的 DayLast/Date/Select 等，专为
  "高频特征 → 日频预测"设计）做 5min→日频聚合，叠加 Alpha158 风格的
  日内因子（尾盘动量与全天动量之比、日内波动与尾盘波动之比、bar 级
  sync/capture 的学习化推广、量能集中度、收盘价日内位置）。
- **日线因子 F1–F7 的两种接入方式**。F1–F7 是 §四.1 定义的七个日线层
  因子（领先指数动量 F1、入场闸门 F2、候选资格 F3、同步率 F4、捕获率
  F5、上涨共振分 F6、动态半衰期 F7）。第一阶段（X1）直接用现成结果：
  把桥文件 `final_rank.parquet` 里的因子列按（日期， 标的）对齐后拼进
  特征矩阵——因子值由 resonance 环境的现行代码算出，公式不重写，
  因此没有重实现风险。第二阶段（X2）可选做进一步：把内置算子能直接
  表达的因子（如 F1 动量、F2 闸门）改写成 qlib 表达式；EW 类因子
  （F4–F7）若也要改写，先经 `custom_ops` 注册自定义算子，并加一道
  一致性核对——表达式结果与 resonance 引擎逐日对齐之后才允许使用。
- **标签**：qlib 惯例用负 Ref 取未来——如 `Ref($close,-1)/Ref($close,-2)-1`
  对齐 T+1 相对收益口径（与 A0 冒烟的 pandas 标签互为对照）。
- **训练**：`DataHandlerLP`（表达式字段）+ `DatasetH` 时间切分 +
  `LGBModel`；路线 A 下是真 qlib.init，workflow Recorder 可用
  （A0 冒烟里置空 `R.log_metrics` 的桩在此路线不需要）。

### 10.3 因子与实验设计（草案，X1 前冻结判据）

- 特征层：上节的 5min 因子库，加上按（日期， 标的）拼入的日线因子
  F1–F7（取自桥文件，接入方式见 10.2）。
- 目标与样本：T 日特征预测 T+1（及 T+2/T+5）概念相对收益排名；样本
  约 243 个 5min 覆盖交易日 × 389 个有覆盖的概念（日频口径）。
- 基线与判据（预注册方向）：基线为现行 F8 重排（5 相位中位）；判据
  遵循 playbook §一（中位提升 >0 且 ≥4/5 相位、孤峰形态约束 L2、
  IC/Rank IC 佐证）；判据定稿写入立项文档，不在本方案。
- 切入点：模型分**替换 F8** 参与 Top5 重排——执行层与验证轨的架构
  不变（分数来源对 ResonanceStrategy 与 E-Gate 是透明的，合同面无需
  改动）。

### 10.4 数据现实与措辞边界（先把预期讲清楚）

1. 5min 数据的硬下界是 2025-09-22（HF 留存约一年）：训练窗只有约
   一年，且是单一 regime（2025-26 成长牛；V4.3 自身 2022-24 时间外推
   −56% 在案）——结论必须按 L3 上界措辞，禁止外推宣称。
2. **5min 日历非连续**（243 天，按需矩阵采集决定）：跨 day/5min 的
   因子只在其交集日有效，因子覆盖率/NaN 报告须按日历口径显式上报；
   若需补齐缺口走 `ops/collect_minute5.py --backfill`（约 14.2M
   dataVol，需配额）。
3. 概念 5min 覆盖 389/529（142 个无 HF，含两个退役码）：训练 universe
   存在选择偏差，评估沿用降级计数口径。
4. 概念目录是 2026-09-19 的幸存者快照（playbook §四）。
5. 训练与验证必须按时间切分（防泄漏），禁止随机切分，也禁止用测试窗
   信息做特征筛选。

### 10.5 里程碑

| 步骤 | 内容 | 门槛 |
|---|---|---|
| X0 | 数据层注入冒烟（三接口 + init + day/5min 双频表达式核对） | ✅ 2026-09-28 已通过（`research/qlib_route_a/qlib_provider_smoke.py`，exit=0） |
| X0' | provider 固化为 `research/qlib_route_a/qlib_provider.py` + 单元测试 | 冒烟 A–D 四项断言进测试（叶子字段相等/表达式与 pandas 核对/NaN 语义/双频日历） |
| X1 | 5min 因子清单冻结 + `qlib_pipeline.py` 特征管道 | 因子覆盖率与 NaN 报告（按 10.4.2 日历口径） |
| X2 | F8 基线复跑 + 模型训练 + 时间切分验证 | IC/Rank IC 落盘 |
| X3 | 模型分替换 F8 的 5 相位对照 | 预注册判据裁决（playbook §一） |
