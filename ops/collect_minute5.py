"""5min 分钟数据按需采集：(code,day) 需求矩阵裁剪 + 区间合并请求 + 覆盖感知断点。

用法：
    conda run -n resonance python ops/collect_minute5.py               # 增量（新日期/新代码）
    conda run -n resonance python ops/collect_minute5.py --dry-run     # 只报请求量与预估 dataVol
    conda run -n resonance python ops/collect_minute5.py --backfill --from 2026-09-01
    conda run -n resonance python ops/collect_minute5.py --backfill --codes 885311.TI,883957.TI

成本工程（docs/research/minute-resonance-design.md v3）：5min=48bar/日。需求 = 日线池
（v3：短窗 w∈{2..5} 的 Top10，逐日并集）成员的信号日 + 3 日回看，仅限分钟
留存期（2025-09-22 起）。断点以已有 parquet 的 (code, date) 覆盖为准。

2026-09-26 全指标扩展（用户指令：指标直接取接口，不做本地推导）：9 指标
open/high/low/close/avg_price/volume/amount/change/change_ratio，值与量纲均为
接口原样。dataVol=48bar×9指标≈432/code·日，是 close 单指标的 9 倍——历史
close-only 行**默认不重采**（增量语义不变），--backfill 显式回补：仅对缺指标
字段的 (code,day) 重采并以全指标行覆盖旧行（keep=last）。留存窗口滚动 ~1 年，
窗口外的最早几天（2025-09-22~25 前后）接口已不可回取，永久保持 close-only。

产出：data/cache/minute5_bars.parquet（长表 symbol,datetime + 9 指标列；
历史行新列可为 NaN）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.minute import MINUTE_WINDOW_DAYS, POOL_SIZE  # noqa: E402
from resonance.ifind import (  # noqa: E402
    MINUTE_INDICATOR_COLS, IfindError, fetch_minute_bars,
)

OUT_FILE = config.CACHE_DIR / "minute5_bars.parquet"
PARTIAL_FILE = config.CACHE_DIR / "minute5_bars.partial.parquet"
MINUTE_START = "2025-09-22"   # 滚动留存上界
POOL_WINDOWS = (2, 3, 4, 5)   # v3：日线层短窗扫描（1 退化为单点无相关）
FULL_COLS = ["symbol", "datetime", *MINUTE_INDICATOR_COLS]


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

    # 合并区间：间隔 >3 个自然日则切分（与增量口径一致）
    return {c: merge_runs(sorted(ds)) for c, ds in need_days.items() if ds}


def merge_runs(days: list) -> list[tuple[str, str]]:
    """排序后的 Timestamp 日列表 → 合并区间（间隔 >3 自然日切分）。"""
    runs = [[days[0], days[0]]]
    for d in days[1:]:
        if (d - runs[-1][1]).days > 3:
            runs.append([d, d])
        else:
            runs[-1][1] = d
    return [(r[0].strftime("%Y-%m-%d"), r[1].strftime("%Y-%m-%d")) for r in runs]


def fetch_window_slide(c: str, s: str, e: str) -> pd.DataFrame:
    """-4309（留存窗口越界）时把起点逐日后滑至窗口内；其余异常原样抛。

    窗口随当日滚动（trial 账户 ~1 年，当前边界 ≈ 2025-09-26）：区间跨边界
    → 滑到窗口内重试；**整段在窗口外**（如 2025-09-22~25 的最早几天）→
    接口永久不可回取，返回空表跳过（保持 close-only）。-4309 错误响应
    不返回数据、不耗 dataVol，逐日试探无成本。
    """
    s_dt = dt.date.fromisoformat(s)
    e_dt = dt.date.fromisoformat(e)
    while True:
        try:
            return fetch_minute_bars([c], s_dt.isoformat(), e, interval="5")
        except IfindError as exc:
            if "-4309" not in str(exc):
                raise
            if s_dt >= e_dt:
                empty = pd.DataFrame(columns=["symbol", "datetime", *MINUTE_INDICATOR_COLS])
                empty["datetime"] = pd.to_datetime(empty["datetime"])
                return empty
            s_dt += dt.timedelta(days=1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true",
                    help="回补数据集内所有缺指标字段的 (code,day)（不限 v3 需求矩阵，"
                         "含 v41_topup 等渠道；≈432 dataVol/日·code）")
    ap.add_argument("--from", dest="from_date", default="0000-00-00",
                    help="仅回补该日期起（YYYY-MM-DD）")
    ap.add_argument("--codes", default="", help="仅处理这些代码（逗号分隔）")
    ap.add_argument("--dry-run", action="store_true", help="只统计请求与预估 dataVol")
    args = ap.parse_args()

    # 覆盖感知断点：covered_any=有 15:00 close 行（增量语义）；
    # covered_full=15:00 行已带全指标（backfill 语义，open 非空代表新 schema）
    frames, covered_any, covered_full = [], set(), set()
    close_only_days: dict[str, set[str]] = {}
    for f in (OUT_FILE, PARTIAL_FILE):
        if f.exists():
            df = pd.read_parquet(f)
            frames.append(df)
            last_day = df[df["datetime"].dt.strftime("%H:%M") == "15:00"].copy()
            if len(last_day) and "open" not in last_day.columns:
                last_day["open"] = float("nan")
            for c, d, full in zip(last_day["symbol"],
                                  last_day["datetime"].dt.strftime("%Y-%m-%d"),
                                  last_day["open"].notna()):
                covered_any.add((c, d))
                if full:
                    covered_full.add((c, d))
                else:
                    close_only_days.setdefault(c, set()).add(d)
    print(f"  已有 {len(covered_any):,} (code,day) close 行：全指标 {len(covered_full):,}，"
          f"close-only {len(covered_any) - len(covered_full):,}")

    if args.backfill:
        # 回补口径：数据集内所有缺指标字段的天（含下午盘 24bar 天 → 重采成全日 48bar）
        intervals = {c: merge_runs(sorted(pd.to_datetime(list(ds))))
                     for c, ds in close_only_days.items()}
    else:
        intervals = build_need()
    if args.codes:
        keep = set(args.codes.split(","))
        intervals = {c: v for c, v in intervals.items() if c in keep}
    n_req = sum(len(v) for v in intervals.values())
    print(f"[{'回补全口径' if args.backfill else '需求矩阵 v3'}] "
          f"{len(intervals)} codes × {n_req} 个区间")

    covered = covered_full if args.backfill else covered_any
    t0, n_fetched, est_points = time.time(), 0, 0
    for c, runs in sorted(intervals.items()):
        for s, e in runs:
            if args.from_date > "0000-00-00":
                s = max(s, args.from_date)  # 裁剪请求区间，避免整段白拉
                if s > e:
                    continue
            trade_days = pd.bdate_range(s, e).strftime("%Y-%m-%d")
            need = {(c, d) for d in trade_days}
            if args.from_date > "0000-00-00":
                need = {p for p in need if p[1] >= args.from_date}
            if not need or need.issubset(covered):
                continue
            if args.dry_run:
                est_points += len(need) * 48 * 9
                n_fetched += 1
                continue
            for attempt in range(3):
                try:
                    df = fetch_window_slide(c, s, e)
                    break
                except Exception as exc:  # noqa: BLE001
                    if attempt == 2:
                        raise
                    print(f"  {c} {s}~{e} 重试 {attempt + 1}: {exc}")
                    time.sleep(5 * (attempt + 1))
            frames.append(df)
            last_day = df[df["datetime"].dt.strftime("%H:%M") == "15:00"]
            pairs = set(zip(last_day["symbol"], last_day["datetime"].dt.strftime("%Y-%m-%d")))
            covered |= pairs
            if "open" in df.columns and len(last_day):
                full = last_day[last_day["open"].notna()]
                covered_full |= set(zip(full["symbol"], full["datetime"].dt.strftime("%Y-%m-%d")))
            n_fetched += 1
            if n_fetched % 25 == 0:
                bars = pd.concat(frames, ignore_index=True).drop_duplicates(
                    subset=["symbol", "datetime"], keep="last"
                )
                bars[FULL_COLS[2:]] = bars.reindex(columns=FULL_COLS[2:])
                bars[FULL_COLS].to_parquet(PARTIAL_FILE, index=False)
                print(f"  增量 {n_fetched} 请求（{time.time() - t0:.0f}s）", flush=True)
            time.sleep(0.35)  # 节流防突发限流（09-26 回补事故教训）

    if args.dry_run:
        print(f"[DRY] 需 {n_fetched} 个区间请求，预估 dataVol ≈ {est_points:,} 点"
              "（48bar×9指标 / code·日）")
        return 0
    if n_fetched == 0:
        print("[OK] 需求已全覆盖，无需补采")
    bars = pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=["symbol", "datetime"], keep="last"
    )
    for col in FULL_COLS[2:]:
        if col not in bars.columns:
            bars[col] = float("nan")
    bars = bars[FULL_COLS].sort_values(["symbol", "datetime"]).reset_index(drop=True)
    bars.to_parquet(OUT_FILE, index=False)
    PARTIAL_FILE.unlink(missing_ok=True)
    full_rows = int(bars["open"].notna().sum())
    print(f"[OK] {len(bars):,} 行 × {bars['symbol'].nunique()} codes → {OUT_FILE}")
    print(f"  全指标行 {full_rows:,} / {len(bars):,}（其余为 close-only 历史）"
          f" | 本次请求 {n_fetched}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
