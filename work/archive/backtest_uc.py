"""用户组合口径验证：短窗上涨共振(4/5日) + 领先指数3日动量闸门 + 逐日滚动检查。

用法：
    conda run -n resonance python work/backtest_uc.py

用户口径变更（2026-09-20，三条，第 4 条为空）：
1. 上涨共振时间维持在 4-5 日（window ∈ {4,5}，条件样本 <min_days=2 回退全窗）；
2. 领先指数 3 日累计涨幅 <0% 不开仓（闸门 = 不开新仓；持仓仍在 Top5 续持）；
3. 改为滚动周期（rebalance_days=1 逐日检查，Top5 缓冲保留）。
其余口径不变（单持仓、Top5 缓冲、exec_lag=1、几何复利、13 宽基领先动量窗=共振窗）。

消融阶梯（归因每个组件）：
  R0 生产配置：全样本 20 日 Pearson + 5 日网格（现行采纳口径，参照）
  R1 仅短窗上涨共振（w∈{4,5}，5 日网格，无闸门）
  R2 = R1 + 3 日闸门
  R3 = R2 + 逐日滚动（完整用户口径）

预注册判据（沿 minute-exec-design §四 阈值）：
- 主判定 = B4%/minute 栈上 R3(w∈{4,5}) vs R0+B4%：5 相位中位 Δ总收益 ≥ +2pp
  且 ≥4/5 相位 Δ≥0 且 中位 Δ回撤 ≥ −1pp → 采纳；裸栈同向作机制参考；
- 统计健康诊断必须报告：n_cond 分布（4-5 日窗上涨条件样本 2-3 天，Pearson
  在 n=2 时退化为 ±1）、闸门关闭频率、换手率。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.backtest import RotationBacktester, perf_stats  # noqa: E402
from resonance.dynamic import make_dynamic_rank_fn, make_up_short_rank_fn  # noqa: E402
from resonance.exec_minute import MinutePrices, run_with_intraday_stop  # noqa: E402
from work.backtest_exec import PHASE_STARTS, load_all  # noqa: E402

OUT_DIR = config.OUTPUTS_DIR / "minute_exec"
WINDOWS = (4, 5)


def cached(inner):
    cache: dict = {}

    def rank_fn(asof):
        key = str(pd.Timestamp(asof).date())
        if key not in cache:
            cache[key] = inner(asof)
        return cache[key]

    rank_fn.leader_history = inner.leader_history
    if getattr(inner, "gate_fn", None) is not None:
        rank_fn.gate_fn = inner.gate_fn
    return rank_fn


def run_stack(close_sub, prices, rank_fn, rebal, mode):
    """bare = stop_pct=0.999（等价性已单测锁定）；B4% = minute 盘中止损。"""
    if mode == "bare":
        return run_with_intraday_stop(close_sub, prices, rank_fn, rebalance_days=rebal,
                                      topk=5, stop_pct=0.999, mode="close")
    return run_with_intraday_stop(close_sub, prices, rank_fn, rebalance_days=rebal,
                                  topk=5, stop_pct=0.04, mode="minute")


def main() -> int:
    close, open_, minute_wide, concepts = load_all()
    prices = MinutePrices(close, open_, minute_wide)
    bench = close["883957.TI"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    stats_by: dict = {}
    rows = []
    for i, start in enumerate(PHASE_STARTS):
        phase = f"P{i+1}({start})"
        bench_nav = bench.loc[start:]
        bench_nav = bench_nav / bench_nav.iloc[0]
        close_sub = close[concepts].loc[start:]

        configs = [("R0", 20, 5, "prod")]
        for w in WINDOWS:
            configs += [("R1", w, 5, "up"), ("R2", w, 5, "up_gate"), ("R3", w, 1, "up_gate")]
        for rung, w, rebal, kind in configs:
            st_key = f"{rung}_w{w}"
            if rung == "R0":
                rank_fn = cached(make_dynamic_rank_fn(close, concepts, window=20, exec_lag=1))
            else:
                stats_by.setdefault(st_key, {})
                inner = make_up_short_rank_fn(close, concepts, window=w, min_days=2,
                                              gate_days=3, use_gate=(kind == "up_gate"),
                                              exec_lag=1, stats=stats_by[st_key])
                rank_fn = cached(inner)
            for mode in ("bare", "b4"):
                out = run_stack(close_sub, prices, rank_fn, rebal, mode)
                ps = perf_stats(out["nav_curve"], bench_nav)
                rows.append({"config": st_key, "rung": rung, "w": w, "rebal": rebal,
                             "stack": mode, "phase": phase,
                             "total": ps["total_return"], "maxdd": ps["max_drawdown"],
                             "sharpe": ps["sharpe"], "excess": ps.get("excess_wealth"),
                             "n_sw": len(out["switches"]),
                             "stops": out["stats"]["stops"],
                             "flat_days": out["stats"]["flat_days"],
                             "degraded_days": out["stats"]["degraded_days"],
                             "gate_blocked": out["stats"].get("gate_blocked", 0)})

    tbl = pd.DataFrame(rows)
    order = [c for c in tbl["config"].unique()]

    print("===== 消融阶梯 × 双栈（总收益 / 最大回撤 / 夏普，5 相位中位数）=====")
    for stack in ("bare", "b4"):
        print(f"\n--- stack={stack} ---")
        sub = tbl[tbl["stack"] == stack]
        for metric, fmt in (("total", "{:+.2%}".format), ("maxdd", "{:+.2%}".format), ("sharpe", "{:.2f}".format)):
            piv = sub.pivot(index="config", columns="phase", values=metric)
            piv["median"] = piv.median(axis=1)
            print(f"[{metric}]")
            print(piv.loc[order].to_string(float_format=fmt))
        piv_sw = sub.groupby("config")["n_sw"].mean()
        print(f"[平均换仓次数/相位] {piv_sw.loc[order].round(0).to_dict()}")

    print("\n===== 主判定：B4% 栈 R3(w) vs R0 =====")
    b4 = tbl[(tbl["stack"] == "b4")].set_index(["config", "phase"])
    r0 = b4.loc["R0_w20"]
    verdicts = {}
    for cfg in order:
        if not cfg.startswith("R3"):
            continue
        d_t = (b4.loc[cfg, "total"] - r0["total"]).dropna()
        d_d = (b4.loc[cfg, "maxdd"] - r0["maxdd"]).dropna()
        ok = d_t.median() >= 0.02 and (d_t >= 0).sum() >= 4 and d_d.median() >= -0.01
        dom_t = b4.loc[cfg, "total"].median() - r0["total"].median()
        dom_d = b4.loc[cfg, "maxdd"].median() - r0["maxdd"].median()
        verdicts[cfg] = "adopt" if ok else "neutral"
        print(f"{cfg}: mod Δret {d_t.median()*100:+.1f}pp / Δdd {d_d.median()*100:+.1f}pp / "
              f"相位 {(d_t >= 0).sum()}/5 | dom Δret {dom_t*100:+.1f}pp / Δdd {dom_d*100:+.1f}pp "
              f"→ {verdicts[cfg].upper()}")
    print("参考（裸栈 R3 vs R0-bare）：")
    bare = tbl[tbl["stack"] == "bare"].set_index(["config", "phase"])
    r0b = bare.loc["R0_w20"]
    for cfg in order:
        if not cfg.startswith("R3"):
            continue
        d_t = (bare.loc[cfg, "total"] - r0b["total"]).dropna()
        d_d = (bare.loc[cfg, "maxdd"] - r0b["maxdd"]).dropna()
        print(f"{cfg}: mod Δret {d_t.median()*100:+.1f}pp / Δdd {d_d.median()*100:+.1f}pp / "
              f"相位 {(d_t >= 0).sum()}/5")

    print("\n===== 统计健康诊断 =====")
    for key, st in stats_by.items():
        nc = st.get("n_cond", [])
        n2 = sum(1 for x in nc if x <= 2)
        print(f"{key}: n_cond 中位 {np.median(nc):.1f}（P10 {np.percentile(nc,10):.0f}/P90 "
              f"{np.percentile(nc,90):.0f}）| n_cond≤2 占比 {100*n2/max(len(nc),1):.0f}% | "
              f"回退 {st.get('fallback',0)} 信号日 | 闸门关闭 {st.get('gate_blocked_days',0)} 信号日")
    deg = tbl[tbl["stack"] == "b4"].groupby("config")["degraded_days"].mean()
    print(f"[B4% 平均降级天数/相位] {deg.loc[order].round(1).to_dict()}")

    print("\n===== 全窗（2025-01-02 起）裸栈上下文（相位运气警示，仅参考）=====")
    bench_fw = bench.loc["2025-01-02":]
    bench_fw = bench_fw / bench_fw.iloc[0]
    close_fw = close[concepts].loc["2025-01-02":]
    pr = MinutePrices(close, open_, minute_wide)
    for rung, w, rebal, kind in [("R0", 20, 5, "prod")] + [
            (r, w, rb, k) for w in WINDOWS for r, rb, k in
            (("R1", 5, "up"), ("R2", 5, "up_gate"), ("R3", 1, "up_gate"))]:
        if rung == "R0":
            rf = cached(make_dynamic_rank_fn(close, concepts, window=20, exec_lag=1))
        else:
            rf = cached(make_up_short_rank_fn(close, concepts, window=w, min_days=2,
                                              gate_days=3, use_gate=(kind == "up_gate"),
                                              exec_lag=1, stats={}))
        out = run_with_intraday_stop(close_fw, pr, rf, rebalance_days=rebal, topk=5,
                                     stop_pct=0.999, mode="close")
        ps = perf_stats(out["nav_curve"], bench_fw)
        print(f"{rung}_w{w}: total {ps['total_return']:+.2%} | maxdd {ps['max_drawdown']:+.2%} "
              f"| sharpe {ps['sharpe']:.3f} | n_sw {len(out['switches'])}")

    tbl.to_csv(OUT_DIR / "phase_results_uc.csv", index=False)
    print(f"\n[OK] 结果写入 {OUT_DIR}/phase_results_uc.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
