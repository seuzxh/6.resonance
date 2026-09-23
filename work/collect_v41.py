"""V4.1 数据补采：上证指数 000001.SH / 深证成指 399001.SZ（日线 + 5min）。

V4.1 指数池变更（docs/v4-best-plan.md §3）：+上证指数/深证成指，−红利指数/
国证2000。本脚本只补两个新代码；700050.TI / 932000.CSI 的 5min 经实测无
HF 覆盖（ifind.py §high_frequency 注释），不强补——分钟重排在这些领先日
按预注册降级策略回退日线排序（计数上报）。

用法：
    conda run -n resonance python work/collect_v41.py
断点语义：日线/5min 已含该 code 的对应区间则跳过。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.ifind import fetch_history_data, fetch_minute_close  # noqa: E402

NEW_DAILY = ["000001.SH", "399001.SZ", "883417.TI", "883404.TI"]
# 上证指数 / 深证成指 / 大盘股 / 同花顺情绪指数（2026-09-22 用户追加锚）
NEW_MINUTE = ["000001.SH", "883417.TI", "399001.SZ", "883404.TI"]
# 399001.SZ（深证成指）5min 为锚实验全流程终选所需（曾随出池停采）
DAILY_START, DAILY_END = "2024-10-08", "2026-09-18"
M5_START, M5_END = "2025-09-22", "2026-09-18"


def month_runs(start: str, end: str) -> list[tuple[str, str]]:
    idx = pd.date_range(start, end, freq="MS")
    runs = []
    first_end = (idx[0] - pd.Timedelta(days=1)).strftime("%Y-%m-%d") if len(idx) else end
    if first_end >= start:
        runs.append((start, min(first_end, end)))          # 首月残段
    for i, m in enumerate(idx):
        s = m.strftime("%Y-%m-%d")
        e = (idx[i + 1] - pd.Timedelta(days=1)).strftime("%Y-%m-%d") if i + 1 < len(idx) else end
        runs.append((max(s, start), min(e, end)))
    return runs


def topup_daily() -> None:
    f = config.CACHE_DIR / "daily_bars.parquet"
    bars = pd.read_parquet(f)
    runs_of: dict[str, list[tuple[str, str]]] = {}
    for c in NEW_DAILY:
        sub = bars[bars["symbol"] == c]["date"]
        if sub.empty:
            runs_of[c] = month_runs(DAILY_START, DAILY_END)
        elif str(sub.min()) > DAILY_START:                 # 前段残缺 → 补首日起至现存最早日前
            gap_end = (pd.Timestamp(sub.min()) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            runs_of[c] = [(DAILY_START, gap_end)]
    if not runs_of:
        print(f"[daily] 全部已存在且覆盖完整：{NEW_DAILY}")
        return
    frames = []
    for c, cruns in runs_of.items():
        for s, e in cruns:
            for attempt in range(3):
                try:
                    got = fetch_history_data([c], s, e)
                    break
                except Exception as ex:  # noqa: BLE001
                    print(f"  {c} {s}~{e} 重试 {attempt + 1}: {ex}")
                    time.sleep(3.0)
            else:
                raise RuntimeError(f"{c} {s}~{e} 三次失败")
            frames.extend(got)
            time.sleep(0.5)
    new = pd.concat(frames, ignore_index=True)
    print(f"[daily] 新增 {new['symbol'].nunique()} codes × {len(new)} 行 "
          f"({new['date'].min()}~{new['date'].max()})")
    out = pd.concat([bars, new], ignore_index=True)
    out.to_parquet(f, index=False)
    print(f"[daily] 写回 {f}")


def topup_minute5() -> None:
    f = config.CACHE_DIR / "minute5_bars.parquet"
    m5 = pd.read_parquet(f)
    m5["datetime"] = pd.to_datetime(m5["datetime"])
    have = set(m5["symbol"])
    todo = [c for c in NEW_MINUTE if c not in have]
    if not todo:
        print(f"[minute5] 全部已存在：{NEW_MINUTE}")
        return
    frames = []
    for c in todo:
        # 按季度分段（48 bar/日 × ~60 日 ≈ 2.9k 点/段，安全低于 MaxPoints）
        for s, e in [(r.start_time.strftime("%Y-%m-%d"), r.end_time.strftime("%Y-%m-%d"))
                     for r in pd.period_range(M5_START, M5_END, freq="Q")]:
            span_end = min(e, M5_END)
            for attempt in range(3):
                try:
                    df = fetch_minute_close([c], s, span_end, interval="5")
                    if not df.empty:
                        frames.append(df)
                    break
                except Exception as ex:  # noqa: BLE001
                    print(f"  {c} {s}~{span_end} 重试 {attempt + 1}: {ex}")
                    time.sleep(3.0)
            else:
                raise RuntimeError(f"{c} {s}~{span_end} 三次失败")
            time.sleep(0.4)
    if frames:
        new = pd.concat(frames, ignore_index=True)
        print(f"[minute5] 新增 {new['symbol'].nunique()} codes × {len(new)} bar "
              f"({new['datetime'].min()}~{new['datetime'].max()})")
        out = pd.concat([m5, new], ignore_index=True)
        out.to_parquet(f, index=False)
        print(f"[minute5] 写回 {f}")
    else:
        print("[minute5] 无新数据（HF 无覆盖）")


def main() -> int:
    topup_daily()
    topup_minute5()
    # 验证
    bars = pd.read_parquet(config.CACHE_DIR / "daily_bars.parquet")
    for c in NEW_DAILY:
        sub = bars[bars["symbol"] == c]
        print(f"[verify] daily {c}: {len(sub)} 行 {sub['date'].min()}~{sub['date'].max()}")
    m5 = pd.read_parquet(config.CACHE_DIR / "minute5_bars.parquet")
    m5["datetime"] = pd.to_datetime(m5["datetime"])
    for c in NEW_MINUTE:
        sub = m5[m5["symbol"] == c]
        days = sub["datetime"].dt.date.nunique()
        print(f"[verify] m5 {c}: {len(sub)} bar / {days} 日")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
