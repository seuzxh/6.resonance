"""历史扩展验证：2022-01→2024-12 纯日线共振（2026-09-23 用户指令）。

> 场景：无分钟数据的更早年份 → 日线栈（daily_top=0），三锚动选（V4.3 生产
> 口径减分钟层）+ 深证成指单锚 + 9 池动选对照。窗口 2022-01-04→2024-12-31，
> 5 相位起点（2022 首个交易日前 4 个相邻日），10bp 决策 + 0bp 参考。
> ⚠️ 幸存者偏差（目录为 2026 快照回填，2022-2024 已消亡概念缺席）与
> 参数时段外推双重警示：本验证是"时间外推 + 幸存者上偏"的混合口径，
> 结果偏乐观解读；结论以年度结构与相对基准超额为主。

用法：
    conda run -n resonance python work/exp_hist_2022.py
产出：outputs/exp_hist_2022/report 表格（控制台 + csv）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.backtest import perf_stats  # noqa: E402
from resonance.v3 import V3Backtester, V3Params, V3Signals, yearly_returns  # noqa: E402
from work.backtest_v3 import load_wide  # noqa: E402

END = "2024-12-31"
TRIO = ["399001.SZ", "399303.SZ", "000688.SH"]
BASE = dict(topk=3, daily_top=0, hl_source="leader")


def main() -> int:
    close_all, concepts = load_wide()
    cal = close_all.index
    anchor_pos = cal.searchsorted(pd.Timestamp("2022-01-04"))
    phases = [str(cal[anchor_pos - 4 + k].date()) for k in range(5)]
    print(f"窗口 2022-01-04→{END}，5 相位：{phases}")

    # 有效概念规模（逐年中位候选数，幸存者口径下限）
    sig_probe = V3Signals(close_all, concepts, TRIO, "883957.TI", V3Params(**BASE))
    for y in (2022, 2023, 2024):
        ii = [i for i, d in enumerate(cal) if d.year == y
              and sig_probe.has_leader[i] and sig_probe.gate[i]]
        n = [len(sig_probe.ranking(i)) for i in ii[::10]] if ii else [0]
        print(f"{y} 有效信号日 {len(ii)} | 抽样候选概念中位 {int(np.median(n))}")

    rows = []
    stacks = [("三锚动选(D3)", TRIO), ("深证成指单锚(C1)", ["399001.SZ"]),
              ("9池动选(A9)", list(config.V41_BROAD_POOL))]
    for label, pool in stacks:
        for cost in (10.0, 0.0):
            tot, dd, sh = [], [], []
            yrs_acc: dict[str, list] = {}
            chg, stp = [], []
            for s in phases:
                bt = V3Backtester(close_all, concepts, broad_codes=pool,
                                  params=V3Params(**BASE).with_(cost_bp=cost))
                out = bt.run(s, END)
                st = perf_stats(out["nav_curve"])
                yr = yearly_returns(out["nav_curve"])
                tot.append(st["total_return"]); dd.append(st["max_drawdown"]); sh.append(st["sharpe"])
                for y, v in yr.items():
                    yrs_acc.setdefault(y, []).append(v)
                chg.append(out["stats"]["position_changes"]); stp.append(out["stats"]["stop_count"])
            row = {"栈": label, "cost": f"{int(cost)}bp",
                   "总收益": float(np.median(tot)), "回撤": float(np.median(dd)),
                   "夏普": float(np.median(sh))}
            for y in ("2022", "2023", "2024"):
                row[y] = float(np.median(yrs_acc.get(y, [np.nan])))
            row["换仓"] = float(np.median(chg)); row["止损"] = float(np.median(stp))
            rows.append(row)
    df = pd.DataFrame(rows)
    print(df.to_string(index=False, float_format=lambda v: f"{v:+,.3f}"))

    bench = close_all["883957.TI"].loc["2022-01-04":END]
    print(f"\n对照（同窗买入持有）：同花顺全A {bench.iloc[-1]/bench.iloc[0]-1:+.1%} | "
          f"沪深300 {close_all['000300.SH'].loc['2022-01-04':END].iloc[-1]/close_all['000300.SH'].loc['2022-01-04':END].iloc[0]-1:+.1%} | "
          f"创业板指 {close_all['399006.SZ'].loc['2022-01-04':END].iloc[-1]/close_all['399006.SZ'].loc['2022-01-04':END].iloc[0]-1:+.1%} | "
          f"科创50 {close_all['000688.SH'].loc['2022-01-04':END].iloc[-1]/close_all['000688.SH'].loc['2022-01-04':END].iloc[0]-1:+.1%}")
    outdir = config.OUTPUTS_DIR / "exp_hist_2022"
    outdir.mkdir(parents=True, exist_ok=True)
    df.to_csv(outdir / "table.csv", index=False)
    print(f"[OK] {outdir}/table.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
