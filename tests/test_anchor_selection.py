"""动态选锚研究的时点与成交边界测试；合成数据、离线运行。"""
import numpy as np
import pandas as pd
import pytest

from research import anchor_selection_core as core
from resonance.v3 import V3Backtester, V3Params


def sample_nav():
    dates = pd.bdate_range("2020-01-01", periods=100)
    nav = pd.DataFrame({"A": 1.003 ** np.arange(100),
                        "B": 1.002 ** np.arange(100)}, index=dates)
    active = pd.DataFrame(True, index=dates, columns=nav.columns)
    return nav, active


def test_schedule_cannot_see_future_returns():
    nav, active = sample_nav()
    before = core.select_schedule(nav, active, nav.index[40], lookback=20)
    nav.iloc[76:, 1] *= 100
    after = core.select_schedule(nav, active, nav.index[40], lookback=20)
    pd.testing.assert_frame_equal(before.iloc[:76], after.iloc[:76])


def test_unprofitable_or_underobserved_candidates_leave_no_anchor():
    nav, active = sample_nav()
    nav["A"] = 1.0
    active["B"] = False
    result = core.select_schedule(nav, active, nav.index[40], lookback=20)
    assert result["anchor"].isna().all()


def test_soft_penalty_changes_close_contest_but_is_not_a_hard_cap():
    nav, active = sample_nav()
    raw = core.select_schedule(nav, active, nav.index[40], lookback=20)
    limited = core.select_schedule(nav, active, nav.index[40], lookback=20,
                                   penalty=0.5)
    assert set(raw["anchor"].dropna()) == {"A"}
    assert "B" in set(limited["anchor"].dropna())
    active["B"] = False
    only_one = core.select_schedule(nav, active, nav.index[40], lookback=20,
                                    penalty=0.5)
    assert set(only_one["anchor"].dropna()) == {"A"}


def test_quality_uses_exact_trailing_return_and_drawdown():
    dates = pd.bdate_range("2020-01-01", periods=4)
    nav = pd.DataFrame({"A": [1.0, 1.2, 1.08, 1.188]}, index=dates)
    active = nav.gt(0)
    score = core.quality_scores(nav, active, lookback=3, min_active=1)
    assert pd.isna(score.iloc[2, 0])
    assert score.iloc[3, 0] == pytest.approx(0.188 - 0.10)


def synthetic_prices():
    days = pd.bdate_range("2020-01-01", periods=75)
    rng = np.random.default_rng(34)
    ra = rng.normal(0.004, 0.012, len(days))
    rb = rng.normal(0.003, 0.011, len(days))
    returns = np.array([ra, rb, ra * 1.4, rb * 1.4, (ra + rb) / 2]).T
    close = pd.DataFrame(100 * np.cumprod(1 + returns, axis=0), index=days,
                         columns=["A", "B", "C1", "C2", "ALLA"])
    open_ = close.shift(1) * 1.002
    open_.iloc[0] = close.iloc[0]
    return close, open_


def test_cached_fixed_anchor_preserves_trades_and_nav():
    close, open_ = synthetic_prices()
    params = V3Params(hl_source="leader", cost_bp=10, exec_price="open")
    engine = V3Backtester(close, ["C1", "C2"], ["A"], "ALLA", params,
                          open_all=open_)
    expected = engine.run(close.index[15])
    bank = core.RankingBank({"A": engine})
    schedule = pd.Series("A", index=close.index)
    result = bank.run(schedule, close.index[15], close.index[-1], 10)
    pd.testing.assert_series_equal(result["nav_curve"], expected["nav_curve"])
    pd.testing.assert_frame_equal(result["trades"], expected["trades"])


def test_anchor_switch_executes_next_open_without_inheriting_shadow_nav():
    close, open_ = synthetic_prices()
    params = V3Params(hl_source="leader", cost_bp=10, exec_price="open", min_hold=0)
    engines = {a: V3Backtester(close, ["C1", "C2"], [a], "ALLA", params,
                               open_all=open_) for a in ["A", "B"]}
    bank = core.RankingBank(engines)
    schedule = pd.Series("A", index=close.index)
    schedule.iloc[35:] = "B"
    result = bank.run(schedule, close.index[15], close.index[-1], 10)
    trades = result["trades"].set_index("date").to_dict("index")
    nav = 1.0
    holding = None
    for date, observed in result["nav_curve"].items():
        prev = close.index[close.index.get_loc(date) - 1]
        traded = date in trades
        if traded:
            trade = trades[date]
            if holding is not None:
                nav *= open_.at[date, holding] / close.at[prev, holding]
            nav *= 0.999 ** (2 if trade["type"] == "switch" else 1)
            holding = None if pd.isna(trade["to"]) else trade["to"]
        if holding is not None:
            base = open_.at[date, holding] if traded else close.at[prev, holding]
            nav *= close.at[date, holding] / base
        assert observed == pytest.approx(nav, abs=1e-10)
    truncated = schedule.copy()
    truncated.iloc[35:] = "A"
    control = bank.run(truncated, close.index[15], close.index[-1], 10)
    pd.testing.assert_series_equal(result["nav_curve"].loc[:close.index[35]],
                                   control["nav_curve"].loc[:close.index[35]])


def test_unknown_anchor_fails_instead_of_silent_cash():
    close, open_ = synthetic_prices()
    engine = V3Backtester(close, ["C1", "C2"], ["A"], "ALLA",
                          V3Params(exec_price="open"), open_all=open_)
    bank = core.RankingBank({"A": engine})
    with pytest.raises(ValueError, match="unknown"):
        bank.run(pd.Series("TYPO", index=close.index), close.index[15],
                 close.index[-1], 10)


def test_occupancy_judgment_uses_difference_of_phase_medians():
    from research.anchor_selection_run import paired_summary
    rows = []
    for variant, rates in [("candidate", [.1, .2, .3, .9, .9]),
                            ("baseline", [.2, .8, .4, .5, 1.0])]:
        for phase, rate in enumerate(rates):
            rows.append({"window": "main", "cost": 10, "variant": variant,
                         "phase": phase, "total": .1, "dd": -.1,
                         "max_rolling_occupancy": rate})
    result = paired_summary(pd.DataFrame(rows), "main", 10, "candidate", "baseline")
    assert result["occupancy_median_delta"] == pytest.approx(-.2)
    assert result["occupancy_delta"] == pytest.approx(-.1)


def test_zero_event_phases_are_not_dropped_from_count_median():
    from research import anchor_selection_run as runner
    table = pd.DataFrame([{"audit_stale_leader_window": 3}, {}, {}, {}, {}])
    normalized = runner.normalize_counts(table)
    assert normalized.audit_stale_leader_window.median() == 0
