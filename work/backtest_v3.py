"""V3 最佳方案锚点对照 + 相位稳健性基线（docs/v3-best-plan.md）。

用法：
    conda run -n resonance python work/backtest_v3.py

对照锚点（V3 文档 §9，GPT 会话 2025-01-01~2026-09-18，~390 概念目录）：
    0bp:  +299.11% 回撤 −19.16% 夏普 2.64（2025 +116.08% / 2026 +84.71%）
    10bp: +243.14% 回撤 −19.73% 夏普 2.36   30bp: +153.44% 回撤 −21.06% 夏普 1.81
    持仓变更 102 次，止损 4 次，初始建仓日错开 0/1/2 日结果一致

本机概念目录为 529 个快照（2026-09-19）vs 会话 ~390（2026-09-17），锚点
对照按方向/量级判定，不做逐点对齐（沿基线复现的残差归因口径）。
相位协议（docs/v3-optimization-design.md）：5 相位起点
    2024-12-27 / 12-30 / 12-31 / 2025-01-02 / 01-03，取中位数。

产出：outputs/v3/{nav_curves.csv, trades.csv, report_baseline.md 由本脚本打印}
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.backtest import perf_stats  # noqa: E402
from resonance.v3 import V3Backtester, V3Params, yearly_returns  # noqa: E402

OUT_DIR = config.OUTPUTS_DIR / "v3"

ANCHORS = {  # V3 文档 §9
    "total_0bp": 2.9911, "dd_0bp": -0.1916, "sharpe_0bp": 2.64,
    "y2025_0bp": 1.1608, "y2026_0bp": 0.8471,
    "total_10bp": 2.4314, "dd_10bp": -0.1973, "sharpe_10bp": 2.36,
    "y2025_10bp": 0.9766, "y2026_10bp": 0.7360,
    "total_30bp": 1.5344, "dd_30bp": -0.2106, "sharpe_30bp": 1.81,
    "y2025_30bp": 0.6533, "y2026_30bp": 0.5329,
    "changes": 102, "stops": 4,
}

# 相位协议：锚点相位 2025-01-02 居中，两侧各 2 个相邻交易日
PHASES = ["2024-12-27", "2024-12-30", "2024-12-31", "2025-01-02", "2025-01-03"]


def load_wide() -> tuple[pd.DataFrame, list[str]]:
    bars = pd.read_parquet(config.CACHE_DIR / "daily_bars.parquet")
    catalog = pd.read_csv(config.DATA_DIR / "concept_catalog.csv")
    got = [c for c in catalog["code"] if c in set(bars["symbol"])]
    close_all = bars.pivot(index="date", columns="symbol", values="close").sort_index()
    close_all.index = pd.to_datetime(close_all.index)
    return close_all, got


def evaluate(
    close_all: pd.DataFrame,
    concepts: list[str],
    p: V3Params,
    start: str,
    end: str | None = None,
    minute_prices=None,
) -> dict:
    """单配置单窗口运行 → 统计 dict（含原始输出供诊断）。"""
    bt = V3Backtester(close_all, concepts, params=p, minute_prices=minute_prices)
    out = bt.run(start, end)
    nav = out["nav_curve"]
    bench = close_all["883957.TI"].loc[nav.index[0]:]
    bench_nav = bench / bench.iloc[0]
    st = perf_stats(nav, bench_nav)
    yr = yearly_returns(nav)
    s = out["stats"]
    return {
        "total": st["total_return"], "dd": st["max_drawdown"], "sharpe": st["sharpe"],
        "excess": st.get("excess_wealth"),
        "y2025": yr.get("2025", float("nan")), "y2026": yr.get("2026", float("nan")),
        "changes": s["position_changes"], "stops": s["stop_count"],
        "entries": s["entries"], "switches": s["switches"], "exits": s["exits"],
        "flat_pct": s["flat_days"] / max(len(nav), 1),
        "degraded": s.get("degraded_days", 0),
        "_out": out,
    }


def med(rows: list[dict], key: str) -> float:
    return float(np.median([r[key] for r in rows]))


def fmt(x: float) -> str:
    return f"{x:+.2%}" if x is not None and abs(x) < 20 else (f"{x:+.2%}" if x is not None else "—")


def main() -> int:
    close_all, concepts = load_wide()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = V3Params()

    # --- 一、锚点相位（2025-01-02）× 三档成本 ---
    print("=" * 72)
    print("一、锚点相位 2025-01-02，0/10/30bp vs V3 文档锚点")
    rows = []
    for cost in (0.0, 10.0, 30.0):
        r = evaluate(close_all, concepts, base.with_(cost_bp=cost), "2025-01-02")
        tag = f"{int(cost)}bp"
        rows.append({
            "cost": tag,
            "total": r["total"], "dd": r["dd"], "sharpe": r["sharpe"],
            "y2025": r["y2025"], "y2026": r["y2026"],
            "changes": r["changes"], "stops": r["stops"],
            "flat_pct": r["flat_pct"], "excess": r["excess"],
        })
        globals()[f"_last_{int(cost)}"] = r  # 保存原始输出用于落盘
    anchor_row = {
        "cost": "0bp锚", "total": ANCHORS["total_0bp"], "dd": ANCHORS["dd_0bp"],
        "sharpe": ANCHORS["sharpe_0bp"], "y2025": ANCHORS["y2025_0bp"],
        "y2026": ANCHORS["y2026_0bp"], "changes": ANCHORS["changes"],
        "stops": ANCHORS["stops"], "flat_pct": float("nan"), "excess": float("nan"),
    }
    df = pd.DataFrame(rows + [anchor_row])
    print(df.to_string(index=False, float_format=lambda v: f"{v:.4f}"))

    # --- 二、相位稳健性（5 相位，10bp 决策口径 + 0bp 对照）---
    print("\n" + "=" * 72)
    print("二、相位稳健性：5 相位起点（协议见 docs/v3-optimization-design.md）")
    phase_rows = []
    for start in PHASES:
        for cost in (0.0, 10.0):
            r = evaluate(close_all, concepts, base.with_(cost_bp=cost), start)
            phase_rows.append({
                "start": start, "cost": f"{int(cost)}bp",
                "total": r["total"], "dd": r["dd"], "sharpe": r["sharpe"],
                "y2025": r["y2025"], "y2026": r["y2026"],
                "changes": r["changes"], "stops": r["stops"],
                "flat_pct": r["flat_pct"],
            })
    pdf = pd.DataFrame(phase_rows)
    print(pdf.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    for cost in ("0bp", "10bp"):
        sub = [r for r in phase_rows if r["cost"] == cost]
        print(f"\n[{cost}] 5 相位中位数：总收益 {fmt(med(sub, 'total'))} | "
              f"回撤 {med(sub, 'dd'):.2%} | 夏普 {med(sub, 'sharpe'):.3f} | "
              f"2025 {fmt(med(sub, 'y2025'))} | 2026 {fmt(med(sub, 'y2026'))} | "
              f"变更 {med(sub, 'changes'):.0f} | 止损 {med(sub, 'stops'):.0f} | "
              f"空仓 {med(sub, 'flat_pct'):.1%}")

    # --- 三、诊断：领先频率 / 半衰期分布 / 建仓错日 ---
    r0 = globals().get("_last_0")
    if r0 is not None:
        s = r0["_out"]["stats"]
        lead = pd.Series(s["leaders"])
        freq = lead.map(config.BROAD_INDEX_POOL).value_counts()
        print("\n三、诊断（锚点相位 0bp）")
        print("领先指数频率：", dict(freq))
        hl = pd.Series(s["half_lives"]).value_counts().sort_index()
        print("半衰期分布：", dict(hl))
        print(f"闸门失败日：{s['gate_fail_days']} | 检查日：{s['check_days']} | "
              f"信号日：{s['signal_days']} | 空仓日占比：{r0['flat_pct']:.1%}")
        for shift in (1, 2):
            rs = evaluate(close_all, concepts, base,
                          pd.Timestamp("2025-01-02") + pd.tseries.offsets.BDay(shift))
            print(f"建仓起点 +{shift} 日：总收益 {fmt(rs['total'])} "
                  f"（变更 {rs['changes']}，与锚点相位同: {abs(rs['total'] - r0['total']) < 1e-9}）")

    # --- 落盘 ---
    curves = {f"{r['cost']}": globals()[f"_last_{int(float(r['cost'][:-2]))}"]["_out"]["nav_curve"]
              for r in rows}
    bench = close_all["883957.TI"].loc[curves["0bp"].index[0]:]
    curves["benchmark_quanA"] = bench / bench.iloc[0]
    pd.concat(curves, axis=1).to_csv(OUT_DIR / "nav_curves.csv")
    globals()["_last_0"]["_out"]["trades"].to_csv(OUT_DIR / "trades.csv", index=False)
    pdf.to_csv(OUT_DIR / "phase_table.csv", index=False)
    print(f"\n[OK] 产出写入 {OUT_DIR}/（nav_curves.csv / trades.csv / phase_table.csv）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
