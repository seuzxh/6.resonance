# resonance — 指数—概念上涨共振策略研究

概念板块指数与宽基指数的"共振"（相关性）分析及其驱动的指数轮动回测。
数据源同花顺 iFinD REST；迁移自 ChatGPT Codex 会话（2026-09-17/18），
起源上下文见 [docs/research/gpt-session-summary.md](docs/research/gpt-session-summary.md)。

## 现行生产口径：V4.3 三锚动选（2026-09-23 用户定档，暂定）

- **锚定指数池**：深证成指 / 国证2000 / 科创50 三锚动选
  （`config.V43_ANCHOR_POOL`）。
- **栈**：20 日上涨共振 EW 信号 → 日线 Top5 预选 → 收盘前 24 根 5min 纯分钟
  重排 → Top3 缓冲 + 领先指数动态半衰期 + 5% 收盘止损 + 最短持有 3 日 +
  冷静期 1 日；T+1、几何复利、10bp 决策成本。冻结参数见
  [docs/spec/v43-best-plan.md](docs/spec/v43-best-plan.md) §三。
- **样本内实测（10bp，5 相位中位）**：完整周期 **+165.4% / −16.2% / 夏普
  1.98**；分钟子窗 +87.6% / −13.0% / 2.07；领先分布 73/66/51 三锚均衡轮动。
- **警示（知情保留）**：2022-24 时间外推三锚 **−56.2% 未通过**（成长牛市
  regime 依赖）；深证成指单锚 −1.7% 为唯一跨 regime 存活者，由 OOS C1 轨
  并行裁决；防御层与 regime 过滤路线已证伪关闭。全部数字为指数研究口径
  （不可直接交易、概念目录幸存者偏差、样本内上界——实验手册 L3）。
- **OOS**：四轨（D3 三锚主轨 / A9 九池 / B13 十三池 / C1 深证成指单锚对照）
  样本外验证，≥60 信号日按预注册判据裁决，评价期禁改参
  （[docs/ops/oos-validation-design.md](docs/ops/oos-validation-design.md)）。
  当前每日同步**暂停中**，用户通知后开启（runner 无状态重放、幂等）。

## 版本链与方法论

V3 纯日线栈（+74.4%）→ V4.1 13 池 + 分钟重排 → V4.2 topk3 + leader 半衰期
（9 池 +25.1%）→ **V4.3 三锚动选（现行）**。旧版本文档与报告已清理，
git 历史（≤0790071）完整可溯；在位方法论沉淀：

- [实验手册：七条定律与负结论登记表](docs/research/experiment-playbook.md)
- [锚点终选实验（三锚决策依据）](outputs/exp_anchor/report.md)
- [2022-24 历史外推验证](outputs/exp_hist_2022/report.md)

## 环境

只使用 conda 环境 `resonance`（Python 3.12）：

```bash
conda run -n resonance python -m pytest -q          # 离线测试（全 mock）
conda run -n resonance python work/probe.py         # iFinD 冒烟（需网络+凭证）
conda run -n resonance python work/signal_daily.py  # 每日 OOS runner（D3 主轨）
conda run -n resonance python work/validate_data.py      # 数据体检
conda run -n resonance python work/collect.py            # 日线采集（断点续传）
conda run -n resonance python work/collect_minute5.py    # 5min 采集（需求矩阵裁剪）
conda run -n resonance python work/backtest_v3.py        # V3 栈锚点对照 + 相位表
conda run -n resonance python work/backtest_v41.py       # V4.1 分钟重排栈复现
```

凭证不进仓库：refresh_token 从 `/home/zxh/qlib_data/scripts/` 全局源或环境变量
`IFIND_REFRESH_TOKEN` 读取；access_token 缓存于 `/home/zxh/qlib_data/.ifind_token`
（多项目共享）。

## 目录

```
resonance/     Python 包：config / ifind 客户端 / metrics 共振指标 / backtest 轮动引擎 / v3 现行引擎
work/          运维脚本（采集、回测复现、每日 OOS runner、站点发布）
tests/         离线单元测试（全部 mock）
data/          本地缓存（gitignored）
outputs/       交付物（exp_anchor / exp_hist_2022 / oos）
docs/          文档，按生命周期归类（导航见 docs/README.md）：
               spec/ 冻结规格 · ops/ 每日运行 · research/ 寻优研究 · data/ 数据层
docs/diagrams/ 交互式图表（架构/流程/时序/数据流/状态机，archify 生成）
```

## 硬约束

1. 禁止未来函数：T 日信号最早 T+1 计收益。
2. 指数不可直接交易；任何"实盘化"结论必须注明缺费率/滑点/跟踪误差。
3. 概念目录是当前快照，历史回测存在幸存者偏差，结论须带此标注。
4. 凭证不入库（secrets 纪律，见 `resonance/ifind.py` 文档字符串）。
5. OOS 评价期内禁止修改冻结参数。
