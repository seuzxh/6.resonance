"""updown_resonance_rankings 离线单测：条件相关手算校验 / 回退 / 排除规则 / 注入。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from resonance.dynamic import make_dynamic_rank_fn
from resonance.metrics import resonance_rankings, updown_resonance_rankings


def _rets(index, leader, concepts):
    df = pd.DataFrame({"L": leader}, index=index)
    for c, v in concepts.items():
        df[c] = v
    return df


def test_updown_corr_matches_manual():
    """上涨日条件相关的手算校验（与全样本相关可分辨）。"""
    idx = pd.date_range("2026-01-05", periods=4, freq="B")
    leader = [0.01, -0.02, 0.02, -0.01]
    # A 与 L 同涨同跌；B 在 L 上涨日反向（且非常数，Pearson 有定义）
    df = _rets(idx, leader, {"A": [0.02, -0.03, 0.03, -0.02],
                             "B": [-0.005, 0.05, -0.02, 0.05]})
    out = updown_resonance_rankings(df, "L", ["A", "B"], window=4, side="up", min_days=1)
    # 上涨日 = 第 0、2 天：A(0.02,0.03) 与 L(0.01,0.02) 同向递增 → corr=1
    # B(-0.005,-0.02) 递减 → corr=-1
    assert out["concept"].iloc[0] == "A"
    assert np.isclose(out["corr"].iloc[0], 1.0)
    assert np.isclose(out[out["concept"] == "B"]["corr"].iloc[0], -1.0)
    # down 侧（第 1、3 天）：A(-0.03,-0.02) 与 L(-0.02,-0.01) 同向 → 仍居首
    dn = updown_resonance_rankings(df, "L", ["A", "B"], window=4, side="down", min_days=1)
    assert dn["concept"].iloc[0] == "A"


def test_fallback_when_too_few_condition_days():
    """条件样本不足 → 回退全窗口相关并计数。"""
    idx = pd.date_range("2026-01-05", periods=6, freq="B")
    leader = [0.01, 0.01, -0.02, -0.01, -0.02, 0.02]  # 上涨日 3 < min_days=4
    df = _rets(idx, leader, {"A": [0.01, 0.02, -0.03, -0.02, -0.01, 0.03]})
    stats = {}
    out = updown_resonance_rankings(df, "L", ["A"], window=6, side="up", min_days=4, stats=stats)
    full = resonance_rankings(df, "L", ["A"], window=6)
    assert stats["fallback"] == 1
    assert np.isclose(out["corr"].iloc[0], full["corr"].iloc[0])


def test_concept_with_gap_excluded():
    """窗口内有 NaN 的概念被排除（对齐 rolling min_periods 口径）。"""
    idx = pd.date_range("2026-01-05", periods=5, freq="B")
    leader = [0.01, 0.02, 0.015, -0.02, 0.02]
    df = _rets(idx, leader, {"A": [0.01, 0.02, 0.015, -0.01, 0.02],
                             "N": [0.01, np.nan, 0.02, -0.01, 0.02]})
    out = updown_resonance_rankings(df, "L", ["A", "N"], window=5, side="up", min_days=2)
    assert list(out["concept"]) == ["A"]


def test_ranking_fn_injection_via_dynamic_factory():
    """make_dynamic_rank_fn 注入上涨共振后，榜首与全样本口径可分辨。"""
    idx = pd.date_range("2026-01-05", periods=30, freq="B")
    rng = np.random.default_rng(7)
    base = rng.normal(0, 0.01, 30)
    # A 在 L 上涨日跟随、下跌日近乎不动；B 在 L 上涨日反向
    ret_a = np.where(base > 0, base, base * 0.05) + 0.0005
    ret_b = np.where(base > 0, -base, base * 0.05) + 0.0005
    close = pd.DataFrame({"L": 100.0 * np.cumprod(1 + base),
                          "A": 100.0 * np.cumprod(1 + ret_a),
                          "B": 100.0 * np.cumprod(1 + ret_b)}, index=idx)
    stats: dict = {}
    rank_up = make_dynamic_rank_fn(
        close, ["A", "B"], broad_codes=["L"], window=20, exec_lag=1,
        ranking_fn=lambda r, l, cs, w, asof=None: updown_resonance_rankings(
            r, l, cs, w, side="up", min_days=8, asof=asof, stats=stats))
    out = rank_up(idx[-2])
    assert not out.empty and out["concept"].iloc[0] == "A"
