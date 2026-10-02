"""替代机制的行为测试；真实小数据，无网络与生产缓存。"""
import numpy as np
import pandas as pd

from research.resonance_alternatives_core import (
    adjusted_momentum, capped_schedule, mean_ranking, consensus_final,
)
from resonance.v3 import MinuteBarProvider


def rank(codes, scores):
    return pd.DataFrame({"concept": codes, "score": scores,
                         "sync": scores, "capture": np.ones(len(codes))})


def test_risk_adjustment_penalizes_oscillation_and_uses_no_future():
    days = pd.bdate_range("2024-01-01", periods=50)
    returns = pd.DataFrame({"steady": [.005] * 50,
                            "noisy": [.05, -.03] * 25}, index=days)
    close = 100 * (1 + returns).cumprod()
    score = adjusted_momentum(close, 20)
    assert score.iloc[20]["steady"] > score.iloc[20]["noisy"]
    assert score.iloc[:20].isna().all().all()
    changed = close.copy()
    changed.iloc[31:] *= 3
    pd.testing.assert_frame_equal(score.iloc[:31], adjusted_momentum(changed, 20).iloc[:31])
    missing = close.copy()
    missing.iloc[15, 0] = np.nan
    assert np.isnan(adjusted_momentum(missing, 20).iloc[20, 0])


def test_cap_counts_today_and_keeps_prior_history():
    scores = pd.DataFrame({"a": [3.] * 130, "b": [2.] * 130, "c": [1.] * 130})
    selected = capped_schedule(scores, .6)
    assert selected.iloc[:36].eq("a").all()
    assert selected.iloc[36] == "b"
    assert selected.iloc[60] == "a"
    for code in scores:
        assert selected.eq(code).rolling(60, min_periods=1).sum().max() <= 36
    future = scores.copy()
    future.iloc[80:, 2] = 10
    pd.testing.assert_series_equal(selected.iloc[:80], capped_schedule(future, .6).iloc[:80])


def test_cap_has_explicit_no_anchor_instead_of_exceeding_quota():
    scores = pd.DataFrame({"a": [1.] * 65})
    selected = capped_schedule(scores, .4)
    assert selected.iloc[:24].eq("a").all()
    assert selected.iloc[24:60].isna().all()
    assert selected.iloc[60] == "a"


def test_mean_ranking_aligns_concepts_and_averages_scores_not_components():
    left = rank(["a", "b", "c"], [1.0, .8, .9])
    right = rank(["b", "a", "d"], [.6, .2, 1.4])
    combined = mean_ranking([left, right])
    assert combined.concept.tolist() == ["b", "a"]
    np.testing.assert_allclose(combined.score, [.7, .6])
    assert mean_ranking([left, right.iloc[:0]]).empty


def test_consensus_missing_anchor_preserves_daily_order():
    day = pd.Timestamp("2024-01-02")
    times = pd.date_range("2024-01-02 13:00", periods=24, freq="5min")
    provider = MinuteBarProvider(pd.DataFrame({"a": np.arange(100, 124),
                                              "x": np.arange(200, 224),
                                              "y": np.arange(300, 324)}, index=times))
    daily = rank(["x", "y"], [1., .8])
    stats = {}
    result = consensus_final(daily, ["a", "b"], day, provider, stats)
    pd.testing.assert_frame_equal(result, daily)
    assert stats["minute_fallback_leader"] == 1


def test_consensus_missing_concepts_falls_back_and_reports_count():
    day = pd.Timestamp("2024-01-02")
    times = pd.date_range("2024-01-02 13:00", periods=24, freq="5min")
    provider = MinuteBarProvider(pd.DataFrame({"a": np.arange(100, 124),
                                              "b": np.arange(200, 224),
                                              "x": np.arange(300, 324)}, index=times))
    daily = rank(["x", "y"], [1., .8])
    stats = {}
    result = consensus_final(daily, ["a", "b"], day, provider, stats)
    pd.testing.assert_frame_equal(result, daily)
    assert stats["minute_excluded"] == 1
    assert stats["minute_fallback_sparse"] == 1
