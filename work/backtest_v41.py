"""V4.1 最佳方案回测（覆盖受限口径）：日线Top5 + 24根5min 纯分钟重排 + Top2。

规格：docs/v4-best-plan.md（V4.1，2026-09-22）。窗口 2025-09-22→2026-09-18
（5min 可用约束），5 相位起点（09-22…09-26），0/10/30bp。

⚠️ 覆盖受限（数据残差，见 outputs/v4/report.md）：iFinD HF 月度配额耗尽
（-4318），现有 5min 为 dyn5 时代需求矩阵（400 codes）；V4.1 Top5 概念-日
对覆盖率 49.8%，领先指数缺 bar 45/172 天（微盘股 36 + 中证2000 6 +
上证指数 2 + 深证成指 1，其中 700050/932000 为 HF 无覆盖，配额恢复后亦无源）。
分钟层按预注册降级策略执行（领先缺→回退日线序；概念缺→剔除；<2 可评→回退），
全部计数上报。锚点对照按方向判定，不做逐点对齐。

对照锚点（V4.1 文档 §11/§12，会话端 ~390 概念，170 信号日）：
    V4.1 分钟版：0bp +108.94%/−19.46%/2.35 | 10bp +90.37%/−19.94%/2.08 | 30bp +57.96%/−20.96%/1.52
                 2025段 +21.69% / 2026段 +56.44%（10bp），换仓 61，止损 4
    纯日线排序：10bp +67.05%/−21.63%/1.67

用法：
    conda run -n resonance python work/backtest_v41.py
产出：outputs/v4/{nav_curves.csv, trades.csv, phase_table.csv}
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.v3 import MinuteBarProvider, V3Params  # noqa: E402
from work.backtest_v3 import evaluate, load_wide  # noqa: E402
from work.opt_v3 import load_production  # noqa: E402

OUT_DIR = config.OUTPUTS_DIR / "v4"
V41_PHASES = ["2025-09-22", "2025-09-23", "2025-09-24", "2025-09-25", "2025-09-26"]
V41_END = "2026-09-18"

# 生产栈 = V4.1 规格 + 2026-09-22 用户优化指令采纳项（outputs/v4/report_v42.md）：
# topk 2→3（A 轮）+ 动态半衰期基准全A→当日领先指数（B 轮）；跨日分钟窗（C 轮）证伪不采纳。
V41_STACK = V3Params(topk=3, daily_top=5, hl_source="leader")
DAILY_CTRL = V3Params(topk=3, hl_source="leader")              # 纯日线对照（同参数族，去分钟层）

ANCHORS = {
    "m_0bp": (1.0894, -0.1946, 2.35), "m_10bp": (0.9037, -0.1994, 2.08),
    "m_30bp": (0.5796, -0.2096, 1.52),
    "m_seg": (0.2169, 0.5644), "m_trades": (61, 4),
    "d_10bp": (0.6705, -0.2163, 1.67),
}


def build_provider() -> MinuteBarProvider:
    m5 = pd.read_parquet(config.CACHE_DIR / "minute5_bars.parquet")
    m5["datetime"] = pd.to_datetime(m5["datetime"])
    wide = m5.pivot(index="datetime", columns="symbol", values="close").sort_index()
    return MinuteBarProvider(wide)


def run_stack(close_all, concepts, p: V3Params, prov, start, cost) -> dict:
    """V4.1 池运行（分钟层按 p.daily_top 启停）。"""
    from resonance.v3 import V3Backtester
    bt = V3Backtester(close_all, concepts, broad_codes=list(config.V41_BROAD_POOL),
                      params=p.with_(cost_bp=cost),
                      minute_bars_provider=prov if p.daily_top > 0 else None)
    return bt.run(start, V41_END)


def stats_row(tag: str, out: dict) -> dict:
    from resonance.backtest import perf_stats
    from resonance.v3 import yearly_returns
    nav = out["nav_curve"]
    st = perf_stats(nav)
    yr = yearly_returns(nav)
    s = out["stats"]
    return {
        "tag": tag, "total": st["total_return"], "dd": st["max_drawdown"],
        "sharpe": st["sharpe"], "y2025": yr.get("2025", float("nan")),
        "y2026": yr.get("2026", float("nan")),
        "changes": s["position_changes"], "stops": s["stop_count"],
        "flat_pct": s["flat_days"] / max(len(nav), 1),
        "m_layer": s.get("minute_layer_days", 0),
        "m_fb_leader": s.get("minute_fallback_leader", 0),
        "m_fb_sparse": s.get("minute_fallback_sparse", 0),
        "m_excluded": s.get("minute_excluded", 0),
    }


def main() -> int:
    close_all, concepts = load_wide()
    prov = build_provider()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 96)
    print("一、V4.1 栈 vs 纯日线对照（相位 2025-09-22，0/10/30bp）vs V4.1 文档锚点")
    rows = []
    for tag, p in (("v41_minute", V41_STACK), ("daily_ctrl", DAILY_CTRL)):
        for cost in (0.0, 10.0, 30.0):
            out = run_stack(close_all, concepts, p, prov, "2025-09-22", cost)
            rows.append(stats_row(f"{tag}@{int(cost)}bp", out))
            globals()[f"_out_{tag}_{int(cost)}"] = out
    anchor_rows = [
        {"tag": "锚:v41@0bp", "total": 1.0894, "dd": -0.1946, "sharpe": 2.35,
         "y2025": float("nan"), "y2026": float("nan"), "changes": 61, "stops": 4},
        {"tag": "锚:v41@10bp", "total": 0.9037, "dd": -0.1994, "sharpe": 2.08,
         "y2025": 0.2169, "y2026": 0.5644, "changes": 61, "stops": 4},
        {"tag": "锚:daily@10bp", "total": 0.6705, "dd": -0.2163, "sharpe": 1.67,
         "y2025": float("nan"), "y2026": float("nan"), "changes": 63, "stops": 5},
    ]
    df = pd.DataFrame(rows + anchor_rows)
    print(df.to_string(index=False, float_format=lambda v: f"{v:.4f}"))

    print("\n" + "=" * 96)
    print("二、5 相位中位（V4.1 栈 vs 纯日线，10bp/0bp）")
    prow = []
    for start in V41_PHASES:
        for cost in (10.0, 0.0):
            for tag, p in (("v41_minute", V41_STACK), ("daily_ctrl", DAILY_CTRL)):
                out = run_stack(close_all, concepts, p, prov, start, cost)
                r = stats_row(f"{tag}", out)
                r.update(start=start, cost=f"{int(cost)}bp")
                prow.append(r)
    pdf = pd.DataFrame(prow)
    for cost in ("10bp", "0bp"):
        for tag in ("v41_minute", "daily_ctrl"):
            sub = [r for r in prow if r["cost"] == cost and r["tag"] == tag]
            print(f"[{tag}@{cost}] 总收益 {np.median([r['total'] for r in sub]):+.2%} | "
                  f"回撤 {np.median([r['dd'] for r in sub]):.2%} | "
                  f"夏普 {np.median([r['sharpe'] for r in sub]):.3f} | "
                  f"2025段 {np.median([r['y2025'] for r in sub]):+.2%} | "
                  f"2026段 {np.median([r['y2026'] for r in sub]):+.2%} | "
                  f"变更 {np.median([r['changes'] for r in sub]):.0f} | "
                  f"止损 {np.median([r['stops'] for r in sub]):.0f} | "
                  f"分钟层日 {np.median([r['m_layer'] for r in sub]):.0f} "
                  f"(回退L {np.median([r['m_fb_leader'] for r in sub]):.0f} / "
                  f"回退S {np.median([r['m_fb_sparse'] for r in sub]):.0f} / "
                  f"剔除 {np.median([r['m_excluded'] for r in sub]):.0f})")

    print("\n" + "=" * 96)
    print("三、同窗参照（V3 规格栈 / V3+防御层终栈，旧 13 池，10bp，5 相位中位）")
    from resonance.v3 import V3Backtester
    from resonance.backtest import perf_stats

    for name, p in (("V3规格栈", V3Params()), ("V3+防御层", V3Params(**load_production()))):
        tots, dds, shs = [], [], []
        for start in V41_PHASES:
            bt = V3Backtester(close_all, concepts, params=p.with_(cost_bp=10.0))
            out = bt.run(start, V41_END)
            st = perf_stats(out["nav_curve"])
            tots.append(st["total_return"]); dds.append(st["max_drawdown"]); shs.append(st["sharpe"])
        print(f"[{name}] 总收益 {np.median(tots):+.2%} | 回撤 {np.median(dds):.2%} | "
              f"夏普 {np.median(shs):.3f}")

    # 落盘
    curves = {}
    for tag in ("v41_minute", "daily_ctrl"):
        for cost in (0, 10, 30):
            curves[f"{tag}_{cost}bp"] = globals()[f"_out_{tag}_{cost}"]["nav_curve"]
    bench = close_all["883957.TI"].loc[curves["v41_minute_0bp"].index[0]:V41_END]
    curves["benchmark_quanA"] = bench / bench.iloc[0]
    pd.concat(curves, axis=1).to_csv(OUT_DIR / "nav_curves.csv")
    globals()["_out_v41_minute_10"]["trades"].to_csv(OUT_DIR / "trades.csv", index=False)
    pdf.to_csv(OUT_DIR / "phase_table.csv", index=False)
    print(f"\n[OK] 产出写入 {OUT_DIR}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
