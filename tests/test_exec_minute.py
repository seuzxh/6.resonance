"""exec_minute 离线单测：合成数据验证成交时点重算与盘中止损路径（含边界与降级）。"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from resonance.backtest import RotationBacktester
from resonance.exec_minute import (
    MinutePrices,
    build_minute_wide,
    rerun_trade_times,
    run_with_intraday_stop,
)


def _wide(rows: dict[str, dict[str, float]], idx: pd.DatetimeIndex) -> pd.DataFrame:
    df = pd.DataFrame(index=idx)
    for col, series in rows.items():
        df[col] = [series.get(str(d.date()), np.nan) for d in idx]
    return df


def _minute_long(day: str, bars: dict[str, dict[str, float]]) -> pd.DataFrame:
    recs = []
    for code, tspx in bars.items():
        for ts, px in tspx.items():
            recs.append({"symbol": code, "datetime": pd.Timestamp(f"{day} {ts}"), "close": px})
    return pd.DataFrame(recs)


def test_t1500_reproduces_baseline_nav():
    """t='15:00' 时净值必须逐日复现基线引擎（等价性前提）。"""
    idx = pd.date_range("2026-01-05", periods=8, freq="B")
    close = _wide({"A": {str(d.date()): 100 + 2 * i for i, d in enumerate(idx)},
                   "B": {str(d.date()): 50 + i for i, d in enumerate(idx)}}, idx)
    calls = {"n": 0}

    def rank_fn(asof):
        calls["n"] += 1
        first = "A" if calls["n"] % 2 else "B"  # 每次检查换榜，制造多回合调仓
        second = "B" if first == "A" else "A"
        return pd.DataFrame({"concept": [first, second], "corr": [0.9, 0.5]})

    bt = RotationBacktester(close, rank_fn, rebalance_days=2, topk=1)
    out = bt.run()
    prices = MinutePrices(close, close.copy(), pd.DataFrame())
    rerun = rerun_trade_times(out, prices, "15:00")
    pd.testing.assert_series_equal(
        rerun["nav_curve"].dropna(), out["nav_curve"].dropna(), check_names=False)


def test_intraday_trade_time_factor_math():
    """手算校验：调仓日 t 的净值因子 = (P_t/C)_旧 × (C/P_t)_新；窗口首日强制收盘。"""
    idx = pd.date_range("2026-01-05", periods=6, freq="B")
    d = [str(x.date()) for x in idx]
    close = _wide({"A": {d[0]: 100, d[1]: 110, d[2]: 121, d[3]: 125.0},
                   "B": {d[3]: 165, d[4]: 181.5, d[5]: 181.5}}, idx)
    minute = build_minute_wide(_minute_long(d[3], {"A": {"09:35": 130.0}, "B": {"09:35": 150.0}}))
    prices = MinutePrices(close, close.copy(), minute)
    bt_out = {
        "nav_curve": pd.Series(1.0, index=idx),
        "switches": [
            {"date": idx[1], "action": "switch", "from": None, "to": "A"},
            {"date": idx[4], "action": "switch", "from": "A", "to": "B"},
        ],
    }
    rerun = rerun_trade_times(bt_out, prices, "09:35")
    expected = (110 / 100) * (121 / 110) * ((130 / 121) * (165 / 150)) * (181.5 / 165) * 1.0
    assert math.isclose(rerun["nav_curve"].iloc[-1], expected, rel_tol=1e-12)
    # 窗口首日（首调仓 τ=信号日的 exec_lag 边界）强制收盘 → 首日因子恒 1
    assert math.isclose(rerun["nav_curve"].iloc[0], 1.0, rel_tol=1e-12)
    # 逐调仓对数收益差：首段（入场）无旧仓不记，A→B 一笔
    assert len(rerun["log_ratios"]) == 1
    assert math.isclose(rerun["log_ratios"][0], math.log(130 / 125) + math.log(165 / 150), rel_tol=1e-12)


def test_missing_minute_price_degrades_to_close():
    """无分钟数据时成交价退化为当日收盘（计数上报）。"""
    idx = pd.date_range("2026-01-05", periods=4, freq="B")
    d = [str(x.date()) for x in idx]
    close = _wide({"A": {d[0]: 100, d[1]: 110, d[2]: 115, d[3]: 120.0},
                   "B": {d[1]: 50, d[2]: 55, d[3]: 60.0}}, idx)
    prices = MinutePrices(close, close.copy(), pd.DataFrame())
    bt_out = {"nav_curve": pd.Series(1.0, index=idx),
              "switches": [{"date": idx[2], "action": "switch", "from": "A", "to": "B"},
                           {"date": idx[1], "action": "switch", "from": None, "to": "A"}]}
    rerun = rerun_trade_times(bt_out, prices, "10:30")
    # τ=d[1]（A→B 调仓）无 10:30 分钟价 → 双侧退化收盘，净值应与 15:00 基线一致
    expected = (110 / 100) * 1.0 * (55 / 50) * (60 / 55)
    assert math.isclose(rerun["nav_curve"].iloc[-1], expected, rel_tol=1e-12)
    assert prices.degraded >= 1


def test_intraday_stop_triggers_mid_path():
    """盘中止损：峰值回撤越限 → 触发 bar 卖出，空仓至下一检查日再按引擎规则入场。"""
    idx = pd.date_range("2026-01-05", periods=8, freq="B")
    d = [str(x.date()) for x in idx]
    close = _wide({"A": {d[0]: 100, d[1]: 102, d[2]: 97, d[3]: 96, d[4]: 98,
                         d[5]: 100, d[6]: 103, d[7]: 104.0}}, idx)
    longs = [
        _minute_long(d[1], {"A": {"09:35": 101.0, "15:00": 102.0}}),
        _minute_long(d[2], {"A": {"09:35": 98.0, "15:00": 97.0}}),
        _minute_long(d[3], {"A": {"09:35": 97.0, "09:40": 96.5}}),
        _minute_long(d[6], {"A": {"09:35": 103.5, "15:00": 103.0}}),
        _minute_long(d[7], {"A": {"09:35": 104.0, "15:00": 104.0}}),
    ]
    minute = build_minute_wide(pd.concat(longs, ignore_index=True))

    def rank_fn(asof):
        return pd.DataFrame({"concept": ["A"], "corr": [0.8]})

    prices = MinutePrices(close, close.copy(), minute)
    out = run_with_intraday_stop(close, prices, rank_fn, rebalance_days=5, topk=5,
                                 stop_pct=0.05, mode="minute")
    nav = out["nav_curve"]
    # 路径：d1 收盘 102（峰值 102）；d2 收盘 97（97 > 102*0.95=96.9 不触发）；
    # d3 09:40 mark 96.5 < 96.9 → 止损：当日因子 96.5/97；d4/d5 空仓；检查日 d6
    # （i=6=1+5）重新入场 A（anchor=C_{d5}=100）→ d6 因子 103/100；d7 104/103。
    expected = 1.0 * (102 / 100) * (97 / 102) * (96.5 / 97) * 1.0 * 1.0 * (103 / 100) * (104 / 103)
    assert math.isclose(nav.iloc[-1], expected, rel_tol=1e-12)
    assert out["stats"]["stops"] == 1
    assert out["stats"]["flat_days"] == 2
    ev = out["stop_events"][0]
    assert str(ev["date"].date()) == d[3] and ev["time"] is not None


def test_close_mode_stop_only_at_close():
    """收盘粒度对照：同路径仅在收盘 mark 判定（d3 09:40 的破位不触发，收盘 96 触发）。"""
    idx = pd.date_range("2026-01-05", periods=8, freq="B")
    d = [str(x.date()) for x in idx]
    close = _wide({"A": {d[0]: 100, d[1]: 102, d[2]: 97, d[3]: 96, d[4]: 98,
                         d[5]: 100, d[6]: 103, d[7]: 104.0}}, idx)
    longs = [_minute_long(d[1], {"A": {"15:00": 102.0}}),
             _minute_long(d[2], {"A": {"15:00": 97.0}}),
             _minute_long(d[3], {"A": {"09:40": 96.5, "15:00": 96.0}}),
             _minute_long(d[6], {"A": {"15:00": 103.0}}),
             _minute_long(d[7], {"A": {"15:00": 104.0}})]
    minute = build_minute_wide(pd.concat(longs, ignore_index=True))

    def rank_fn(asof):
        return pd.DataFrame({"concept": ["A"], "corr": [0.8]})

    prices = MinutePrices(close, close.copy(), minute)
    out = run_with_intraday_stop(close, prices, rank_fn, rebalance_days=5, topk=5,
                                 stop_pct=0.05, mode="close")
    expected = 1.0 * (102 / 100) * (97 / 102) * (96 / 97) * 1.0 * 1.0 * (103 / 100) * (104 / 103)
    assert math.isclose(out["nav_curve"].iloc[-1], expected, rel_tol=1e-12)
    assert out["stats"]["stops"] == 1


def test_no_stop_reproduces_baseline():
    """stop_pct=0 相当于从不止损：分钟模式净值应与基线引擎一致（等价性前提）。"""
    idx = pd.date_range("2026-01-05", periods=10, freq="B")
    d = [str(x.date()) for x in idx]
    px_a = {dd: 100 + 3 * i + (i % 3) for i, dd in enumerate(d)}
    px_b = {dd: 50 - 0.5 * i for i, dd in enumerate(d)}
    close = _wide({"A": px_a, "B": px_b}, idx)

    def rank_fn(asof):
        return pd.DataFrame({"concept": ["A", "B"], "corr": [0.9, 0.4]})

    bt = RotationBacktester(close, rank_fn, rebalance_days=3)
    base = bt.run()
    longs = []
    for dd in d:
        longs.append(_minute_long(dd, {"A": {"09:35": px_a[dd] * 0.99, "15:00": float(px_a[dd])}}))
    minute = build_minute_wide(pd.concat(longs, ignore_index=True))
    prices = MinutePrices(close, close.copy(), minute)
    # 极宽止损带（99.9%）保证路径上永不触发 → 与基线引擎等价
    out = run_with_intraday_stop(close, prices, rank_fn, rebalance_days=3, topk=5,
                                 stop_pct=0.999, mode="minute")
    assert np.allclose(out["nav_curve"].values, base["nav_curve"].values, rtol=1e-9)
