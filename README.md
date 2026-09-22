# resonance — 指数—概念共振与指数轮动回测

概念板块指数与宽基指数的"共振"（相关性）分析，及其驱动的指数轮动回测。
迁移自 ChatGPT Codex 会话「验证 iFinD 方案」（2026-09-17/18，Windows），
完整上下文见 [docs/gpt-session-summary.md](docs/gpt-session-summary.md)。

## 项目定位

- **数据层**：同花顺 iFinD REST（quantapi 网关），指数/概念日线 + 概念目录。
- **指标层**：20 日 Pearson 相关（主口径）、控制全A偏相关（信息量口径）、5/60 日对照。
- **回测层**：指数轮动收益回测器（Top5 缓冲、T+1、几何复利）；VectorBT 适配。
- **研究性质**：指数收益累加研究，费率/滑点为 0，不代表可实盘成交。

## 当前状态

- [x] 2026-09-19 建仓迁移：iFinD 客户端、共振指标、回测引擎核心、离线测试就绪。
- [x] 2026-09-19 数据采集（全量）：概念目录快照 529 个；13 宽基 + 529 概念日线落盘
      （478 交易日；首轮配额中断，换备用账号 token 后断点续传补全）。
- [x] 2026-09-19 基线复现（核心口径对齐）：dyn5 总收益 +152.36% vs 锚点 +148.59%，
      超额 +79.60% vs +80.13%，夏普 1.840 vs 1.792，回撤 −35.37% 精确一致。
      判定会话口径：exec_lag=1（信号次日收盘入场）、全A 基准日 = 2024-12-31 收盘。
      **⚠️ 新发现：调仓网格相位敏感性极高（相邻相位总收益 +29%~+152%），锚点恰为
      最优相位**——总收益数字不可作稳健预期。见
      [outputs/index_backtest_framework/reproduction_report.md](outputs/index_backtest_framework/reproduction_report.md)。
- [x] 2026-09-19 分钟共振探索 v1–v4b（预注册验证，**五代指标全部不采纳**）。
      v4（极值时刻弹性：某日权重指数 5~20min 最大涨/跌时刻的同时刻概念响应）：
      8/8 HARMFUL；v4b（用户追加：只涨窗 e_up=C_up/R_up）：7/8 HARMFUL +
      1 NEUTRAL（w5·N1 +3.3pp 但 3/5 相位不达标），IC 无信息（w3 接近显著
      负向 −0.12/t=−1.64）。五代证据链（60min/5min 滚动相关、短窗、极值双窗/
      只涨弹性）系统性排除"Top10 池内分钟共振再排序"；分钟数据剩余方向：
      领导层日内确认 / 盘中风控。见
      [outputs/minute_resonance/report.md](outputs/minute_resonance/report.md)。
- [ ] 探索方向①：扩展权重/风格指数池；②动态调仓区间；③止损；④空仓/国债避险
      （任何新结论须过网格相位稳健性检验，如多相位取中位数）。

## 环境

只使用 conda 环境 `resonance`（Python 3.12，vectorbt 1.1.0）：

```bash
conda run -n resonance python -m pytest -q          # 离线测试（18 个）
conda run -n resonance python work/probe.py         # iFinD 冒烟（需网络+凭证）
conda run -n resonance python work/collect.py       # 日线采集（目录+日线，断点续传）
conda run -n resonance python work/collect_minute.py    # 60min 分钟采集（全窗）
conda run -n resonance python work/collect_minute5.py   # 5min 分钟采集（需求矩阵裁剪）
conda run -n resonance python work/validate_data.py # 数据验证（schema/网格/跨源对照）
conda run -n resonance python work/backtest_dynamic.py  # 基线复现（变体矩阵）
conda run -n resonance python work/backtest_minute.py   # 分钟共振验证（5 相位×三变体）
```

凭证不进仓库：refresh_token 从 `/home/zxh/qlib_data/scripts/` 全局源或环境变量
`IFIND_REFRESH_TOKEN` 读取；access_token 缓存于 `/home/zxh/qlib_data/.ifind_token`（多项目共享）。

## 目录

```
resonance/     Python 包：config / ifind 客户端 / metrics 共振指标 / backtest 轮动引擎
work/          运维脚本（probe 冒烟、采集、看板）
tests/         离线单元测试（全部 mock）
data/          本地缓存（gitignored）
outputs/       交付物（ifind_validation / index_backtest_framework）
docs/          GPT 会话纪要、方案文档
```

## 硬约束

1. 禁止未来函数：T 日信号最早 T+1 计收益。
2. 指数不可直接交易；任何"实盘化"结论必须注明缺费率/滑点/跟踪误差。
3. 概念目录是当前快照，历史回测存在幸存者偏差，结论须带此标注。
4. 凭证不入库（secrets 纪律，见 `resonance/ifind.py` 文档字符串）。
