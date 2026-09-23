"""概念层诊断（探索性）：持仓明细 / 逐块对比宽基 / 泄漏排除测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.backtest import RotationBacktester, perf_stats  # noqa: E402
from resonance.dynamic import leader_index, make_dynamic_rank_fn  # noqa: E402
from resonance.metrics import resonance_rankings  # noqa: E402


def load():
    bars = pd.read_parquet(config.CACHE_DIR / "daily_bars.partial.parquet")
    catalog = pd.read_csv(config.DATA_DIR / "concept_catalog.csv")
    concepts = [c for c in catalog["code"] if c in set(bars["symbol"])]
    name = dict(zip(catalog["code"], catalog["name"]))
    close = bars.pivot(index="date", columns="symbol", values="close").sort_index()
    close.index = pd.to_datetime(close.index)
    return close, concepts, name


def main():
    close, concepts, name = load()
    cal = close.loc[config.BACKTEST_START:].index
    returns = close.pct_change()
    broad = list(config.BROAD_INDEX_POOL)

    print("=== 逐调仓块诊断（dyn5, exec_lag=1, warm）===")
    print(f"{'信号日':>10} {'执行日':>10} {'领先指数':>10} {'持有概念':>16} {'块内概念收益':>10} {'块内宽基收益':>10}")
    pos = 1
    nav = 1.0
    nav_l = 1.0
    blocks = []
    while pos < len(cal):
        sig = cal[pos - 2] if pos - 2 >= 0 else cal[0]
        leader = leader_index(close[broad], sig)
        if leader is not None:
            rk = resonance_rankings(returns, leader, concepts, config.SIGNAL_WINDOW, asof=sig)
            held = rk["concept"].iloc[0] if len(rk) and rk["corr"].iloc[0] > 0 else None
        else:
            leader, held = None, None
        blk = cal[pos : pos + 5]
        r_c = (close.loc[blk[-1], held] / close.loc[cal[pos - 1], held] - 1) if held else 0.0
        r_l = (close.loc[blk[-1], leader] / close.loc[cal[pos - 1], leader] - 1) if leader else 0.0
        nav *= 1 + r_c
        nav_l *= 1 + r_l
        blocks.append((str(sig.date()), leader, held, r_c, r_l))
        pos += 5
    for sig, leader, held, r_c, r_l in blocks:
        print(f"{sig:>10} {(config.BROAD_INDEX_POOL.get(leader) or '-')[:8]:>10} "
              f"{(name.get(held) or '-')[:12]:>14} {r_c:>10.2%} {r_l:>10.2%}")
    print(f"\n概念层复利: {nav-1:+.2%} | 宽基层复利: {nav_l-1:+.2%}")

    # 泄漏排除：exec_lag=-1（信号用执行日之后的收盘，纯诊断）
    print("\n=== 泄漏诊断：exec_lag 对 dyn5 总收益的影响 ===")
    for lag in (-1, 0, 1):
        rank_fn = make_dynamic_rank_fn(close, concepts, exec_lag=lag)
        bt = RotationBacktester(close[concepts].loc[config.BACKTEST_START:], rank_fn, rebalance_days=5)
        out = bt.run()
        st = perf_stats(out["nav_curve"])
        print(f"exec_lag={lag:>2}: total={st['total_return']:+.2%} sharpe={st['sharpe']:.2f}")


if __name__ == "__main__":
    main()
