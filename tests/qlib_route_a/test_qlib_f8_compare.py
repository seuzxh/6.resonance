"""F8 替换钩子测试（resonance env；合成数据，离线）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd

from research.qlib_route_a.qlib_f8_compare import make_model_post_rank


def _ranking():
    return pd.DataFrame({
        "concept": ["C1", "C2", "C3", "C4", "C5"],
        "score": [5.0, 4.0, 3.0, 2.0, 1.0]})


def test_reorder_by_model_score():
    pred = pd.DataFrame({"date": ["2026-01-05"] * 3,
                         "concept": ["C3", "C1", "C4"],
                         "pred": [0.9, 0.5, 0.1]})
    fn, stats = make_model_post_rank(pred)
    out = fn(_ranking(), pd.Timestamp("2026-01-05"))
    # 与 F8 语义一致：最终榜只含可评分概念，按模型分降序
    assert list(out["concept"]) == ["C3", "C1", "C4"]
    assert stats["model_days"] == 1 and stats["model_excluded"] == 2


def test_fallback_when_sparse():
    pred = pd.DataFrame({"date": ["2026-01-05"],
                         "concept": ["C1"], "pred": [0.3]})
    fn, stats = make_model_post_rank(pred)
    out = fn(_ranking(), pd.Timestamp("2026-01-05"))
    assert list(out["concept"]) == ["C1", "C2", "C3", "C4", "C5"]  # 原序
    assert stats["model_fallback_sparse"] == 1


def test_missing_day_falls_back():
    # 当日无任何预测 → 全部剔除 → 可评 0 <2 → 回退
    pred = pd.DataFrame({"date": ["2026-01-06"], "concept": ["C1"],
                         "pred": [0.3]})
    fn, stats = make_model_post_rank(pred)
    out = fn(_ranking(), pd.Timestamp("2026-01-05"))
    assert list(out["concept"]) == ["C1", "C2", "C3", "C4", "C5"]
    assert stats["model_fallback_sparse"] == 1 and stats["model_excluded"] == 5
