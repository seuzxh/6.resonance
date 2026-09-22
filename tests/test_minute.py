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


# --- v4：极值时刻弹性 ---
from resonance.minute import (  # noqa: E402
    DEAD_DAY_SPREAD,
    elasticity_table,
    extreme_window,
    make_elasticity_rank_fn,
)


def test_extreme_window_finds_max_updown_within_length_cap():
    # 构造 47 腿：第 10-13 腿 +2%（4 bar=20min 大涨），第 30-31 腿 -1.5%（2 bar 大跌）
    legs = np.zeros(47)
    legs[10:14] = 0.02
    legs[30:32] = -0.015
    ext = extreme_window(legs)
    assert ext["up"] == (10, 13) and abs(ext["r_up"] - 0.08) < 1e-9
    assert ext["dn"] == (30, 31) and abs(ext["r_dn"] + 0.03) < 1e-9
    # 长度上限约束：6 bar 的缓涨不得超过 4 bar 的急涨
    legs2 = np.zeros(47)
    legs2[5:11] = 0.01   # 6 bar × 1% = 6%
    legs2[20] = 0.05     # 1 bar × 5% = 5% → 若 6bar 允许则 up 应为 (5,10)；
    ext2 = extreme_window(legs2)
    assert abs(ext2["r_up"] - 0.05) < 1e-9  # 6% 窗口超长被禁，取 1bar 5%


def test_elasticity_table_direction_and_dead_day():
    """同步概念弹性 ≈1；反向概念 <0；死日（领导极值和 <0.1%）整行 NaN。"""
    rng = np.random.default_rng(9)
    n_days = 12
    rows = []
    for k, d in enumerate(pd.date_range("2025-01-06", periods=n_days, freq="B")):
        base = np.zeros(47)
        base[15:19] = 0.004          # 领导极值涨窗
        base[35:37] = -0.003         # 领导极值跌窗
        noise = rng.normal(0, 1e-4, 47)
        lead = base + noise
        sync = 1.2 * base + rng.normal(0, 1e-4, 47)   # 同向放大 1.2 倍
        inv = -0.8 * base + rng.normal(0, 1e-4, 47)    # 反向
        flat = rng.normal(0, 1e-4, 47)                 # 无响应
        times = (
            [d + pd.Timedelta(minutes=5 * i) for i in range(1, 25)]
            + [d.replace(hour=13, minute=0) + pd.Timedelta(minutes=5 * i) for i in range(1, 24)]
        )
        for code, arr in (("LEADER", lead), ("SYNC", sync), ("INV", inv), ("FLAT", flat)):
            for t, v in zip(times, arr):
                rows.append((code, t, v))
    mrets = pd.DataFrame(rows, columns=["symbol", "datetime", "ret"]).pivot(
        index="datetime", columns="symbol", values="ret").sort_index()
    mrets.index = pd.to_datetime(mrets.index)

    tabs = elasticity_table(mrets, ["LEADER"])
    tab = tabs["LEADER"]
    assert len(tab) == n_days
    med = tab.median()
    assert abs(med["SYNC"] - 1.2) < 0.2
    assert med["INV"] < -0.3
    assert abs(med["FLAT"]) < 0.3

    # 死日：领导日内波动 < 0.1% → 该日整行 NaN
    d_dead = pd.Timestamp("2025-01-06") + pd.Timedelta(days=20)
    dead_rows = []
    times = ([d_dead + pd.Timedelta(minutes=5 * i) for i in range(1, 25)]
             + [d_dead.replace(hour=13, minute=0) + pd.Timedelta(minutes=5 * i) for i in range(1, 24)])
    for code in ("LEADER", "SYNC"):
        for t in times:
            dead_rows.append((code, t, rng.normal(0, 1e-5)))
    mrets2 = pd.DataFrame(dead_rows, columns=["symbol", "datetime", "ret"]).pivot(
        index="datetime", columns="symbol", values="ret").sort_index()
    mrets2.index = pd.to_datetime(mrets2.index)
    tab2 = elasticity_table(mrets2, ["LEADER"])["LEADER"]
    assert len(tab2) == 0  # 死日无行
    assert DEAD_DAY_SPREAD == 1e-3


def test_elasticity_table_up_only_mode():
    """v4b（只涨窗）：e = C_up / R_up；跟涨概念 ≈1.2，反涨概念 <0，无跌幅参与。"""
    rng = np.random.default_rng(11)
    n_days = 12
    rows = []
    for d in pd.date_range("2025-01-06", periods=n_days, freq="B"):
        base = np.zeros(47)
        base[15:19] = 0.004          # 领导最大涨幅窗（和 1.6%）
        base[35:37] = -0.006         # 领导更大跌幅窗（-1.2%，up-only 应忽略）
        noise = rng.normal(0, 1e-4, 47)
        lead = base + noise
        follower = 1.2 * base + rng.normal(0, 1e-4, 47)  # 涨窗跟 1.2，跌窗跟 0.72
        # 构造"只跌不涨"的反例：涨窗为 0，跌窗放大 → up-only 弹性应 ≈0
        only_dn = np.zeros(47)
        only_dn[35:37] = -0.01
        only_dn = only_dn + rng.normal(0, 1e-4, 47)
        times = (
            [d + pd.Timedelta(minutes=5 * i) for i in range(1, 25)]
            + [d.replace(hour=13, minute=0) + pd.Timedelta(minutes=5 * i) for i in range(1, 24)]
        )
        for code, arr in (("LEADER", lead), ("FOLLOW", follower), ("ONLYDN", only_dn)):
            for t, v in zip(times, arr):
                rows.append((code, t, v))
    mrets = pd.DataFrame(rows, columns=["symbol", "datetime", "ret"]).pivot(
        index="datetime", columns="symbol", values="ret").sort_index()
    mrets.index = pd.to_datetime(mrets.index)

    tab = elasticity_table(mrets, ["LEADER"], use_down=False)["LEADER"]
    med = tab.median()
    # FOLLOW：涨窗响应 1.2×0.016/0.016 = 1.2（跌幅窗不参与，否则会被 1.2×-0.012 拖低）
    assert abs(med["FOLLOW"] - 1.2) < 0.2
    # ONLYDN：涨窗无响应 → e ≈ 0（若误用跌窗会得到大负值）
    assert abs(med["ONLYDN"]) < 0.2
