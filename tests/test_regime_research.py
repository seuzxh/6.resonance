"""市场状态识别研究离线测试（regime-detection R0，全部合成数据，无网络）。

覆盖：特征无未来函数、复用口径与 v3 引擎逐点一致、HMM 种子确定性与
状态贴标签、前向滤波概率合法性、PELT 段长约束、闸门复算、forward 收益。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))

import regime_common as rc  # noqa: E402
from resonance.v3 import V3Params, compound_window, dynamic_half_life  # noqa: E402


def _closes(n=60, seed=7, drift=0.0, vol=0.01):
    rng = np.random.default_rng(seed)
    ret = rng.normal(drift, vol, n)
    return pd.Series(100 * np.exp(np.cumsum(ret)),
                     index=pd.bdate_range("2024-01-01", periods=n))


def test_vol5_uses_only_past_five_returns():
    f = rc.build_features(_closes())
    lr = f["logret"]
    for t in (10, 30, 59):
        expect = lr.iloc[t - 4: t + 1].std(ddof=1)
        assert abs(f["vol5"].iloc[t] - expect) < 1e-12
    # 无未来函数：改动 t 之后的收盘价不得影响 ≤t 的 vol5
    head = f["vol5"].iloc[: 30].to_numpy()
    f2 = rc.build_features(pd.concat([_closes().iloc[:30], _closes(seed=99).iloc[30:]]))
    assert np.allclose(f2["vol5"].iloc[:30].to_numpy(), head, equal_nan=True)


def test_compound_win_matches_v3():
    close = _closes(seed=3)
    close.iloc[20] = np.nan          # 引入缺口：窗口含 NaN → NaN
    p = V3Params()
    for pos in range(15, len(close)):
        v3 = compound_window(close.to_frame("A"), ["A"], p.leader_window, pos).iloc[0]
        mine = rc.compound_win(close, p.leader_window).iloc[pos]
        assert (pd.isna(v3) and pd.isna(mine)) or abs(v3 - mine) < 1e-12


def test_path_dd_tier_matches_v3():
    close = _closes(seed=5)
    p = V3Params()
    dd = rc.path_dd_win(close, p.dd_window)
    for pos in range(5, len(close)):
        v3_hl = dynamic_half_life(close, pos, p.dd_window, p.dd_tiers, p.half_lives)
        mine = rc.half_life_tier(float(dd.iloc[pos]))
        assert v3_hl == mine


def test_hmm_seed_determinism_and_vol_ordering():
    rng = np.random.default_rng(11)
    X = np.vstack([rng.normal(0, 0.5, (150, 2)), rng.normal(0, 3.0, (150, 2))])
    vol_raw = np.abs(X[:, 1])
    a = rc.fit_hmm_best_seed(X, vol_raw, 2, seeds=range(3))
    b = rc.fit_hmm_best_seed(X, vol_raw, 2, seeds=range(3))
    assert a.seed == b.seed and np.array_equal(a.labels, b.labels)
    lab = a.relabel[a.labels]
    assert vol_raw[lab == 1].mean() > vol_raw[lab == 0].mean()


def test_forward_filter_probs_valid():
    rng = np.random.default_rng(13)
    X = rng.normal(0, 1, (80, 2))
    fit = rc.fit_hmm_best_seed(X, np.abs(X[:, 1]), 2, seeds=range(2))
    probs = rc.forward_filter_probs(fit.model, X)
    assert probs.shape == (80, 2)
    assert np.all(probs >= 0) and np.all(probs <= 1)
    assert np.allclose(probs.sum(axis=1), 1.0)


def test_pelt_breakpoints_min_size_and_determinism():
    rng = np.random.default_rng(17)
    X = np.vstack([rng.normal(0, 0.5, (100, 2)), rng.normal(0, 4.0, (100, 2))])
    b1 = rc_zscore_pelt(X, pen=10.0)
    b2 = rc_zscore_pelt(X, pen=10.0)
    assert b1 == b2
    bounds = [0, *b1, len(X)]
    assert all(e - s >= 20 for s, e in zip(bounds[:-1], bounds[1:]))


def rc_zscore_pelt(X, pen):
    import ruptures as rpt
    algo = rpt.Pelt(model="rbf", min_size=20, jump=1).fit(X)
    return [int(e) for e in algo.predict(pen=pen) if e < len(X)]


def test_dual_anchor_states_gate_and_tier():
    # A 指数平稳微涨，B 指数近 10 日强动量但近 3 日回撤明显
    idx = pd.bdate_range("2024-01-01", periods=40)
    a = pd.Series(np.linspace(100, 102, 40), index=idx)
    b = pd.Series(np.concatenate([np.full(25, 100),
                                  np.linspace(100, 115, 15)]), index=idx)
    st = rc.dual_anchor_states(pd.DataFrame({"399001.SZ": a, "000852.SH": b}))
    last = st.iloc[-1]
    assert last["leader"] == "000852.SH"
    assert bool(last["gate"])
    # 构造 B 近 3 日下跌：闸门应失败且 leader 仍为 B
    b_down = b.copy()
    b_down.iloc[-3:] = b_down.iloc[-4] * np.array([0.99, 0.97, 0.95])
    st2 = rc.dual_anchor_states(pd.DataFrame({"399001.SZ": a, "000852.SH": b_down}))
    assert not bool(st2["gate"].iloc[-1])


def test_forward_returns_hand_check():
    lr = pd.Series([0.01, 0.02, -0.01, 0.03, 0.04, 0.05, -0.02])
    fr = rc_forward_returns(lr)
    assert abs(fr["r_t1"].iloc[0] - 0.02) < 1e-12
    assert abs(fr["r_t5"].iloc[0] - (0.02 - 0.01 + 0.03 + 0.04 + 0.05)) < 1e-12
    assert pd.isna(fr["r_t20"].iloc[0])            # 窗口尾不足 → 丢弃
    assert abs(fr["r_t1"].iloc[5] - (-0.02)) < 1e-12


def rc_forward_returns(lr):
    import regime_walkforward as wf  # research/ 已在 sys.path（见文件头）
    return wf.forward_returns(lr)


def test_event_date_on_calendar():
    dates = pd.bdate_range("2024-01-01", periods=10)
    # 2024-01-06 周六 → 映射到下一交易日 2024-01-08（下标 5）
    assert rc.event_date_on_calendar(dates, "2024-01-06") == 5
    assert rc.event_date_on_calendar(dates, "2024-01-02") == 1
