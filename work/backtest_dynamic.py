"""动态宽基→概念策略基线复现（GPT 会话 2026-09-18 主线）。

用法：
    conda run -n resonance python work/backtest_dynamic.py

对照锚点（docs/gpt-session-summary.md §三，2025-01-01~2026-09-18）：
    动态宽基→概念 5日: +148.59% 超额+80.13% 夏普1.792 回撤-35.37%
    动态宽基→概念 3日: +111.87% 夏普1.574 回撤-30.19%
    只持最强宽基:      +121.06%   同花顺全A: +38.01%
    领先频率: 微盘股26 创业板指17 科创50 14 上证50/科创综指6 北证50/中证2000 5

产出：
    outputs/index_backtest_framework/reproduction_report.md（入库）
    outputs/index_backtest_framework/nav_curves.csv / leader_log.csv（gitignored）

口径说明：exec_lag=1（信号 T 收盘 → T+1 收盘入场，T+2 起计收益）为 GPT 锚点
对齐口径（work/probe_leader_rules.py 探测判定）；exec_lag=0 为当收盘价成交口径。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402

from resonance import config  # noqa: E402
from resonance.backtest import RotationBacktester, perf_stats  # noqa: E402
from resonance.dynamic import make_dynamic_rank_fn  # noqa: E402

OUT_DIR = config.OUTPUTS_DIR / "index_backtest_framework"
ANCHORS = {
    "dyn5_total": 1.4859, "dyn5_excess": 0.8013, "dyn5_sharpe": 1.792, "dyn5_dd": -0.3537,
    "dyn3_total": 1.1187, "dyn3_sharpe": 1.574, "dyn3_dd": -0.3019,
    "leader_total": 1.2106, "bench_total": 0.3801,
    "dyn5_excess_p": 0.0266,
}
LEADER_FREQ_ANCHOR = {"微盘股": 26, "创业板指": 17, "科创50": 14, "上证50": 6,
                      "科创综指": 6, "北证50": 5, "中证2000": 5}


def load_wide() -> tuple[pd.DataFrame, list[str]]:
    f = config.CACHE_DIR / "daily_bars.parquet"
    if not f.exists():
        f = config.CACHE_DIR / "daily_bars.partial.parquet"
    bars = pd.read_parquet(f)
    catalog = pd.read_csv(config.DATA_DIR / "concept_catalog.csv")
    got_concepts = [c for c in catalog["code"] if c in set(bars["symbol"])]
    close_all = bars.pivot(index="date", columns="symbol", values="close").sort_index()
    close_all.index = pd.to_datetime(close_all.index)
    return close_all, got_concepts


def make_momentum_rank_fn(close_all: pd.DataFrame, rebal: int, exec_lag: int, cold: bool):
    """只持最强宽基：13 宽基按 20 日动量排名，topk=1（无缓冲）。恒 corr>0（无空仓门槛）。"""
    broad = list(config.BROAD_INDEX_POOL)
    close_broad = close_all[broad]
    cal = close_all.index
    cutoff = None
    if cold:
        start_pos = cal.searchsorted(pd.Timestamp(config.BACKTEST_START))
        cutoff = cal[start_pos + config.SIGNAL_WINDOW]

    def rank_fn(asof) -> pd.DataFrame:
        asof = pd.Timestamp(asof)
        pos = cal.searchsorted(asof)
        sig = cal[max(pos - exec_lag, 0)]
        if cutoff is not None and sig < cutoff:
            return pd.DataFrame(columns=["concept", "corr"])
        sub = close_broad.loc[:sig]
        if len(sub) < config.SIGNAL_WINDOW + 1:
            return pd.DataFrame(columns=["concept", "corr"])
        mom = (sub.iloc[-1] / sub.iloc[-config.SIGNAL_WINDOW - 1] - 1).dropna()
        if mom.empty:
            return pd.DataFrame(columns=["concept", "corr"])
        mom = mom.sort_values(ascending=False)
        rank_fn.leader_history[str(asof.date())] = str(mom.index[0])
        return pd.DataFrame({"concept": mom.index, "corr": mom.values + 10.0})

    rank_fn.leader_history: dict[str, str | None] = {}
    return rank_fn


def run_variant(close_all, rank_fn, trade_cols, rebal, topk=config.TOPK_BUFFER):
    bt = RotationBacktester(close_all[trade_cols].loc[config.BACKTEST_START:],
                            rank_fn, rebalance_days=rebal, topk=topk)
    return bt.run()


def wilcoxon_excess(nav: pd.Series, bench: pd.Series, period: int) -> tuple[float, int]:
    """非重叠 period 日超额简单收益的 Wilcoxon 符号秩检验（vs 0）。"""
    df = pd.concat([nav.pct_change(), bench.pct_change()], axis=1, keys=["s", "b"]).dropna()
    n_blk = len(df) // period
    exc = [float(df.iloc[i * period : (i + 1) * period]["s"].sum()
                 - df.iloc[i * period : (i + 1) * period]["b"].sum())
           for i in range(n_blk)]
    if len(exc) < 5 or all(e == 0 for e in exc):
        return float("nan"), len(exc)
    return float(stats.wilcoxon(exc).pvalue), len(exc)


def main() -> int:
    close_all, concepts = load_wide()
    broad = list(config.BROAD_INDEX_POOL)
    bench_close = close_all["883957.TI"].loc[config.BACKTEST_START:]
    bench_nav = bench_close / bench_close.iloc[0]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    variants = [
        # (tag, 动态/宽基, rebal, exec_lag, cold, topk)
        ("dyn5_lag0_warm", "dyn", 5, 0, False, config.TOPK_BUFFER),
        ("dyn5_lag1_warm", "dyn", 5, 1, False, config.TOPK_BUFFER),
        ("dyn5_lag1_cold", "dyn", 5, 1, True, config.TOPK_BUFFER),
        ("dyn3_lag1_cold", "dyn", 3, 1, True, config.TOPK_BUFFER),
        ("leader5_lag1_cold", "leader", 5, 1, True, 1),
        ("leader5_lag0_warm", "leader", 5, 0, False, 1),
    ]
    results, curves, freqs = {}, {"benchmark_quanA": bench_nav}, {}
    for tag, kind, rebal, lag, cold, topk in variants:
        if kind == "dyn":
            rank_fn = make_dynamic_rank_fn(
                close_all, concepts, window=config.SIGNAL_WINDOW,
                cold_start=cold, backtest_start=config.BACKTEST_START, exec_lag=lag)
            cols = concepts
        else:
            rank_fn = make_momentum_rank_fn(close_all, rebal, lag, cold)
            cols = broad
        out = run_variant(close_all, rank_fn, cols, rebal, topk)
        st = perf_stats(out["nav_curve"], bench_nav)
        st["excess_p"], st["n_blocks"] = wilcoxon_excess(out["nav_curve"], bench_nav, rebal)
        results[tag] = st
        curves[tag] = out["nav_curve"]
        lead = pd.Series(rank_fn.leader_history).dropna()
        lead = lead[[d >= config.BACKTEST_START for d in lead.index]]
        freqs[tag] = lead.map(config.BROAD_INDEX_POOL).value_counts()

    # --- 输出 ---
    print("\n===== 复现结果 vs GPT 会话锚点（2025-01-01 ~ 数据截止）=====")
    rows = [{"tag": t, "total": s["total_return"], "excess": s.get("excess_wealth"),
             "sharpe": s["sharpe"], "maxdd": s["max_drawdown"],
             "excess_p": s.get("excess_p")} for t, s in results.items()]
    print(pd.DataFrame(rows).to_string(index=False,
          formatters={"total": "{:.2%}".format, "excess": "{:.2%}".format,
                      "maxdd": "{:.2%}".format, "excess_p": "{:.4f}".format}))
    print(f"\n锚点: dyn5 +{ANCHORS['dyn5_total']:.0%}/超额+{ANCHORS['dyn5_excess']:.0%}/"
          f"夏普{ANCHORS['dyn5_sharpe']}/回撤{ANCHORS['dyn5_dd']:.1%} | "
          f"dyn3 +{ANCHORS['dyn3_total']:.0%}/夏普{ANCHORS['dyn3_sharpe']} | "
          f"最强宽基 +{ANCHORS['leader_total']:.0%} | 全A +{ANCHORS['bench_total']:.0%}")

    for tag in ("dyn5_lag1_cold", "dyn5_lag1_warm", "dyn5_lag0_warm"):
        print(f"\n===== 领先指数频率（{tag}）=====")
        print(freqs[tag].to_string())
    print(f"锚点: {LEADER_FREQ_ANCHOR}（合计 {sum(LEADER_FREQ_ANCHOR.values())}）")

    pd.concat(curves, axis=1).to_csv(OUT_DIR / "nav_curves.csv")
    print(f"\n[OK] 净值曲线写入 {OUT_DIR}/nav_curves.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
