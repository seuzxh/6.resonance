# work

运维与实验脚本（按「每日运行 / 数据层 / 研究复现」分组；文档归类见
[docs/README.md](../docs/README.md)）。

## 每日运行（推理过程——只读冻结规格）

- `signal_daily.py`：每日 OOS runner（增量采集 → 5min 自愈补齐 → OOS 起点
  无状态重放 → 信号/净值/审计落盘；15:05 后运行）。
- `publish_site.py`：站点数据发布（recent / nav / signals / archive JSON →
  `site/public/data/`，支持 `--dry-run`）。

## 数据层（采集与体检）

- `collect.py`：日线采集（断点续传；`--catalog-only` 重建概念目录快照）。
- `collect_minute5.py`：5min 采集（按需求矩阵裁剪，省配额）。
- `collect_v41.py` / `collect_v41_qlib.py` / `collect_v41_topup.py`：V4.1 时代
  日线/分钟采集（iFinD 主链路与 qlib bin 降级链路、增量补齐）。
- `validate_data.py`：数据体检；`probe.py`：iFinD 接口冒烟。

## 研究复现（历史过程）

- `backtest_v3.py`：V3 栈锚点对照 + 相位表。
- `backtest_v41.py`：V4.1 分钟重排栈（现行生产栈）复现。

收口实验脚本（dyn5 归档、锚点终选系列、2022-24 历史外推、资金流探测、
opt_v3 优化网格）已分两轮清理：2026-09-23（≤0790071）与 2026-09-26
（≤5fff188，过度工程审计）。结论留档 `docs/research/` 与 `outputs/exp_*`，
git 历史完整可溯。
