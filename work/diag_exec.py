"""执行层诊断：缺失覆盖定位 / A 两腿分解 / B 止损时点 / 3% 边界检查 / 双口径中位数。

用法：conda run -n resonance python work/diag_exec.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.backtest import RotationBacktester, perf_stats  # noqa: E402
from resonance.exec_minute import (  # noqa: E402
    TRADE_TIMES,
    MinutePrices,
    build_minute_wide,
    rerun_trade_times,
    run_with_intraday_stop,
)
from work.backtest_exec import PHASE_STARTS, cached_rank_fn, load_all  # noqa: E402


def main() -> int:
    close, open_, minute_wide, concepts = load_all()
    prices = MinutePrices(close, open_, minute_wide)
    rank_fn = cached_rank_fn(close, concepts)
    minute_codes = set(minute_wide.columns)

    # ---------- ① 缺失覆盖定位 ----------
    miss_trade, miss_hold = set(), set()
    for start in PHASE_STARTS:
        bt = RotationBacktester(close[concepts].loc[start:], rank_fn, rebalance_days=5)
        out = bt.run()
        days = list(out["nav_curve"].index)
        from resonance.exec_minute import _reconstruct_holding
        held, trade_of = _reconstruct_holding(days, out["switches"])
        for d in days:
            if d in trade_of:
                sw = next(s for s in out["switches"] if s["date"] == trade_of[d])
                for c in (sw["from"], sw["to"]):
                    if c is not None and not prices.day_marks(c, d):
                        miss_trade.add((c, d))
        # B 路径（8%/minute 代表点）
        b = run_with_intraday_stop(close[concepts].loc[start:], prices, rank_fn,
                                   rebalance_days=5, topk=5, stop_pct=0.08, mode="minute")
        miss_hold.update(b["degraded_log"])
    miss_all = miss_trade | miss_hold
    no_hf = {c for c, _ in miss_all if c not in minute_codes}
    gap = {(c, d) for c, d in miss_all if c in minute_codes}
    print(f"① 缺失覆盖：调仓日缺失 {len(miss_trade)} 对 | B持仓日缺失(去重) {len(miss_hold)} 对")
    print(f"   其中数据源完全无 HF 的代码: {len(no_hf)} 个（不可补，降级收盘是上限）")
    print(f"   代码有 HF 但当日缺（需求矩阵缺口，可补采）: {len(gap)} 对")
    if gap:
        days_of_gap = sorted({str(d.date()) for _, d in gap})
        print(f"   缺口日期范围: {days_of_gap[0]} ~ {days_of_gap[-1]}，涉及 {len({c for c, _ in gap})} 个代码")

    # ---------- ② A 两腿分解（逐调仓，跨相位合并）----------
    print("\n② A 两腿分解（对数收益，>0 = 早成交对该腿有利）：")
    legs = {t: {"exit": [], "entry": []} for t in TRADE_TIMES}
    for start in PHASE_STARTS:
        bt = RotationBacktester(close[concepts].loc[start:], rank_fn, rebalance_days=5)
        out = bt.run()
        for t in TRADE_TIMES:
            rr = rerun_trade_times(out, prices, t)
            for rec in rr["switches_log"]:
                d = rec["date"]
                p_old = prices.price(rec["old"], d, t) if t not in ("open", "15:00") else None
                p_new = prices.price(rec["new"], d, t) if t not in ("open", "15:00") else None
                if t == "15:00":
                    continue
                c_old, c_new = prices.close_at(d, rec["old"]), prices.close_at(d, rec["new"])
                po = prices._at(prices.open_wide, d, rec["old"]) if t == "open" else p_old
                pn = prices._at(prices.open_wide, d, rec["new"]) if t == "open" else p_new
                if all(math.isfinite(x) and x > 0 for x in (po, c_old)):
                    legs[t]["exit"].append(math.log(po / c_old))   # 卖旧腿：>0 = 旧日内走弱，早卖好
                if all(math.isfinite(x) and x > 0 for x in (pn, c_new)):
                    legs[t]["entry"].append(math.log(c_new / pn))  # 买新腿：>0 = 新日内走强，早买好
    rows = [{"t": t, "exit_leg(mean)": np.mean(v["exit"]) if v["exit"] else np.nan,
             "entry_leg(mean)": np.mean(v["entry"]) if v["entry"] else np.nan,
             "n": len(v["exit"])} for t, v in legs.items() if t != "15:00"]
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda x: f"{x:+.4%}"))

    # ---------- ③ B 止损时点与单次损益 ----------
    print("\n③ B[8%/minute] 止损事件诊断：")
    all_ev = []
    for start in PHASE_STARTS:
        b = run_with_intraday_stop(close[concepts].loc[start:], prices, rank_fn,
                                   rebalance_days=5, topk=5, stop_pct=0.08, mode="minute")
        all_ev.extend(b["stop_events"])
    ev = pd.DataFrame(all_ev)
    ev["hour"] = ev["time"].map(lambda x: x.hour if x is not None else None)
    print("   触发时点分布（小时）:", ev["hour"].value_counts().sort_index().to_dict())
    print(f"   止损当日收益 中位 {ev['day_ret'].median():+.2%} | 均值 {ev['day_ret'].mean():+.2%} | n={len(ev)}")

    # ---------- ④ 3% 边界检查（不入选择，只验 4% 不是悬崖边）----------
    print("\n④ 边界检查 x=3%（报告用，不参与择优）：")
    for mode in ("minute", "close"):
        tots, dds = [], []
        for start in PHASE_STARTS:
            prices.degraded = 0
            b = run_with_intraday_stop(close[concepts].loc[start:], prices, rank_fn,
                                       rebalance_days=5, topk=5, stop_pct=0.03, mode=mode)
            st = perf_stats(b["nav_curve"])
            tots.append(st["total_return"])
            dds.append(st["max_drawdown"])
        print(f"   3%/{mode}: total 中位 {np.median(tots):+.2%} | maxdd 中位 {np.median(dds):+.2%} | stops 均值 "
              f"{np.mean([b['stats']['stops'] for b in [run_with_intraday_stop(close[concepts].loc[s:], prices, rank_fn, 5, 5, 0.03, mode) for s in PHASE_STARTS]]):.1f}")

    # ---------- ⑤ 双口径中位数表（B，minute 模式）----------
    print("\n⑤ B[minute] 双口径：中位数差(median-of-deltas) vs 差的中位数(delta-of-medians)：")
    base_rows, grid_rows = {}, {}
    for start in PHASE_STARTS:
        bt = RotationBacktester(close[concepts].loc[start:], rank_fn, rebalance_days=5)
        base_rows[start] = perf_stats(bt.run()["nav_curve"])
        for x in (0.04, 0.06, 0.08, 0.10):
            b = run_with_intraday_stop(close[concepts].loc[start:], prices, rank_fn,
                                       rebalance_days=5, topk=5, stop_pct=x, mode="minute")
            grid_rows.setdefault(x, {})[start] = perf_stats(b["nav_curve"])
    for x, per in grid_rows.items():
        dt = [per[s]["total_return"] - base_rows[s]["total_return"] for s in PHASE_STARTS]
        dd = [per[s]["max_drawdown"] - base_rows[s]["max_drawdown"] for s in PHASE_STARTS]
        dom_t = np.median([per[s]["total_return"] for s in PHASE_STARTS]) - np.median(
            [base_rows[s]["total_return"] for s in PHASE_STARTS])
        dom_d = np.median([per[s]["max_drawdown"] for s in PHASE_STARTS]) - np.median(
            [base_rows[s]["max_drawdown"] for s in PHASE_STARTS])
        print(f"   {x:.0%}: Δret(mod)={np.median(dt)*100:+.1f}pp Δdd(mod)={np.median(dd)*100:+.1f}pp | "
              f"Δret(dom)={dom_t*100:+.1f}pp Δdd(dom)={dom_d*100:+.1f}pp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
