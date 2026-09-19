"""5min 分钟数据按需采集：(code,day) 需求矩阵裁剪 + 区间合并请求 + 覆盖感知断点。

用法：
    conda run -n resonance python work/collect_minute5.py

成本工程（docs/minute-resonance-design.md v3）：5min=48bar/日。需求 = 日线池
（v3：短窗 w∈{2..5} 的 Top10，逐日并集）成员的信号日 + 3 日回看，仅限分钟
留存期（2025-09-22 起）。断点以已有 parquet 的 (code, date) 覆盖为准，增量补采。

产出：data/cache/minute5_bars.parquet（长表 symbol,datetime,close）。
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.minute import MINUTE_WINDOW_DAYS, POOL_SIZE  # noqa: E402
from resonance.ifind import fetch_minute_close  # noqa: E402

OUT_FILE = config.CACHE_DIR / "minute5_bars.parquet"
PARTIAL_FILE = config.CACHE_DIR / "minute5_bars.partial.parquet"
MINUTE_START = "2025-09-22"   # 滚动留存上界
POOL_WINDOWS = (2, 3, 4, 5)   # v3：日线层短窗扫描（1 退化为单点无相关）


def build_need() -> dict[str, list[tuple[str, str]]]:
    """短窗日线 Top10 池（×覆盖，逐 w 并集）∪ 3 日回看 → 每代码合并日期区间。"""
    bars = pd.read_parquet(config.CACHE_DIR / "daily_bars.parquet")
    catalog = pd.read_csv(config.DATA_DIR / "concept_catalog.csv")
    concepts = [c for c in catalog["code"] if c in set(bars["symbol"])]
    close = bars.pivot(index="date", columns="symbol", values="close").sort_index()
    close.index = pd.to_datetime(close.index)
    broad = list(config.BROAD_INDEX_POOL)
    returns = close.pct_change()
    cov = json.loads((config.CACHE_DIR / "minute_coverage.json").read_text())
    have = set(cov["have"])

    cal = close.loc[MINUTE_START:]
    need_days: dict[str, set[pd.Timestamp]] = {}
    for c in broad:
        if c in have:
            need_days[c] = set(cal.index)  # 领导指数全留存期
    day_list = sorted(cal.index)

    for w in POOL_WINDOWS:
        # 逐日领先指数（短窗动量）
        leader_by_day = {}
        for d in day_list:
            sub = close[broad].loc[:d]
            if len(sub) >= w + 1:
                mom = (sub.iloc[-1] / sub.iloc[-w - 1] - 1).dropna()
                if len(mom):
                    leader_by_day[d] = mom.idxmax()
        for leader in set(leader_by_day.values()):
            corr = returns[concepts].rolling(w).corr(returns[leader])
            days = [d for d, l in leader_by_day.items() if l == leader]
            sub = corr.loc[days]
            pos = {d: i for i, d in enumerate(day_list)}
            for d in sub.index:
                row = sub.loc[d].dropna()
                if len(row) < POOL_SIZE:
                    continue
                pool = set(row.nlargest(POOL_SIZE).index) & have
                i = pos[d]
                for j in range(max(0, i - MINUTE_WINDOW_DAYS + 1), i + 1):
                    dj = day_list[j]
                    for c in pool:
                        need_days.setdefault(c, set()).add(dj)

    # 合并区间：间隔 >3 个自然日则切分
    intervals: dict[str, list[tuple[str, str]]] = {}
    for c, days in need_days.items():
        ds = sorted(days)
        if not ds:
            continue
        runs = [[ds[0], ds[0]]]
        for d in ds[1:]:
            if (d - runs[-1][1]).days > 3:
                runs.append([d, d])
            else:
                runs[-1][1] = d
        intervals[c] = [(r[0].strftime("%Y-%m-%d"), r[1].strftime("%Y-%m-%d")) for r in runs]
    return intervals


def main() -> int:
    intervals = build_need()
    n_req = sum(len(v) for v in intervals.values())
    print(f"[需求矩阵 v3] {len(intervals)} codes × {n_req} 个区间")

    # 覆盖感知断点：已有 parquet 的 (code, date) 集合
    frames = []
    covered: set[tuple[str, str]] = set()
    for f in (OUT_FILE, PARTIAL_FILE):
        if f.exists():
            df = pd.read_parquet(f)
            frames.append(df)
            last_day = df[df["datetime"].dt.strftime("%H:%M") == "15:00"]
            covered |= set(zip(last_day["symbol"], last_day["datetime"].dt.strftime("%Y-%m-%d")))
    print(f"  已有覆盖 {len(covered):,} (code,day)")

    t0, n_fetched = time.time(), 0
    total = n_req
    for c, runs in sorted(intervals.items()):
        for s, e in runs:
            # 该区间内需要的交易日（有日线者）是否已全覆盖
            trade_days = pd.bdate_range(s, e).strftime("%Y-%m-%d")
            need = {(c, d.strftime("%Y-%m-%d")) for d in pd.to_datetime(trade_days)}
            if need and need.issubset(covered):
                continue
            for attempt in range(3):
                try:
                    df = fetch_minute_close([c], s, e, interval="5")
                    break
                except Exception as exc:  # noqa: BLE001
                    if attempt == 2:
                        raise
                    print(f"  {c} {s}~{e} 重试 {attempt + 1}: {exc}")
                    time.sleep(5 * (attempt + 1))
            frames.append(df)
            last_day = df[df["datetime"].dt.strftime("%H:%M") == "15:00"]
            covered |= set(zip(last_day["symbol"], last_day["datetime"].dt.strftime("%Y-%m-%d")))
            n_fetched += 1
            if n_fetched % 25 == 0:
                pd.concat(frames, ignore_index=True).drop_duplicates(
                    subset=["symbol", "datetime"], keep="last"
                ).to_parquet(PARTIAL_FILE, index=False)
                print(f"  增量 {n_fetched} 请求（{time.time() - t0:.0f}s）", flush=True)
            time.sleep(0.15)

    if n_fetched == 0:
        print("[OK] 需求已全覆盖，无需补采")
    bars = pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=["symbol", "datetime"], keep="last"
    )
    bars.to_parquet(OUT_FILE, index=False)
    PARTIAL_FILE.unlink(missing_ok=True)
    per_day = bars.groupby([bars["symbol"], bars["datetime"].dt.date]).size()
    print(f"[OK] {len(bars):,} 行 × {bars['symbol'].nunique()} codes → {OUT_FILE}")
    print(f"  每日bar数分布: {per_day.value_counts().to_dict()} | 本次新增请求 {n_fetched}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
