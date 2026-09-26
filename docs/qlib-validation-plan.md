# qlib 回测验证方案（指数共振策略独立引擎复算）——设计定稿待评审

> 2026-09-26 设计。目标：把现行 V4.3 三锚动选生产口径（含 V4.1 分钟重排层）
> 放到 qlib（pyqlib 0.9.7）上做**独立引擎回测验证**，用现有 `data/cache/`
> parquet 数据，**零网络、零生产参数改动**。本文件为预注册实验设计
> （experiment-playbook §五检查清单逐条回答见 §九）。
>
> 状态：**方案设计，未开跑**。实施前需用户过目 §十 待决策项。

## 一、验证什么：三问分解（qlib 的诚实定位）

本验证**不是**"用 qlib 重写策略找新收益"，而是三层独立复算。 qlib 在本
项目中的角色与边界：

| # | 验证问题 | qlib 提供什么 | 抓什么错 |
|---|---|---|---|
| Q1 | **引擎正确性**：自研 `V3Backtester` 事件循环的记账（双边成本、T+1 收盘成交 T+2 起算、5% 收盘止损、冷却期、topk 缓冲、最短持有）在独立第三方框架复算下是否逐笔一致？ | qlib Exchange / Account / Report 的成交与记账框架 | 自研回测器的实现 bug（成本漏记/复利次序/日期错位）——本项目所有历史锚点（+165.4% 等）都出自该引擎，值得一次独立复核 |
| Q2 | **数据链路正确性**：parquet → qlib bin → qlib API 读回是否无损？ | 独立数据读取通道（`D.features`） | dump 格式错位、日历对齐错位、NaN 语义丢失 |
| Q3 | **分析增量**：本项目从未测过的信号诊断 | qlib 数据通道上的 IC/Rank IC/衰减分析、标准报告 | 盲区补测（不产生新收益结论） |

**明确不做的事**（避免伪等价）：

1. **不用 `TopkDropoutStrategy` 做等价验证**。该组件是"分数→权重"式调仓，
   无法表达闸门退出、min_hold、止损、cooldown、exec_lag=1 等路径依赖规则；
   强行近似会得到**另一个策略**。qlib 等价验证走自定义 `BaseStrategy`
   移植（§五 P3）；TopkDropout 仅作可选归因对照（§六）。
2. **不把 5min 数据 dump 进 qlib**。分钟层是"T 收盘时刻的重排打分器"
   （尾盘 24 bar 重排日线 Top5），不是盘中成交；且本项目 5min 为 48 槽
   bar 结束时刻制，与 qlib 高频日历 50 槽制网格不同（data-inventory §六）。
   分钟层经信号桥文件进入（§五 P2）。
3. **不动生产/OOS**。评价期禁改参（v43-best-plan §四）；本验证零改动
   V4.3 参数与 OOS 四轨。若 Q1 发现引擎 bug 导致历史锚点漂移，**上报
   用户决策**，不自行改参或改历史结论。
4. **不并入外部共享 qlib 库**。沿 2026-09-25 调研结论（data-inventory
   §三）：自建独立根（日历/instruments 全自管）；外部 `~/.qlib/qlib_data/
   cn_data` 为后复权口径，禁止混算，仅作双通道对照参照（既有先例
   `work/validate_data.py`、`work/collect_v41_qlib.py`）。

## 二、已具备的基础设施（本方案的事实基础）

| 资产 | 现状 | 在本方案中的用途 |
|---|---|---|
| `data/cache/daily_bars.parquet` | 593,804 行 × 546 标的，2021-12-01~2026-09-24，不复权 | dump 源（唯一数据输入） |
| `data/cache/minute5_bars.parquet` | 404 标的，2025-09-22~2026-09-23，48 bar/日 | 信号桥的分钟层输入（不 dump） |
| conda env `qlib` | pyqlib 0.9.7 ｜ pandas 2.3.3 ｜ numpy 2.4.6 ｜ py3.12 | qlib 端全部脚本运行环境（**不往 resonance env 装 pyqlib**——resonance 是 pandas 3.0.5，pyqlib 0.9.7 未经此版本验证） |
| conda env `resonance` | pandas 3.0.5，无 pyqlib | 信号计算、参考引擎、dump 脚本（dump 只用 numpy，env 无关） |
| `resonance/v3.py` | V3Signals（预计算信号）+ V3Backtester（耦合事件循环） | P2 桥文件的数据源 + 参考净值引擎 |
| bin 格式先例 | `work/collect_v41_qlib.py` 已逆向并交叉验证过 bin 头约定（`bin[0]`=起始日历下标，b0=1152 滑动对齐 4 指数互证）；`~/qlib_data/scripts/dump.py` 有成熟 dump CLI（本项目不依赖它，但格式约定一致） | dump 脚本的格式依据 |
| 冻结锚点 | V4.3 完整周期 5 相位中位 **+165.4%/−16.2%/1.98@10bp**（日线栈，exp_anchor_full9.py，2025-01-02 相位族）；分钟子窗 **+87.6%/−13.0%/2.07**；V4.1 九池 +90.37%@10bp 等 | 锚点对照按项目惯例**方向/量级判定**（目录快照漂移不做逐点对齐） |

## 三、总体架构：两环境三段桥

核心思想：把"信号"与"执行"先解耦成**可独立差分定位**的三段等价链——

```
V3Backtester（信号+执行耦合，resonance env，现状引擎）
   ≡①  ReplayBacktester（榜桥文件驱动，同执行规则，resonance env）
   ≡②  ResonanceStrategy（qlib BaseStrategy + qlib Exchange 记账，qlib env）

①差异 → 信号抽取 bug；②差异 → 执行/记账实现 bug（自研 vs qlib 任一方）。
```

数据与工件流：

```
data/cache/daily_bars.parquet
  ├─ P1 dump ──▶ data/qlib_root/resonance_day/{calendars,instruments,features}
  │                  └─ qlib D.features 读回（qlib env）──数据门 D-Gate
  ├─ P2 信号桥（resonance env，V3Signals + MinuteBarProvider 分钟重排）
  │      └─▶ outputs/qlib_bridge/final_rank.parquet（逐日最终榜+闸门+降级计数）
  │      └─▶ outputs/qlib_bridge/ref_runs/（参考净值/逐笔交易，float32 cast 口径）
  └─ P3 qlib 等价回测（qlib env）──引擎门 E-Gate──▶ P4 IC/报告
                                               └─▶ outputs/qlib_validation/report.md
```

环境纪律（对应 CLAUDE.md 硬约束 1/2）：

- `resonance` env：`work/qlib_dump.py`、`work/qlib_bridge_export.py`；
  离线，只读 parquet。
- `qlib` env：`work/qlib_equivalence.py`、`work/qlib_analysis.py`；
  只读 qlib 根 + 桥文件，`qlib.init(provider_uri=…, region=REG_CN)`
  指向自建根，无任何网络数据源。
- 两 env 唯一契约 = qlib 根 + 桥文件（pandas 2/3 语义差异被文件边界隔离）。

## 四、P0 环境定策与冒烟（0.5 天）

**任务**

1. 确认 `qlib` env 可 `qlib.init` 指向临时最小根（手造 2 标的 × 10 日 bin）
   并 `D.features` 读回；记录 pyqlib 0.9.7 对自建根的日历/instruments 要求
   （如 instruments 文件名与 `UNIVERSE` 约定、`region` 参数行为）。
2. 仓库内建 `work/` 目录骨架与运行说明（两 env 命令行），不写业务逻辑。

**验收 G0**：qlib env 下读回最小根 close 与手造数据逐元素相等；全程断网。

## 五、P1–P3 主体验证（4–6 天）

### P1 独立 qlib 数据根 + 数据门 D-Gate（1 天）

**dump 设计**（`work/qlib_dump.py`，纯 numpy/stdlib，两 env 均可跑）：

- 根目录 `data/qlib_root/resonance_day/`（在 .gitignore 的 data/ 下，
  可由 parquet 重建；建成后登记 data-inventory）。
- `calendars/day.txt`：daily_bars 日期并集（2021-12-01~2026-09-24）。
- 代码映射（可逆，映射表落 `code_map.csv`）：`.SH→sh / .SZ→sz / .TI→ti /
  .CSI→csi / .BJ→bj`，如 `885907.TI→ti885907`、`399001.SZ→sz399001`。
- `features/<code>/{open,high,low,close,volume}.day.bin`：float32，
  头部 `bin[0]`=该标的起始日历下标（沿 collect_v41_qlib.py 已验证约定），
  前导 NaN 原样写（概念激活晚的语义保留）；amount/turnover 不 dump
  （信号与执行只用 close；外库 amount 字段有 dump 损坏前科）。
- `instruments/`：`all.txt`（546 码，起止=首/末有效日）、`concept.txt`
  （529，目录∩bars）、`broad13.txt`（历史复现口径）、`anchor_v43.txt`
  （三锚）、`pool_v41_9.txt`（A9 对照）。
- 幂等：重跑整树重建；**不含任何已退役码的"instruments 池文件"新增**
  （broad13 含 700050/932000 属历史复现冻结例外，文件头注释标明，见 §九.8）。

**D-Gate 验收（预注册）**：

1. float32 域网格相等：`D.features(codes, ["$close"], …)` 读回与
   `daily_bars.pivot.astype(np.float32)` 逐元素相等（NaN 位置亦相等）；
   546 码 × 全历史 100% 通过。
2. instruments 覆盖相等：各池文件码集与 config 定义/目录快照完全一致；
   每码起止日与首末有效交易日一致。
3. 双通道抽查（沿 validate_data.py 前例）：双重存在宽基
   （000300/000905/000852/399006）本根 close 与外部 `cn_data` 后复权价
   的比值序列平稳（factor 一致性），防止 dump 端口径事故。
4. 单元测试（resonance env 离线）：`tests/test_qlib_dump.py` 覆盖 bin
   round-trip、头下标、NaN 保留、代码映射可逆。

### P2 信号桥 + 执行解耦（1–2 天）

**桥文件**（`work/qlib_bridge_export.py`，resonance env）：

- `outputs/qlib_bridge/final_rank.parquet`：逐日全序最终榜（经 V4.1 分钟
  重排层），列 `date, rank, concept, score, leader, gate, half_life,
  minute_layer, minute_fb_leader, minute_fb_sparse, minute_excluded`；
  闸门失败/无候选日 = 该日零行。导出窗 = 数据全史（2022-01 起有信号）。
- `outputs/qlib_bridge/ref_runs/`：参考引擎（V3Backtester 原样）在
  **float32 cast 后 close 矩阵**上重跑的净值与逐笔交易 CSV，配置覆盖
  P3 全部窗口×相位×成本（§五 P3 列表）。两端引擎吃同一份 float32
  矩阵，把数据差与引擎差彻底分离。

**ReplayBacktester（①段等价）**：resonance env 内新写"吃桥文件 + close
矩阵"的重放执行器（执行规则逐条照抄 V3Backtester：T+1 收盘成交、T+2
起算、topk=3 缓冲、min_hold=3、5% 收盘止损、cooldown=1、成本 10bp/边、
switch 双边）。

**G2 验收（预注册）**：三窗（§P3 窗口表）× 5 相位 × {0,10,30}bp 全配置下，
ReplayBacktester 与 V3Backtester **逐笔交易 100% 一致**（date/type/from/to/
价格 float32 相等），nav 终值相对差 ≤ 1e-9。失败即信号抽取有 bug，修完
再进 P3。

### P3 qlib 等价回测 + 引擎门 E-Gate（2–3 天）

**ResonanceStrategy（qlib env）**：继承 `qlib.backtest.strategy.BaseStrategy`，
配 `SimulatorExecutor`（日频）+ Exchange 研究模式：

- Exchange：`deal_price="close"`、`trade_unit=None`、`volume=None`、
  `limit_threshold=None`、`open_cost=close_cost=cost_bp/1e4`、`min_cost=0`
  ——概念指数不可直接交易，沿"指数收益研究口径"（统一成本压力假设）。
- exec_lag=1 的实现：桥文件为 T 日收盘信号，Strategy 在 T+1 决策时点读
  T 日榜并按收盘价成交（qlib 日频"决策→当日成交"约定下 = 消费 T−1 榜），
  无未来数据（CLAUDE.md 硬约束 3 保持）。
- 路径依赖状态（持仓龄、止损基准价、冷却期）封闭在 Strategy 实例内，
  换仓拆 sell+buy 两笔走 Exchange（成本次序与自研 nav 公式一致：
  switch 双边）。

**窗口×相位×成本矩阵（全量复用既有冻结协议，不新设网格）**：

| 窗口 | 相位起点族 | 栈 | 池 |
|---|---|---|---|
| 完整周期 2025-01-02→2026-09-18 | 2024-12-27/12-30/12-31/01-02/01-03（5 相位中位） | 日线栈（topk=3, hl=leader） | V43 三锚 |
| 分钟子窗 2025-09-22→2026-09-18 | 09-22…09-26（5 相位中位） | 全栈（+daily_top=5, 24bar 分钟重排，经桥文件） | V43 三锚 |
| 延伸窗 →2026-09-24（当前数据末） | 同上 5 相位 | 同上 | 同上（信息性，无锚点） |

成本 {0, 10, 30} bp。对照 V4.1 九池窗口（backtest_v41.py 口径）作第四窗
（可选）。

**E-Gate 验收（预注册）**：

1. 逐笔一致：全配置 qlib 成交记录与 `ref_runs/` 逐笔匹配 100%（日期/
   标的/方向/价格 float32 相等）；nav 终值相对差 ≤ 1e-9，逐日 nav 最大
   绝对差 ≤ 1e-9。
2. 绩效一致：total/max_dd 与自研 `perf_stats` 至 4 位小数相等（夏普声明
   年化因子差异：自研 244 vs qlib 默认 250，报告中两边口径并列）。
3. 锚点对照（方向/量级）：完整周期 10bp 中位落在 +165.4% 邻域
   （容差沿锚点复现惯例 ±10%相对量级，因目录快照 529 vs 会话期数量级
   漂移属已知残差）；分钟子窗对照 +87.6% 同理。
4. 若 qlib Exchange 成本模型细节导致 (2) 无法达标且排查后确认属框架
   差异而非 bug：启用**保底方案 B**——成本按自研公式在 Strategy 层记，
   qlib 只承担仓位/成交/报表框架，报告中显式声明降级及原因。

## 六、P4 分析增量（1 天）

1. **IC 家族（本项目首测）**：桥文件日线榜 score 与分钟层最终 score 分别
   对 T+1/T+2/T+5 概念收益做 Rank IC（Spearman）逐日序列 → 均值/ICIR/
   衰减曲线；按 5 相位窗口分窗报告；t 统计与多重比较措辞沿 playbook §一。
2. **qlib 标准报表**：equivalence run 的 `risk_analysis`（年化/波动/IR/
   回撤）+ 组合收益归因图（`qlib.contrib.report`，本地渲染，不上传）。
3. **归因对照（可选一窗）**：TopkDropoutStrategy(topk=1, 分数=桥 score,
   同成本) vs 等价栈同窗——量化"规则覆盖层"（闸门/止损/缓冲/min_hold）
   的贡献差，报告明确标注**非等价对照**。
4. 全部结论措辞遵守 L3：样本内、幸存者目录、指数不可交易。

## 七、P5 收尾登记（0.5 天）

1. `outputs/qlib_validation/report.md`：三问结论 + D/E-Gate 证据 +
   IC 诊断 + 锚点对照表 + 方案 B 是否启用的声明。
2. 文档四件套更新：data-inventory（qlib 根条目）、experiment-playbook
   §三或新验证条目、CLAUDE.md 常用入口（两 env 命令）、README 一段。
3. 记忆与 OOS 纪律复核：确认零生产改动；若 E-Gate 暴露自研引擎 bug →
   单独上报（含影响的历史锚点清单），由用户决策处置。

## 八、风险与对策

| # | 风险 | 对策 |
|---|---|---|
| R1 | pyqlib 0.9.7 × pandas 2.3.3 在自建根上的行为差异（instruments 约定/region 假设） | P0 冒烟先行；根内自带 code_map.csv 与 README，格式不依赖外部库 |
| R2 | float32 量化使阈值附近决策翻转 | 两端引擎统一吃 float32 cast 矩阵（P2 ref_runs 口径），决策一致性由构造保证；报告中注明净值与 float64 口径的终值差（信息性） |
| R3 | qlib Exchange 成本/成交细节不可配平 | E-Gate 兜底 + 保底方案 B（§五 P3.4）|
| R4 | 概念前导 NaN / 激活日语义在 bin 通道丢失 | D-Gate 第 1 条 NaN 位置相等验收；信号端 eligibility 规则不变 |
| R5 | pandas 2/3 跨 env 语义差 | 唯一契约为文件（qlib 根 + 桥 parquet）；resonance env 不 import pyqlib，qlib env 不 import resonance |
| R6 | 相位敏感性误读（网格相位敏感性极高，基线复现 §一.3） | 全部结论只取 5 相位中位；单相位数字仅诊断用 |
| R7 | 已退役码混入 | 池文件层面控制（§五 P1）；脚本入口调 `config.assert_no_retired`（dump 在 resonance env 可 import config） |
| R8 | 数据根被误当外部共享库更新 | 根目录 README 首行声明"独立根，禁并入/禁 cron 更新"；位于 data/ 下不入 git |

## 九、playbook §五检查清单（预注册回答）

1. 机制一句话：qlib 独立引擎复算自研回测器，抓实现 bug——非新 alpha
   假设，不涉 L1 快慢挤占。
2. 判据已预注册：D-Gate（§五 P1）、G2（§五 P2）、E-Gate（§五 P3）。
3. 单轴：仅新增验证层，生产栈零改动；网格全量复用既有冻结协议
   （V3/V4.1 双 5 相位），无新网格无边缘外探针需求。
4. 相位协议：复用两套 5 相位中位协议；本验证是等价性检验而非收益
   检验，检验力由逐笔一致（确定性）保证。
5. 数据覆盖：日线层 546 码全量；分钟层覆盖与降级计数沿桥文件透传
   （minute_fb_* 列），复现既有 49.8%/389-142 口径。
6. §三登记表：不撞（验证层非策略改动；负结论同样登记——若 qlib 复算
   不一致且定位为自研 bug，将修正并重发历史锚点影响清单）。
7. 措辞：等价验证结论 ≠ 收益结论；IC 诊断为样本内；幸存者目录与
   指数不可交易标注进报告模板。
8. `assert_no_retired`：dump 脚本入口自检；broad13.txt 属历史复现
   冻结例外（含 700050/932000），文件头注释声明，不用于新实验。

## 十、待用户决策项（开跑前确认）

1. **环境定策**：qlib 端用现有 `qlib` env（推荐，pyqlib 0.9.7 已就绪），
   resonance env 保持无 pyqlib。是否同意？
2. **数据根位置**：`data/qlib_root/resonance_day/`（gitignore 内、可由
   parquet 一键重建）。是否同意？
3. **TopkDropout 归因对照**（§六.3）：默认做一窗；可裁掉。
4. **第四窗（V4.1 九池对照复现）**：默认可选；可裁掉。
5. **P6 远期项（默认不做）**：5min dump 独立 qlib 根 + NestedExecutor
   盘中执行探索——分钟层已定性为"重排打分器"而非盘中成交，qlib 高频
   执行框架与其语义不匹配，除非未来引入真正的盘中交易规则。

## 十一、里程碑总表

| 阶段 | 产出 | 门槛（预注册） | 预估 |
|---|---|---|---|
| P0 | 环境冒烟 + 骨架 | qlib.init 自建根读回相等 | 0.5 天 |
| P1 | qlib 数据根 + dump 脚本 + 测试 | D-Gate 三条全过 | 1 天 |
| P2 | 信号桥 + ref_runs + ReplayBacktester | G2 逐笔 100% | 1–2 天 |
| P3 | ResonanceStrategy + 等价矩阵 | E-Gate 四条全过 | 2–3 天 |
| P4 | IC 诊断 + qlib 报表 + 归因对照 | 报告落盘 | 1 天 |
| P5 | 登记 + 文档四件套 | 评审通过 | 0.5 天 |
