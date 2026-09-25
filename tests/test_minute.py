"""离线单测：分钟共振精选模块（合成数据，无网络）。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from resonance.minute import (  # noqa: E402
    make_minute_rank_fn,
    minute_resonance_score,
    minute_returns,
)


def _minute_long(seed=7, n_days=40):
    """合成 60min 长表：LEADER + CON_HI（分钟强共振）+ CON_LO（分钟弱）。

    每日 4 bar（10:30/11:30/14:00/15:00）；CON_HI 日内腿 = LEADER 腿 + 小噪声；
    CON_LO 独立噪声（日线层面三者与 LEADER 的日收益相关性近似，由日频价格另造）。
    """
    rng = np.random.default_rng(seed)
    days = pd.date_range("2025-01-01", periods=n_days, freq="B")
    rows = []

    def base_walk():
        return 1000 * np.cumprod(1 + rng.normal(0, 0.004, n_days))

    leader_day_close = base_walk()
    con_hi_close = leader_day_close * (1 + rng.normal(0, 0.001, n_days))
    con_lo_close = base_walk()

    for i, d in enumerate(days):
        legs = rng.normal(0, 0.002, 4)  # 4 段累计切分当日
        def intraday(c):
            segs = 1 + rng.normal(0, 0.001, 3)
            return [c * segs[0], c * segs[0] * segs[1], c * segs[0] * segs[1] * segs[2], c]
        leader_intra = intraday(leader_day_close[i])
        # CON_HI：日内腿跟随 leader 的日内相对变化
        hi_intra = [leader_intra[k] * (1 + rng.normal(0, 0.0002)) for k in range(4)]
        lo_intra = intraday(con_lo_close[i])
        for code, series in (("LEADER", leader_intra), ("CON_HI", hi_intra), ("CON_LO", lo_intra)):
            for hhmm, c in zip(("10:30", "11:30", "14:00", "15:00"), series):
                rows.append((code, pd.Timestamp(f"{d.date()} {hhmm}"), float(c)))
    return pd.DataFrame(rows, columns=["symbol", "datetime", "close"])


def test_minute_returns_three_legs_no_overnight():
    long = _minute_long(n_days=10)
    rets = minute_returns(long)
    # 每日 3 腿：10 个交易日 × 3 = 30 行
    assert len(rets) == 10 * 3
    # 首腿时间戳为 11:30（10:30 为每日首 bar，无收益被剔）
    assert rets.index[0].strftime("%H:%M") == "11:30"
    # 隔夜剔除：15:00 行的下一行时间不跨日接 10:30 产生收益（结构上 10:30 行已删）
    times = rets.index.strftime("%H:%M")
    assert "10:30" not in times


def test_minute_returns_5min_shape_47_legs():
    """5min（48 bar/日）结构：minute_returns 应产出每日 47 腿。"""
    rng = np.random.default_rng(5)
    d = pd.Timestamp("2025-01-06 09:30")
    times = (
        [d + pd.Timedelta(minutes=5 * k) for k in range(1, 25)]                 # 09:35..11:30
        + [d.replace(hour=13, minute=0) + pd.Timedelta(minutes=5 * k) for k in range(1, 25)]  # 13:05..15:00
    )
    walk = pd.Series(1000 * np.cumprod(1 + rng.normal(0, 0.001, 48)), index=pd.DatetimeIndex(times))
    long = pd.DataFrame({"symbol": "X", "datetime": times, "close": walk.values})
    rets = minute_returns(long)
    assert len(rets) == 47
    assert rets.index[0].strftime("%H:%M") == "09:40"   # 首 bar 09:35 无收益
    assert rets.index[-1].strftime("%H:%M") == "15:00"
    # 午休跨段（11:30→13:05）保留为日内腿，隔夜不存在（单日）
    stamps = rets.index.strftime("%H:%M")
    assert "13:05" in stamps


def test_minute_score_prefers_tracking_concept():
    long = _minute_long(n_days=40)
    rets = minute_returns(long)
    asof = pd.Timestamp(long["datetime"].max())
    hi = minute_resonance_score(rets, "CON_HI", "LEADER", asof, window_bars=60)
    lo = minute_resonance_score(rets, "CON_LO", "LEADER", asof, window_bars=60)
    assert hi > 0.85
    assert hi > lo + 0.3


def test_minute_score_insufficient_sample_nan():
    long = _minute_long(n_days=5)
    rets = minute_returns(long)
    val = minute_resonance_score(rets, "CON_HI", "LEADER", pd.Timestamp(long["datetime"].max()), window_bars=60)
    assert pd.isna(val)


def test_rank_fn_minute_layer_ranks_and_falls_back():
    # 合成日线宽表（3 宽基 + 2 概念），保证 LEADER 领先
    n = 120
    rng = np.random.default_rng(3)
    dates = pd.date_range("2024-10-01", periods=n, freq="B")
    leader = pd.Series(np.cumprod(1 + rng.normal(0.002, 0.005, n)), index=dates)
    other = pd.Series(np.cumprod(1 + rng.normal(0.0, 0.005, n)), index=dates)
    con_hi = leader * (1 + rng.normal(0, 0.001, n))
    con_lo = pd.Series(np.cumprod(1 + rng.normal(0, 0.006, n)), index=dates)
    close = pd.DataFrame({"LEADER": leader, "OTHER": other, "CON_HI": con_hi, "CON_LO": con_lo})
    minute_long = _minute_long(n_days=60)
    rets = minute_returns(minute_long)

    fn = make_minute_rank_fn(close, ["CON_HI", "CON_LO"], rets,
                             broad_codes=["LEADER", "OTHER"], window_bars=60)
    rk = fn(dates[-1])
    assert list(rk["concept"]) == ["CON_HI", "CON_LO"]

    # 降级：分钟表无 LEADER 列 → 返回日线排名（仍非空、顺序由日线相关决定）
    rets_no_leader = rets.drop(columns=["LEADER"])
    fn2 = make_minute_rank_fn(close, ["CON_HI", "CON_LO"], rets_no_leader,
                              broad_codes=["LEADER", "OTHER"], window_bars=60)
    rk2 = fn2(dates[-1])
    assert not rk2.empty and set(rk2["concept"]) == {"CON_HI", "CON_LO"}
    assert fn2.stats["minute_active"] == 0
