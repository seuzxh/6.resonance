# 5min 因子覆盖率报告（T4）

- 5min 日历日数（特征实际覆盖日）：243
- 概念 universe：389（5min 覆盖；全目录 529）
- 逐因子非 NaN 率（全体均值）：

| 因子 | 非 NaN 率 |
|---|---|
| TAIL_MOM24 | 30.9% |
| FULL_MOM48 | 30.9% |
| TAIL_VOL24 | 35.4% |
| FULL_VOL48 | 35.4% |
| TAIL_VRATIO | 34.8% |
| DAY_POS | 30.5% |
| HI_PUMP | 30.2% |
| TAIL_ACC | 30.9% |

- 头 4 日（2025-09-22~25）为 close-only 历史，OHLCV 类因子 该段为 NaN（数据清单 §二.2）。
- F1–F7 桥列非 NaN 率：score 40.8%。