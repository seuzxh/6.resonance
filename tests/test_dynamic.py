"""离线单测：动态宽基→概念策略模块（全部合成数据，无网络）。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from resonance.dynamic import leader_index, make_dynamic_rank_fn  # noqa: E402
from resonance.metrics import resonance_rankings  # noqa: E402


def _make_close(n=80, seed=11):
    """三个宽基 + 两个概念：BROIDA 持续涨、BROB 平、BROC 跌；CON_A 跟随 BROIDA。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    broa = pd.Series(np.cumprod(1 + rng.normal(0.002, 0.005, n)), index=dates)
    brob = pd.Series(np.cumprod(1 + rng.normal(0.0, 0.004, n)), index=dates)
    broc = pd.Series(np.cumprod(1 + rng.normal(-0.002, 0.005, n)), index=dates)
    con_a = broa * (1 + rng.normal(0, 0.001, n))  # 与 BROIDA 近乎同向
    con_b = pd.Series(np.cumprod(1 + rng.normal(0, 0.01, n)), index=dates)
    close = pd.DataFrame({"BROIDA": broa, "BROB": brob, "BROC": broc,
                          "CON_A": con_a, "CON_B": con_b})
    return close


def test_leader_index_picks_max_momentum():
    close = _make_close()
    assert leader_index(close[["BROIDA", "BROB", "BROC"]], close.index[-1]) == "BROIDA"


def test_leader_index_all_falling_picks_smallest_decline():
    dates = pd.date_range("2025-01-01", periods=30, freq="B")
    close = pd.DataFrame({"A": np.linspace(1.0, 0.8, 30),   # -20%
                          "B": np.linspace(1.0, 0.9, 30)},  # -10%
                         index=dates)
    assert leader_index(close, dates[-1]) == "B"


def test_leader_index_insufficient_history_returns_none():
    close = _make_close(n=10)
    assert leader_index(close[["BROIDA", "BROB"]], close.index[-1]) is None


def test_dynamic_rank_fn_ranks_leader_correlated_concept_top():
    close = _make_close()
    fn = make_dynamic_rank_fn(close, ["CON_A", "CON_B"],
                              broad_codes=["BROIDA", "BROB", "BROC"])
    rk = fn(close.index[-1])
    assert rk.iloc[0]["concept"] == "CON_A"  # 领先是 BROIDA，CON_A 与其同向


def test_dynamic_rank_fn_cold_start_empty_before_cutoff():
    close = _make_close()
    start = close.index[30]
    fn = make_dynamic_rank_fn(close, ["CON_A", "CON_B"],
                              broad_codes=["BROIDA", "BROB", "BROC"],
                              cold_start=True, backtest_start=str(start.date()))
    # 窗口起点当日：数据虽有（合成全历史），但冷启动口径按"无 lookback"截断
    early = fn(close.index[35])
    assert early.empty
    late = fn(close.index[55])
    assert not late.empty and late.iloc[0]["concept"] == "CON_A"


def test_dynamic_rank_fn_exec_lag_shifts_signal_day():
    close = _make_close()
    concepts = ["CON_A", "CON_B"]
    fn = make_dynamic_rank_fn(close, concepts, broad_codes=["BROIDA", "BROB", "BROC"], exec_lag=1)
    asof = close.index[-1]
    # 合成数据中任何 20 日窗口的领先者都是 BROIDA（漂移 +0.2%/日 vs 0/-0.2%）
    expect = resonance_rankings(close.pct_change(), "BROIDA", concepts, 20,
                                asof=close.index[-2])
    got = fn(asof)
    pd.testing.assert_frame_equal(got.reset_index(drop=True), expect.reset_index(drop=True))


def test_leader_history_populated():
    close = _make_close()
    fn = make_dynamic_rank_fn(close, ["CON_A", "CON_B"],
                              broad_codes=["BROIDA", "BROB", "BROC"])
    fn(close.index[-1])
    assert fn.leader_history[str(close.index[-1].date())] == "BROIDA"
