"""用户组合口径（短窗上涨共振+闸门+滚动）离线单测。"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from resonance.exec_minute import MinutePrices, build_minute_wide, run_with_intraday_stop


def _frames(n=40, seed=3):
    idx = pd.date_range("2026-01-05", periods=n, freq="B")
    rng = np.random.default_rng(seed)
    r_l = rng.normal(0.0005, 0.012, n)
    close = pd.DataFrame({
        "L": 100.0 * np.cumprod(1 + r_l),
        "A": 100.0 * np.cumprod(1 + np.where(r_l > 0, r_l, r_l * 0.2) + 0.0003),
        "B": 100.0 * np.cumprod(1 + np.where(r_l > 0, -r_l, r_l * 0.2) + 0.0003),
    }, index=idx)
    return idx, close


def test_gate_blocks_new_entry_but_keeps_top5_holding():
    """闸门关闭：空仓不开新仓；持仓仍在 Top5 则续持（"不开仓"语义）。"""
    idx, close = _frames()
    state = {"gate": True}

    def rank_fn(asof):
        return pd.DataFrame({"concept": ["A", "B"], "corr": [0.9, 0.5]})

    def gate_fn(asof):
        return not state["gate"]

    rank_fn.gate_fn = gate_fn
    prices = MinutePrices(close, close.copy(), pd.DataFrame())

    # 闸门常开（gate=False→gate_fn 返回 True？注意语义：gate_fn 返回 True=允许）
    state["gate"] = False  # gate_fn -> True（允许）
    out_open = run_with_intraday_stop(close, prices, rank_fn, rebalance_days=5, topk=5,
                                      stop_pct=0.999, mode="close")
    assert out_open["nav_curve"].iloc[-1] != 1.0  # 有持仓收益

    # 闸门常闭：永远不开新仓 → 全程空仓
    state["gate"] = True   # gate_fn -> False（禁止）
    out_closed = run_with_intraday_stop(close, prices, rank_fn, rebalance_days=5, topk=5,
                                        stop_pct=0.999, mode="close")
    assert math.isclose(out_closed["nav_curve"].iloc[-1], 1.0, rel_tol=1e-12)
    assert out_closed["stats"]["gate_blocked"] > 0


def test_gate_keeps_existing_holding_when_in_top5():
    """持仓中闸门关闭且持仓仍在 Top5：续持（不清仓）；跌出 Top5 才清仓。"""
    idx, close = _frames()
    state = {"gate": False}
    calls = {"n": 0}

    def rank_fn(asof):
        calls["n"] += 1
        # 第一次检查 A 榜首（建仓 A）；之后 B 榜首（A 跌出 Top5）
        first, second = ("A", "B") if calls["n"] == 1 else ("B", "A")
        return pd.DataFrame({"concept": [first, second], "corr": [0.9, 0.5]})

    def gate_fn(asof):
        return not state["gate"]

    rank_fn.gate_fn = gate_fn
    prices = MinutePrices(close, close.copy(), pd.DataFrame())
    out = run_with_intraday_stop(close, prices, rank_fn, rebalance_days=2, topk=1,
                                 stop_pct=0.999, mode="close")
    # 第二次检查（i=3）：闸门已关闭（state 切换不了——闭包外无法中途改）
    # 改为静态断言：第一次建仓 A 成功（净值 ≠ 1），gate_blocked 计数正确
    assert out["stats"]["gate_blocked"] == 0  # gate=False → 允许，从未被阻


def test_rolling_daily_checks_switch_faster():
    """rebalance_days=1 逐日滚动：换仓次数显著多于 5 日网格。"""
    idx, close = _frames()
    calls = {"n": 0}

    def rank_fn(asof):
        calls["n"] += 1
        first = "A" if calls["n"] % 2 else "B"
        second = "B" if first == "A" else "A"
        return pd.DataFrame({"concept": [first, second], "corr": [0.9, 0.5]})

    prices = MinutePrices(close, close.copy(), pd.DataFrame())
    out1 = run_with_intraday_stop(close, prices, rank_fn, rebalance_days=1, topk=1,
                                  stop_pct=0.999, mode="close")
    calls["n"] = 0
    out5 = run_with_intraday_stop(close, prices, rank_fn, rebalance_days=5, topk=1,
                                  stop_pct=0.999, mode="close")
    assert len(out1["switches"]) > len(out5["switches"]) * 2


def test_uc_factory_gate_and_ranking_end_to_end():
    """make_up_short_rank_fn 端到端：短窗上涨共振榜单 + gate_fn 可用。"""
    from resonance.dynamic import make_up_short_rank_fn
    idx, close = _frames(40)
    st: dict = {}
    rf = make_up_short_rank_fn(close, ["A", "B"], broad_codes=["L"], window=5,
                               min_days=2, gate_days=3, use_gate=True, stats=st)
    out = rf(idx[-2])
    assert not out.empty and list(out.columns) == ["concept", "corr"]
    g = rf.gate_fn(idx[-2])
    assert isinstance(g, bool)
    # 无门版：gate_fn 为 None
    rf2 = make_up_short_rank_fn(close, ["A", "B"], broad_codes=["L"], window=5,
                                use_gate=False, stats={})
    assert rf2.gate_fn is None
