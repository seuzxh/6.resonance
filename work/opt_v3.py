"""V3 十轮参数优化运行器（协议：docs/v3-optimization-design.md §二/§三，预注册）。

用法：
    conda run -n resonance python work/opt_v3.py --round N     # 运行第 N 轮网格
    conda run -n resonance python work/opt_v3.py --adopt '{"stop_loss": 0.04}'
                                                               # 采纳后更新生产栈

生产栈状态存 outputs/v3/production.json（起点 = V3 默认参数；每轮采纳后增量
更新并随报告提交）。判据引用协议 §二原文，verdict 仅作判定辅助，最终采纳
在 round 报告中逐条引用判据记录。

产出：outputs/v3/rounds/round_N.md + 控制台表格。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.exec_minute import MinutePrices, build_minute_wide  # noqa: E402
from resonance.v3 import V3Params  # noqa: E402
from work.backtest_v3 import PHASES, evaluate, load_wide  # noqa: E402

OUT_DIR = config.OUTPUTS_DIR / "v3"
ROUNDS_DIR = OUT_DIR / "rounds"
PROD_FILE = OUT_DIR / "production.json"

# 冻结终栈（R9 采纳，outputs/v3/rounds/round_9.md；production.json 缺失时的兜底，
# 保证全新检出可复现——json 被-gitignore，栈态以代码+报告为准）
FROZEN_FINAL = {"defense_dd": 0.04, "defense_strong": True}

# R10 冻结复核：终栈交互重验证网格（单轴扰动 vs 终栈）
R10_GRID = [
    ("defense_off", {"defense_dd": 0.0}),
    ("defense_weak", {"defense_strong": False}),
    ("x4", {"stop_loss": 0.04}), ("x6", {"stop_loss": 0.06}),
    ("cd0", {"cooldown": 0}), ("cd2", {"cooldown": 2}),
    ("topk2", {"topk": 2}), ("topk4", {"topk": 4}),
    ("P5", {"pos_window": 5}),
    ("W8", {"res_window": 8}), ("W12", {"res_window": 12}),
    ("W14", {"res_window": 14}), ("W15", {"res_window": 15}),
    ("L15", {"leader_window": 15}),
    ("H2", {"min_hold": 2}), ("H5", {"min_hold": 5}),
]

# ---- 预注册网格（协议 §三）----
ROUNDS: dict[int, list[tuple[str, dict]]] = {
    1: [("x3", {"stop_loss": 0.03}), ("x4", {"stop_loss": 0.04}),
        ("x5(规格)", {}), ("x6", {"stop_loss": 0.06}), ("x8", {"stop_loss": 0.08}),
        ("x10", {"stop_loss": 0.10}),
        ("x5_cd0", {"cooldown": 0}), ("x5_cd2", {"cooldown": 2}),
        ("x4_cd0", {"stop_loss": 0.04, "cooldown": 0}),
        ("x4_cd2", {"stop_loss": 0.04, "cooldown": 2}),
        ("x6_cd0", {"stop_loss": 0.06, "cooldown": 0}),
        ("x6_cd2", {"stop_loss": 0.06, "cooldown": 2})],
    3: [("topk1", {"topk": 1}), ("topk2", {"topk": 2}), ("topk3(规格)", {}),
        ("topk4", {"topk": 4}), ("topk5", {"topk": 5})],
    4: [("hl(4,3,2)", {"half_lives": (4, 3, 2)}), ("hl(6,4,2)", {"half_lives": (6, 4, 2)}),
        ("tiers(3,5)%", {"dd_tiers": (0.03, 0.05)}),
        ("hl(4,3,2)+tiers(3,5)", {"half_lives": (4, 3, 2), "dd_tiers": (0.03, 0.05)})],
    5: [("W7", {"res_window": 7}), ("W8", {"res_window": 8}),
        ("W10(规格)", {}), ("W12", {"res_window": 12}), ("W15", {"res_window": 15})],
    6: [("L5", {"leader_window": 5}), ("L10(规格)", {}),
        ("L15", {"leader_window": 15}), ("L20", {"leader_window": 20})],
    7: [("P2", {"pos_window": 2}), ("P3(规格)", {}), ("P5", {"pos_window": 5})],
    8: [("H2", {"min_hold": 2}), ("H3(规格)", {}), ("H5", {"min_hold": 5})],
    9: [("strong4%", {"defense_dd": 0.04, "defense_strong": True}),
        ("strong2%", {"defense_dd": 0.02, "defense_strong": True}),
        ("weak4%", {"defense_dd": 0.04, "defense_strong": False}),
        ("weak2%", {"defense_dd": 0.02, "defense_strong": False})],
}

# R2 专用：1 年窗 + 分钟层（协议 §三.R2）
R2_WINDOW = ("2025-10-30", "2026-09-18")
R2_PHASES = ["2025-10-30", "2025-10-31", "2025-11-03", "2025-11-04", "2025-11-05"]
R2_GRID = [("close_x4", {"stop_loss": 0.04, "stop_mode": "close"}),
           ("close_x5", {"stop_loss": 0.05, "stop_mode": "close"}),
           ("close_x6", {"stop_loss": 0.06, "stop_mode": "close"}),
           ("close_x8", {"stop_loss": 0.08, "stop_mode": "close"}),
           ("minute_x4", {"stop_loss": 0.04, "stop_mode": "minute"}),
           ("minute_x5", {"stop_loss": 0.05, "stop_mode": "minute"}),
           ("minute_x6", {"stop_loss": 0.06, "stop_mode": "minute"}),
           ("minute_x8", {"stop_loss": 0.08, "stop_mode": "minute"})]


def load_production() -> dict:
    if PROD_FILE.exists():
        d = json.loads(PROD_FILE.read_text())
        for k in ("half_lives", "dd_tiers"):
            if k in d:
                d[k] = tuple(d[k])
        return d
    return dict(FROZEN_FINAL)


def save_production(d: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PROD_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2))


def prod_params(extra: dict | None = None) -> V3Params:
    kw = {**load_production(), **(extra or {})}
    return V3Params(**kw)


def run_phases(close_all, concepts, p: V3Params, phases, cost=10.0, end=None, mp=None):
    """一组相位 → 每相位统计 dict 列表（同序于 phases）。"""
    return [evaluate(close_all, concepts, p.with_(cost_bp=cost), s, end=end,
                     minute_prices=mp) for s in phases]


def med(rows, key):
    return float(np.median([r[key] for r in rows]))


def verdict(variant_rows, prod_rows):
    """协议 §二判据 → (verdict, Δ中位总收益, 回撤改善中位, 胜相位数)。"""
    d_tot = [v["total"] - b["total"] for v, b in zip(variant_rows, prod_rows)]
    d_dd = [v["dd"] - b["dd"] for v, b in zip(variant_rows, prod_rows)]
    m_tot, m_dd = float(np.median(d_tot)), float(np.median(d_dd))
    wins = sum(1 for d in d_tot if d >= 0)
    if m_tot >= 0.02 and wins >= 4 and m_dd >= -0.01:
        v = "ADOPT-CANDIDATE"
    elif m_tot < -0.02:
        v = "HARMFUL"
    elif abs(m_tot) <= 0.02 or wins < 4:
        v = "NEUTRAL"
    else:
        v = "NEUTRAL(回撤代价)"
    return v, m_tot, m_dd, wins


def table_row(label, rows10, rows0, prod_rows10, note=""):
    v, m_tot, m_dd, wins = verdict(rows10, prod_rows10)
    return {
        "variant": label,
        "med_total@10bp": med(rows10, "total"), "med_dd@10bp": med(rows10, "dd"),
        "med_sharpe@10bp": med(rows10, "sharpe"),
        "med_y2025": med(rows10, "y2025"), "med_y2026": med(rows10, "y2026"),
        "med_total@0bp": med(rows0, "total"),
        "changes": med(rows10, "changes"), "stops": med(rows10, "stops"),
        "Δtotal": m_tot, "Δdd": m_dd, "wins": f"{wins}/5",
        "verdict": v, "note": note,
    }


def fmt_df(df: pd.DataFrame) -> str:
    pct = ["med_total@10bp", "med_dd@10bp", "med_y2025", "med_y2026",
           "med_total@0bp", "Δtotal", "Δdd"]
    return df.to_string(index=False, formatters={c: (lambda v: f"{v:+.2%}") for c in pct})


def round_report(n: int, title: str, df: pd.DataFrame, prod_desc: str,
                 decision: str) -> str:
    lines = [
        f"# R{n} {title}", "",
        f"> 生产栈：{prod_desc} ｜ 判据：docs/v3-optimization-design.md §二（预注册）",
        "",
        "```", fmt_df(df), "```", "",
        f"## 判定", "", decision, "",
    ]
    return "\n".join(lines)


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--adopt", type=str, default=None,
                    help='采纳的参数覆盖 JSON，如 \'{"stop_loss": 0.04}\'')
    args = ap.parse_args()

    if args.adopt is not None:
        prod = load_production()
        upd = json.loads(args.adopt)
        prod.update(upd)
        save_production(prod)
        print(f"[OK] 生产栈更新：{prod}")
        return 0

    ROUNDS_DIR.mkdir(parents=True, exist_ok=True)
    close_all, concepts = load_wide()

    if args.round == 2:
        return round2(close_all, concepts)
    if args.round == 10:
        return round10(close_all, concepts)

    grid = ROUNDS[args.round]
    prod = prod_params()
    prod_rows = {c: run_phases(close_all, concepts, prod, PHASES, cost=c)
                 for c in (0.0, 10.0)}
    rows = []
    for label, over in grid:
        p = prod_params(over)
        r10 = run_phases(close_all, concepts, p, PHASES, cost=10.0)
        r0 = run_phases(close_all, concepts, p, PHASES, cost=0.0)
        rows.append(table_row(label, r10, r0, prod_rows[10.0]))
    df = pd.DataFrame(rows)
    print(fmt_df(df))
    return 0


def round2(close_all, concepts) -> int:
    """R2：止损执行粒度 close vs minute（1 年窗，协议 §三.R2）。"""
    bars = pd.read_parquet(config.CACHE_DIR / "daily_bars.parquet")
    close_wide = bars.pivot(index="date", columns="symbol", values="close").sort_index()
    open_wide = bars.pivot(index="date", columns="symbol", values="open").sort_index()
    m5 = pd.read_parquet(config.CACHE_DIR / "minute5_bars.parquet")
    m5["datetime"] = pd.to_datetime(m5["datetime"])
    minute_wide = build_minute_wide(m5)
    mp = MinutePrices(close_wide, open_wide, minute_wide)

    prod = prod_params()
    rows = []
    res = {}
    for label, over in R2_GRID:
        p = prod_params(over)
        r10 = run_phases(close_all, concepts, p, R2_PHASES, cost=10.0,
                         end=R2_WINDOW[1], mp=mp if p.stop_mode == "minute" else None)
        res[label] = r10
    # 对照 = 同窗 close_x5（生产止损规则收盘粒度）
    base10 = res["close_x5"]
    for label, over in R2_GRID:
        r = res[label]
        d_tot = [v["total"] - b["total"] for v, b in zip(r, base10)]
        d_dd = [v["dd"] - b["dd"] for v, b in zip(r, base10)]
        rows.append({
            "variant": label,
            "med_total@10bp": med(r, "total"), "med_dd@10bp": med(r, "dd"),
            "med_sharpe@10bp": med(r, "sharpe"),
            "stops": med(r, "stops"),
            "degraded": med(r, "degraded"),
            "Δtotal_vs_close5": float(np.median(d_tot)),
            "Δdd_vs_close5": float(np.median(d_dd)),
            "wins": f"{sum(1 for d in d_dd if d >= 0)}/5(dd)",
        })
    df = pd.DataFrame(rows)
    print(fmt_df(df))
    # 分钟 vs 收盘同 x 增量（粒度剂量对照）
    print("\n分钟 vs 收盘（同 x，粒度增量）：")
    for x in ("4", "5", "6", "8"):
        rm, rc = res[f"minute_x{x}"], res[f"close_x{x}"]
        d_dd = [v["dd"] - b["dd"] for v, b in zip(rm, rc)]
        d_tot = [v["total"] - b["total"] for v, b in zip(rm, rc)]
        print(f"  x={x}%: 中位Δdd {np.median(d_dd):+.2%} | 中位Δtotal {np.median(d_tot):+.2%} | "
              f"止损数 min/close {med(rm,'stops'):.0f}/{med(rc,'stops'):.0f}")
    return 0


def round10(close_all, concepts) -> int:
    """R10 冻结复核：终栈交互重验证 + 三档成本与择优分数总表（协议 §三.R10）。"""
    prod = prod_params()
    print(f"冻结终栈：{prod}\n")
    print("== A. 终栈交互重验证（单轴扰动，10bp，5 相位中位）==")
    base = run_phases(close_all, concepts, prod, PHASES, cost=10.0)
    rows = [{"variant": "FINAL", **_med_row(base)}]
    for label, over in R10_GRID:
        r = run_phases(close_all, concepts, prod_params(over), PHASES, cost=10.0)
        row = {"variant": label, **_med_row(r)}
        row["Δtotal"] = row["med_total@10bp"] - rows[0]["med_total@10bp"]
        row["Δdd"] = row["med_dd@10bp"] - rows[0]["med_dd@10bp"]
        rows.append(row)
    df = pd.DataFrame(rows)
    pct = ["med_total@10bp", "med_dd@10bp", "med_y2025", "med_y2026", "Δtotal", "Δdd"]
    print(df.to_string(index=False, formatters={c: (lambda v: f"{v:+.2%}") for c in pct}))

    print("\n== B. 三档成本与择优分数（5 相位中位；分数=med(total@30bp)+min(y25,y26)@10bp−|dd@10bp|）==")
    from resonance.v3 import V3Params
    for name, p in (("V3规格栈", V3Params()), ("V3+防御层(终栈)", prod)):
        out = {}
        for cost in (0.0, 10.0, 30.0):
            out[cost] = run_phases(close_all, concepts, p, PHASES, cost=cost)
            print(f"{name} @{int(cost):2d}bp: " + _fmt_med(out[cost]))
        score = (med(out[30.0], "total")
                 + min(med(out[10.0], "y2025"), med(out[10.0], "y2026"))
                 - abs(med(out[10.0], "dd")))
        print(f"  → 择优分数: {score:+.4f}\n")
    return 0


def _med_row(rows) -> dict:
    return {
        "med_total@10bp": med(rows, "total"), "med_dd@10bp": med(rows, "dd"),
        "med_sharpe@10bp": med(rows, "sharpe"),
        "med_y2025": med(rows, "y2025"), "med_y2026": med(rows, "y2026"),
        "changes": med(rows, "changes"), "stops": med(rows, "stops"),
    }


def _fmt_med(rows) -> str:
    return (f"total {med(rows,'total'):+.2%} | dd {med(rows,'dd'):+.2%} | "
            f"sharpe {med(rows,'sharpe'):.3f} | y25 {med(rows,'y2025'):+.2%} | "
            f"y26 {med(rows,'y2026'):+.2%} | chg {med(rows,'changes'):.0f} | "
            f"stops {med(rows,'stops'):.0f}")


if __name__ == "__main__":
    raise SystemExit(main())
