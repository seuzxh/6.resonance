# 版本目录

> 2026-09-27 建（版本整理任务）。git 历史是博物馆、工作区是车间：
> 旧版本打进 tag 后 `git checkout <tag>` 即可复现；生命周期规则见
> CLAUDE.md「版本生命周期」节。

| tag | 口径要点 | 结论位置 | 归档日期 |
|---|---|---|---|
| `v4.3.0-trio-anchor`（已存在） | V4.3 三锚动选生产口径：完整周期 +165.4%/−16.2%/夏普 1.98；分钟子窗 +87.6%/−13.0%/2.07；参数 = V4.2 全量继承 + 锚池替换。**现行生产（D3 主轨）** | [docs/spec/v43-best-plan.md](../spec/v43-best-plan.md) | 2026-09-23 |
| `v3.0.0-up-resonance-frozen` | V3 规格独立复现入口（`ops/backtest_v3.py`）冻结。V3 终栈 = V3 规格 + 防御 strong4%：+74.4%/−13.2%/夏普 1.30@10bp。**V3 参数快照**（原 `outputs/v3/production.json`，随目录删除抄录）：`{"defense_dd": 0.04, "defense_strong": true}` | v3-best-plan.md 已随 2026-09-23 清理删除（git ≤0790071 可溯）；引擎本体 `resonance/v3.py` 在役（V4.3 生产） | 2026-09-27 |
| `legacy-stack-frozen` | GPT 骨架与分钟共振旧口径冻结：`resonance/metrics.py`、`resonance/backtest.py::RotationBacktester`（GPT 会话迁移骨架）+ `resonance/minute.py`（分钟共振 v1–v4，被 V4.1 分钟重排取代） | [gpt-session-summary.md](../research/gpt-session-summary.md)、[minute-resonance-design.md](../research/minute-resonance-design.md) | 2026-09-27 |

## 关联待办

- 上两 tag 涉及的四个文件，物理删除执行于分层重构 T3/T6
  （[docs/superpowers/plans/2026-09-27-layering-refactor.md](../superpowers/plans/2026-09-27-layering-refactor.md)）；
  删除 commit 须引用 tag 名（已写入计划）。
- `outputs/v3/`（V3 时代产物，git-ignored 磁盘残留、无文档引用）已于
  2026-09-27 删除：冻结参数快照抄录于上表，明细 nav/trades CSV 不再保留，
  结论数值以本目录与 git ≤0790071 报告为准。
