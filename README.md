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
- [ ] 数据采集：13 宽基指数池 + 概念目录 2025-01-01 起日线落盘。
- [ ] 复现 GPT 会话的动态宽基→概念策略基线（5日调仓）。
- [ ] 探索方向①：扩展权重/风格指数池；②动态调仓区间；③止损；④空仓/国债避险。

## 环境

只使用 conda 环境 `resonance`（Python 3.12，vectorbt 1.1.0）：

```bash
conda run -n resonance python -m pytest -q          # 离线测试
conda run -n resonance python work/probe.py         # iFinD 冒烟（需网络+凭证）
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
