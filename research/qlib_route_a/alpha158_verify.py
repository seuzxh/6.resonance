"""A158-V1 三臂对照（resonance env，V4.4 开盘口径）——预注册判决。

预注册设计（判据先冻结）：docs/research/alpha158-verify-plan.md §三。
臂：A=F8 基线；B=3 因子 LGBM 重排；C=BETA20 单因子重排。
判据：成功=中位提升>0 且 ≥4/5 相位；反效=中位差≤−2pp；无效=其余；
孤峰 L2 检查。test 段 5 相位，低检验力已标注。

用法：conda run -n resonance python research/qlib_route_a/alpha158_verify.py
产出：outputs/qlib_ml/a158_phases.csv + 判决打印 + a158_report.md
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from resonance import config
from resonance.backtest import perf_stats
from resonance.v3 import MinuteBarProvider, V3Backtester, V3Params

END = "2026-09-23"
COST_BP = 10.0
EXEC = "open"
PHASES = ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"]


def make_score_post_rank(score_tbl: dict, tag: str):
    """镜像 F8' 降级链的重排钩子：score_tbl[(date, concept)] = 分值。"""
    stats = {f"{tag}_days": 0, f"{tag}_excluded": 0,
             f"{tag}_fallback_sparse": 0}

    def post_rank(ranking: pd.DataFrame, date) -> pd.DataFrame:
        key = str(pd.Timestamp(date).date())
        top = ranking.head(5)
        scored = [(score_tbl[(key, r.concept)], r.concept)
                  for r in top.itertuples(index=False)
                  if (key, r.concept) in score_tbl]
        stats[f"{tag}_days"] += 1
        stats[f"{tag}_excluded"] += len(top) - len(scored)
        if len(scored) < 2:
            stats[f"{tag}_fallback_sparse"] += 1
            return ranking
        scored.sort(reverse=True)
        return pd.DataFrame({"concept": [c for _, c in scored],
                             "score": [s for s, _ in scored]})

    return post_rank, stats


def main() -> int:
    bars = pd.read_parquet(config.CACHE_DIR / "daily_bars.parquet")
    catalog = pd.read_csv(config.DATA_DIR / "concept_catalog.csv")
    close_all = bars.pivot(index="date", columns="symbol", values="close").sort_index()
    open_all = bars.pivot(index="date", columns="symbol", values="open").sort_index()
    for w in (close_all, open_all):
        w.index = pd.to_datetime(w.index)
    concepts = [c for c in catalog["code"] if c in close_all.columns]
    m5 = pd.read_parquet(config.CACHE_DIR / "minute5_bars.parquet")
    prov = MinuteBarProvider(m5.pivot(index="datetime", columns="symbol",
                                      values="close").sort_index())
    out_ml = config.OUTPUTS_DIR / "qlib_ml"
    pred = pd.read_parquet(out_ml / "pred_a158_3f.parquet")
    feats = pd.read_parquet(out_ml / "alpha158_features.parquet")
    beta = feats["BETA20"]
    beta_tbl = {(str(ts.date()), c): float(v)
                for (ts, c), v in beta.items()}
    pred_tbl = {(d, c): float(p) for d, c, p in zip(pred["date"], pred["concept"],
                                                    pred["pred"])}

    rows = []
    arm_stats = {}
    for arm in ("A_f8", "B_lgbm3", "C_beta20"):
        for start in PHASES:
            if arm == "A_f8":
                p = V3Params(topk=3, daily_top=5, hl_source="leader",
                             exec_price=EXEC, cost_bp=COST_BP)
                bt = V3Backtester(close_all, concepts,
                                  broad_codes=list(config.V43_ANCHOR_POOL),
                                  params=p, minute_bars_provider=prov,
                                  open_all=open_all)
                extra = {}
            else:
                tbl = pred_tbl if arm == "B_lgbm3" else beta_tbl
                post_rank, extra = make_score_post_rank(tbl, arm)
                p = V3Params(topk=3, hl_source="leader",
                             exec_price=EXEC, cost_bp=COST_BP)
                bt = V3Backtester(close_all, concepts,
                                  broad_codes=list(config.V43_ANCHOR_POOL),
                                  params=p, post_rank=post_rank,
                                  open_all=open_all)
            out = bt.run(start, END)
            st = perf_stats(out["nav_curve"])
            rows.append({"arm": arm, "start": start,
                         "total": st["total_return"], "dd": st["max_drawdown"],
                         "sharpe": st["sharpe"],
                         "changes": out["stats"]["position_changes"],
                         **extra})
            for k, v in extra.items():
                arm_stats[k] = arm_stats.get(k, 0) + v
    df = pd.DataFrame(rows)
    df.to_csv(out_ml / "a158_phases.csv", index=False)

    med = {}
    for arm in ("A_f8", "B_lgbm3", "C_beta20"):
        sub = df[df["arm"] == arm]
        med[arm] = (float(np.median(sub["total"])),
                    float(np.median(sub["dd"])),
                    float(np.median(sub["sharpe"])))
    print(df.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    print("\n===== 三臂 5 相位中位（10bp，V4.4 开盘口径，test 段）=====")
    for arm in ("A_f8", "B_lgbm3", "C_beta20"):
        print(f"[{arm}] 总收益 {med[arm][0]:+.2%} | 回撤 {med[arm][1]:.2%} | "
              f"夏普 {med[arm][2]:.2f}")

    verdicts = {}
    for arm, label in (("B_lgbm3", "主处理 B（3因子LGBM）"),
                       ("C_beta20", "副处理 C（BETA20 单因子）")):
        diff_pp = (med[arm][0] - med["A_f8"][0]) * 100
        win = int(sum(b >= a for b, a in zip(
            df[df["arm"] == arm]["total"], df[df["arm"] == "A_f8"]["total"])))
        v = "success" if (win >= 4 and diff_pp > 0) else (
            "harmful" if diff_pp <= -2 else "neutral")
        verdicts[arm] = (v, diff_pp, win)
        print(f"\n{label} vs A：{v.upper()}（中位差 {diff_pp:+.2f}pp，"
              f"胜出相位 {win}/5）")

    lines = ["# A158-V1 报告（预注册判据：alpha158-verify-plan.md §三）", "",
             f"三臂 5 相位中位（10bp、V4.4 开盘、test 段 2026-08-03→{END}）：", "",
             "| 臂 | 总收益中位 | 回撤 | 夏普 |", "|---|---:|---:|---:|"]
    for arm in ("A_f8", "B_lgbm3", "C_beta20"):
        lines.append(f"| {arm} | {med[arm][0]:+.2%} | {med[arm][1]:.2%} "
                     f"| {med[arm][2]:.2f} |")
    lines += ["", "## 判决（预注册判据）", ""]
    for arm, label in (("B_lgbm3", "B（3因子LGBM）vs A"),
                       ("C_beta20", "C（BETA20 单因子）vs A")):
        v, d, w = verdicts[arm]
        lines.append(f"- {label}：**{v.upper()}**（中位差 {d:+.2f}pp，"
                     f"胜出 {w}/5）")
    lines += ["", f"相位明细（孤峰 L2 检查用）：", "",
              "| 臂 | 相位 | 总收益 |", "|---|---|---:|"]
    for _, r in df.iterrows():
        lines.append(f"| {r['arm']} | {r['start']} | {r['total']:+.2%} |")
    lines += ["", f"降级计数（累计 5 相位）：{arm_stats}", "",
              "措辞边界：样本内上界（L3）、单一 regime、389/529 覆盖选择偏差、"
              "幸存者目录、指数不可交易；test 段 36 日低检验力，'无效'判定"
              "置信有限。"]
    (out_ml / "a158_report.md").write_text("\n".join(lines))
    print(f"\n[OK] → {out_ml}/a158_phases.csv / a158_report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
