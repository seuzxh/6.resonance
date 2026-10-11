"""R3 HMM 对照：全样本结构确认（ISOLATED，不进任何回测）（§四 R3）。

预注册：docs/research/regime-detection-plan.md §四 R3 冻结细节。
状态数 K∈{2,3,4} 按 BIC 取优；10 种子取最优对数似然；状态按 vol5 均值升序贴标签。
全样本结果隔离存放：所有输出带 ISOLATED_FULLSAMPLE=1 列。

用法：conda run -n resonance python research/regime_hmm.py
产出：outputs/regime/hmm_states.csv / hmm_summary.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import regime_common as rc  # noqa: E402


def main() -> None:
    feats = pd.read_parquet(rc.OUT_DIR / "features.parquet")
    state_rows, summary_rows = [], []

    for code in rc.ALL_CODES:
        f = feats[feats["symbol"] == code].set_index("date").sort_index()
        f = f.dropna(subset=["logret", "vol5"])
        X = rc.zscore(f[["logret", "vol5"]].to_numpy())
        vol_raw = f["vol5"].to_numpy()

        fits = {k: rc.fit_hmm_best_seed(X, vol_raw, k) for k in rc.HMM_STATES_CANDIDATES}
        best_k = min(fits, key=lambda k: fits[k].bic)
        fit = fits[best_k]

        labels = fit.relabel[fit.labels]           # vol 升序重贴标签
        state_rows.append(pd.DataFrame({
            "symbol": code, "date": f.index,
            "close": f["close"].to_numpy(),
            "logret": f["logret"].to_numpy(),
            "vol5": f["vol5"].to_numpy(),
            "state_raw": fit.labels,
            "state": labels,
            "ISOLATED_FULLSAMPLE": 1,
        }))
        for k, ft in fits.items():
            summary_rows.append({
                "symbol": code, "k": k, "chosen": k == best_k,
                "seed": ft.seed, "loglik": round(ft.loglik, 2), "bic": round(ft.bic, 2),
            })
        # 状态统计（选中 K）
        for s in range(best_k):
            m = labels == s
            summary_rows.append({
                "symbol": code, "k": best_k, "chosen": True,
                "seed": np.nan, "loglik": np.nan, "bic": np.nan,
                "state": s, "n_days": int(m.sum()),
                "mean_vol5": round(float(f["vol5"][m].mean()), 6),
                "mean_logret": round(float(f["logret"][m].mean()), 6),
            })
        print(f"{code}: K(BIC)={best_k} 各K BIC=" +
              ", ".join(f"{k}:{fits[k].bic:.1f}" for k in fits))

    states = pd.concat(state_rows, ignore_index=True)
    states.to_csv(rc.OUT_DIR / "hmm_states.csv", index=False)
    pd.DataFrame(summary_rows).to_csv(rc.OUT_DIR / "hmm_summary.csv", index=False)
    print("R3 完成：全样本隔离状态表", states.shape)


if __name__ == "__main__":
    main()
