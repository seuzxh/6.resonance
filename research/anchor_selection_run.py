"""执行动态选锚预注册矩阵；conda resonance 环境，只读历史数据。

设计：docs/research/anchor-selection.md。代码变量使用英文，产物字段字典由报告解释。
"""
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
from research.anchor_selection_core import (
    RankingBank, occupancy_stats, quality_scores, select_schedule,
)

ANCHORS = ["399001.SZ", "399303.SZ", "000688.SH", "000852.SH", "399006.SZ"]
NAMES = dict(zip(ANCHORS, ["深证成指", "国证2000", "科创50", "中证1000", "创业板指"]))
BASE_POOLS = {"trio": ANCHORS[:3], "single": ANCHORS[:1],
              "dual": [ANCHORS[0], ANCHORS[3]], "momentum5": ANCHORS}
LABELS = {"trio": "现行三锚", "single": "深证单锚", "dual": "深证与中证1000双锚",
          "momentum5": "五指数动量动选", "quality60_p0": "60日质量选锚",
          "quality60_p0.1": "60日质量选锚＋占用惩罚"}
END = pd.Timestamp("2026-09-18")
WINDOWS = {"main": ("2022-06-01", END),
           "early": ("2022-06-01", pd.Timestamp("2024-12-31")),
           "recent": ("2025-01-01", END)}


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


def paired_summary(results, window, cost, candidate, baseline):
    subset = results[(results.window == window) & (results.cost == cost)]
    left = subset[subset.variant == candidate].set_index("phase")
    right = subset[subset.variant == baseline].set_index("phase")
    assert len(left) == len(right) == 5
    diff = left.total - right.total
    return {"window": window, "cost": cost, "candidate": candidate, "baseline": baseline,
            "delta": float(diff.median()), "wins": int((diff > 0).sum()),
            "dd_delta": float((left.dd - right.dd).median()),
            "occupancy_delta": float((left.max_rolling_occupancy - right.max_rolling_occupancy).median()),
            "occupancy_median_delta": float(left.max_rolling_occupancy.median() - right.max_rolling_occupancy.median())}


def normalize_counts(results):
    """计数未出现代表零事件；所有相位都必须进入计数中位数。"""
    columns = [c for c in results if c.startswith(("minute_", "audit_", "selection_"))]
    return results.fillna({c: 0 for c in columns})


def daily_diagnostic(close, open_, concepts, outdir):
    """主矩阵后按补充预注册执行固定日线对照，不进行新参数搜索。"""
    params = V3Params(topk=3, daily_top=0, hl_source="leader", cost_bp=10, exec_price="open")
    engines = {code: V3Backtester(close, concepts, [code], params=params, open_all=open_)
               for code in ANCHORS}
    bank = RankingBank(engines)
    dual = V3Backtester(close, concepts, BASE_POOLS["dual"], params=params, open_all=open_)
    fixed = pool_schedule(dual)
    original = dual.run("2022-06-01", END)
    replay = bank.run(fixed, "2022-06-01", END, 10)
    pd.testing.assert_series_equal(original["nav_curve"], replay["nav_curve"])
    pd.testing.assert_frame_equal(original["trades"], replay["trades"])
    rows = []
    for phase, warm_start in enumerate(close.index[close.index >= "2022-01-04"][:5]):
        nav, active = {}, {}
        for code in ANCHORS:
            result = bank.run(pd.Series(code, index=close.index), warm_start, END, 10)
            assert_executable(result, close, open_)
            nav[code] = result["nav_curve"].reindex(close.index)
            active[code] = result["holdings"].holding.notna().reindex(close.index, fill_value=False)
        nav, active = pd.DataFrame(nav), pd.DataFrame(active)
        scores = quality_scores(nav, active, 60)
        for window, (begin, end) in WINDOWS.items():
            start = close.index[close.index >= begin][phase]
            variants = {"dual": fixed}
            for penalty in [0.0, 0.1]:
                variants[f"quality60_p{penalty:g}"] = select_schedule(
                    nav, active, start, penalty=penalty, scores=scores)["anchor"]
            for tag, schedule in variants.items():
                result = bank.run(schedule, start, end, 10)
                assert_executable(result, close, open_)
                stats = perf_stats(result["nav_curve"])
                rows.append({"window": window, "cost": 10, "phase": phase + 1,
                             "variant": tag, "total": stats["total_return"],
                             "dd": stats["max_drawdown"], "sharpe": stats["sharpe"],
                             **occupancy_stats(schedule.loc[start:end])})
    results = pd.DataFrame(rows)
    results.to_csv(outdir / "daily_diagnostic.csv", index=False)
    pairs = [paired_summary(results, window, 10, candidate, baseline)
             for window in WINDOWS for candidate, baseline in
             [("quality60_p0", "dual"), ("quality60_p0.1", "quality60_p0")]]
    pd.DataFrame(pairs).to_csv(outdir / "daily_diagnostic_paired.csv", index=False)
    print(results.groupby(["window", "variant"])[["total", "dd"]].median().to_string())
    print(pd.DataFrame(pairs).to_string(index=False))


def plot_results(outdir, curves, selections):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.ticker import FuncFormatter
    for font in font_manager.fontManager.ttflist:
        if "CJK" in font.name or "WenQuanYi" in font.name:
            plt.rcParams["font.family"] = font.name
            break
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(3, 1, figsize=(14, 11), sharex=True,
                              gridspec_kw={"height_ratios": [3, 1.2, 1]})
    colors = {"trio": "#6f7e8c", "single": "#218380", "dual": "#e59b25",
              "momentum5": "#b8b8b8", "quality60_p0": "#3763c7", "quality60_p0.1": "#b93b60"}
    for tag, nav in curves.items():
        if tag not in LABELS:
            continue
        axes[0].plot(nav.index, nav, label=LABELS[tag], color=colors[tag], linewidth=1.5)
        if tag in ["dual", "quality60_p0", "quality60_p0.1"]:
            axes[1].plot(nav.index, 100 * (nav / nav.cummax() - 1), color=colors[tag],
                         label=LABELS[tag], linewidth=1)
    axes[0].set_yscale("log")
    axes[0].yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
    axes[0].yaxis.set_minor_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
    axes[0].set_ylabel("净值（对数坐标）")
    axes[1].set_ylabel("回撤（%）")
    selection = selections["quality60_p0.1"]["anchor"]
    palette = ["#218380", "#e59b25", "#8a62b5", "#3763c7", "#b93b60"]
    for code, color in zip(ANCHORS, palette):
        occupation = selection.eq(code).rolling(60, min_periods=1).sum() / 60
        axes[2].plot(selection.index, occupation, label=NAMES[code], color=color)
    axes[2].set_ylabel("惩罚方案\n60日锚占用率")
    for ax in axes:
        ax.grid(alpha=0.18)
    axes[0].legend(ncol=3, fontsize=9)
    axes[2].legend(ncol=5, fontsize=8)
    fig.suptitle("预注册动态选锚实验｜第3相位：2022-06-06至2026-09-18\n"
                 "次日开盘、单边10个基点；样本内上界，概念指数不可直接交易", fontsize=13)
    fig.text(0.5, 0.01, "成本为统一压力假设；存活概念目录有幸存者偏差；统计裁决使用5相位，非本图单相位。",
             ha="center", fontsize=9)
    fig.tight_layout(rect=(0, .035, 1, .96))
    fig.savefig(outdir / "nav_comparison.png", dpi=160)
    plt.close(fig)


def write_convergence(outdir):
    """逐笔核对主窗口最后一次相位分歧，避免完整摘要掩盖后续收敛。"""
    summary = []
    for tag in LABELS:
        sequences = []
        for phase in range(1, 6):
            trades = pd.read_csv(outdir / f"trades_main_{tag}_phase{phase}.csv",
                                 parse_dates=["date"]).fillna("")
            sequences.append({row["date"]: tuple(row[k] for k in ["type", "from", "to", "price"])
                              for row in trades.to_dict("records")})
        dates = sorted(set().union(*(set(s) for s in sequences)))
        different = [date for date in dates if len({s.get(date) for s in sequences}) > 1]
        last = max(different) if different else None
        summary.append({"variant": tag,
                        "last_trade_disagreement": str(last.date()) if last is not None else None,
                        "common_later_trade_dates": sum(last is None or date > last for date in dates)})
    pd.DataFrame(summary).to_csv(outdir / "phase_convergence.csv", index=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("/home/zxh/projects/6.resonance/data"))
    parser.add_argument("--out", type=Path, default=Path("outputs/anchor_selection"))
    parser.add_argument("--daily-diagnostic", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    close, open_, concepts, provider, audit = load_data(args.data_root)
    if args.daily_diagnostic:
        daily_diagnostic(close, open_, concepts, args.out)
        return
    (args.out / "data_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2))
    params = V3Params(topk=3, daily_top=5, hl_source="leader", cost_bp=10, exec_price="open")
    engines = {code: V3Backtester(close, concepts, [code], params=params,
                                 minute_bars_provider=provider, open_all=open_) for code in ANCHORS}
    bank = RankingBank(engines)
    bases = {tag: V3Backtester(close, concepts, pool, params=params,
                              minute_bars_provider=provider, open_all=open_)
             for tag, pool in BASE_POOLS.items()}
    schedules = {tag: pool_schedule(bt) for tag, bt in bases.items()}
    audit["cache_equivalence"] = []
    # 与原引擎比较完整窗口的净值和交易，保护缓存排名注入边界。
    for tag, engine in bases.items():
        original = engine.run("2022-06-01", END)
        replay = bank.run(schedules[tag], "2022-06-01", END, 10)
        pd.testing.assert_series_equal(original["nav_curve"], replay["nav_curve"])
        pd.testing.assert_frame_equal(original["trades"], replay["trades"])
        audit["cache_equivalence"].append(tag)
    print("data and four baseline equivalence checks passed", flush=True)
    rows, years, occupations, decisions, shadow_stats = [], [], [], [], []
    plot_curves, plot_selections = {}, {}
    warmups = close.index[close.index >= "2022-01-04"][:5]
    completed = 0
    for cost in [10, 0, 30]:
        for phase, warm_start in enumerate(warmups):
            shadow_nav, shadow_active = {}, {}
            for code in ANCHORS:
                result = bank.run(pd.Series(code, index=close.index), warm_start, END, cost)
                assert_executable(result, close, open_)
                shadow_stats.append({"anchor": code, "cost": cost, "phase": phase + 1,
                                     "start": str(warm_start.date()),
                                     **{key: value for key, value in result["stats"].items()
                                        if isinstance(value, (int, float))}})
                shadow_nav[code] = result["nav_curve"].reindex(close.index)
                shadow_active[code] = result["holdings"]["holding"].notna().reindex(close.index, fill_value=False)
            nav = pd.DataFrame(shadow_nav)
            active = pd.DataFrame(shadow_active)
            if cost == 10:
                nav.to_csv(args.out / f"shadow_nav_phase{phase + 1}.csv")
            scores = {w: quality_scores(nav, active, w) for w in [40, 60, 90]}
            for window, (begin, end) in WINDOWS.items():
                start = close.index[close.index >= begin][phase]
                variants = {tag: (schedule, None, 0, 0.0) for tag, schedule in schedules.items()}
                for w in [40, 60, 90]:
                    for penalty in [0.0, 0.05, 0.10, 0.20]:
                        tag = f"quality{w}_p{penalty:g}"
                        selection = select_schedule(nav, active, start, lookback=w,
                                                    penalty=penalty, scores=scores[w])
                        variants[tag] = (selection["anchor"], selection, w, penalty)
                for tag, (schedule, selection, w, penalty) in variants.items():
                    result = bank.run(schedule, start, end, cost)
                    assert_executable(result, close, open_)
                    curve = result["nav_curve"]
                    stats = perf_stats(curve)
                    relevant = schedule.loc[start:end]
                    row = {"window": window, "start": str(start.date()), "end": str(end.date()),
                           "phase": phase + 1, "cost": cost, "variant": tag,
                           "lookback": w, "penalty_strength": penalty,
                           "total": stats["total_return"], "dd": stats["max_drawdown"],
                           "sharpe": stats["sharpe"],
                           "trade_hash": trade_hash(result["trades"]),
                           **occupancy_stats(relevant)}
                    row.update({key: value for key, value in result["stats"].items()
                                if isinstance(value, (int, float))})
                    rows.append(row)
                    for year, value in yearly_returns(curve).items():
                        years.append({"window": window, "phase": phase + 1, "cost": cost,
                                      "variant": tag, "year": year, "total": value})
                    for code in ANCHORS:
                        occupations.append({"window": window, "phase": phase + 1, "cost": cost,
                                            "variant": tag, "anchor": code,
                                            "days": int(relevant.eq(code).sum()), "all_days": len(relevant)})
                    if cost == 10 and tag in LABELS:
                        ident = f"{window}_{tag}_phase{phase + 1}"
                        curve.to_csv(args.out / f"nav_{ident}.csv")
                        result["trades"].to_csv(args.out / f"trades_{ident}.csv", index=False)
                        if selection is not None:
                            detail = selection.loc[start:end].copy()
                            detail["window"] = window
                            detail["phase"] = phase + 1
                            detail["variant"] = tag
                            detail.index.name = "date"
                            decisions.append(detail.reset_index())
                    if cost == 10 and window == "main" and phase == 2 and tag in LABELS:
                        plot_curves[tag] = curve
                        if selection is not None:
                            plot_selections[tag] = selection.loc[start:end]
                    completed += 1
                pd.DataFrame(rows).to_csv(args.out / "results.csv", index=False)
                print(f"{completed}/720 cost={cost} phase={phase+1} window={window} elapsed={time.monotonic()-started:.0f}s", flush=True)
    results = normalize_counts(pd.DataFrame(rows))
    results.to_csv(args.out / "results.csv", index=False)
    results.groupby(["window", "cost", "variant"]).median(numeric_only=True).to_csv(args.out / "medians.csv")
    pd.DataFrame(years).to_csv(args.out / "years.csv", index=False)
    pd.DataFrame(occupations).to_csv(args.out / "occupancy.csv", index=False)
    normalize_counts(pd.DataFrame(shadow_stats)).to_csv(args.out / "shadow_stats.csv", index=False)
    pd.concat(decisions, ignore_index=True).to_csv(args.out / "decisions.csv", index=False)
    pairs = []
    for window in WINDOWS:
        for cost in [10, 0, 30]:
            for candidate, baseline in [("quality60_p0", "dual"), ("quality60_p0.1", "quality60_p0")]:
                pairs.append(paired_summary(results, window, cost, candidate, baseline))
    pd.DataFrame(pairs).to_csv(args.out / "paired_tests.csv", index=False)
    audit["completed_actual_runs"] = completed
    audit["completed_shadow_runs"] = 75
    audit["elapsed_seconds"] = time.monotonic() - started
    audit["input_unchanged"] = {key: digest(Path(path)) == audit["input_sha256"][key]
                                 for key, path in audit["input_paths"].items()}
    assert all(audit["input_unchanged"].values())
    (args.out / "data_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2))
    plot_results(args.out, plot_curves, plot_selections)
    write_convergence(args.out)
    print(pd.DataFrame(pairs).to_string(index=False), flush=True)
    print(f"complete: {args.out.resolve()}", flush=True)


if __name__ == "__main__":
    main()
