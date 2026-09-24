# 文档导航

本站为 **resonance** 项目的文档 Pages。仓库与代码（含 README）见
[GitHub · seuzxh/6.resonance](https://github.com/seuzxh/6.resonance)。

**现行方案：[V4.3 三锚动选（暂定）](docs/v43-best-plan.md)**
—— 深证成指/国证2000/科创50 三锚动选 + 日线 Top5 × 24 根 5 分钟纯分钟重排
+ Top3 缓冲；OOS 四轨样本外验证运行中（每日同步暂停，用户通知后开启）。

## 📌 现行方案与冻结记录

- [V4.3 三锚动选（现行，2026-09-23 定档）](docs/v43-best-plan.md)

## 🧪 方法论与协议

- [实验手册：七条定律与负结论登记表（新实验必读）](docs/experiment-playbook.md)
- [OOS 样本外验证设计（四轨，预注册）](docs/oos-validation-design.md)

## 📊 实验报告

- [锚点实验：13 锚全流程终选 + 9 池拆解归因](outputs/exp_anchor/report.md)
- [2022-2024 历史扩展验证（时间外推警示级）+ 数据体检](outputs/exp_hist_2022/report.md)

## 📐 图表（交互式）

- [系统架构：数据源 → 缓存 → V3 引擎 → 产出](docs/diagrams/architecture.html)
- [OOS 每日运行流程（三道数据防线）](docs/diagrams/oos-daily-workflow.html)
- [信号生命周期时序（T → T+1 → T+2）](docs/diagrams/signal-lifecycle-sequence.html)
- [数据流：双链路采集与降级修复](docs/diagrams/data-pipeline-dataflow.html)
- [持仓状态机（建仓/持有/检查/风控出口）](docs/diagrams/holding-lifecycle.html)

## 🗄 起源

- [GPT 会话迁移纪要（项目起源）](docs/gpt-session-summary.md)

---

⚠️ 全部回测为指数收益研究口径（概念指数不可直接交易、统一成本压力假设、
概念目录幸存者偏差）；样本内数字按上界理解（实验手册 L3）。
旧版本文档（V3/V4.1/V4.2 方案与报告、dyn5 归档）已于 2026-09-23 清理，
git 历史（≤0790071）完整可溯。
