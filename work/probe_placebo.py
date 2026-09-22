"""安慰剂检验（探索性判别）：把 5min 收益表按列独立重排（破坏与领先指数的
配对相关、保留边际分布），在完全相同的激活/降级管线里产生"噪声分钟分"。

对比：w=3 真实 3日5min 分的 5 相位中位差 vs 10 个随机种子的中位差分布。
若随机也能到 +28pp 量级 → w=3 SUCCESS 为换手路径噪声而非信息。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.backtest import RotationBacktester, perf_stats  # noqa: E402
from resonance.minute import make_minute_rank_fn, minute_returns  # noqa: E402

W = 3


def main() -> int:
    close_bars = pd.read_parquet(config.CACHE_DIR / "daily_bars.parquet")
    m5 = pd.read_parquet(config.CACHE_DIR / "minute5_bars.parquet")
    catalog = pd.read_csv(config.DATA_DIR / "concept_catalog.csv")
    concepts = [c for c in catalog["code"] if c in set(close_bars["symbol"])]
    close = close_bars.pivot(index="date", columns="symbol", values="close").sort_index()
    close.index = pd.to_datetime(close.index)
    broad = list(config.BROAD_INDEX_POOL)
    returns = close.pct_change()
    mrets = minute_returns(m5)
    dcorr = {leader: returns[concepts].rolling(W).corr(returns[leader]) for leader in broad}
    cal = close.loc["2025-01-02":]
    phase_starts = [str(d.date()) for d in cal.index[:5]]
    bench = close["883957.TI"]

    def medians(mr) -> tuple[float, float]:
        meds_d, meds_m = [], []
        for start in phase_starts:
            b = bench.loc[start:]
            b = b / b.iloc[0]
            fn_d = make_minute_rank_fn(close, concepts, pd.DataFrame(), window=W,
                                       exec_lag=1, window_bars=141, daily_corr=dcorr)
            fn_m = make_minute_rank_fn(close, concepts, mr, window=W, exec_lag=1,
                                       window_bars=141, daily_corr=dcorr)
            out_d = RotationBacktester(close[concepts].loc[start:], fn_d, rebalance_days=5).run()
            out_m = RotationBacktester(close[concepts].loc[start:], fn_m, rebalance_days=5).run()
            meds_d.append(perf_stats(out_d["nav_curve"])["total_return"])
            meds_m.append(perf_stats(out_m["nav_curve"])["total_return"])
        return float(np.median(meds_d)), float(np.median(meds_m))

    med_d, med_real = medians(mrets)

    placebos = []
    for seed in range(10):
        rng = np.random.default_rng(seed)
        fake = mrets.copy()
        for col in fake.columns:
            vals = fake[col].to_numpy()
            mask = ~np.isnan(vals)
            scrambled = np.full_like(vals, np.nan)
            scrambled[mask] = vals[mask][rng.permutation(mask.sum())]  # 覆盖模式不变，时间配对破坏
            fake[col] = scrambled
        _, med_fake = medians(fake)
        placebos.append((med_fake - med_d) * 100)
    print(f"daily-only 中位: {med_d:+.2%}")
    print(f"真实 3日5min 分 中位: {med_real:+.2%} → 中位差 {(med_real - med_d) * 100:+.1f}pp")
    print(f"安慰剂中位差（按列重排，n=10）: {['%+.1f' % p for p in placebos]}")
    print(f"安慰剂分布: 均值 {np.mean(placebos):+.1f}pp | 极差 [{min(placebos):+.1f}, {max(placebos):+.1f}]pp | "
          f"≥真实值个数 {sum(1 for p in placebos if p >= (med_real - med_d) * 100)}/10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
