from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.exposure_attribution_run import add_recovery_flags, build_segments


def test_build_segments_switch_cost_boundary():
    trades = pd.DataFrame([
        {"date": "2026-01-02", "type": "entry", "from": None, "to": "A", "nav": 0.999},
        {"date": "2026-01-05", "type": "switch", "from": "A", "to": "B", "nav": 0.999 * 1.02},
        {"date": "2026-01-06", "type": "exit", "from": "B", "to": None, "nav": 0.999 * 1.02 * 1.01 * 0.999},
    ])
    nav = pd.DataFrame({"date": pd.bdate_range("2026-01-02", periods=3), "nav": np.linspace(1, 1.02, 3)})
    seg = build_segments(trades, nav)
    assert seg["exit_reason"].tolist() == ["switch", "exit"]
    assert seg["origin"].tolist() == ["cold_entry", "switch_entry"]
    # A 段终点剔除 B 的买入成本，只保留 A 的双边成本。
    expected_a = 1.02 / (1 - 0.001) - 1.0
    assert abs(seg.loc[0, "net_return"] - expected_a) < 1e-12
    assert not seg["open_position"].any()


def test_open_segment_is_excluded_and_counted():
    trades = pd.DataFrame([
        {"date": "2026-01-02", "type": "entry", "from": None, "to": "A", "nav": 0.999},
    ])
    nav = pd.DataFrame({"date": pd.bdate_range("2026-01-02", periods=4), "nav": [1, 1, 1, 1]})
    seg = build_segments(trades, nav)
    assert len(seg) == 1 and bool(seg.loc[0, "open_position"])
    assert pd.isna(seg.loc[0, "net_return"])


def test_recovery_flag_uses_pre_reentry_window_only():
    days = pd.bdate_range("2026-01-02", periods=5)
    seg = pd.DataFrame([
        {"start_date": days[0], "end_date": days[0], "concept": "A", "origin": "cold_entry",
         "exit_reason": "exit", "net_return": -0.05, "holding_days": 1, "open_position": False},
        {"start_date": days[4], "end_date": days[4], "concept": "B", "origin": "cold_entry",
         "exit_reason": "exit", "net_return": 0.03, "holding_days": 1, "open_position": False},
    ])
    bars = pd.DataFrame([
        {"symbol": "399001.SZ", "date": day, "close": 100.0}
        for day in days
    ] + [
        {"symbol": "000852.SH", "date": day, "close": 100.0}
        for day in days
    ])
    bars.loc[(bars["symbol"] == "399001.SZ") & (bars["date"] == days[2]), "close"] = 106.0
    out = add_recovery_flags(seg, bars)
    assert bool(out.loc[0, "recovery_before_reentry"])
    assert abs(out.loc[0, "next_net_return"] - 0.03) < 1e-12
    assert not bool(out.loc[1, "recovery_before_reentry"])
