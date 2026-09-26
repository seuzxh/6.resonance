# ops

生产与运维脚本（读冻结规格的运行面；与 docs/ops/ 每日运行层同名同义）。
脚本按「每日运行 / 数据层 / 研究复现」分组；文档归类见
[docs/README.md](../docs/README.md)。探索验证脚本在 [research/](../research/)，
不进本目录。

## 每日运行（推理过程——只读冻结规格）

- `signal_daily.py`：每日 OOS runner（增量采集 → 5min 自愈补齐 → OOS 起点
  无状态重放 → 信号/净值/审计落盘；尾部挂钩站点发布；15:05 后运行）。
- `publish_site.py`：站点数据发布（recent / nav / signals / archive JSON →
  `site/public/data/`，支持 `--dry-run`）。

## 数据层（采集与体检）

- `collect.py`：日线采集（断点续传；`--catalog-only` 重建概念目录快照）。
- `collect_minute5.py`：5min 采集（按需求矩阵裁剪，省配额）。
- `validate_data.py`：数据体检；`probe.py`：iFinD 接口冒烟。

## 研究复现（长期有效的基线入口）

- `backtest_v3.py`：V3 栈锚点对照 + 相位表（`load_wide`/`evaluate` 被
  `backtest_v41.py` 复用）。
- `backtest_v41.py`：V4.1 分钟重排栈（现行生产栈）复现。

## 清理与重组史

- 收口实验脚本两轮清理：2026-09-23（≤0790071）与 2026-09-26（≤5fff188，
  过度工程审计）。结论留档 `docs/research/` 与 `outputs/`，git 历史可溯。
- 2026-09-26 目录重组（work/ → ops/ + research/，生产与探索分离）：
  `collect_v41.py` / `collect_v41_qlib.py` / `collect_v41_topup.py` 三个
  V4.1 时代休眠采集脚本删除（git 可溯），本目录只保留现役链路。
