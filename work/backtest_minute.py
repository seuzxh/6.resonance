"""分钟共振精选验证回测 v3：短窗日线层（w 扫描）× 3 日 5min 分钟层，多相位协议。

用法：
    conda run -n resonance python work/backtest_minute.py

v3 协议（docs/minute-resonance-design.md）：
- 窗口 2025-01-02 → 数据截止；相位起点 = 2025 年前 5 个交易日；
- 日线层 w ∈ {2,3,4,5}（短窗扫描；w=1 无相关定义已排除）+ w=20 参照；
- 分钟层 = 近 3 日 5min（141 bar）；daily-only 对照 = 同 rank_fn 管线但不注入
  分钟表（纯日线降级路径，保证两变体日线层完全同码）；
- 分钟数据仅 2025-09-22 起：早期信号自动降级（结构性不对称，结果标注）；
- 预注册判定（每个 w 独立）：成功 ≥4/5 相位 分钟≥日线 且中位提升>0；
  无效 |中位差|≤2pp 或方向不一致；反效 中位下降>2pp。另注意 4 个 w 的
  多重比较（最多 1 个 w 偶然达标属预期噪声）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
from scipy import stats as sps  # noqa: E402

from resonance import config  # noqa: E402
from resonance.backtest import RotationBacktester, perf_stats  # noqa: E402
from resonance.minute import MINUTE_WINDOW_BARS, make_minute_rank_fn, minute_returns  # noqa: E402

OUT_DIR = config.OUTPUTS_DIR / "minute_resonance"
POOL_WINDOWS = (2, 3, 4, 5)
REF_WINDOW = 20
JUDGE = {"success": "≥4/5 相位 分钟≥日线 且 中位数提升>0",
         "neutral": "|中位差|≤2pp 或方向不一致",
         "harmful": "中位数下降 >2pp"}


def load():
    bars = pd.read_parquet(config.CACHE_DIR / "daily_bars.parquet")
    m5 = pd.read_parquet(config.CACHE_DIR / "minute5_bars.parquet")
    catalog = pd.read_csv(config.DATA_DIR / "concept_catalog.csv")
    concepts = [c for c in catalog["code"] if c in set(bars["symbol"])]
    close = bars.pivot(index="date", columns="symbol", values="close").sort_index()
    close.index = pd.to_datetime(close.index)
    return close, minute_returns(m5), concepts


def precompute_daily_corr(close, concepts, w, broad):
    """每领导指数一次性预计算全概念池 w 日滚动相关（w 扫描提速）。"""
    returns = close.pct_change()
    out = {}
    for leader in broad:
        out[leader] = returns[concepts].rolling(w).corr(returns[leader])
    return out


def wilcoxon_excess(nav, bench, period=5):
    df = pd.concat([nav.pct_change(), bench.pct_change()], axis=1, keys=["s", "b"]).dropna()
    n_blk = len(df) // period
    exc = [float(df.iloc[i * period:(i + 1) * period]["s"].sum()
                 - df.iloc[i * period:(i + 1) * period]["b"].sum()) for i in range(n_blk)]
    if len(exc) < 5 or all(e == 0 for e in exc):
        return float("nan")
    return float(sps.wilcoxon(exc).pvalue)


def main() -> int:
    close, mrets, concepts = load()
    bench = close["883957.TI"]
    broad = list(config.BROAD_INDEX_POOL)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 相位起点 = 2025 年前 5 个交易日
    cal = close.loc["2025-01-02":]
    phase_starts = [str(d.date()) for d in cal.index[:5]]
    print(f"窗口 {cal.index[0].date()} → {cal.index[-1].date()}（{len(cal)} 交易日）"
          f"，相位 {phase_starts}")

    rows, curves = [], {}
    for w in list(POOL_WINDOWS) + [REF_WINDOW]:
        dcorr = precompute_daily_corr(close, concepts, w, broad)
        for i, start in enumerate(phase_starts):
            bench_nav = bench.loc[start:]
            bench_nav = bench_nav / bench_nav.iloc[0]
            for kind, mr in (("daily", None), ("minute", mrets)):
                rank_fn = make_minute_rank_fn(
                    close, concepts, mr if mr is not None else pd.DataFrame(),
                    window=w, exec_lag=1, window_bars=MINUTE_WINDOW_BARS,
                    daily_corr=dcorr)
                bt = RotationBacktester(close[concepts].loc[start:], rank_fn,
                                        rebalance_days=5)
                out = bt.run()
                st = perf_stats(out["nav_curve"], bench_nav)
                rows.append({"w": w, "phase": f"P{i+1}", "kind": kind,
                             "total": st["total_return"], "excess": st["excess_wealth"],
                             "sharpe": st["sharpe"], "maxdd": st["max_drawdown"],
                             "p": wilcoxon_excess(out["nav_curve"], bench_nav)})
                curves[f"w{w}_{kind}_P{i+1}"] = out["nav_curve"]
                if kind == "minute":
                    s = rank_fn.stats
                    rows[-1]["minute_active%"] = 100 * s["minute_active"] / max(s["calls"], 1)
                    rows[-1]["pick_changed%"] = 100 * s["pick_changed"] / max(s["minute_active"], 1)
        print(f"  w={w} 完成", flush=True)

    tbl = pd.DataFrame(rows)
    tbl.to_csv(OUT_DIR / "phase_results_v3.csv", index=False)
    pd.concat(curves, axis=1).to_csv(OUT_DIR / "nav_curves_v3.csv")

    print("\n===== v3：总收益（5 相位），每 w 两变体 =====")
    piv = tbl.pivot_table(index="w", columns=["kind", "phase"], values="total")
    for w in list(POOL_WINDOWS) + [REF_WINDOW]:
        d = [piv.loc[w, ("daily", f"P{i+1}")] for i in range(5)]
        m = [piv.loc[w, ("minute", f"P{i+1}")] for i in range(5)]
        tag = " (参照)" if w == REF_WINDOW else ""
        print(f"w={w}{tag}: daily  {['%+7.2f%%' % (x*100) for x in d]} 中位 {pd.Series(d).median():+.2%}")
        print(f"          minute {['%+7.2f%%' % (x*100) for x in m]} 中位 {pd.Series(m).median():+.2%}"
              f"  Δ中位 {(pd.Series(m).median()-pd.Series(d).median())*100:+.1f}pp"
              f"  分钟≥日线 {(pd.Series(m)>=pd.Series(d)).sum()}/5")

    print("\n===== 预注册判定（每 w 独立；注意 4 个 w 的多重比较）=====")
    for w in POOL_WINDOWS:
        d = tbl[(tbl["w"] == w) & (tbl["kind"] == "daily")]["total"]
        m = tbl[(tbl["w"] == w) & (tbl["kind"] == "minute")]["total"]
        win = int((m.values >= d.values).sum())
        med_diff = (m.median() - d.median()) * 100
        if win >= 4 and med_diff > 0:
            v = "success"
        elif med_diff <= -2:
            v = "harmful"
        else:
            v = "neutral"
        print(f"  w={w}: {v.upper()}（中位差 {med_diff:+.1f}pp，{win}/5 相位）")
    extra = tbl[(tbl["kind"] == "minute") & (tbl["w"].isin(POOL_WINDOWS))][
        ["w", "phase", "minute_active%", "pick_changed%"]]
    print("\n机制诊断：")
    print(extra.to_string(index=False,
          formatters={"minute_active%": "{:.0f}%".format, "pick_changed%": "{:.0f}%".format}))
    print(f"\n[OK] 结果写入 {OUT_DIR}/phase_results_v3.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
