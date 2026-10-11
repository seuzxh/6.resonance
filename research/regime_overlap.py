"""R4 交叉验证 + 增量对照：第一道 Go/No-Go（§四 R4）。

预注册：docs/research/regime-detection-plan.md §四 R4 冻结细节。
危险期集合 D = 全A HMM 全样本高波状态日；对照集合 G（双锚闸门拦截日）、
H（领先指数高风险定档日）、F（九特征事件）。判据 3（确认率 ≥70%）与
判据 5（亏损覆盖 ≥30% 且 D 内持仓日占比 ≥20%）在本脚本裁决。

用法：conda run -n resonance python research/regime_overlap.py
产出：outputs/regime/overlap_summary.csv / f_events_state.csv / r4_verdict.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import regime_common as rc  # noqa: E402


def main() -> None:
    feats = pd.read_parquet(rc.OUT_DIR / "features.parquet")
    hmm = pd.read_csv(rc.OUT_DIR / "hmm_states.csv", parse_dates=["date"])

    main_states = hmm[hmm["symbol"] == rc.MAIN_CODE].set_index("date").sort_index()
    k = int(main_states["state"].max()) + 1
    D = main_states["state"] == (k - 1)                 # 高波状态日

    # ---- 集合 G / H：双锚闸门与高风险定档（v3 口径复算） ----
    closes = feats.pivot(index="date", columns="symbol", values="close")
    dual = closes[rc.GATE_DUAL].dropna()
    st = rc.dual_anchor_states(dual)
    st = st.reindex(main_states.index)
    G = ~st["gate"].fillna(False).astype(bool)          # 闸门拦截日（含无领先日）
    H = (st["hl"] == 2)

    # ---- 判据 3：HMM 切换日 ↔ ruptures 中心档断点（±5 交易日） ----
    labels = main_states["state"].to_numpy()
    switch = np.where(np.diff(labels) != 0)[0] + 1       # 新状态首日下标
    segs = pd.read_csv(rc.OUT_DIR / "ruptures_central_segments.csv", parse_dates=["start"])
    central_dates = pd.DatetimeIndex(
        segs[segs["symbol"] == rc.MAIN_CODE]["start"].to_numpy()[1:])  # 首段起点不是断点
    dates = main_states.index
    bk_idx = np.array([dates.get_loc(d) for d in central_dates if d in dates])
    if len(switch) and len(bk_idx):
        dmin = np.abs(switch[:, None] - bk_idx[None, :]).min(axis=1)
        confirm = float((dmin <= rc.CONFIRM_WINDOW_DAYS).mean())
        rev = float((np.abs(bk_idx[:, None] - switch[None, :]).min(axis=1)
                     <= rc.CONFIRM_WINDOW_DAYS).mean())
    else:
        confirm, rev = 0.0, 0.0

    # ---- 判据 5：已实现亏损段覆盖 + D 内持仓日占比 ----
    seg_path = rc.MAIN_OUTPUTS / "exposure_attribution" / "segments.parquet"
    segs_acc = pd.read_parquet(seg_path)
    main3 = segs_acc[(segs_acc["window"] == "main") & (segs_acc["phase"] == 3)
                     & (segs_acc["open_position"] == False)].copy()
    main3["start_date"] = pd.to_datetime(main3["start_date"])
    main3["end_date"] = pd.to_datetime(main3["end_date"])
    cal = main_states.index
    covered_flags, held_mask = [], np.zeros(len(cal), dtype=bool)
    for _, row in main3.iterrows():
        s = cal.searchsorted(row["start_date"])
        e = cal.searchsorted(row["end_date"], side="right")
        held_mask[s:e] = True
        covered_flags.append(bool(D.to_numpy()[s:e].any()))
    main3["touched_D"] = covered_flags
    main3["log_loss"] = -np.log1p(main3["net_return"])
    losses = main3[main3["net_return"] < 0]
    cov_weight = float(losses.loc[losses["touched_D"], "log_loss"].sum())
    all_weight = float(losses["log_loss"].sum())
    coverage = cov_weight / all_weight if all_weight > 0 else 0.0

    acct_start = main3["start_date"].min()
    d_in_acct = D & (D.index >= acct_start)
    held_share = float((held_mask & d_in_acct.to_numpy()).sum() / d_in_acct.sum()) \
        if d_in_acct.sum() else 0.0

    # ---- F 九特征事件：信号日状态 × 不利退出（描述性） ----
    ev = pd.read_csv(rc.MAIN_OUTPUTS / "exposure_danger_diagnostic" / "events.csv",
                     parse_dates=["signal_date", "exec_date"])
    pos = [min(dates.searchsorted(d), len(dates) - 1) for d in ev["signal_date"]]
    ev["state_signal_day"] = [int(labels[p]) for p in pos]
    ev["in_D"] = [bool(D.to_numpy()[p]) for p in pos]
    grp = ev.groupby("in_D")["adverse_exit"].agg(["mean", "count"])

    summary = pd.DataFrame([
        {"item": "confirm_rate_hmm_to_ruptures", "value": round(confirm, 4),
         "threshold": ">=0.70", "pass": confirm >= 0.70},
        {"item": "confirm_rate_reverse_descriptive", "value": round(rev, 4),
         "threshold": "", "pass": None},
        {"item": "D_days_total", "value": int(D.sum()), "threshold": "", "pass": None},
        {"item": "D_overlap_G_rate", "value": round(float((D & G).sum() / D.sum()), 4),
         "threshold": "", "pass": None},
        {"item": "D_overlap_H_rate", "value": round(float((D & H).sum() / D.sum()), 4),
         "threshold": "", "pass": None},
        {"item": "loss_coverage_by_D", "value": round(coverage, 4),
         "threshold": ">=0.30", "pass": coverage >= 0.30},
        {"item": "held_day_share_in_D", "value": round(held_share, 4),
         "threshold": ">=0.20", "pass": held_share >= 0.20},
    ])
    summary.to_csv(rc.OUT_DIR / "overlap_summary.csv", index=False)
    ev.to_csv(rc.OUT_DIR / "f_events_state.csv", index=False)

    verdict = {
        "criterion_3_confirm": {"pass": bool(confirm >= 0.70), "value": round(confirm, 4)},
        "criterion_5_increment": {
            "pass": bool(coverage >= 0.30 and held_share >= 0.20),
            "coverage": round(coverage, 4), "held_share": round(held_share, 4),
            "n_loss_segments": int(len(losses)),
            "n_covered_loss_segments": int(losses["touched_D"].sum()),
        },
        "F_descriptive_adverse_rate": {
            str(idx): {"adverse_rate": round(float(r["mean"]), 4), "n": int(r["count"])}
            for idx, r in grp.iterrows()
        },
    }
    rc.OUT_DIR.joinpath("r4_verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
