"""R5 滚动前推：月度重估 + 前向滤波，无前视状态序列（§四 R5）。

预注册：docs/research/regime-detection-plan.md §四 R5 冻结细节。
每月首个交易日以上月末为止的数据重估 HMM（K 固定为 R3 BIC 选择，标准化随
训练窗重算，10 种子），月内逐日只用前向滤波概率取状态。判据 6（一致率 ≥60%）
与判据 7（高低波态 T+20 Welch t 两阶段窗分别 p<0.05）在本脚本评估。

用法：conda run -n resonance python research/regime_walkforward.py [--refit-months 1|2]
产出：outputs/regime/wf_states.csv / wf_consistency.csv / forward_returns.csv /
      welch_tests.csv / wf_criteria.json
（--refit-months 2 为判据 6 未达标的预注册补救，只允许重跑一次）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import ttest_ind

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import regime_common as rc  # noqa: E402

EARLY_END = "2024-12-31"        # 阶段窗切分（预注册冻结）
RECENT_START = "2025-01-02"


def forward_returns(logret: pd.Series) -> pd.DataFrame:
    """T+1 / T+5 / T+20 对数收益（自 T+1 日起算；窗口尾不足丢弃）。"""
    lr = logret.to_numpy()
    n = len(lr)
    r1 = np.full(n, np.nan)
    r5 = np.full(n, np.nan)
    r20 = np.full(n, np.nan)
    for t in range(n):
        if t + 1 < n:
            r1[t] = lr[t + 1]
        if t + 5 < n:
            r5[t] = lr[t + 1: t + 6].sum()
        if t + 20 < n:
            r20[t] = lr[t + 1: t + 21].sum()
    return pd.DataFrame({"r_t1": r1, "r_t5": r5, "r_t20": r20}, index=logret.index)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refit-months", type=int, default=1,
                    help="判据 6 补救：重估间隔月数（预注册只允许 1→2 一次）")
    args = ap.parse_args()

    hmm = pd.read_csv(rc.OUT_DIR / "hmm_states.csv", parse_dates=["date"])
    ms = hmm[hmm["symbol"] == rc.MAIN_CODE].set_index("date").sort_index()
    summary = pd.read_csv(rc.OUT_DIR / "hmm_summary.csv")
    chosen = summary[(summary["symbol"] == rc.MAIN_CODE)
                     & (summary["chosen"]) & summary["k"].notna()]
    k = int(chosen["k"].iloc[0])

    f = ms[["close", "logret", "vol5"]].dropna()
    X_all = f[["logret", "vol5"]].to_numpy()
    ref_state = ms["state"].to_numpy()
    dates = f.index

    # 重估时点：每 refit-months 个月的月首交易日
    months = pd.Series(dates, index=dates).groupby([dates.year, dates.month]).first()
    refit_days = months.to_numpy()[:: args.refit_months]

    wf_state = np.full(len(dates), np.nan)
    for day in refit_days:
        train = dates < day
        if train.sum() < rc.MIN_TRAIN_DAYS:
            continue
        month_days = (dates >= day) & (
            dates < pd.Timestamp(day) + pd.DateOffset(months=args.refit_months))
        if not month_days.any():
            continue
        Xtr_raw, Xte_raw = X_all[train], X_all[month_days]
        mu, sd = Xtr_raw.mean(axis=0), Xtr_raw.std(axis=0, ddof=0)
        sd = np.where(sd == 0, 1.0, sd)
        Xtr, Xte = (Xtr_raw - mu) / sd, (Xte_raw - mu) / sd
        fit = rc.fit_hmm_best_seed(Xtr, Xtr_raw[:, 1], k)
        probs = rc.forward_filter_probs(fit.model, Xte)
        raw = probs.argmax(axis=1)
        idx = np.where(month_days)[0]
        wf_state[idx] = fit.relabel[raw]

    wf = pd.DataFrame({
        "date": dates, "close": f["close"].to_numpy(),
        "state_wf": wf_state, "state_fullsample": ref_state,
    })
    wf.to_csv(rc.OUT_DIR / "wf_states.csv", index=False)

    # ---- 判据 6：天级一致率（有 wf 状态的天 vs R3 全样本标签） ----
    valid = ~np.isnan(wf_state)
    consistency = float((wf_state[valid] == ref_state[valid]).mean()) if valid.any() else 0.0
    by_year = pd.Series({
        str(y): round(float((wf_state[valid & (dates.year == y)]
                             == ref_state[valid & (dates.year == y)]).mean()), 4)
        for y in sorted(set(dates[valid].year))
    })

    # ---- 判据 7：forward 收益分组 + Welch t ----
    fr = forward_returns(f["logret"])
    fr["state"] = wf_state
    fr["date"] = dates
    fr.to_csv(rc.OUT_DIR / "forward_returns.csv", index=False)
    tests = []
    for win, m in (("early", dates <= EARLY_END), ("recent", dates >= RECENT_START)):
        sub = fr[m & valid]
        hi = sub.loc[sub["state"] == k - 1, "r_t20"].dropna()
        lo = sub.loc[sub["state"] == 0, "r_t20"].dropna()
        if len(hi) >= 2 and len(lo) >= 2:
            t, p = ttest_ind(hi, lo, equal_var=False)
        else:
            t, p = np.nan, np.nan
        tests.append({"window": win, "n_high": len(hi), "n_low": len(lo),
                      "mean_high_r20": round(float(hi.mean()), 5) if len(hi) else np.nan,
                      "mean_low_r20": round(float(lo.mean()), 5) if len(lo) else np.nan,
                      "t": round(float(t), 3), "p": round(float(p), 4)})
    welch = pd.DataFrame(tests)
    welch.to_csv(rc.OUT_DIR / "welch_tests.csv", index=False)

    crit = {
        "k_fixed": k,
        "refit_months": args.refit_months,
        "n_valid_days": int(valid.sum()),
        "n_total_days": int(len(dates)),
        "criterion_6_consistency": {
            "pass": bool(consistency >= 0.60), "value": round(consistency, 4),
            "by_year": by_year.to_dict(), "threshold": 0.60,
        },
        "criterion_7_welch_t20": {
            "pass": bool((welch["p"] < 0.05).all()),
            "windows": tests, "threshold": "两窗分别 p<0.05",
        },
        "note": "T+20 样本逐日重叠，存在序列相关，p 值按保守解读",
    }
    rc.OUT_DIR.joinpath("wf_criteria.json").write_text(
        json.dumps(crit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(crit, ensure_ascii=False, indent=2))
    pd.DataFrame({"consistency_overall": [consistency]}).to_csv(
        rc.OUT_DIR / "wf_consistency.csv", index=False)


if __name__ == "__main__":
    main()
