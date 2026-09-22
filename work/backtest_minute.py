"""分钟共振验证回测 v4：极值时刻弹性（5~20min 窗口同时刻响应）。

用法：
    conda run -n resonance python work/backtest_minute.py

v4 协议（docs/minute-resonance-design.md v4）：
- 窗口 2025-09-22 ~ 2026-09-18（分钟数据留存窗），相位起点 = 窗内前 5 个交易日；
- universe 限分钟覆盖：概念 = mrets 列（去领导），领导候选 = 13 宽基 ∩ 有分钟数据
  （11 个，微盘股/中证2000 排除——"不考虑无 min"）；
- 变体：日线窗 w ∈ {2,3,4,5} × {daily-only, 弹性 N=1（当日）, 弹性 N=3（3日中位）}；
  两变体同 universe 同领导池同管线（daily-only = 空弹性表，走同一代码路径）；
- 预注册判定：成功 ≥4/5 相位且中位提升>0；无效 |中位差|≤2pp 或方向不一致；
  反效 中位下降>2pp；多重比较标注（4w × 2N）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
from scipy import stats as sps  # noqa: E402

from resonance import config  # noqa: E402
from resonance.backtest import RotationBacktester, perf_stats  # noqa: E402
from resonance.minute import (  # noqa: E402
    elasticity_table,
    make_elasticity_rank_fn,
    minute_returns,
)

OUT_DIR = config.OUTPUTS_DIR / "minute_resonance"
POOL_WINDOWS = (2, 3, 4, 5)
AGG_DAYS = (1, 3)
WINDOW_START = "2025-09-22"
JUDGE = {"success": "≥4/5 相位 分钟≥日线 且中位提升>0",
         "neutral": "|中位差|≤2pp 或方向不一致",
         "harmful": "中位下降>2pp"}


def load():
    bars = pd.read_parquet(config.CACHE_DIR / "daily_bars.parquet")
    m5 = pd.read_parquet(config.CACHE_DIR / "minute5_bars.parquet")
    catalog = pd.read_csv(config.DATA_DIR / "concept_catalog.csv")
    close = bars.pivot(index="date", columns="symbol", values="close").sort_index()
    close.index = pd.to_datetime(close.index)
    mrets = minute_returns(m5)
    covered = set(mrets.columns)
    leaders = [c for c in config.BROAD_INDEX_POOL if c in covered]
    concepts = [c for c in catalog["code"] if c in covered]
    return close, mrets, concepts, leaders


def wilcoxon_excess(nav, bench, period=5):
    df = pd.concat([nav.pct_change(), bench.pct_change()], axis=1, keys=["s", "b"]).dropna()
    n_blk = len(df) // period
    exc = [float(df.iloc[i * period:(i + 1) * period]["s"].sum()
                 - df.iloc[i * period:(i + 1) * period]["b"].sum()) for i in range(n_blk)]
    if len(exc) < 5 or all(e == 0 for e in exc):
        return float("nan")
    return float(sps.wilcoxon(exc).pvalue)


def main() -> int:
    close, mrets, concepts, leaders = load()
    bench = close["883957.TI"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"universe：概念 {len(concepts)}（分钟覆盖）| 领导 {len(leaders)}/13"
          f"（排除 {[c for c in config.BROAD_INDEX_POOL if c not in leaders]}）")

    cal = close.loc[WINDOW_START:]
    phase_starts = [str(d.date()) for d in cal.index[:5]]
    print(f"窗口 {cal.index[0].date()} → {cal.index[-1].date()}（{len(cal)} 交易日），"
          f"相位 {phase_starts}")

    tables_both = elasticity_table(mrets, leaders)                      # v4：涨+跌双窗
    tables_up = elasticity_table(mrets, leaders, use_down=False)        # v4b：只涨窗
    n_days = sum(len(t) for t in tables_up.values())
    print(f"弹性表：{len(tables_up)} 领导 × 平均 {n_days // max(len(tables_up), 1)} 有效日"
          f"（双窗/只涨窗两套）")

    rows, curves = [], {}
    trade_close = close[concepts].loc[WINDOW_START:]
    returns = close.pct_change()
    for w in POOL_WINDOWS:
        dcorr = {l: returns[concepts].rolling(w).corr(returns[l]) for l in leaders}
        for i, start in enumerate(phase_starts):
            bench_nav = bench.loc[start:]
            bench_nav = bench_nav / bench_nav.iloc[0]
            for kind in ("daily", "N1", "N3"):
                agg = {"daily": None, "N1": 1, "N3": 3}[kind]
                for mode, tabs in (("both", tables_both), ("up", tables_up)):
                    if kind == "daily" and mode == "up":
                        continue  # 基线共用
                    fn = make_elasticity_rank_fn(
                        close, concepts, tabs if agg else {},
                        leaders, window=w, exec_lag=1, agg_days=agg or 1, daily_corr=dcorr)
                    out = RotationBacktester(trade_close.loc[start:], fn,
                                             rebalance_days=5).run()
                    st = perf_stats(out["nav_curve"], bench_nav)
                    rows.append({"w": w, "phase": f"P{i+1}",
                                 "kind": kind if kind == "daily" else f"{kind}_{mode}",
                                 "total": st["total_return"], "excess": st["excess_wealth"],
                                 "sharpe": st["sharpe"], "maxdd": st["max_drawdown"],
                                 "p": wilcoxon_excess(out["nav_curve"], bench_nav)})
                    tag = kind if kind == "daily" else f"{kind}_{mode}"
                    curves[f"w{w}_{tag}_P{i+1}"] = out["nav_curve"]
                    if kind != "daily":
                        s = fn.stats
                        rows[-1]["minute_active%"] = 100 * s["minute_active"] / max(s["calls"], 1)
                        rows[-1]["pick_changed%"] = 100 * s["pick_changed"] / max(s["minute_active"], 1)
        print(f"  w={w} 完成", flush=True)

    tbl = pd.DataFrame(rows)
    tbl.to_csv(OUT_DIR / "phase_results_v4.csv", index=False)
    pd.concat(curves, axis=1).to_csv(OUT_DIR / "nav_curves_v4.csv")

    print("\n===== v4/v4b：总收益（5 相位中位数）=====")
    for w in POOL_WINDOWS:
        base = tbl[(tbl["w"] == w) & (tbl["kind"] == "daily")]["total"]
        line = f"w={w}: daily {base.median():+7.2%}"
        for kind in ("N1_both", "N3_both", "N1_up", "N3_up"):
            m = tbl[(tbl["w"] == w) & (tbl["kind"] == kind)]["total"]
            if not len(m):
                continue
            win = int((m.values >= base.values).sum())
            line += (f" | {kind.replace('_', '·')} {m.median():+7.2%}"
                     f"（Δ {(m.median()-base.median())*100:+6.1f}pp，{win}/5）")
        print(line)

    print("\n===== 预注册判定（多重比较警示：4w × 4 变体）=====")
    for w in POOL_WINDOWS:
        for kind in ("N1_both", "N3_both", "N1_up", "N3_up"):
            base = tbl[(tbl["w"] == w) & (tbl["kind"] == "daily")]["total"]
            m = tbl[(tbl["w"] == w) & (tbl["kind"] == kind)]["total"]
            if not len(m):
                continue
            win = int((m.values >= base.values).sum())
            diff = (m.median() - base.median()) * 100
            v = "success" if (win >= 4 and diff > 0) else ("harmful" if diff <= -2 else "neutral")
            print(f"  w={w} {kind}: {v.upper()}（中位差 {diff:+.1f}pp，{win}/5 相位）")
    print(f"\n[OK] 结果写入 {OUT_DIR}/phase_results_v4.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
