"""F1–F7 因子导出测试（resonance env；合成数据，离线）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from resonance.v3 import V3Params
from research.qlib_route_a.qlib_factor_export import export_factors


def _synthetic_close() -> pd.DataFrame:
    """3 锚 + 2 概念 + 全A × 40 日：锚 A 持续领涨且闸门多数日通过。"""
    days = pd.bdate_range("2026-01-05", periods=40)
    a = pd.Series(np.linspace(100, 140, 40), index=days)
    b = pd.Series(np.linspace(100, 105, 40), index=days)
    c = pd.Series(np.linspace(100, 101, 40), index=days)
    c1 = a * 1.01 + np.sin(np.arange(40))
    c2 = pd.Series(100.0, index=days)
    alla = a * 1.0
    return pd.DataFrame({"ANCHORA": a, "ANCHORB": b, "ANCHORC": c,
                         "CON1": c1, "CON2": c2, "ALLA": alla})


def test_export_schema_and_ranking_properties():
    close = _synthetic_close()
    df = export_factors(close, concepts=["CON1", "CON2"],
                        broad_codes=["ANCHORA", "ANCHORB", "ANCHORC"],
                        params=V3Params(), allA_code="ALLA")
    cols = {"date", "concept", "rank", "score", "sync", "capture",
            "leader", "gate", "half_life"}
    assert cols <= set(df.columns)
    # 概念2 三日复合恒为 0 → 不满足 >0，永不入榜
    assert "CON2" not in set(df["concept"])
    # 前 10 日无完整共振窗 → 首个信号日不早于第 11 日
    dates = sorted(df["date"].unique())
    assert dates[0] >= str(close.index[10].date())
    # 有信号日：leader=动量最强锚、rank 与 score 降序一致、半衰期在档位内
    d0 = df[df["date"] == dates[0]]
    assert d0["leader"].iloc[0] == "ANCHORA"
    assert list(d0["score"]) == sorted(d0["score"], reverse=True)
    assert list(d0["rank"]) == list(range(1, len(d0) + 1))
    assert set(df["half_life"].unique()) <= {5.0, 3.0, 2.0}
    assert df["gate"].all()


def test_gate_failure_days_have_zero_rows():
    # 锚全部阴跌 → leader 三日复合 ≤0 → 闸门失败 → 零行
    close = _synthetic_close()
    falling = pd.Series(np.linspace(140, 100, 40),
                        index=close.index)
    for col in ("ANCHORA", "ANCHORB", "ANCHORC"):
        close[col] = falling
    df = export_factors(close, concepts=["CON1", "CON2"],
                        broad_codes=["ANCHORA", "ANCHORB", "ANCHORC"],
                        params=V3Params(), allA_code="ALLA")
    assert len(df) == 0
