# 共振策略 qlib 独立实现设计：static load 预计算 + 引擎级回测

> 版本：v1.0（2026-09-27）。基于 qlib v0.9.7 源码独立设计（不参考 `qlib-validation-plan.md`）。
> 决策记录：①实现深度 = **引擎级 V+M**（用 qlib 执行/记账/报告全套基础设施，策略语义自研）；
> ②信号采用 **static load**：全量因子离线预计算为静态文件，qlib 侧只消费不计算；
> ③双环境隔离（resonance env pandas 3.x / qlib env pandas 2.x），文件为唯一契约。

---

## 一、结论摘要

- **架构**：双层双环境。env A（现有 resonance 环境）离线产出「最终榜信号」与「qlib 行情 bin 目录」两类静态文件；env B（新建 qlib 环境）只用 qlib 原生回测栈消费它们。
- **static load 的关键收益**：V4.3 生产口径的全部复杂度——三锚动选、闸门、动态半衰期、**V4.1 分钟重排层**（5min 数据）、降级策略、研究钩子（entry_gate/exit_grid/post_rank）——全部封装在预计算侧；qlib 侧只保留一个纯粹的「路径依赖持仓状态机」。5min 数据完全不进 qlib。
- **信号接口**：qlib 原生 `Signal` 契约（`qlib/backtest/signal.py`）天然支持静态预加载；为规避 `SignalWCache` 内部 resam 的跨日「就近取值」边角行为，自写 20 行 `DailyRankSignal(Signal)` 做精确单日查询。
- **策略**：自研 `ResonanceStrategy(BaseStrategy)`（`qlib/strategy/base.py`），逐条复刻 [v3.py](file:///workspace/resonance/v3.py) `run()` 循环语义（Top3 缓冲、最短持有、5% 收盘止损、冷却期、T+1）；不用 `TopkDropoutStrategy`（它是无状态 Topk 语义，无法表达上述状态依赖规则）。
- **验收**：同参数下与 `V3Backtester.run()` 对齐——trades 明细逐笔相同、NAV 单日相对偏差 < 1e-5（固有偏差源为成本记帐口径差，量级 ~c²=1e-6/笔）。

---

## 二、总体架构

```
env A: resonance (pandas 3.0.5)                    env B: qlib (qlib 0.9.7, pandas 2.x)
┌────────────────────────────────┐                ┌────────────────────────────────────┐
│ export_qlib_signals.py         │                │ run.py                             │
│  复用 V3Signals.ranking()      │                │  qlib.init(dump目录)               │
│  复用 MinuteBarProvider        │                │  Exchange(close成交,factor=1,      │
│  复用 _final_ranking 逻辑      │  ── parquet ─▶ │    无涨跌停,双边10bp,min_cost=0)   │
│  (日线榜→Top5→5min重排→终榜)   │                │  DailyRankSignal(Signal)           │
│                                │                │  ResonanceStrategy(BaseStrategy)   │
│ dump_market.py                 │  ── bin ─────▶ │  SimulatorExecutor(day, TT_SERIAL) │
│  日线宽表→qlib bin 数据目录    │                │  backtest_loop() → 组装报告       │
└────────────────────────────────┘                └────────────────────────────────────┘
        文件契约（唯一边界）                              align_check.py 对齐验收
```

| 组件 | 环境 | 职责 |
|------|------|------|
| `resonance/export_qlib_signals.py` | A | 信号预计算 → parquet（§三） |
| `qlib_run/dump_market.py` | B | 日线 → qlib bin 目录（§四） |
| `qlib_run/strategy.py` | B | `ResonanceStrategy` 状态机（§五） |
| `qlib_run/run.py` | B | 组装 Exchange/Account/Executor，跑 `backtest_loop`（§六） |
| `qlib_run/align_check.py` | A/B 均可 | 对齐验收（§七） |

**目录约定**：
- 信号输出：`outputs/qlib_bridge/`（scores / daily_context / meta.json，见 §3.3）
- 行情输出：`~/.qlib/qlib_data/resonance/`（bin 格式，qlib 原生目录结构）
- qlib 侧代码：`/workspace/qlib_run/`（独立顶层包，**不 import resonance**，跨环境边界保持单向）

---

## 三、信号预计算层（static load 核心）

### 3.1 计算内容

对 `close_all.index` 每个交易日 `i` 产出**当日最终榜**，逻辑与 [v3.py](file:///workspace/resonance/v3.py#L416-L448) `_final_ranking` 完全一致：

1. 日线榜：`V3Signals.ranking(i)`（三锚动选 leader → 3 日闸门 → 候选资格 → EW 共振分）；
2. V4.1 分钟重排（`daily_top=5`）：日线 Top5 → `MinuteBarProvider.window_span` 取近 24 根 5min bar → `minute_up_resonance` 重排，含三级降级策略（leader bar 缺→原序；个别概念缺→剔除；可评分<2→原序）；
3. 研究钩子（可选轨）：`post_rank` / `entry_gate` 在此侧应用——它们均不依赖持仓状态，天然可预计算。

**关键语义**：闸门失败日 / 无候选日 / entry_gate 阻塞日 → 当日**无任何输出行**（不是 0 分）。qlib 侧「当日无行」=「空仓则不入场；持仓检查日则退至现金」，与 [v3.py](file:///workspace/resonance/v3.py#L577-L579) 语义一致。

### 3.2 为什么分钟层不进 qlib

V4.1 分钟重排是「当日榜的纯函数」（只依赖日期与 5min 数据，不依赖持仓），因此可整体预计算。这正是 static load 的最大收益：qlib 侧**不需要** NestedExecutor 分钟执行层、不需要分钟行情 dump、`qlib_run` 保持纯日频，复杂度大幅下降。降级事件计数随 `daily_context` 导出，诊断能力不损失。

### 3.3 输出契约

`outputs/qlib_bridge/`（参数指纹不同的实验各自建子目录）：

| 文件 | 格式 | 内容 |
|------|------|------|
| `scores.parquet` | MultiIndex `(date, concept)` → float `score` | **仅有效候选日**有行；score=最终榜排序键（分钟重排后为分钟共振分，量纲无需与日线分一致，仅用于排序） |
| `daily_context.parquet` | 行=全部交易日 | `leader, gate, half_life, n_candidates, minute_layer, minute_fallback_*, minute_excluded` 诊断列 |
| `meta.json` | — | **参数指纹**：V3Params 全字段（V4.3 冻结值见 §3.4）、锚池列表、数据起止日、生成时间；`qlib_run` 启动时强校验，防静态文件陈旧/错配 |
| `exit_grid.parquet`（可选） | date×concept bool | 仅 R2 研究轨启用；依赖持仓状态，故 qlib 策略侧查表（§5.4） |

### 3.4 V4.3 生产口径冻结参数（预计算与回测两侧共用）

来自 [v43-best-plan.md](file:///workspace/docs/spec/v43-best-plan.md) §三：

```python
V3Params(topk=3, daily_top=5, hl_source="leader", half_lives=(5, 3, 2),
         min_hold=3, stop_loss=0.05, cooldown=1, exec_lag=1,
         minute_bars=24, cost_bp=10.0)
broad_codes = list(config.V43_ANCHOR_POOL)   # 399001.SZ / 399303.SZ / 000688.SH
```

注：spec 中 `stop_mode="close"`（现实现内固定为收盘检查）、`defense_dd=0.0`（防御层关闭，无此代码路径），qlib 轨无需额外设计。

### 3.5 实现路径

export 脚本实例化 `V3Backtester`（不调 `run()`），逐日调 `_final_ranking(i, date, st)` 收集结果。该方法目前是私有——**建议在分层重构的「信号/引擎分离」步将其提为公共 `final_ranking()`**（若 qlib 轨先行落地，则以私有调用起步并在重构时替换，行为不变）。

---

## 四、行情数据层：dump bin + Exchange 配置

### 4.1 dump 方案（主案）

用 qlib 仓库自带 `scripts/dump_bin.py` 的等价逻辑（env B 自写 ~100 行，直接读项目日线 parquet），产出 qlib 原生目录结构：

```
~/.qlib/qlib_data/resonance/
  calendars/day.txt        # 逐行 = close_all.index 各交易日 ← 对齐的根基，必须与信号日期轴完全一致
  instruments/all.txt      # "\t".join(code, start, end)，代码沿用项目口径如 885101.TI
  features/<code>/
    open.day/bin close.day/bin high.day/bin low.day/bin volume.day/bin factor.day/bin
```

要点：
- `factor` 恒写 `1.0` → 不复权口径，与项目一致（`config.HD_FIELD_MAP` CPS=0）；
- 概念指数无真实成交量约束 → `volume` 写 iFinD `volume` 字段即可，回测不启用量限制；
- 宽基指数（三锚、全A 等）一并 dump，benchmark 直接取 `883957.TI`；
- `qlib.init(provider_uri=<上述目录>, region="cn")`。

备选（不推荐）：`Exchange(extra_quote=df)` 注入外部行情——需核实 qlib 数据层为空时的行为，且每次构造 Exchange 都要传全量 DataFrame；dump bin 一次性、离线、可被 qlib 全生态（报告/分析工具）直接消费，优势明显。

### 4.2 Exchange 配置（与 v3.py 语义逐项对齐）

对照 `qlib/backtest/exchange.py` `__init__` 签名（v0.9.7）：

| 参数 | 值 | 对齐的 v3.py 语义 |
|------|-----|------------------|
| `freq` | `"day"` | 日频执行 |
| `deal_price` | `"$close"` | T+1 **收盘**成交（`exec_lag=1`） |
| `limit_threshold` | `None` | 指数无涨跌停（且源数据无 `$change`，必须显式关闭） |
| `volume_threshold` | `None` | 概念指数不可交易，纯压力假设，不限量 |
| `open_cost` / `close_cost` | `1e-3` / `1e-3` | `cost_bp=10` 单边 |
| `min_cost` | `0.0` | 关闭默认 5 元最低费用（v3 无此项，不关会产生系统性偏差） |
| `trade_unit` | `None` | 不取整 100 股；浮点股数才能复刻「金额制全仓」NAV 乘法模型 |
| `codes` | `"all"` | instruments 全集 |

---

## 五、策略层：ResonanceStrategy（状态机）

### 5.1 为什么不用 TopkDropoutStrategy

`qlib/contrib/strategy/signal_strategy.py` 的 `TopkDropoutStrategy` 是**无状态** Topk 语义（仅 `hold_thresh` 一个持有期参数）。V4.3 生产规则含五条路径依赖规则（闸门退出、Top3 缓冲、最短持有期、止损相对入场价、冷却期），状态在「入场价/执行日/止损事件」之间跨日传递，必须自研。这正是选「引擎级」而非「应用级」的原因：用 qlib 的执行/记账/报告，策略语义 100% 自控。

### 5.2 状态机

策略实例持有（全部为运行期状态，随 `reset()` 清零）：

```python
self.holding: str | None        # 当前持仓概念
self.entry_px: float | None     # 入场成交价（T+1 收盘价），止损基准
self.exec_bar: int | None       # 入场成交 bar 序号，min_hold 计数基准
self.entry_block_until: int     # 冷却期阻塞上界（bar 序号），-10 初始
```

bar 序号 = `self.trade_calendar.trade_step`（与 `close_all.index` 位置一致，由 §4.1 日历一致性保障）。

### 5.3 generate_trade_decision 伪代码

qlib 执行循环（`qlib/backtest/backtest.py` `collect_data_loop`）逐 bar 调用 `generate_trade_decision`；当前 bar `T+1` 的决策即「T 收盘信号在 T+1 收盘执行」，`exec_lag=1` 由查询方式实现：

```python
def generate_trade_decision(self, execute_result=None):
    tcm = self.trade_calendar
    bar = tcm.trade_step
    today = tcm.get_trade_time()            # (start, end) = 当日
    sig_day = 上一交易日(today)              # exec_lag=1：读 T 日收盘信号
    ranking = self.signal.get_signal(sig_day, sig_day)   # 空表=当日无候选

    orders = []
    pos = self.trade_position               # 决策时点仓位（未含本 bar 订单）
    px = self.trade_exchange.get_quote(持仓, today)["$close"]

    if self.holding is not None:
        # ① 止损：close <= entry_px*(1-5%)，每 bar 检查，不受 min_hold 限制
        if px <= self.entry_px * (1 - p.stop_loss):
            orders.append(SELL 全仓(self.holding))
            self.entry_block_until = bar + p.cooldown     # = S + 1，S 为本 bar
        # ② 持仓检查：成交满 min_hold 个 bar 后
        elif bar - self.exec_bar >= p.min_hold:
            if ranking 为空:
                orders.append(SELL 全仓(self.holding))     # 闸门失败/无候选 → 退现金
            elif self.holding not in ranking.head(p.topk):
                orders += [SELL 全仓(self.holding),          # switch：先卖后买
                           BUY(ranking.iloc[0])]            # 同 bar 串行，卖款即时可用
    # ③ 空仓：冷却期后按当日第 1 名入场
    elif bar > self.entry_block_until and ranking 非空:
        orders.append(BUY(ranking.iloc[0]))

    return TradeDecisionWO(orders)
```

订单成交后同步状态：`post_exe_step(execute_result)` 钩子中按成交回报更新 `holding/entry_px/exec_bar`（BUY：`entry_px=当日close, exec_bar=bar`；SELL：清空）。

### 5.4 买入金额公式（金额制全仓的复刻）

v3.py 是 NAV 乘法模型：`nav *= (1-cost)` 后按收盘价全仓。qlib 是股数记账 + 从现金扣比例费。复刻公式：

```python
A = cash * (1 - open_cost) / px          # 浮点股数；switch 时 cash 为卖单成交后的现金
```

- 卖出侧精确等价：卖出后总资产 = 市值×(1-c)，与 v3 `nav *= (1-cost)` 完全一致；
- 买入侧 qlib 多扣费基差 `c²≈1e-6/笔`（v3 对 NAV 直接乘、qlib 对现金扣费的固有口径差），不可消除，计入验收容差（§七）；
- switch 双边费：`SimulatorExecutor(trade_type=TT_SERIAL)` 串行执行保证「先卖后买、卖款可用」，两单各计一次费用 ≡ v3 的 `(1-cost)²`。

### 5.5 语义映射总表（验收对照清单）

| # | v3.py 规则（[run 循环](file:///workspace/resonance/v3.py#L534-L610)） | qlib 侧实现 |
|---|---|---|
| 1 | 信号 T 收盘 → T+1 收盘成交 | bar T+1 决策步读 `get_signal(T,T)`；`deal_price="$close"` |
| 2 | T+2 起计收益 | 股数记账天然满足（当日买当日按成本价估值） |
| 3 | 金额制全仓 | §5.4 公式，浮点股数 |
| 4 | Top3 缓冲（跌出当日 Top3 → 换第 1 名） | 排序 `ranking.head(topk)` 判断 |
| 5 | min_hold=3（自执行日起算完整收益日） | `bar - exec_bar >= min_hold` |
| 6 | 5% 收盘止损（相对入场价，不受 min_hold 限制） | `entry_px` 状态 + 每 bar close 检查 |
| 7 | 冷却期：止损执行日 S 及后 cooldown 日不入场（i ≤ S+cooldown） | `entry_block_until = S_bar + cooldown`；判 `bar > entry_block_until` |
| 8 | 止损当日不再产生其他动作 | 卖单后直接 return（单 bar 单动作） |
| 9 | 闸门失败/无候选：空仓不入场、持仓检查日退现金 | 当日无信号行 → §5.3 ②③ 分支 |
| 10 | 停牌/NaN 收盘：pending 丢弃不成交 | qlib：close NaN → 订单不成交、bar 末过期（语义近似，见 §九-4） |
| 11 | cost_bp=10 单边、switch 双边 | `open_cost=close_cost=1e-3` + 串行 switch |
| 12 | 不复权 | dump `factor=1.0` |
| 13 | R2 exit_grid 研究轨 | 策略侧查 `exit_grid.parquet`（唯一保留在策略侧的钩子，因依赖持仓） |

---

## 六、回测入口与报告

```python
# qlib_run/run.py
qlib.init(provider_uri="~/.qlib/qlib_data/resonance", region="cn")
signal   = DailyRankSignal("outputs/qlib_bridge/scores.parquet")   # 校验 meta.json
strategy = ResonanceStrategy(signal=signal, params=meta.params)
executor = SimulatorExecutor(time_per_step="day", start_time=…, end_time=…,
                             trade_type=SimulatorExecutor.TT_SERIAL,
                             generate_portfolio_metrics=True,
                             common_infra=CommonInfrastructure(
                                 trade_account=Account(init_cash=1e9),
                                 trade_exchange=Exchange(**§4.2 配置)))
portfolio_dict, indicator_dict = backtest_loop(start, end, strategy, executor)
```

报告两轨并行：
- **对齐轨**（§七）：从 `portfolio_dict["1day"]` 取每日账户总值 → NAV 序列；trades 从 executor 的订单回报重建，与 v3 侧逐笔比对。
- **分析轨**：直接用 qlib 原生 `qlib.contrib.evaluate.risk_analysis(portfolio_metrics, benchmark="883957.TI")` 产出年化/波动/回撤/超额，替代项目自写 `yearly_returns`，作为独立视角的绩效报告。

---

## 七、对齐验收（gate：不通过不进分析轨）

**基准**：env A 跑 `V3Backtester.run(BACKTEST_START)`（V4.3 冻结参数），导出 `nav_curve/holdings/trades/stats` 为 parquet，与 qlib 侧输出并排比对。

| 检查项 | 标准 |
|---|---|
| trades 明细 | `date/type/from/to/reason` 逐笔完全相同 |
| 关键计数 | `entries/switches/exits/stop_count/check_days/gate_fail_days/no_leader_days` 相等 |
| NAV 逐日 | 相对偏差 max < 1e-5（固有偏差源 = §5.4 买入费基差 c²≈1e-6/笔，累计 < 1e-4） |
| holdings 序列 | 逐日持仓代码相同 |
| leaders/half_lives | 从 `daily_context.parquet` 对照 v3 stats 相等 |

失败时的定位顺序：日历一致性 → meta.json 参数指纹 → 逐笔 trades 首个分歧点 → 单规则二分（关闭止损/缓冲/冷却分别对齐定位）。

---

## 八、实施步骤

| 步 | 内容 | 环境 | 依赖 |
|----|------|------|------|
| S0 | conda 建 `qlib` 环境（qlib 0.9.7 + pandas 2.x）；pip 不通则 `pip install -e /tmp/qlib_repo`（源码已验证可 clone） | B | — |
| S1 | `export_qlib_signals.py`：终榜 + daily_context + meta.json；先用纯日线轨（`daily_top=0`）调通，再开 V4.3 分钟轨 | A | 分层重构「信号/引擎分离」完成更稳（§3.5）；不阻塞，可私有调用先落地 |
| S2 | `dump_market.py`：bin 目录 + 日历/ instruments 校验（与信号日期轴 diff=0） | B | S0 |
| S3 | `DailyRankSignal` + `ResonanceStrategy`（§五） | B | S1, S2 |
| S4 | `run.py` 组装 + 纯日线轨对齐验收 → 通过后 V4.3 分钟轨验收（§七） | A+B | S3 |
| S5 | qlib 原生 risk_analysis 报告轨 + benchmark 对照 | B | S4 |

**与分层重构（T1-T12）的排序建议**：重构先行不中断；qlib 轨 S0/S2 可并行做（纯环境/数据工程，不碰 resonance 包）；S1/S3 依赖信号层接口稳定——要么等重构的信号/引擎分离步完成后落地（推荐，`final_ranking()` 提公共后 export 脚本干净），要么以私有方法调用先落地、重构时机械替换（行为不变，两不耽误）。

---

## 九、风险与核实点

1. **`SignalWCache.resam` 跨日泄漏**：`method="last"` 会在查询窗口内「就近取值」，若用跨日窗口查询会把旧信号日误当当日候选。已用自写 `DailyRankSignal` 精确单日 `df.xs(date)` 规避；它满足 `Signal` 抽象契约，`create_signal_from` 对 `Signal` 实例原样透传，可无缝嵌入 qlib 体系。
2. **`min_cost=5.0` 默认值**：不显式置 0 会对每笔小额交易引入 5 元固定费，成为系统性偏差源。S4 首轮对齐必查。
3. **日历一致性是全链路根基**：`trade_step`/`min_hold`/冷却期全部以 bar 序号计数，dump 的 `calendars/day.txt` 与 `close_all.index` 必须逐元素相等；S2 末尾强制 diff 断言。
4. **停牌语义差异**：v3 丢弃 pending 后状态保持；qlib 订单在 NaN close 日不成交、bar 末过期，状态同样保持，但「过期后次日的时序」需在 S4 用构造样例核实（概念指数两年内基本无缺行，低风险）。
5. **pandas 版本边界**：parquet 由 pandas 3.x 写、2.x 读，pyarrow 列存格式与 pandas 版本解耦，无兼容风险；信号文件仅含 float/datetime/str 基础类型，刻意不用任何 pandas 3 新特性序列化。
6. **qlib 区域配置**：`region="cn"` 会附带给日频日历加时段（09:30-15:00 对日频无影响）；dump 目录的 calendar 为纯日期序列，qlib `Cal.calendar(freq="day")` 直接消费，无时区陷阱。
7. **TopkDropout 诱惑**：中途切勿为省事切到 `TopkDropoutStrategy`——它无法表达冷却期/止损相对入场价/闸门退出，会产生语义漂移且难以察觉（NAV 相似但 trades 不同）。

---

## 附：qlib v0.9.7 关键源码索引（设计依据）

| 能力 | 位置 |
|---|---|
| 高层回测循环 | `qlib/backtest/backtest.py` — `backtest_loop` / `collect_data_loop`（generator 驱动，逐 bar 决策→执行→post_exe_step） |
| 执行器 | `qlib/backtest/executor.py` — `SimulatorExecutor`（`TT_SERIAL` 支持同 bar 先卖后买） |
| 交易所 | `qlib/backtest/exchange.py` — `limit_threshold=None` / `min_cost` / `trade_unit` / `extra_quote` |
| 策略基类 | `qlib/strategy/base.py` — `BaseStrategy.generate_trade_decision(execute_result)`，`trade_calendar`/`trade_position`/`trade_exchange` 属性 |
| 信号契约 | `qlib/backtest/signal.py` — `Signal` 抽象 / `SignalWCache`（静态缓存）/ `create_signal_from` |
| 订单 | `qlib/backtest/decision.py` — `Order` / `OrderDir` / `TradeDecisionWO` |
| 账户 | `qlib/backtest/account.py` — `Account(init_cash, …)` + `portfolio_metrics` |
| 数据 dump | `scripts/dump_bin.py` — bin 目录格式参照 |
