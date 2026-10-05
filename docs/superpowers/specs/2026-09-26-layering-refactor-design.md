# 分层重构设计：FastAPI 同构（方案 B）

> 2026-09-26 立项。用户对现行分层不满意，要求参考 Python/FastAPI 架构设计
> 方案重设计。已确认三项前置决策：只借鉴分层思想（不引 web 框架）、包+入口
> 全面重排、删除死代码。目录方案选 B（领域语义同构）。

## 一、背景与病根

概念分层（research 写侧 / ops 读侧 / spec 工件 / data 共用，见
[docs/README.md](https://github.com/seuzxh/6.resonance/blob/master/docs/README.md)）是对的，但代码物理结构没有承载它：

| 病灶 | 表现 |
|---|---|
| 版本命名巨石 | `resonance/v3.py` 635 行装着信号核 + 分钟重排 + 回测引擎 + 统计；名字叫 v3，里面跑的是 V4.3 |
| config 三合一 | 基础设施(URL/token) + 领域池 + 冻结规格混在 `config.py` |
| 厚入口 | `signal_daily.py`(247 行)/`backtest_v41.py`(169 行)/`publish_site.py`(402 行) 内联编排逻辑、冻结参数与口径逻辑 |
| 死代码链 | `metrics.py` → `minute.py` 旧口径 → `backtest.py` 的 `RotationBacktester`，整条 GPT 会话时代旧栈（现役生产用 `v3.py` 内的 V4.1 分钟重排） |

## 二、FastAPI 分层 → 本项目映射

| FastAPI 概念 | 职责 | 本项目对应 |
|---|---|---|
| routers + main 组装根 | 入参解析、组装依赖、调服务 | `ops/` 薄入口（15:05 硬闸等运行纪律、git 部署动作留入口） |
| services/ | 用例编排，无传输层概念 | `resonance/services/` |
| repositories/ | 数据访问 | `resonance/dataio/` |
| models/ | 领域模型与纯逻辑 | `resonance/domain/`（纯无 IO） |
| schemas/ | 边界契约 DTO | `resonance/specs.py`（含 `V3Params` 参数模式类——参数即契约） |
| core/config.py | 基础设施配置 | `resonance/core/` |

关键同构：**specs ≈ schemas 是本映射的灵魂**。项目既有哲学「冻结规格解耦
研究轨/运行轨、两侧只依赖工件」与 FastAPI「两侧只依赖 schema 不互相依赖」
完全同构；且版本语义随之归位——版本不再是文件名（v3.py），而是 specs 里的
参数组合工件（「参数即模型」的物理化），代码是版本无关的通用引擎。

## 三、目标目录与依赖规则

```
resonance/
├── core/
│   └── config.py        # ①基础设施：端点/token/目录/采集参数/分钟常量
├── specs.py             # ②冻结规格工件（≈ schemas，零依赖最内层）：
│                        #   V3Params 参数类 + 池 + FROZEN/ANCHORS/TRACKS
│                        #   + RETIRED_NO_HF_CODES 及 assert_no_retired
├── domain/              # ③纯策略内核（无 IO，离线测试主场）
│   ├── resonance.py     #   评分内核：up_resonance_scores_np / minute_up_resonance
│   ├── signals.py       #   V3Signals / v3_ranking / compound_window / dynamic_half_life
│   ├── minute.py        #   MinuteBarProvider（纯内存 bar 供给，非 IO）
│   └── engine.py        #   V3Backtester 事件循环
├── dataio/              # ④数据访问（≈ repositories）
│   ├── ifind.py         #   iFinD REST 客户端（token/分块，原样迁移）
│   ├── cache.py         #   data/cache 宽表落盘/加载（只存原始宽表；「拼接」不在此层：展示口径归 services/publish，bar 窗拼接归 domain/minute）
│   └── outputs.py       #   outputs/oos 运行产物读取（nav/trades/信号 JSON）
└── services/            # ⑤用例编排（≈ services）
    ├── daily.py         #   OOS 每日 runner 用例：update_daily/topup/replay_track
    ├── backtest.py      #   生产栈回测复现：build_provider/run_stack/stats_row
    ├── performance.py   #   perf_stats / yearly_returns
    └── publish.py       #   站点数据构建用例（见 §六）
```

依赖铁律（单向，无环）：

- `specs.py` 不依赖任何层（纯 dataclass + 字面量 + 守卫）；
- `domain/` 只依赖 `specs`（及 stdlib/numpy/pandas），不许 import
  dataio/services/core/ops；
- `dataio/` 只依赖 `core`，只碰 IO 不做策略决策；
- `services/` 编排 domain + dataio，降级与配额决策归此层；
- `ops/`（及 `research/` 探索脚本）是组装根，可依赖全部；research 也可
  直调 domain 做纯计算实验。

依赖注入用构造函数传实例（测试注入 mock dataio，落实「网络必须 mock」的
既有硬约束），不引 DI 框架。注：`V3Backtester` 构造器的 `broad_codes`
默认值（原取 config.BROAD_INDEX_POOL）删除，调用方显式传池。**已知例外
一处**（2026-09-27 复核修正：原「生产调用方全部显式传参」不成立）：
`backtest_v41.py` 第三节 V3 规格栈对照依赖该默认（13 池），迁移时须补
`broad_codes=list(specs.BROAD_INDEX_POOL)`（同值显式，行为不变）。

## 四、迁移映射

| 现状 | 去向 | 备注 |
|---|---|---|
| `v3.py` `V3Params` | `specs.py` | 参数模式类是契约的数据类部分 |
| `v3.py` 评分内核两函数 | `domain/resonance.py` | |
| `v3.py` 信号层（V3Signals 等） | `domain/signals.py` | |
| `v3.py` `MinuteBarProvider` | `domain/minute.py` | |
| `v3.py` `V3Backtester` | `domain/engine.py` | |
| `v3.py` `yearly_returns`、`backtest.py` `perf_stats` | `services/performance.py` | np_sqrt 内联函数清理为 numpy |
| `ifind.py` 整体 | `dataio/ifind.py` | 原样迁移 |
| ops 内联 `load_wide`/宽表落盘 | `dataio/cache.py` | |
| `signal_daily.py` update_daily/topup_minute/replay_track | `services/daily.py` | main 留入口只做闸+组装+打印 |
| `signal_daily.py` `POOL13`/`TRACKS`/`FROZEN`/`OOS_START` | `specs.py` | |
| `backtest_v41.py` build_provider/run_stack/stats_row | `services/backtest.py` | |
| `backtest_v41.py` 冻结常量族（`V41_STACK`/`DAILY_CTRL`/`V41_PHASES`/`V41_END` 与验证锚点数值 `ANCHORS`） | `specs.py` | 两处同名 ANCHORS 含义不同：此处为**复现验证锚点数值**；`publish_site.py` 的 `ANCHORS` 是三锚池（改引用 `specs.V43_ANCHOR_POOL`） |
| `config.py` URL/token/目录/`CONCEPT_CODE_RANGE`/`COLLECT_START`/`HD_FIELD_MAP` | `core/config.py` | |
| `config.py` 三池（`V43_ANCHOR_POOL`/`V41_BROAD_POOL`/`BROAD_INDEX_POOL`）、`RETIRED_NO_HF_CODES`+`assert_no_retired` | `specs.py` | |
| `minute.py` 采集常量（`MINUTE_INTERVAL` 等，`collect_minute5` 在用） | `core/config.py` | |
| `metrics.py`、`minute.py` 其余、`RotationBacktester`、`SIGNAL_WINDOW` 等 GPT 参数 | **删除** | 实施时以 grep 引用核实；`BENCHMARK_INDEXES`/`BACKTEST_START` 等疑似无引用者同规则处理，发现仍有生产引用则迁 specs 并在 PR 记录 |
| `v3.py` 资金流研究钩子（`entry_gate`/`exit_grid`/`post_rank` 及 `flow_exit` 分支） | **删除** | 2026-09-26 查实仅 test_v3 的钩子用例在引用，生产零消费者（实验已收口，结论在 moneyflow-gate-plan §八）；对应测试随删。§十规则③的现成执行 |
| `ops/backtest_v3.py` | **删除** | 2026-09-27 复核查实为坏入口：line 66 传 `minute_prices=`，现引擎签名早已是 `minute_bars_provider`，运行必 TypeError；其职责（V3 规格栈对照）已被 backtest_v41 第三节覆盖 |
| 文档工件位置引用（CLAUDE.md 关键参数节、README.md:10、docs/README.md:6-7、docs/spec/v43-best-plan.md:5/47、docs/ops/oos-validation-design.md:16）与 CLAUDE.md 测试计数 | **同步改写** | 六处 `config.V43_ANCHOR_POOL`/FROZEN 旧位置引用改指 `resonance/specs.py`；CLAUDE.md「45 个测试」计数随用例删减更新；v43-best-plan 仅改交叉引用路径，非改参 |
| `publish_site.py` | 见 §六 | |
| `__init__.py` docstring | 更新 | 现述「相关性分析与轮动回测」已过时 |

## 五、数据流（每日 runner 为例）

```
ops/signal_daily.py（15:05硬闸 → 组装 specs+dataio+service）
  → services/daily.py：update_then_replay(track)
      → dataio/ifind：fetch_history_data / fetch_minute_close（远程）
      → dataio/cache：宽表落盘/加载
      → domain：signals → engine（纯计算）
      → outputs/oos/*.json（产物，站点消费层只读）
```

降级链路（分钟窗缺失回退、配额尽降级、keep-last/当日新鲜度）语义原样
归位：远程异常在 dataio 抛 `IfindError`；降级决策在 services；纯计算在
domain。不新增语义。

## 六、web 部署页（第二消费边界）

FastAPI 视角下站点是第二组消费 router，同规则处理：

| `publish_site.py` 现状 | 去向 |
|---|---|
| validate/_stitched/_trades_stitched/build_recent/replay_display/build_nav/build_signals/build_archive | `services/publish.py`（stitched 拼接、信号全局倒序等口径集中于此） |
| 读 outputs/oos 与 cache closes | `dataio/outputs.py`（validate_data 可复用） |
| `SHOW_TRACKS`/`TRACK_NAMES`/`PUBLISH_LAG`/`NAV_START` 展示口径 | `services/publish.py` 顶部常量（消费面配置，与策略冻结规格分开） |
| 内联 import `V3Backtester`/`MinuteBarProvider`/`perf_stats` | service 层正常依赖 domain / services.performance |
| main 中 git add/commit/push | 留在薄入口（组装+调 service+部署触发） |

不动：`site/` VitePress 前端（纯展示层，消费 public/data JSON，不属
Python 分层）、Pages CI/EdgeOne 部署链路、四个 JSON 的格式。

## 七、等价性验收（OOS 冻结期硬约束）

1. **重构前跑基线并存档**（离线，用现有 cache，不碰网络）：
   `backtest_v41` 全部栈的 nav/trades 产物；`signal_daily` 四轨重放信号
   （**直调 `replay_track(track, pool, end)` 离线采集**——`main()` 必走
   `update_daily` 走网络，勿经 main；零代码改动）；`publish_site
   --dry-run` 的四个 JSON；
2. **重构后同命令重跑**：nav 序列、trades 明细、信号文件数值逐项一致；
   `publish_site --dry-run` 四 JSON **byte 级一致**（它串起 cache 加载→
   回测重放→stitched 口径→全部 build 逻辑，是最强端到端等价测试）；
3. 存量用例随迁移全绿（pytest 保持离线可跑）；死代码/研究钩子用例随删，
   总数相应下降（原「45 个全绿」措辞与 §八 删例矛盾，2026-09-27 修正）；
4. 重构独立成 commit 可 bisect；`specs.py` 数值与原 config/FROZEN 字面
   一致——不改任何参数语义。

## 八、测试与文档对齐

- `tests/` 按层重排：`test_specs.py`（V3Params/池/退役守卫）、
  `test_domain_{resonance,signals,minute,engine}.py`、
  `test_dataio_ifind.py`、`test_services_{daily,backtest,publish}.py`；
  死代码用例（test_core 的 metrics/RotationBacktester、test_minute 旧
  口径）随删；各测试文件的 sys.path 引导（5 处）随 `pip install -e .`
  一并删除；
- `docs/README.md` 分层叙述补一张「代码五层 ↔ 文档四分」映射表（一句话
  级），不重写。

## 九、决策记录与非目标

已定决策：类名保留 `V3Params`/`V3Signals`/`V3Backtester` 前缀（实现的是
V3 规格族语义，改名噪音大于收益，版本语义由 specs 承载）；展示口径常量
不进 specs；`V3Backtester.broad_codes` 默认值删除（见 §三注）。

非目标（YAGNI）：不引入 FastAPI/pydantic/DI 框架；不做 repository
Protocol 抽象（qlib 备用链路是独立引擎复算、parquet 直驱，不是本包的
第二个 repo 实现，等真有第二消费者再抽象）；不动 docs 目录结构与 site/
前端；不改任何数值语义与参数；不改四个站点 JSON 的格式。

## 十、探索脚本（research/）与实验生命周期

research/ 与 ops/ 同级的消费面组装根：探索脚本可 import services
（复用回测/绩效用例，不再从 ops 抄编排逻辑）或直调 domain 做纯计算
变体；入口纪律不变（playbook §五检查清单 + `specs.assert_no_retired`）。
实验代码按生命周期落位，包内不积累实验残渣：

1. **进行中**：`research/<脚本>.py`（一次性代码，git 可溯即其档案）；
2. **收口**（无论采纳与否）：脚本删除（40a9532 先例）。存活三样——
   结论进 docs/research/、数据表进 outputs/、若采纳则参数组合作为新
   版本工件整体写入 specs.py（晋升门：版本整体替换）。**采纳的实验
   留下参数，不留代码**；
3. **需新内核能力的探索**：钩子先以默认 None 的参数挂在
   domain/engine 上做实验；采纳则保留为通用钩子，证伪则随脚本删除
   （§四资金流钩子行即本规则的执行）。

存量处置：未采纳实验的脚本已随收口清理（结论并入 docs/research/ 各
计划文档），research/ 现仅存活跃探索（qlib_smoke.py），本次无存量迁移。
