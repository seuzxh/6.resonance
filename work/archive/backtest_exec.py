"""分钟级执行层验证回测：A 盘中交易时点 × B 盘中追踪止损，5 相位稳健性协议。

用法：
    conda run -n resonance python work/backtest_exec.py

窗口：2025-10-30 → 2026-09-18（5min 数据留存约束）；5 个相邻相位起点；
同窗同口径（exec_lag=1、热启动、rebal=5、topk=5、20 日信号窗，信号层不变）。
预注册判据见 docs/minute-exec-design.md §四。对照 = 本脚本内重算的日线-only 基线。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats as sps  # noqa: E402

from resonance import config  # noqa: E402
from resonance.backtest import RotationBacktester, perf_stats  # noqa: E402
from resonance.dynamic import make_dynamic_rank_fn  # noqa: E402
from resonance.exec_minute import (  # noqa: E402
    TRADE_TIMES,
    MinutePrices,
    build_minute_wide,
    rerun_trade_times,
    run_with_intraday_stop,
)

OUT_DIR = config.OUTPUTS_DIR / "minute_exec"
PHASE_STARTS = ("2025-10-30", "2025-10-31", "2025-11-03", "2025-11-04", "2025-11-05")
STOP_GRID = (0.04, 0.06, 0.08, 0.10, 0.12)
JUDGE = {
    "A_success": "中位 Δ总收益 ≥ +2pp 且 ≥4/5 相位 Δ≥0 且 中位 Δ回撤 ≥ −1pp",
    "B_success": "中位 Δ回撤 改善 ≥ 3pp 且 中位 Δ总收益 ≥ −2pp 且 ≥4/5 相位回撤改善",
}


def load_all():
    bars = pd.read_parquet(config.CACHE_DIR / "daily_bars.parquet")
    m5 = pd.read_parquet(config.CACHE_DIR / "minute5_bars.parquet")
    catalog = pd.read_csv(config.DATA_DIR / "concept_catalog.csv")
    concepts = [c for c in catalog["code"] if c in set(bars["symbol"])]
    close = bars.pivot(index="date", columns="symbol", values="close").sort_index()
    close.index = pd.to_datetime(close.index)
    open_ = bars.pivot(index="date", columns="symbol", values="open").sort_index()
    open_.index = pd.to_datetime(open_.index)
    return close, open_, build_minute_wide(m5), concepts


def cached_rank_fn(close, concepts):
    """日线信号层（与基线完全一致）+ 按信号日缓存（A/B 网格共享）。"""
    inner = make_dynamic_rank_fn(close, concepts, exec_lag=1)
    cache: dict = {}

    def rank_fn(asof):
        key = str(pd.Timestamp(asof).date())
        if key not in cache:
            cache[key] = inner(asof)
        return cache[key]

    rank_fn.leader_history = inner.leader_history
    return rank_fn


def decomposition(close, open_, concepts, start):
    """机制证据：窗口内概念池隔夜/日内收益分解（日线开盘-收盘）。"""
    sub_c = close[concepts].loc[start:]
    sub_o = open_[concepts].loc[start:]
    intraday = (sub_c / sub_o - 1).replace([np.inf, -np.inf], np.nan)
    prev_c = close[concepts].shift(1).loc[start:]
    overnight = (sub_o / prev_c - 1).replace([np.inf, -np.inf], np.nan)
    rows = []
    for name, df in (("overnight", overnight), ("intraday", intraday)):
        daily_mean = df.mean(axis=1).dropna()
        rows.append({"segment": name, "mean_daily": daily_mean.mean(),
                     "t_stat": sps.ttest_1samp(daily_mean, 0).statistic,
                     "p_value": sps.ttest_1samp(daily_mean, 0).pvalue,
                     "n_days": len(daily_mean)})
    return pd.DataFrame(rows)


def stats_row(nav, bench_nav, **extra):
    st = perf_stats(nav, bench_nav)
    st.update(extra)
    return st


def main() -> int:
    close, open_, minute_wide, concepts = load_all()
    bench = close["883957.TI"]
    prices = MinutePrices(close, open_, minute_wide)
    rank_fn = cached_rank_fn(close, concepts)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # ---------- 机制证据 ----------
    dec = decomposition(close, open_, concepts, PHASE_STARTS[0])
    print("===== 机制证据：概念池隔夜/日内收益分解（2025-10-30 起，等权日均值）=====")
    print(dec.to_string(index=False, formatters={
        "mean_daily": "{:.4%}".format, "t_stat": "{:+.2f}".format,
        "p_value": "{:.4g}".format}))
    dec.to_csv(OUT_DIR / "decomposition.csv", index=False)

    rows_a, rows_b, curves = [], [], {"benchmark": bench.loc[PHASE_STARTS[0]:]}
    for i, start in enumerate(PHASE_STARTS):
        phase = f"P{i+1}({start})"
        bench_nav = bench.loc[start:]
        bench_nav = bench_nav / bench_nav.iloc[0]
        curves[f"benchmark_P{i+1}"] = bench_nav

        # --- 基线（日线-only，本窗重算）---
        bt = RotationBacktester(close[concepts].loc[start:], rank_fn, rebalance_days=5)
        base_out = bt.run()
        base_st = stats_row(base_out["nav_curve"], bench_nav)
        rows_a.append({"phase": phase, "variant": "baseline(15:00)", "total": base_st["total_return"],
                       "maxdd": base_st["max_drawdown"], "sharpe": base_st["sharpe"],
                       "excess": base_st.get("excess_wealth"), "n_sw": len(base_out["switches"]),
                       "degraded": 0})
        curves[f"base_P{i+1}"] = base_out["nav_curve"]

        # --- 候选 A：交易时点网格 ---
        for t in TRADE_TIMES:
            prices.degraded = 0
            rr = rerun_trade_times(base_out, prices, t)
            st = stats_row(rr["nav_curve"], bench_nav)
            lrs = np.array(rr["log_ratios"]) if rr["log_ratios"] else np.array([])
            rows_a.append({"phase": phase, "variant": f"A:{t}", "total": st["total_return"],
                           "maxdd": st["max_drawdown"], "sharpe": st["sharpe"],
                           "excess": st.get("excess_wealth"), "n_sw": len(base_out["switches"]),
                           "degraded": prices.degraded,
                           "lr_mean": lrs.mean() if len(lrs) else np.nan,
                           "lr_pos%": 100 * (lrs > 0).mean() if len(lrs) else np.nan})
            if t in ("open", "09:35", "14:30"):
                curves[f"A{t}_P{i+1}"] = rr["nav_curve"]

        # --- 候选 B：止损网格（minute / close 对照）---
        for x in STOP_GRID:
            for mode in ("minute", "close"):
                prices.degraded = 0
                out = run_with_intraday_stop(close[concepts].loc[start:], prices, rank_fn,
                                             rebalance_days=5, topk=5, stop_pct=x, mode=mode)
                st = stats_row(out["nav_curve"], bench_nav)
                rows_b.append({"phase": phase, "variant": f"B{x:.0%}/{mode}", "x": x, "mode": mode,
                               "total": st["total_return"], "maxdd": st["max_drawdown"],
                               "sharpe": st["sharpe"], "excess": st.get("excess_wealth"),
                               "stops": out["stats"]["stops"],
                               "flat_days": out["stats"]["flat_days"],
                               "degraded_days": out["stats"]["degraded_days"],
                               "degraded": prices.degraded})
                if mode == "minute" and x in (0.06, 0.08):
                    curves[f"B{x:.0%}m_P{i+1}"] = out["nav_curve"]

    tbl_a = pd.DataFrame(rows_a)
    tbl_b = pd.DataFrame(rows_b)

    # ---------- A 判定 ----------
    print("\n===== 候选 A：交易时点网格（总收益 / 最大回撤，5 相位）=====")
    piv_t = tbl_a.pivot(index="variant", columns="phase", values="total")
    piv_d = tbl_a.pivot(index="variant", columns="phase", values="maxdd")
    for piv, name in ((piv_t, "total"), (piv_d, "maxdd")):
        piv["median"] = piv.median(axis=1)
        print(f"\n--- {name} ---")
        cols = [c for c in piv.columns]
        print(piv.loc[list(dict.fromkeys(tbl_a["variant"]))].to_string(
            formatters={c: "{:+.2%}".format for c in cols[:5]} | {"median": "{:+.2%}".format}))
    base_med_t = piv_t.loc["baseline(15:00)", "median"]
    base_med_d = piv_d.loc["baseline(15:00)", "median"]
    verdicts_a = {}
    for v in piv_t.index:
        if v == "baseline(15:00)":
            continue
        d_t = (piv_t.loc[v] - piv_t.loc["baseline(15:00)"]).drop("median")
        d_d = (piv_d.loc[v] - piv_d.loc["baseline(15:00)"]).drop("median")
        ok = d_t.median() >= 0.02 and (d_t >= 0).sum() >= 4 and d_d.median() >= -0.01
        verdicts_a[v] = "success" if ok else ("harmful" if d_t.median() <= -0.02 else "neutral")
        print(f"A[{v}] 中位Δ收益 {d_t.median()*100:+.1f}pp 中位Δ回撤 {d_d.median()*100:+.1f}pp "
              f"改善相位 {(d_t >= 0).sum()}/5 → {verdicts_a[v].upper()}")
    lr_all = tbl_a[tbl_a["variant"].str.startswith("A:")].groupby("variant")["lr_mean"].first()
    print("\n逐调仓机制（日内对数收益差 lr 均值，>0 = 早成交占优）：")
    print(lr_all.to_string(float_format="{:+.4%}".format))

    # ---------- B 判定 ----------
    print("\n===== 候选 B：追踪止损网格（总收益 / 最大回撤，5 相位中位数）=====")
    for mode in ("minute", "close"):
        sub = tbl_b[tbl_b["mode"] == mode]
        med_t = sub.pivot(index="x", columns="phase", values="total")
        med_d = sub.pivot(index="x", columns="phase", values="maxdd")
        med_t["median"], med_d["median"] = med_t.median(axis=1), med_d.median(axis=1)
        print(f"\n--- mode={mode} 总收益 ---")
        print(med_t.to_string(float_format="{:+.2%}".format))
        print(f"--- mode={mode} 最大回撤 ---")
        print(med_d.to_string(float_format="{:+.2%}".format))
    base_row = tbl_b.iloc[0]  # 对齐基线：用 tbl_a 的基线行
    base_a = tbl_a[tbl_a["variant"] == "baseline(15:00)"].set_index("phase")
    verdicts_b = {}
    for (x, mode), sub in tbl_b.groupby(["x", "mode"]):
        sub = sub.set_index("phase")
        d_d = (sub["maxdd"] - base_a["maxdd"]).dropna()
        d_t = (sub["total"] - base_a["total"]).dropna()
        stops = int(sub["stops"].mean())
        ok = d_d.median() >= 0.03 and d_t.median() >= -0.02 and (d_d > 0).sum() >= 4
        verdicts_b[(x, mode)] = "success" if ok else "neutral"
        print(f"B[{x:.0%}/{mode}] 中位Δ收益 {d_t.median()*100:+.1f}pp 中位Δ回撤 {d_d.median()*100:+.1f}pp "
              f"回撤改善相位 {(d_d > 0).sum()}/5 均止损次数 {stops} → {verdicts_b[(x, mode)].upper()}")

    tbl_a.to_csv(OUT_DIR / "phase_results_A.csv", index=False)
    tbl_b.to_csv(OUT_DIR / "phase_results_B.csv", index=False)
    pd.concat(curves, axis=1).to_csv(OUT_DIR / "nav_curves.csv")
    print(f"\n[OK] 结果写入 {OUT_DIR}/")
    print(f"判定标准（预注册） A: {JUDGE['A_success']}")
    print(f"                    B: {JUDGE['B_success']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
