"""三种替代机制预注册矩阵；仅用 conda resonance，只读主仓历史数据。"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from resonance import config
from resonance.backtest import perf_stats
from resonance.v3 import MinuteBarProvider, V3Backtester, V3Params, yearly_returns
from research.resonance_alternatives_core import (
    RankingBank, ConsensusBank, occupancy_stats, raw_momentum,
    adjusted_momentum, best_schedule, capped_schedule,
)

ANCHORS = ["399001.SZ", "399303.SZ", "000688.SH", "000852.SH", "399006.SZ"]
BASE_POOLS = {"trio": ANCHORS[:3], "single": ANCHORS[:1],
              "dual": [ANCHORS[0], ANCHORS[3]], "momentum5": ANCHORS}
END = pd.Timestamp("2026-09-18")
WINDOWS = {"main": ("2022-06-01", END),
           "early": ("2022-06-01", pd.Timestamp("2024-12-31")),
           "recent": ("2025-01-01", END)}
CENTERS = ["risk20", "consensus3", "cap60"]


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_data(data_root):
    files = {"daily": data_root / "cache/daily_bars.parquet",
             "minute": data_root / "cache/minute5_bars.parquet",
             "catalog": data_root / "concept_catalog.csv"}
    audit = {"input_sha256": {key: digest(path) for key, path in files.items()},
             "input_paths": {key: str(path) for key, path in files.items()},
             "cutoff": str(END.date())}
    bars = pd.read_parquet(files["daily"])
    bars["date"] = pd.to_datetime(bars["date"])
    bars = bars[bars.date <= END].copy()
    assert not bars.duplicated(["symbol", "date"]).any()
    calendar = pd.DatetimeIndex(sorted(bars.loc[bars.symbol.eq(ANCHORS[0]), "date"]))
    removed = sorted(set(bars.date) - set(calendar))
    audit["removed_noncalendar_dates"] = [str(d.date()) for d in removed]
    audit["removed_noncalendar_rows"] = int((~bars.date.isin(calendar)).sum())
    bars = bars[bars.date.isin(calendar)]
    catalog = pd.read_csv(files["catalog"])
    available = set(bars.symbol)
    concepts = [c for c in catalog["code"] if c in available]
    columns = list(dict.fromkeys(ANCHORS + ["883957.TI"] + concepts))
    close = bars.pivot(index="date", columns="symbol", values="close").reindex(
        index=calendar, columns=columns)
    open_ = bars.pivot(index="date", columns="symbol", values="open").reindex(
        index=calendar, columns=columns)
    assert close[ANCHORS + ["883957.TI"]].notna().all().all()
    config.assert_no_retired(ANCHORS)
    minute = pd.read_parquet(files["minute"])
    minute["datetime"] = pd.to_datetime(minute["datetime"])
    minute = minute[minute.datetime < END + pd.Timedelta(days=1)]
    minute = minute[minute.symbol.isin(columns)]
    assert not minute.duplicated(["symbol", "datetime"]).any()
    wide = minute.pivot(index="datetime", columns="symbol", values="close").sort_index()
    provider = MinuteBarProvider(wide)
    audit.update({"daily_rows": len(bars), "daily_first": str(calendar[0].date()),
                  "daily_last": str(calendar[-1].date()), "calendar_days": len(calendar),
                  "concepts": len(concepts), "minute_rows": len(minute),
                  "minute_first": str(wide.index[0]), "minute_last": str(wide.index[-1]),
                  "anchor_minute_rows": minute[minute.symbol.isin(ANCHORS)].groupby(
                      "symbol").size().to_dict()})
    return close, open_, concepts, provider, audit


def pool_schedule(engine):
    sig = engine.sig
    return pd.Series([engine.broad[j] if ok else None
                      for j, ok in zip(sig.leader_idx, sig.has_leader)],
                     index=engine.close.index)


def trade_hash(trades):
    if trades.empty:
        return "empty"
    cols = [c for c in ["date", "type", "from", "to", "price"] if c in trades]
    return hashlib.sha256(trades[cols].to_csv(index=False).encode()).hexdigest()


def assert_executable(result, close, open_):
    """不得把缺失持仓估值或成交价静默记为零收益。"""
    holding = result["holdings"]["holding"].dropna()
    for date, code in holding.items():
        assert np.isfinite(close.at[date, code]) and close.at[date, code] > 0
    for row in result["trades"].to_dict("records"):
        for side in ("from", "to"):
            code = row.get(side)
            if pd.notna(code):
                assert np.isfinite(open_.at[row["date"], code]) and open_.at[row["date"], code] > 0


def normalize_counts(results):
    """计数未出现代表零事件；所有相位都必须进入计数中位数。"""
    columns = [c for c in results if c.startswith(("minute_", "audit_", "selection_"))]
    return results.fillna({c: 0 for c in columns})


def paired(results):
    rows = []
    for (mode, window, cost), subset in results.groupby(["mode", "window", "cost"]):
        for tag in subset.variant.unique():
            for baseline in ["dual", "momentum5"]:
                if tag == baseline or baseline not in set(subset.variant):
                    continue
                left = subset[subset.variant == tag].set_index("phase")
                right = subset[subset.variant == baseline].set_index("phase")
                assert len(left) == len(right) == 5
                delta = left.total - right.total
                rows.append({"mode": mode, "window": window, "cost": cost,
                             "candidate": tag, "baseline": baseline,
                             "delta": delta.median(), "wins": int((delta > 0).sum()),
                             "dd_delta": (left.dd - right.dd).median(),
                             "occupancy_median_delta": (left.max_rolling_occupancy.median()
                                                         - right.max_rolling_occupancy.median())
                             if left.max_rolling_occupancy.notna().any() else np.nan})
    return pd.DataFrame(rows)


def converge(trades):
    sequences = []
    for frame in trades:
        if frame.empty:
            sequences.append({})
        else:
            frame = frame.fillna("")
            sequences.append({row["date"]: tuple(row.get(k) for k in ["type", "from", "to", "price"])
                              for row in frame.to_dict("records")})
    dates = sorted(set().union(*(set(s) for s in sequences)))
    different = [date for date in dates if len({s.get(date) for s in sequences}) > 1]
    return str(max(different).date()) if different else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("/home/zxh/projects/6.resonance/data"))
    parser.add_argument("--out", type=Path, default=Path("outputs/resonance_alternatives"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    close, open_, concepts, provider, audit = load_data(args.data_root)
    rows, years, convergence, weight_tables, selection_tables = [], [], [], [], []
    audit["equivalence"] = []
    raw = raw_momentum(close[ANCHORS])
    for mode in ["production_shape", "daily_only"]:
        params = V3Params(topk=3, daily_top=5 if mode == "production_shape" else 0,
                          hl_source="leader", cost_bp=10, exec_price="open")
        engines = {c: V3Backtester(close, concepts, [c], params=params,
                                   minute_bars_provider=provider, open_all=open_) for c in ANCHORS}
        bank = RankingBank(engines)
        bases = {tag: V3Backtester(close, concepts, pool, params=params,
                                   minute_bars_provider=provider, open_all=open_)
                 for tag, pool in BASE_POOLS.items()}
        schedules = {tag: pool_schedule(bt) for tag, bt in bases.items()}
        for tag, engine in bases.items():
            original = engine.run("2022-06-01", END)
            replay = bank.run(schedules[tag], "2022-06-01", END, 10)
            pd.testing.assert_series_equal(original["nav_curve"], replay["nav_curve"])
            pd.testing.assert_frame_equal(original["trades"], replay["trades"])
            audit["equivalence"].append(f"{mode}:{tag}")
        pd.testing.assert_series_equal(schedules["momentum5"].fillna("").astype(str),
                                       best_schedule(raw).fillna("").astype(str))
        print(f"{mode}: original four baselines match", flush=True)
        for window in ([10, 20, 40, 60] if mode == "production_shape" else [20]):
            schedules[f"risk{window}"] = best_schedule(adjusted_momentum(close[ANCHORS], window))
        for cap in ([.4, .6, .8] if mode == "production_shape" else [.6]):
            selection = capped_schedule(raw, cap)
            assert all(selection.eq(c).rolling(60, min_periods=1).sum().max() <= cap * 60 + 1e-10
                       for c in ANCHORS)
            schedules[f"cap{int(100*cap)}"] = selection
        daily_ranks = {c: [engine.sig.ranking(i) for i in range(len(close))]
                       for c, engine in engines.items()}
        consensus = {f"consensus{k}": ConsensusBank(engines, bases["momentum5"], k, daily_ranks)
                     for k in ([2, 3, 4, 5] if mode == "production_shape" else [3])}
        # k=1 must be exactly the original five-anchor rule, including its own gate.
        one = ConsensusBank(engines, bases["momentum5"], 1, daily_ranks)
        original = bases["momentum5"].run("2022-06-01", END)
        replay = one.run("2022-06-01", END, 10)
        pd.testing.assert_series_equal(original["nav_curve"], replay["nav_curve"])
        pd.testing.assert_frame_equal(original["trades"], replay["trades"])
        audit["equivalence"].append(f"{mode}:consensus1")
        for tag, instance in consensus.items():
            weights = instance.weights.copy()
            weights["variant"], weights["mode"] = tag, mode
            weight_tables.append(weights.rename_axis("date").reset_index())
        for tag, selection in schedules.items():
            selection_tables.append(pd.DataFrame({"date": selection.index, "anchor": selection,
                                                   "variant": tag, "mode": mode}))
        tags = list(schedules) + list(consensus) if mode == "production_shape" else ["dual"] + CENTERS
        main_trades = {tag: [] for tag in tags}
        for cost in ([10, 0, 30] if mode == "production_shape" else [10]):
            for window, (begin, end) in WINDOWS.items():
                for phase, start in enumerate(close.index[close.index >= begin][:5], start=1):
                    for tag in tags:
                        if tag in consensus:
                            result = consensus[tag].run(start, end, cost)
                            concentration = consensus[tag].contribution_stats(start, end)
                        else:
                            selection = schedules[tag]
                            result = bank.run(selection, start, end, cost)
                            concentration = occupancy_stats(selection.loc[start:end])
                        assert_executable(result, close, open_)
                        curve = result["nav_curve"]
                        metrics = perf_stats(curve)
                        row = {"mode": mode, "window": window, "phase": phase, "cost": cost,
                               "variant": tag, "start": str(start.date()), "end": str(end.date()),
                               "total": metrics["total_return"], "dd": metrics["max_drawdown"],
                               "sharpe": metrics["sharpe"], "trade_hash": trade_hash(result["trades"]),
                               "cash_days": int(result["holdings"].holding.isna().sum()),
                               **concentration}
                        row.update({k: v for k, v in result["stats"].items() if isinstance(v, (int, float))})
                        rows.append(row)
                        for year, value in yearly_returns(curve).items():
                            years.append({"mode": mode, "window": window, "cost": cost,
                                          "phase": phase, "variant": tag, "year": year, "total": value})
                        if cost == 10:
                            ident = f"{mode}_{window}_{tag}_phase{phase}"
                            curve.to_csv(args.out / f"nav_{ident}.csv")
                            result["trades"].to_csv(args.out / f"trades_{ident}.csv", index=False)
                            if window == "main":
                                main_trades[tag].append(result["trades"])
                print(f"{len(rows)}/735 {mode} cost={cost} {window}, {time.monotonic()-started:.0f}s", flush=True)
            normalize_counts(pd.DataFrame(rows)).to_csv(args.out / "results.csv", index=False)
        for tag, frames in main_trades.items():
            convergence.append({"mode": mode, "variant": tag,
                                "last_trade_disagreement": converge(frames)})
    results = normalize_counts(pd.DataFrame(rows))
    assert len(results) == 735
    results.to_csv(args.out / "results.csv", index=False)
    results.groupby(["mode", "window", "cost", "variant"]).median(numeric_only=True).to_csv(
        args.out / "medians.csv")
    pairs = paired(results)
    pairs.to_csv(args.out / "paired_tests.csv", index=False)
    pd.DataFrame(years).to_csv(args.out / "years.csv", index=False)
    pd.DataFrame(convergence).to_csv(args.out / "phase_convergence.csv", index=False)
    pd.concat(weight_tables, ignore_index=True).to_csv(args.out / "contributions.csv", index=False)
    pd.concat(selection_tables, ignore_index=True).to_csv(args.out / "selections.csv", index=False)
    audit["actual_runs"] = len(rows)
    audit["elapsed_seconds"] = time.monotonic() - started
    audit["input_unchanged"] = {k: digest(Path(p)) == audit["input_sha256"][k]
                                 for k, p in audit["input_paths"].items()}
    assert all(audit["input_unchanged"].values())
    (args.out / "data_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2))
    print(pairs[(pairs.candidate.isin(CENTERS)) & (pairs.baseline == "dual")].to_string(index=False))


if __name__ == "__main__":
    main()
