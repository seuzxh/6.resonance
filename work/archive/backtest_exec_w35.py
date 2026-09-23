"""信号窗口重验证：w ∈ {20(参照), 5, 3} × 基线 + B 盘中追踪止损网格。

用法：
    conda run -n resonance python work/backtest_exec_w35.py

用户口径变更（2026-09-19）："20日太久了，改为 3日 和 5日"。w 同时作用于
领先指数动量窗与概念相关窗（make_dynamic_rank_fn 的单一 window 参数，
与 v3 短窗设计一致）。信号层其余口径不变（5日调仓、单持仓、Top5缓冲、
exec_lag=1、几何复利）。
判定沿用 docs/minute-exec-design.md §四 的 B 预注册判据，按窗口分别对照
**同窗基线**；5 相位中位数，另报双口径（median-of-deltas / delta-of-medians）。
附：全窗（2025-01-02 起）日线-only w 对比作上下文（带相位运气警示）。
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
from work.backtest_exec import PHASE_STARTS, load_all  # noqa: E402

OUT_DIR = config.OUTPUTS_DIR / "minute_exec"
WINDOWS = (20, 5, 3)
STOP_GRID = (0.04, 0.06, 0.08, 0.10, 0.12)


def cached_rank_w(close, concepts, w):
    inner = make_dynamic_rank_fn(close, concepts, window=w, exec_lag=1)
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

    rows, base_rows, leader_freq = [], {}, {}
    for w in WINDOWS:
        rank_fn = cached_rank_w(close, concepts, w)
        for i, start in enumerate(PHASE_STARTS):
            phase = f"P{i+1}({start})"
            bench_nav = bench.loc[start:]
            bench_nav = bench_nav / bench_nav.iloc[0]
            bt = RotationBacktester(close[concepts].loc[start:], rank_fn, rebalance_days=5)
            base = bt.run()
            st = perf_stats(base["nav_curve"], bench_nav)
            base_rows[(w, phase)] = {"total": st["total_return"], "maxdd": st["max_drawdown"],
                                     "sharpe": st["sharpe"], "excess": st.get("excess_wealth"),
                                     "n_sw": len(base["switches"])}
            for x in STOP_GRID:
                for mode in ("minute", "close"):
                    prices.degraded = 0
                    out = run_with_intraday_stop(close[concepts].loc[start:], prices, rank_fn,
                                                 rebalance_days=5, topk=5, stop_pct=x, mode=mode)
                    st = perf_stats(out["nav_curve"], bench_nav)
                    rows.append({"w": w, "phase": phase, "x": x, "mode": mode,
                                 "total": st["total_return"], "maxdd": st["max_drawdown"],
                                 "sharpe": st["sharpe"], "excess": st.get("excess_wealth"),
                                 "stops": out["stats"]["stops"],
                                 "flat_days": out["stats"]["flat_days"],
                                 "degraded_days": out["stats"]["degraded_days"]})
        lead = pd.Series(rank_fn.leader_history).dropna()
        lead = lead[[d >= PHASE_STARTS[0] for d in lead.index]]
        leader_freq[w] = lead.map(config.BROAD_INDEX_POOL).value_counts()

    tbl = pd.DataFrame(rows)
    base = pd.DataFrame([{"w": w, "phase": p, **v} for (w, p), v in base_rows.items()])

    # ---------- 基线对比（同协议 5 相位）----------
    print("===== 基线（日线-only）随信号窗口变化，5 相位 =====")
    piv_t = base.pivot(index="w", columns="phase", values="total"); piv_t["median"] = piv_t.median(axis=1)
    piv_d = base.pivot(index="w", columns="phase", values="maxdd"); piv_d["median"] = piv_d.median(axis=1)
    piv_s = base.pivot(index="w", columns="phase", values="sharpe"); piv_s["median"] = piv_s.median(axis=1)
    for name, piv in (("总收益", piv_t), ("最大回撤", piv_d), ("夏普", piv_s)):
        print(f"--- {name} ---")
        print(piv.to_string(float_format="{:+.2f}".format if name == "夏普" else "{:+.2%}".format))
    print("\n领先指数频率（窗口内信号日）：")
    for w in WINDOWS:
        print(f"w={w:>2}: {leader_freq[w].to_dict()}")

    # ---------- B 网格 × 窗口判定 ----------
    verdicts = []
    print("\n===== B 止损网格按窗口（vs 同窗基线，5 相位中位数）=====")
    for w in WINDOWS:
        b = base[base["w"] == w].set_index("phase")
        sub = tbl[tbl["w"] == w]
        print(f"\n--- w={w} ---")
        for mode in ("minute", "close"):
            g = sub[sub["mode"] == mode]
            mt = g.pivot(index="x", columns="phase", values="total"); mt["median"] = mt.median(axis=1)
            md = g.pivot(index="x", columns="phase", values="maxdd"); md["median"] = md.median(axis=1)
            print(f"[mode={mode}] 总收益: "); print(mt.to_string(float_format="{:+.2%}".format))
            print(f"[mode={mode}] 最大回撤: "); print(md.to_string(float_format="{:+.2%}".format))
        for (x, mode), gg in sub.groupby(["x", "mode"]):
            gg = gg.set_index("phase")
            d_t = (gg["total"] - b["total"]).dropna()
            d_d = (gg["maxdd"] - b["maxdd"]).dropna()
            dom_t = np.median(gg["total"]) - np.median(b["total"])
            dom_d = np.median(gg["maxdd"]) - np.median(b["maxdd"])
            ok = d_d.median() >= 0.03 and d_t.median() >= -0.02 and (d_d > 0).sum() >= 4
            v = "success" if ok else "neutral"
            verdicts.append({"w": w, "x": x, "mode": mode, "verdict": v,
                             "dret_mod": d_t.median(), "ddd_mod": d_d.median(),
                             "dret_dom": dom_t, "ddd_dom": dom_d})
        for r in [r for r in verdicts if r["w"] == w]:
            print(f"B[w={w}/{r['x']:.0%}/{r['mode']}] Δret(mod)={r['dret_mod']*100:+.1f}pp "
                  f"Δdd(mod)={r['ddd_mod']*100:+.1f}pp | Δret(dom)={r['dret_dom']*100:+.1f}pp "
                  f"Δdd(dom)={r['ddd_dom']*100:+.1f}pp → {r['verdict'].upper()}")

    # ---------- 全窗日线-only 上下文（相位运气警示）----------
    print("\n===== 全窗（2025-01-02 起）日线-only 上下文（锚点相位=相位运气上限，仅参考）=====")
    bench_fw = bench.loc["2025-01-02":]; bench_fw = bench_fw / bench_fw.iloc[0]
    for w in WINDOWS:
        rank_fn = cached_rank_w(close, concepts, w)
        bt = RotationBacktester(close[concepts].loc["2025-01-02":], rank_fn, rebalance_days=5)
        st = perf_stats(bt.run()["nav_curve"], bench_fw)
        print(f"w={w:>2}: total {st['total_return']:+.2%} | maxdd {st['max_drawdown']:+.2%} "
              f"| sharpe {st['sharpe']:.3f} | excess {st.get('excess_wealth'):+.2%}")

    tbl.to_csv(OUT_DIR / "phase_results_w35.csv", index=False)
    base.to_csv(OUT_DIR / "baseline_w35.csv", index=False)
    pd.DataFrame(verdicts).to_csv(OUT_DIR / "verdicts_w35.csv", index=False)
    print(f"\n[OK] 结果写入 {OUT_DIR}/phase_results_w35.csv 等")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
