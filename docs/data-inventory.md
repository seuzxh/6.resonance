# 数据资产清单与口径说明（data inventory）

> 2026-09-25 整理 ｜ 覆盖项目全部自采行情数据（parquet/CSV/JSON 缓存）、外部
> 只读 qlib 库、采集与更新链路、覆盖边界与口径注意事项。文中数字为当日实测
> 快照；复核方法见 §七。后续数据变更（OOS 日增、池调整）后应更新本文。

## 一、存放位置与权威性

| 位置 | 角色 | 最后写入（mtime 证据） |
|---|---|---|
| `.worktrees/minute-exec/data/cache/` | **生产活跃副本**：OOS runner 增量写入 | 2026-09-24 13:59（daily/5min） |
| `data/cache/`（master 主仓） | 基线复现快照，已停更 | 2026-09-19（基线冻结时点） |
| `~/.qlib/qlib_data/` | 外部共享 qlib 库，**只读**，非本项目采集 | cron 日更（见 §三） |

**重要**：`data/` 下所有行情文件均被 `.gitignore` 排除（`data/cache/`、
`data/*.parquet`、`data/*.csv`）——行情只存在本地磁盘，**没有 git 恢复渠道**，
灾备依赖采集脚本重放（§五）与外部 qlib 库。`data/concept_catalog.csv` 同样
不入库（快照文件，可由 `work/collect.py --catalog-only` 重建）。

## 二、行情数据集明细

以下明细以 **worktree 生产副本** 为准；master 快照差异单独标注。

### 1. 日线 `daily_bars.parquet`（29.6MB）

| 维度 | 值 |
|---|---|
| 规模 | 593,258 行 × 546 标的（每标的最长 1,169 交易日） |
| 时间范围 | 2021-12-01 ~ 2026-09-23（原始采集自 2024-10-08 起；前段 2021-12~2024-10 为 2022-24 时间外推验证补采，`work/collect_hist_2022.py`） |
| 字段 | symbol, date, pre_close, open, high, low, close, pct_chg, volume, amount, turnover_ratio |
| 复权 | 不复权（iFinD history_data，CPS=0） |

标的构成：**533 个同花顺指数(.TI)**（883 段：883957 全A、883417 大盘股；
885/886 段概念 531 个）+ 13 个非 .TI 代码：

```
000001.SH 上证指数   000015.SH 红利指数  000016.SH 上证50  000300.SH 沪深300
000680.SH 科创综指   000688.SH 科创50   000852.SH 中证1000 000905.SH 中证500
399001.SZ 深证成指   399006.SZ 创业板指 399303.SZ 国证2000 899050.BJ 北证50
932000.CSI 中证2000（已退役，见 §四）
```

master 快照差异：542 标的（.TI 531 + 非 .TI 11），2024-10-08 ~ 2026-09-18，
258,236 行——缺 000001.SH / 399001.SZ（V4.1 新增）与 883417.TI 等 2 个 .TI。

### 2. 5 分钟 `minute5_bars.parquet`（11.6MB）

| 维度 | 值 |
|---|---|
| 规模 | 1,561,388 行 × 404 标的（.TI 392 + 12 非 .TI，V4.1 九池与 V4.3 三锚全部在内） |
| 时间范围 | 2025-09-22 ~ 2026-09-23（受 HF 分钟留存期 ~1 年限制，硬下界） |
| 字段 | 仅 close（省配额口径；生产栈只用尾盘 24 根重排） |
| 时隙网格 | bar **结束时刻** 09:35~11:30 + 13:05~15:00，48 根/日（注意与基础库 5min 日历 50 槽制不同，见 §六） |

master 快照：400 标的 / 1,358,688 行，同下界至 2026-09-18。

### 3. 60 分钟 `minute_bars.parquet`（1.4MB）——已证伪封存

198,312 行 × 207 标的（分钟验证窗 Top10 并集清单），仅 close，4 bar/日
（10:30/11:30/14:00/15:00），2025-09-22 ~ 2026-09-18。时点择时探索已证伪
（2026-09-19），**不再更新**，仅作历史归档；后续任何用途需先读
experiment-playbook §三负结论登记。

### 4. 概念目录 `data/concept_catalog.csv`

529 个概念：code, name, snapshot_date=2026-09-19。幸存者偏差口径：当日存活
目录快照，已退市/未激活概念不在内（playbook §四）。

### 5. 对照基准 `data/cache/probe_benchmark.csv`

4 标的（883957.TI / 000680.SH / 399006.SZ / 700050.TI）× 15 日的 iFinD
抽查值（close, pct_chg），2026-08-31~09-18。用于双通道交叉验证
（`work/validate_data.py`），防止采集链路口径漂移。

### 6. 元数据与审计文件

| 文件 | 位置 | 内容 |
|---|---|---|
| `minute_coverage.json` | master | HF 5min 覆盖清单：have=400 / miss=142（miss 全部为 .TI 概念 141 + 932000.CSI） |
| `minute_fetch_list.json` | master | 60min 采集清单 207 码（§二.3 的采集对象） |
| `top10_union.json` | master | 分钟验证窗日线 Top10 并集 279 码 |
| `v41_topup_audit.csv` | worktree | V4.1 Top5 概念 5min 增量补采审计 35 行（code, 区间, 应得/实得天数, bar 数） |
| `v41_topup_nocover.json` | worktree | 无 HF 覆盖不再重试名单：`["932000.CSI"]` |
| `oos_nocover.json` | worktree | OOS 自愈分钟无覆盖名单（当前空） |

## 三、外部 qlib 库（只读依赖，非本项目资产）

| 目录 | 内容 | 日历现状 |
|---|---|---|
| `~/.qlib/qlib_data/cn_data` | 个股日线 bin（~5,590 目录，7 字段后复权+factor） | day.txt 随外部 cron（工作日 15:30，`/home/zxh/qlib_data/scripts/cron_daily.sh`，中焯 K 线 API）日更至 2026-09-24；其 1min/5min 日历 2026-07 后停更 |
| `~/.qlib/qlib_data/cn_data_1min` | 个股 1min bin | 1min.txt 日更至 2026-09-24 |

本项目仅在 iFinD 配额耗尽时走 `work/collect_v41_qlib.py` 读取其中
000001.SH / 399001.SZ 两指数日线 bin。解码口径：`bin[0]`=起始日历下标
（对齐锚 b0=1152，4 指数滑动对齐交叉验证），amount 字段 dump 损坏弃用。

**口径警示**：基础库价格为后复权（×factor），本项目数据不复权——两库数据
不可直接混算。若需把本项目数据同步为 qlib bin，2026-09-25 调研结论为
**独立 qlib 根方案**（自建日历与 instruments，不并入共享库），详见会话记录。

## 四、覆盖边界与已知缺口

1. **HF 分钟留存期 ~1 年**：5min/60min 数据硬下界 2025-09-22，更早不可采。
2. **已退役指数（禁止新实验使用）**：`700050.TI` 微盘股、`932000.CSI` 中证2000
   ——HF 端点永久无 5min 数据（09-19 首测 + 09-25 同请求对照复核 0 bar），
   登记于 `resonance/config.py::RETIRED_NO_HF_CODES`，入口自检
   `config.assert_no_retired`。唯一保留例外：OOS 预注册冻结轨 A9/B13（评价期
   禁改参），其领先日按预注册降级回退日线排序。
3. **概念 5min 覆盖 389/529**：142 个概念指数无 HF 分钟数据，属数据源边界，
   非采集缺失。
4. **待补**：883404（同花顺情绪指数）日线尚未采集（配额恢复后补齐）。
5. master 快照不含 V4.1 新增代码（000001.SH/399001.SZ/883417.TI），复现
   V4.1+ 口径必须用 worktree 副本。

## 五、采集与更新链路

| 脚本（work/） | 数据源 | 产出 |
|---|---|---|
| `collect.py` | iFinD history_data（10 码/请求，断点续采） | concept_catalog + 全量日线 |
| `collect_minute5.py` | iFinD high_frequency（按需矩阵：信号日+3 日回看） | minute5_bars（全时段） |
| `collect_v41.py` | iFinD（V4.1 两新代码日线+5min） | 增量并入 daily/minute5 |
| `collect_v41_topup.py` | iFinD HF（下午盘 24bar 省配额口径） | 9 池 Top5 概念 5min 增量 + audit |
| `collect_hist_2022.py` | iFinD（2021-12→2024-10 长区间单请求） | 日线前段扩展 |
| `collect_v41_qlib.py` | 本地 qlib bin（只读） | 000001.SH/399001.SZ 日线补采 |
| `collect_minute.py` | iFinD HF（60min） | minute_bars（已封存） |
| `signal_daily.py` | iFinD（增量日线 + 自愈分钟 + 无状态重放） | OOS 四轨信号与净值，**并日增 data/cache** |

运行纪律（09-24 事故后固化）：runner **只能在 15:05 后跑**（盘中硬闸）；
分钟自愈带 keep-last/当日新鲜度检查；HF 15:00 bar 作收盘价链路。
runner 当前状态见 CLAUDE.md（暂停中，用户通知后开启）。

## 六、口径与使用注意

- **不复权**：所有价格为原始价；涨跌幅 pct_chg 由 close 推导。与外部 qlib
  基础库（后复权）口径不同，禁止混算。
- **T+1**：T 日信号最早 T+1 计收益（CLAUDE.md 硬约束 3）。
- **5min 时隙**：bar 结束时刻制、48 根/日；基础库 5min 日历为 09:30/13:00
  起的 50 槽制，两者网格不同。
- **幸存者偏差**：概念目录为 2026-09-19 存活快照，结论措辞须按 playbook L3。
- **两套副本**：跑实验前确认用的是哪套 cache（`config.CACHE_DIR` 随 checkout
  走）；V4.1+ 口径一律以 worktree 副本为准。

## 七、复核与更新本文档

数字复核脚本要点（conda env `resonance`）：

```python
import pandas as pd
for p in ["data/cache", ".worktrees/minute-exec/data/cache"]:
    for n in ["daily_bars.parquet", "minute5_bars.parquet"]:
        df = pd.read_parquet(f"{p}/{n}")
        print(p, n, len(df), df["symbol"].nunique(),
              df["date" if "date" in df else "datetime"].min(),
              df["date" if "date" in df else "datetime"].max())
```

OOS 日增或池调整后，重跑上式并更新 §一/§二 的数字与 mtime。
