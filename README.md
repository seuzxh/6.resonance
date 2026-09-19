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
- [x] 2026-09-19 分钟共振探索 v1+v2（预注册验证，结论 **均不采纳**）：Top10 日线池内
      分钟共振再排序——60min 版 NEUTRAL（中位差 +2.3pp 方向不一致）、**5min 版
      HARMFUL（中位差 −9.6pp）**，剂量效应明确：粒度越细越差；IC 诊断表明池内
      相关度排序（任意频率）对未来 5 日收益无预测力。5min 数据经 (code,day) 需求
      矩阵裁剪采集（611k dataVol，15:00 bar 与日线 0bps 一致）。见
      [outputs/minute_resonance/report.md](outputs/minute_resonance/report.md)。
- [x] 2026-09-19 分钟级执行层优化（信号层不变，**B 项采纳**）：A 盘中交易时点
      NEUTRAL 不采纳（两腿对消：早卖旧 −0.25% vs 早买新 +0.29%，净≈0）；
      **B 5min 盘中追踪止损 SUCCESS——推荐 x=4%**：5 相位中位总收益 +28.93%
      vs 基线 +13.02%，最大回撤 −17.30% vs −36.70%，夏普 0.55→1.33，
      收益/回撤/夏普 5/5 相位全部改善；分钟 vs 收盘粒度增量 = 收益 +13.4pp +
      回撤 +5.7pp（64% 止损在上午 9–10 点触发=隔夜跳空早离场）。10/10 网格点
      过预注册判据。见 [outputs/minute_exec/report.md](outputs/minute_exec/report.md)
      与 [docs/minute-exec-design.md](docs/minute-exec-design.md)。
- [x] 2026-09-19 信号窗口变更验证（用户指令 w∈{3,5}）：**缩短信号窗损大于益**——
      w=3 证伪（验证窗中位 −12.6%，全窗超额 −20pp 跑输全A）；w=5 收益让渡近半
      （全窗 +53% vs +152%）换更浅回撤；盘中止损增量与信号窗强相关（网格通过率
      w=20 10/10 > w=5 2/10 > w=3 1/10）。**最优组合仍为 w=20 + B4% 盘中止损**
      （帕累托支配所有短窗配置）；次优 w=5+B4%（+20.7%/−19.5%）。短窗 B 结果
      需 1,209 对补采去覆盖混杂后才是最终口径。见
      [outputs/minute_exec/report_w35.md](outputs/minute_exec/report_w35.md)。
- [x] 2026-09-20 上涨共振验证（用户指令，up=上涨日条件相关，down 对照）：
      **不采纳**——裸栈 +4.2pp 小改善真实但被止损层吸收（生产栈 B4% 上
      −2.3~−4.8pp，功能重叠）；全窗 up +109% vs full +152%。**维持全样本
      20 日 Pearson + B4% 盘中止损（+28.9%/−17.3%）**；up 裸栈
      （+17.2%/−31.4%）留作无止损场景备选。判定脚本对照 bug 已修正并记录。
      见 [outputs/minute_exec/report_upres.md](outputs/minute_exec/report_upres.md)。
- [x] 2026-09-20 用户组合口径验证（短窗上涨共振 4/5 日 + 3 日动量闸门 + 逐日滚动，
      消融阶梯归因）：**完整组合不替换生产配置**（B4% 栈让渡收益 11~20pp，止损与
      快信号第三次功能挤占）；**但 R3_w5 裸栈（+20.1%/−19.9%/0.82，5/5 相位，
      无分钟依赖）作为纯日线场景备选入库**。组件归因：短窗up共振 +3.6pp、闸门
      −6.7pp、滚动 +10.2pp（换手 6 倍）；逐日滚动消解网格相位敏感性（协议检验力
      相应下降）。见 [outputs/minute_exec/report_uc.md](outputs/minute_exec/report_uc.md)。
- [ ] 探索方向①：扩展权重/风格指数池；②动态调仓区间（日频滚动已验，见 UC R3）；
      ③止损（执行层 4% 版已
      验证，可再试基准自适应 x）；④空仓/国债避险（任何新结论须过网格相位
      稳健性检验，如多相位取中位数）。

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
conda run -n resonance python work/backtest_exec.py     # 执行层验证（时点网格+止损网格×双粒度）
conda run -n resonance python work/backtest_exec_w35.py  # 信号窗 w∈{20,5,3} 重验证
conda run -n resonance python work/backtest_upres.py    # 上涨共振 vs 全样本口径验证
conda run -n resonance python work/backtest_uc.py      # 用户组合口径（短窗up+闸门+滚动）消融
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
