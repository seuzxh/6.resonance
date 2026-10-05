# resonance — 指数—概念上涨共振策略研究

概念指数与宽基指数的上涨共振分析及其驱动的指数轮动回测。
数据源同花顺 iFinD REST；迁移自 ChatGPT Codex 会话（2026-09-17/18），
起源上下文见 [docs/research/gpt-session-summary.md](docs/research/gpt-session-summary.md)。

## 当前方案（2026-10-05核对）

先读[当前详细方案](docs/spec/current-plan.md)，再看[现有流程图](docs/diagrams/current-plan-20261003/current-workflow.html)。

- 生产主轨D3是深证成指、国证2000、科创50三锚动选，采用V4.4次日开盘执行。
- 生产并行纸面轨D2是深证成指、中证1000双锚动选；两轨参数完全相同。
- 日线共振窗口为10日，前5名经最近24根五分钟行情重排；最终前三名用于单仓续持缓冲。
- 简单成分比例只保留观察，机器学习增强未通过完整采纳要求。
- 用户已停止本轮持续自主探索。生产参数和样本外评价保持冻结。
- 手动账单已补至2026-09-30；外部调度实时状态未在本次核验。正式四轨为D3（三锚主轨）、A9（九池对照）、B13（历史十三池名义对照）、C1（深证成指单锚）；D2双锚生产并行纸面轨、G2国证2000单锚与K5科创50单锚展示轨不是正式评价轨。

生产部署、盘中监控与复盘见[生产并行部署与复盘方案](docs/ops/production-deployment-and-review.md)。

概念指数不可直接交易，成本是统一压力假设；存活目录有幸存者偏差，历史研究按样本内上界解释。
下一轮方向见[开仓、空仓与仓位恢复路线](docs/research/exposure-roadmap.md)，本次只整理、未开跑。
完整数字及窗口见当前详细方案，避免把不同起始日期或开盘、收盘执行口径混用。

## 版本与方法论

[V4.3历史冻结记录](docs/spec/v43-best-plan.md)说明三锚来源；
[V4.4执行规格](docs/spec/v44-open-exec-plan.md)说明现行开盘口径。
[研究总账](docs/research/qlib-autonomous-summary.md)汇总10785次账户回放及未通过的门槛，
[实验手册](docs/research/experiment-playbook.md)保留预注册要求与负结论。

## 环境

只使用 conda 环境 `resonance`（Python 3.12）：

```bash
conda run -n resonance python -m pytest -q          # 离线测试（全 mock）
conda run -n resonance python ops/probe.py         # iFinD 冒烟（需网络+凭证）
conda run -n resonance python ops/signal_daily.py  # 每日样本外（OOS）runner（运行器，D3主轨）
conda run -n resonance python ops/audit_agent.py  # 凌晨生产审计智能体（只读复盘）
conda run -n resonance python ops/validate_data.py      # 数据体检
conda run -n resonance python ops/collect.py            # 日线采集（断点续传）
conda run -n resonance python ops/collect_minute5.py    # 5min 采集（需求矩阵裁剪）
conda run -n resonance python ops/backtest_v3.py        # V3 栈锚点对照 + 相位表
conda run -n resonance python ops/backtest_v41.py       # V4.1 分钟重排栈复现
```

凭证不进仓库：refresh_token 从 `/home/zxh/qlib_data/scripts/` 全局源或环境变量
`IFIND_REFRESH_TOKEN` 读取；access_token 缓存于 `/home/zxh/qlib_data/.ifind_token`
（多项目共享）。

## 目录

```
resonance/     Python 包：config / ifind 客户端 / metrics 共振指标 / backtest 轮动引擎 / v3 现行引擎
ops/           生产与运维脚本（采集、数据体检、回测复现、每日 OOS runner、站点发布）
research/      探索验证脚本（预注册实验实施，收口即清理）
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
