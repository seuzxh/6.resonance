"""实验：固定锚共振对比——哪个指数作为共振锚定效果最好（2026-09-22 用户指令）。

预注册（docs/experiment-playbook.md §五检查清单已过，判据本节先于回测固定）：
- 假设机制：若某风格的"概念共振结构"最具持续性/信息量，固定锚定它可能
  优于动量动态换锚；反之则佐证动态选择的必要性。
- 设计：锚点 ∈ 16 个可用风格指数（全A/微盘/科创综指/创业板/科创50/上证50/
  北证50/中证2000/沪深300/中证500/中证1000/国证2000/红利/上证指数/深证成指/
  大盘股）；实现=单元素指数池（引擎恒选该指数为领先：闸门/上涨共振/
  半衰期基准全部锚定于它，与 L6 语义一致）。对照=动量动态选锚（9池/13池
  生产池）。其余参数全部 V4.2 冻结值（topk3/最短持有3/止损5%/T+1），
  **日线栈（daily_top=0）**：分钟层刻意排除——iFinD 配额耗尽，且不同锚的
  Top5 需求不同导致分钟覆盖不可比（L7 数据混杂）。
- 窗口/相位：2025-01-02→2026-09-18（日线数据完整），5 相位起点
  {2024-12-27,12-30,12-31,2025-01-02,01-03} 取中位；成本 10bp 决策 + 0bp 参考。
- 判定：①排名主判据=中位总收益@10bp，辅助=回撤/夏普/年度均衡
  （min(y25,y26)，L2）；②相位一致性：≥4/5 相位排名前半才算稳健（防孤峰）；
  ③"固定锚替换动态锚"的生产级结论需过标准判据（Δ≥+2pp、≥4/5 相位、
  Δ回撤≥−1pp）且过 L1 挤占检查（固定锚=放弃风格轮动，须与动态锚差距
  显著为正才有意义）。

用法：
    conda run -n resonance python work/exp_anchor.py
产出：outputs/exp_anchor/report.md（表格部分）+ 控制台
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
from work.backtest_v3 import PHASES, load_wide  # noqa: E402

END = "2026-09-18"
ANCHORS = {
    "883957.TI": "同花顺全A", "700050.TI": "微盘股", "000680.SH": "科创综指",
    "399006.SZ": "创业板指", "000688.SH": "科创50", "000016.SH": "上证50",
    "899050.BJ": "北证50", "932000.CSI": "中证2000", "000300.SH": "沪深300",
    "000905.SH": "中证500", "000852.SH": "中证1000", "399303.SZ": "国证2000",
    "000015.SH": "红利指数", "000001.SH": "上证指数", "399001.SZ": "深证成指",
    "883417.TI": "大盘股",
}
POOL9 = list(config.V41_BROAD_POOL)
POOL13 = ["883957.TI", "700050.TI", "000680.SH", "399006.SZ", "000688.SH", "000016.SH",
          "899050.BJ", "932000.CSI", "000300.SH", "000905.SH", "000852.SH", "399303.SZ",
          "000015.SH", "000001.SH", "399001.SZ"]
# V4.2 冻结值减分钟层（daily_top=0）；hl_source=leader=锚定指数自身（固定锚下
# 即该指数，动态对照下即当日领先——两侧语义一致）
BASE = dict(topk=3, daily_top=0, hl_source="leader")


def run(pool, cost):
    tot, dd, sh, y25, y26, chg, stp, flat, gate_days = [], [], [], [], [], [], [], [], []
    for s in PHASES:
        bt = V3Backtester(CLOSE, CONCEPTS, broad_codes=pool,
                          params=V3Params(**BASE).with_(cost_bp=cost))
        out = bt.run(s, END)
        st = perf_stats(out["nav_curve"])
        yr = yearly_returns(out["nav_curve"])
        tot.append(st["total_return"]); dd.append(st["max_drawdown"]); sh.append(st["sharpe"])
        y25.append(yr.get("2025", np.nan)); y26.append(yr.get("2026", np.nan))
        s_ = out["stats"]
        chg.append(s_["position_changes"]); stp.append(s_["stop_count"])
        flat.append(s_["flat_days"] / max(len(out["nav_curve"]), 1))
        gate_days.append(len(s_["leaders"]))
    return tot, dd, sh, y25, y26, chg, stp, flat, gate_days


def row(label, cost=10.0):
    pool = [label] if label in ANCHORS else POOL9 if label == "DYN9" else POOL13
    tot, dd, sh, y25, y26, chg, stp, flat, gd = run(pool, cost)
    tot0, *_ = run(pool, 0.0)[0:1]
    return {
        "anchor": ANCHORS.get(label, "动态锚·9池" if label == "DYN9" else "动态锚·13池"),
        "total@10bp": float(np.median(tot)), "dd@10bp": float(np.median(dd)),
        "sharpe": float(np.median(sh)),
        "y2025": float(np.median(y25)), "y2026": float(np.median(y26)),
        "min_year": float(np.median(np.minimum(y25, y26))),
        "total@0bp": float(np.median(tot0)),
        "changes": float(np.median(chg)), "stops": float(np.median(stp)),
        "flat%": float(np.median(flat)) * 100,
        "gate_days": float(np.median(gd)),
        "_phases": tot,
    }


def main() -> int:
    global CLOSE, CONCEPTS
    CLOSE, CONCEPTS = load_wide()
    rows = [row("DYN9"), row("DYN13")]
    for code in ANCHORS:
        if code in CLOSE.columns:
            rows.append(row(code))
        else:
            print(f"[skip] {code} 无日线数据")
    df = pd.DataFrame(rows).sort_values("total@10bp", ascending=False).reset_index(drop=True)
    df["rank"] = df.index + 1
    show = df.drop(columns=["_phases"])
    print(show.to_string(index=False, float_format=lambda v: f"{v:,.3f}"))

    print("\n相位一致性（各锚 5 相位总收益@10bp）：")
    for _, r in df.iterrows():
        print(f"  {r['anchor']:10s} {[f'{t:+.0%}' for t in r['_phases']]}")
    (config.OUTPUTS_DIR / "exp_anchor").mkdir(parents=True, exist_ok=True)
    df.drop(columns=["_phases"]).to_csv(config.OUTPUTS_DIR / "exp_anchor" / "table.csv", index=False)
    print(f"\n[OK] {config.OUTPUTS_DIR}/exp_anchor/table.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
