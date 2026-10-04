from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.exposure_danger_diagnostic_run import (
    add_bins, signal_date_before, summarize_bins,
)
from research.exposure_danger_diagnostic_run import test_candidates as evaluate_candidates


def test_signal_date_is_previous_trading_day():
    cal = pd.bdate_range("2026-01-05", periods=5)
    assert signal_date_before(cal, cal[2]) == cal[1]


def test_bins_use_preregistered_quartiles():
    values = np.arange(1.0, 41.0)
    events = pd.DataFrame({feature: values for feature in (
        "leader3", "leader_dd10", "leader_dvol10", "vol_ratio_5_20",
        "anchor_mom_gap10", "anchor_corr20", "concept3", "concept10",
        "concept_minus_leader3",
    )})
    binned, thresholds = add_bins(events)
    assert binned["leader3_bin"].value_counts().sort_index().tolist() == [10, 10, 10, 10]


def test_candidate_requires_cross_stage_consistency():
    rows = []
    for stage, direction in [("early", 1.0), ("recent", -1.0)]:
        for bin_name, adverse, ret in [("Q1", 0.2 if direction > 0 else 0.8, 0.02 if direction > 0 else -0.02),
                                        ("Q4", 0.8 if direction > 0 else 0.2, -0.02 if direction > 0 else 0.02)]:
            rows.append({"stage": stage, "feature": "leader3", "bin": bin_name,
                         "n": 20, "adverse_rate": adverse, "median_return": ret})
    summary = pd.DataFrame(rows)
    binned = pd.DataFrame({"leader3": np.arange(40.0), "leader3_bin": ["Q1"] * 10 + ["Q2"] * 10 + ["Q3"] * 10 + ["Q4"] * 10,
                           "stage": ["early"] * 20 + ["recent"] * 20,
                           "adverse_exit": [False] * 40, "year_month": ["2026-01"] * 40})
    result = evaluate_candidates(binned, summary)
    assert result.iloc[0]["risk_end"] == "none"
    assert not result.iloc[0]["candidate"]
