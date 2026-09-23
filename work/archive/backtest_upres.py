"""上涨共振验证：榜单指标 full(全样本20日Pearson) vs up(上涨日条件) vs down(对照)。

用法：
    conda run -n resonance python work/backtest_upres.py

用户口径变更（2026-09-20）："使用上涨共振来进行计算"。上涨共振 = 信号窗口内
仅取领先指数上涨日（收益>0）计算概念-指数 Pearson 相关（下跌日条件版作对照）；
条件样本 <8 回退全窗相关并计数。信号层其余口径与执行口径全部不变
（20日领先动量、5日调仓、单持仓、Top5缓冲、exec_lag=1、几何复利）。

预注册判据（沿 minute-exec-design §四 阈值风格）：
- 主判定 = B4%/minute 栈（采纳中的生产配置）上 up vs full：
  5 相位中位 Δ总收益 ≥ +2pp 且 ≥4/5 相位 Δ≥0 且 中位 Δ回撤 ≥ −1pp → 采纳；
- 裸栈（无止损）同判据作机制参考，须同向；
- down 对照若同样达标 → 改判 NEUTRAL（条件样本缩减/池churn 伪效应，
  非上涨特异性）；
- |中位差| ≤ 2pp → NEUTRAL；中位下降 >2pp → HARMFUL。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.backtest import RotationBacktester, perf_stats  # noqa: E402
from resonance.dynamic import make_dynamic_rank_fn  # noqa: E402
from resonance.exec_minute import MinutePrices, run_with_intraday_stop  # noqa: E402
from resonance.metrics import updown_resonance_rankings  # noqa: E402
from work.backtest_exec import PHASE_STARTS, load_all  # noqa: E402

OUT_DIR = config.OUTPUTS_DIR / "minute_exec"
STOP_GRID = (0.04, 0.06, 0.08, 0.10, 0.12)
VARIANTS = ("full", "up", "down")


def cached_rank_variant(close, concepts, variant, stats):
    inner_stats = stats
    if variant == "full":
        inner = make_dynamic_rank_fn(close, concepts, window=20, exec_lag=1)
    else:
        def ranking(r, l, cs, w, asof=None):
            return updown_resonance_rankings(r, l, cs, w, side=variant, min_days=8,
                                             asof=asof, stats=inner_stats)
        inner = make_dynamic_rank_fn(close, concepts, window=20, exec_lag=1,
                                     ranking_fn=ranking)
    cache: dict = {}

    def rank_fn(asof):
        key = str(pd.Timestamp(asof).date())
        if key not in cache:
            cache[key] = inner(asof)
        return cache[key]

    rank_fn.leader_history = inner.leader_history
    return rank_fn


def main() -> int:
    close, open_, minute_wide, concepts = load_all()
    prices = MinutePrices(close, open_, minute_wide)
    bench = close["883957.TI"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows, base_rows, stats_by_variant = [], {}, {v: {} for v in VARIANTS}
    rank_fns = {}
    for v in VARIANTS:
        rank_fns[v] = cached_rank_variant(close, concepts, v, stats_by_variant[v])
        for i, start in enumerate(PHASE_STARTS):
            phase = f"P{i+1}({start})"
            bench_nav = bench.loc[start:]
            bench_nav = bench_nav / bench_nav.iloc[0]
            bt = RotationBacktester(close[concepts].loc[start:], rank_fns[v], rebalance_days=5)
            base = bt.run()
            st = perf_stats(base["nav_curve"], bench_nav)
            base_rows[(v, phase)] = {"total": st["total_return"], "maxdd": st["max_drawdown"],
                                     "sharpe": st["sharpe"], "excess": st.get("excess_wealth"),
                                     "n_sw": len(base["switches"])}
            for x in STOP_GRID:
                prices.degraded = 0
                out = run_with_intraday_stop(close[concepts].loc[start:], prices, rank_fns[v],
                                             rebalance_days=5, topk=5, stop_pct=x, mode="minute")
                st = perf_stats(out["nav_curve"], bench_nav)
                rows.append({"variant": v, "phase": phase, "x": x, "mode": "minute",
                             "total": st["total_return"], "maxdd": st["max_drawdown"],
                             "sharpe": st["sharpe"], "excess": st.get("excess_wealth"),
                             "stops": out["stats"]["stops"],
                             "flat_days": out["stats"]["flat_days"],
                             "degraded_days": out["stats"]["degraded_days"]})
            # B4%/close（粒度归因）
            prices.degraded = 0
            out = run_with_intraday_stop(close[concepts].loc[start:], prices, rank_fns[v],
                                         rebalance_days=5, topk=5, stop_pct=0.04, mode="close")
            st = perf_stats(out["nav_curve"], bench_nav)
            rows.append({"variant": v, "phase": phase, "x": 0.04, "mode": "close",
                         "total": st["total_return"], "maxdd": st["max_drawdown"],
                         "sharpe": st["sharpe"], "excess": st.get("excess_wealth"),
                         "stops": out["stats"]["stops"], "flat_days": out["stats"]["flat_days"],
                         "degraded_days": out["stats"]["degraded_days"]})

    tbl = pd.DataFrame(rows)
    base = pd.DataFrame([{"variant": v, "phase": p, **d} for (v, p), d in base_rows.items()])

    # ---------- 基线对比 ----------
    print("===== 基线（日线-only，无止损）× 指标变体，5 相位 =====")
    for metric in ("total", "maxdd", "sharpe"):
        piv = base.pivot(index="variant", columns="phase", values=metric)
        piv["median"] = piv.median(axis=1)
        print(f"--- {metric} ---")
        print(piv.loc[list(VARIANTS)].to_string(
            float_format="{:+.2f}".format if metric == "sharpe" else "{:+.2%}".format))

    # ---------- 指标诊断 ----------
    print("\n===== 指标诊断 =====")
    for v in VARIANTS:
        s = stats_by_variant[v]
        nc = s.get("n_cond", [])
        if nc:
            print(f"{v}: 条件样本中位 {np.median(nc):.0f} 天（P10 {np.percentile(nc,10):.0f} / "
                  f"P90 {np.percentile(nc,90):.0f}）| 回退全窗 {s.get('fallback', 0)} 个信号日")
        else:
            print(f"{v}: （全样本口径）")
    # Top10 池重叠（full vs up/down，P1 信号日）
    from resonance.metrics import resonance_rankings
    returns = close.pct_change()
    cal = close.index
    for v in ("up", "down"):
        jacs = []
        for d in cal[cal >= pd.Timestamp(PHASE_STARTS[0])]:
            lead = rank_fns["full"].leader_history.get(str(d.date()))
            if lead is None:
                continue
            sig = cal[cal.searchsorted(d) - 1]
            f = set(resonance_rankings(returns, lead, concepts, 20, asof=sig)["concept"].head(10))
            m = set(updown_resonance_rankings(returns, lead, concepts, 20, side=v,
                                              min_days=8, asof=sig)["concept"].head(10))
            if f and m:
                jacs.append(len(f & m) / len(f | m))
        print(f"Top10 池 Jaccard(full vs {v}): 均值 {np.mean(jacs):.2f}（n={len(jacs)} 信号日）")

    # ---------- B4% 栈判定（主）----------
    print("\n===== 主判定：B4%/minute 栈 up/down vs full =====")
    b4 = tbl[(tbl["x"] == 0.04) & (tbl["mode"] == "minute")].set_index(["variant", "phase"])
    bf = base.set_index(["variant", "phase"])
    verdicts = {}
    for v in ("up", "down"):
        d_t, d_d = [], []
        for p in [f"P{i+1}({s})" for i, s in enumerate(PHASE_STARTS)]:
            # 同栈对照：v+B4% vs full+B4%（指标效应）；vs 裸基线仅是止损效应
            d_t.append(b4.loc[(v, p), "total"] - b4.loc[("full", p), "total"])
            d_d.append(b4.loc[(v, p), "maxdd"] - b4.loc[("full", p), "maxdd"])
        d_t, d_d = pd.Series(d_t), pd.Series(d_d)
        ok = d_t.median() >= 0.02 and (d_t >= 0).sum() >= 4 and d_d.median() >= -0.01
        verdicts[v] = "adopt" if ok else ("harmful" if d_t.median() <= -0.02 else "neutral")
        print(f"B4%栈[{v} vs full] 中位Δ收益 {d_t.median()*100:+.1f}pp | 中位Δ回撤 {d_d.median()*100:+.1f}pp "
              f"| 改善相位 {(d_t >= 0).sum()}/5 → {verdicts[v].upper()}")

    # 裸栈参考（同判据）
    print("\n===== 参考：裸栈（无止损）up/down vs full =====")
    for v in ("up", "down"):
        d_t = [bf.loc[(v, p), "total"] - bf.loc[("full", p), "total"] for p in
               [f"P{i+1}({s})" for i, s in enumerate(PHASE_STARTS)]]
        d_d = [bf.loc[(v, p), "maxdd"] - bf.loc[("full", p), "maxdd"] for p in
               [f"P{i+1}({s})" for i, s in enumerate(PHASE_STARTS)]]
        print(f"裸栈[{v} vs full] 中位Δ收益 {np.median(d_t)*100:+.1f}pp | 中位Δ回撤 {np.median(d_d)*100:+.1f}pp "
              f"| 改善相位 {sum(1 for x in d_t if x >= 0)}/5")

    # down 对照约束
    if verdicts.get("up") == "adopt" and verdicts.get("down") == "adopt":
        print("\n⚠️ down 对照同样达标 → up 判定降级为 NEUTRAL（条件样本缩减伪效应）")
    # B 网格形态（若 up 被采纳）
    print("\n===== B 网格（minute）总收益中位数 × 指标变体 =====")
    g = tbl[tbl["mode"] == "minute"].groupby(["variant", "x"])[["total", "maxdd"]].median()
    print(g.to_string(float_format="{:+.2%}".format))

    # ---------- 全窗上下文 ----------
    print("\n===== 全窗（2025-01-02 起）日线-only 上下文（相位运气警示，仅参考）=====")
    bench_fw = bench.loc["2025-01-02":]
    bench_fw = bench_fw / bench_fw.iloc[0]
    for v in VARIANTS:
        bt = RotationBacktester(close[concepts].loc["2025-01-02":], rank_fns[v], rebalance_days=5)
        st = perf_stats(bt.run()["nav_curve"], bench_fw)
        print(f"{v:>4}: total {st['total_return']:+.2%} | maxdd {st['max_drawdown']:+.2%} "
              f"| sharpe {st['sharpe']:.3f} | excess {st.get('excess_wealth'):+.2%}")

    tbl.to_csv(OUT_DIR / "phase_results_upres.csv", index=False)
    base.to_csv(OUT_DIR / "baseline_upres.csv", index=False)
    print(f"\n[OK] 结果写入 {OUT_DIR}/phase_results_upres.csv 等")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
