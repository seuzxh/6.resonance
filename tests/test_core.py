"""离线单元测试：共振指标与回测引擎（全部 mock，无网络）。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from resonance.backtest import RotationBacktester, perf_stats  # noqa: E402
from resonance.metrics import partial_correlation, resonance_rankings, rolling_correlation  # noqa: E402


def _make_returns(n=120, seed=7):
    rng = np.random.default_rng(seed)
    idx_ret = pd.Series(rng.normal(0, 0.01, n))
    concept_ret = 1.2 * idx_ret + rng.normal(0, 0.005, n)   # 与指数强相关
    other_ret = rng.normal(0, 0.01, n)                       # 与指数无关
    mkt_ret = idx_ret + rng.normal(0, 0.002, n)
    df = pd.DataFrame(
        {"INDEX": idx_ret, "CONCEPT": concept_ret, "OTHER": other_ret, "ALLA": mkt_ret}
    )
    return df


def test_rolling_correlation_orders_correctly():
    rets = _make_returns()
    strong = rolling_correlation(rets, "CONCEPT", "INDEX", 20).iloc[-1]
    weak = rolling_correlation(rets, "OTHER", "INDEX", 20).iloc[-1]
    assert strong > 0.8
    assert abs(weak) < 0.5


def test_partial_correlation_removes_market_beta():
    rets = _make_returns()
    partial = partial_correlation(rets, "CONCEPT", "INDEX", "ALLA", 20).iloc[-1]
    raw = rolling_correlation(rets, "CONCEPT", "INDEX", 20).iloc[-1]
    # 控制市场后相关仍应为正（concept 由 index 驱动），且数值有限
    assert np.isfinite(partial)
    assert partial > 0
    assert partial <= raw + 1e-9


def test_resonance_rankings_top_is_strong_concept():
    rets = _make_returns()
    rk = resonance_rankings(rets, "INDEX", ["CONCEPT", "OTHER"], 20)
    assert rk.iloc[0]["concept"] == "CONCEPT"
    assert rk["corr"].is_monotonic_decreasing


def test_backtest_engine_compounds_geometrically():
    # 构造价格：指数A每期+10%，指数B每期-10%
    dates = pd.date_range("2026-01-01", periods=6, freq="B")
    close = pd.DataFrame(
        {"A": [1.1 ** i for i in range(6)], "B": [0.9 ** i for i in range(6)]},
        index=dates,
    )
    # 排名永远把 A 排第一
    rank_fn = lambda date: pd.DataFrame({"concept": ["A", "B"], "corr": [0.9, 0.1]})
    bt = RotationBacktester(close, rank_fn, rebalance_days=5)
    out = bt.run()
    # 持有 A 共 5 期复利：1.1^5 = 1.61051
    assert abs(out["final_nav"] - 1.1 ** 5) < 1e-9


def test_backtest_topk_buffer_holds_position():
    # A 每期 +1%，B 前半 +2% 后半 0%；每期检查排名
    dates = pd.date_range("2026-01-01", periods=4, freq="B")
    close = pd.DataFrame(
        {"A": [1.01 ** i for i in range(4)], "B": [1.02 ** i for i in range(4)]},
        index=dates,
    )
    state = {"flip": False}

    def rank_fn(date):
        # 第一次 B 第一（买入 B），此后 B 仍在 Top5 内 → 续持，不追 A
        return pd.DataFrame({"concept": ["B", "A"], "corr": [0.9, 0.8]})

    bt = RotationBacktester(close, rank_fn, rebalance_days=1)
    out = bt.run()
    # 全程持有 B：1.02^3
    assert abs(out["final_nav"] - 1.02 ** 3) < 1e-9
    assert all(s["action"] == "eod" or s.get("to") == "B" for s in out["switches"]) or not out["switches"]


def test_perf_stats_shape():
    dates = pd.date_range("2026-01-01", periods=50, freq="B")
    nav = pd.Series(np.linspace(1.0, 1.2, 50), index=dates)
    bench = pd.Series(np.linspace(1.0, 1.1, 50), index=dates)
    st = perf_stats(nav, bench)
    for key in ("total_return", "annualized_return", "annualized_vol", "sharpe", "max_drawdown", "excess_wealth"):
        assert key in st and np.isfinite(st[key])
    assert st["total_return"] > 0
