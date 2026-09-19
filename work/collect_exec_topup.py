"""执行层实验分钟数据补采（泛化版）：各信号窗 w × 止损网格持仓路径的缺失 (code,day)。

用法：
    conda run -n resonance python work/collect_exec_topup.py

需求 = 每窗基线调仓日（旧/新两腿）+ B[minute] 全止损网格持仓日中缺 5min 数据的对；
仅补"代码在 HF 有覆盖但当日缺"的缺口。写入本 worktree 的
data/cache/minute5_bars.parquet（不动主检出共享缓存），增量落盘防中断，
质量门：15:00 bar 与日线收盘 ≤0.1bps、每日 48 bar。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.backtest import RotationBacktester  # noqa: E402
from resonance.exec_minute import (  # noqa: E402
    MinutePrices,
    _reconstruct_holding,
    build_minute_wide,
    run_with_intraday_stop,
)
from resonance.ifind import fetch_minute_close  # noqa: E402
from work.backtest_exec import PHASE_STARTS, load_all  # noqa: E402
from work.backtest_upres import cached_rank_variant  # noqa: E402

OUT_FILE = config.CACHE_DIR / "minute5_bars.parquet"
PARTIAL_FILE = config.CACHE_DIR / "minute5_exec_topup.partial.parquet"
VARIANTS_TO_SCAN = ("up", "down")  # full/w∈{5,3} 已在先前补采中覆盖
STOP_GRID_TO_SCAN = (0.04, 0.06, 0.08, 0.10, 0.12)


def find_missing(close, minute_wide, concepts) -> tuple[set, set]:
    prices = MinutePrices(close, close.copy(), minute_wide)
    miss = set()
    for v in VARIANTS_TO_SCAN:
        rank_fn = cached_rank_variant(close, concepts, v, {})
        for start in PHASE_STARTS:
            bt = RotationBacktester(close[concepts].loc[start:], rank_fn, rebalance_days=5)
            out = bt.run()
            days = list(out["nav_curve"].index)
            _, trade_of = _reconstruct_holding(days, out["switches"])
            for d in days:
                if d in trade_of:
                    sw = next(s for s in out["switches"] if s["date"] == trade_of[d])
                    for c in (sw["from"], sw["to"]):
                        if c is not None and not prices.day_marks(c, d):
                            miss.add((c, d))
            for x in STOP_GRID_TO_SCAN:
                b = run_with_intraday_stop(close[concepts].loc[start:], prices, rank_fn,
                                           rebalance_days=5, topk=5, stop_pct=x, mode="minute")
                miss.update(b["degraded_log"])
    fetchable = {(c, d) for c, d in miss if c in set(minute_wide.columns)}
    no_hf = {c for c, _ in miss} - {c for c, _ in fetchable}
    return fetchable, no_hf


def merge_intervals(pairs) -> dict[str, list[tuple[str, str]]]:
    by_code: dict[str, list[pd.Timestamp]] = {}
    for c, d in pairs:
        by_code.setdefault(c, []).append(d)
    intervals = {}
    for c, ds in by_code.items():
        ds = sorted(set(ds))
        runs = [[ds[0], ds[0]]]
        for d in ds[1:]:
            if (d - runs[-1][1]).days > 3:
                runs.append([d, d])
            else:
                runs[-1][1] = d
        intervals[c] = [(r[0].strftime("%Y-%m-%d"), r[1].strftime("%Y-%m-%d")) for r in runs]
    return intervals


def main() -> int:
    close, open_, minute_wide, concepts = load_all()
    fetchable, no_hf = find_missing(close, minute_wide, concepts)
    print(f"缺失可补 {len(fetchable)} 对 | 无 HF 覆盖代码 {len(no_hf)}: {sorted(no_hf)}")
    if not fetchable:
        print("[OK] 无需补采")
        return 0
    intervals = merge_intervals(fetchable)
    n_req = sum(len(v) for v in intervals.values())
    print(f"补采 {len(intervals)} codes × {n_req} 区间（≈{len(fetchable) * 48 / 1000:.0f}k dataVol）")

    frames = [pd.read_parquet(OUT_FILE)]
    if PARTIAL_FILE.exists():  # 断点续采
        frames.append(pd.read_parquet(PARTIAL_FILE))
        have = set(zip(frames[-1]["symbol"], frames[-1]["datetime"]))
    else:
        have = set()
    t0, n_fetched = time.time(), 0
    for c, runs in sorted(intervals.items()):
        for s, e in runs:
            if any((c, pd.Timestamp(f"{d} 15:00")) in have for d in pd.bdate_range(s, e)):
                pass  # 区间部分已有也会由 keep=last 去重，直接请求
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
            n_fetched += 1
            if n_fetched % 25 == 0:
                pd.concat(frames, ignore_index=True).drop_duplicates(
                    subset=["symbol", "datetime"], keep="last"
                ).to_parquet(PARTIAL_FILE, index=False)
                print(f"  增量 {n_fetched}/{n_req} 请求（{time.time() - t0:.0f}s）", flush=True)
            time.sleep(0.15)

    bars = pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=["symbol", "datetime"], keep="last")

    # --- 质量门 ---
    daily = pd.read_parquet(config.CACHE_DIR / "daily_bars.parquet")
    daily_close = daily.pivot(index="date", columns="symbol", values="close")
    daily_close.index = pd.to_datetime(daily_close.index)
    last = bars[bars["datetime"].dt.strftime("%H:%M") == "15:00"].copy()
    last["day"] = last["datetime"].dt.normalize()
    chk = last[last.apply(lambda r: r["day"] in daily_close.index and
                          r["symbol"] in daily_close.columns, axis=1)]
    ref = chk.apply(lambda r: daily_close.at[r["day"], r["symbol"]], axis=1)
    bad = chk[(chk["close"] - ref).abs() / ref > 1e-5]  # 0.1bps 舍入容差
    per_day = bars.groupby([bars["symbol"], bars["datetime"].dt.date]).size()
    dist = per_day.value_counts().to_dict()
    print(f"[OK] {len(bars):,} 行 × {bars['symbol'].nunique()} codes（{time.time() - t0:.0f}s，{n_fetched} 请求）")
    print(f"  15:00 vs 日线偏差>0.1bps 行数: {len(bad)} | 每日bar数分布: {dist}")
    if bad.empty and dist.get(48, 0) == len(per_day):
        bars.to_parquet(OUT_FILE, index=False)
        PARTIAL_FILE.unlink(missing_ok=True)
        print("  质量门 PASS，已写入")
        return 0
    print("  ⚠️ 质量门未全过——未写入主文件（partial 保留）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
