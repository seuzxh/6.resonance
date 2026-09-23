"""实验：9 池指数逐一单独全流程共振回测（2026-09-22 用户指令澄清版）。

> 澄清口径：每个指数作为**唯一锚**跑**完整全流程**（V4.2 冻结管线：日线
> 上涨共振 Top5 → 当日尾盘 24bar 5min 纯分钟重排 → Top3 缓冲 → 5% 止损/
> 冷静 1 → T+1 收盘），各跑**完整周期**。与 exp_anchor.py（日线栈）的区别
> =启用分钟层。

预注册（playbook §五已过）：
- 覆盖（已探针）：8/9 锚 Top5 分钟覆盖 97-99%、领先 bar 全有；**微盘股
  无 HF 覆盖（0/131 日）→ 其分钟层结构性回退日线序，结果=纯日线栈**，
  单独标注不参与"分钟层增益"对比。
- 窗口双视图：完整周期 2025-01-02→2026-09-18（2025-09-22 前无分钟数据，
  分钟层按预注册降级回退日线序并计数）；分钟子窗 2025-09-22→2026-09-18
  （与 V4.x 历史数字同窗可比）。
- 判定：沿 exp_anchor 判据（排名主判据 10bp 中位总收益；梯队连贯性 L2；
  年度均衡；对照=动态锚·9池全流程）。

用法：
    conda run -n resonance python work/exp_anchor_full9.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.backtest import perf_stats  # noqa: E402
from resonance.v3 import MinuteBarProvider, V3Backtester, V3Params, yearly_returns  # noqa: E402
from work.backtest_v3 import load_wide  # noqa: E402

FULL = ("2025-01-02", ["2024-12-27", "2024-12-30", "2024-12-31", "2025-01-02", "2025-01-03"])
MWIN = ("2025-09-22", ["2025-09-22", "2025-09-23", "2025-09-24", "2025-09-25", "2025-09-26"])
END = "2026-09-18"
PARAMS = dict(topk=3, daily_top=5, hl_source="leader")


def run(close, concepts, pool, prov, phases, cost):
    tot, dd, sh, y25, y26, chg, stp, mfb = [], [], [], [], [], [], [], []
    for s in phases:
        bt = V3Backtester(close, concepts, broad_codes=pool,
                          params=V3Params(**PARAMS).with_(cost_bp=cost),
                          minute_bars_provider=prov)
        out = bt.run(s, END)
        st = perf_stats(out["nav_curve"])
        yr = yearly_returns(out["nav_curve"])
        tot.append(st["total_return"]); dd.append(st["max_drawdown"]); sh.append(st["sharpe"])
        y25.append(yr.get("2025", np.nan)); y26.append(yr.get("2026", np.nan))
        s_ = out["stats"]
        chg.append(s_["position_changes"]); stp.append(s_["stop_count"])
        mfb.append(s_.get("minute_fallback_leader", 0))
    return tot, dd, sh, y25, y26, chg, stp, mfb


def table(close, concepts, prov, win_label, phases):
    rows = []
    entries = [("动态锚·9池", list(config.V41_BROAD_POOL))] + [
        (name, [code]) for code, name in config.V41_BROAD_POOL.items()
    ]
    for label, pool in entries:
        tot, dd, sh, y25, y26, chg, stp, mfb = run(close, concepts, pool, prov, phases, 10.0)
        rows.append({
            "锚": label, "total@10bp": float(np.median(tot)),
            "dd@10bp": float(np.median(dd)), "sharpe": float(np.median(sh)),
            "y2025": float(np.median(y25)), "y2026": float(np.median(y26)),
            "changes": float(np.median(chg)), "stops": float(np.median(stp)),
            "分钟回退L": float(np.median(mfb)),
            "_phases": tot,
        })
    df = pd.DataFrame(rows).sort_values("total@10bp", ascending=False).reset_index(drop=True)
    print(f"\n===== {win_label}（10bp，5 相位中位）=====")
    print(df.drop(columns=["_phases"]).to_string(index=False, float_format=lambda v: f"{v:+,.3f}"))
    return df


def main() -> int:
    close, concepts = load_wide()
    m5 = pd.read_parquet(config.CACHE_DIR / "minute5_bars.parquet")
    m5["datetime"] = pd.to_datetime(m5["datetime"])
    prov = MinuteBarProvider(m5.pivot(index="datetime", columns="symbol", values="close").sort_index())
    df_full = table(close, concepts, prov, f"完整周期 {FULL[0]}→{END}（09-22 前分钟层回退）", FULL[1])
    df_mwin = table(close, concepts, prov, f"分钟子窗 {MWIN[0]}→{END}", MWIN[1])
    outdir = config.OUTPUTS_DIR / "exp_anchor"
    df_full.drop(columns=["_phases"]).to_csv(outdir / "full9_cycle.csv", index=False)
    df_mwin.drop(columns=["_phases"]).to_csv(outdir / "full9_mwin.csv", index=False)
    print(f"\n[OK] {outdir}/full9_cycle.csv / full9_mwin.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
