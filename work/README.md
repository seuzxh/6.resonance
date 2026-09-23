# work

运维与实验脚本。

- `signal_daily.py`：每日 OOS runner（增量采集 → 5min 自愈补齐 → OOS 起点
  无状态重放 → 信号/净值/审计落盘）。
- `exp_anchor.py` / `exp_anchor_full9.py` / `exp_leader_split.py`：锚点终选
  实验系列（三锚决策依据）。
- `exp_hist_2022.py` / `collect_hist_2022.py`：2022-24 历史外推及其数据采集。
- `collect.py` / `collect_minute5.py` / `collect_v41*.py`：日线/分钟采集链路
  （含 qlib 降级与增量补齐）。
- `backtest_dynamic.py` / `backtest_v3.py` / `backtest_v41.py` / `opt_v3.py`：
  历代回测复现与优化入口。
- `probe.py` / `validate_data.py`：iFinD 冒烟与数据体检。

dyn5 时代归档脚本已于 2026-09-23 清理（git 历史 ≤0790071 可溯）。
