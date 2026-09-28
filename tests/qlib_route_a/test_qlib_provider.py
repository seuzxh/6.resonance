"""qlib provider 合成数据测试（qlib env 运行；resonance env 自动跳过）。"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("qlib")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research.qlib_route_a.qlib_provider import (  # noqa: E402
    ParquetCalendarProvider, ParquetData, ParquetFeatureProvider,
    ParquetInstrumentProvider, init_qlib_parquet,
)


@pytest.fixture(scope="module")
def pdata() -> ParquetData:
    days = pd.bdate_range("2026-01-05", periods=6)
    bars = pd.DataFrame(
        {"symbol": ["A"] * 6 + ["B"] * 4,
         "date": list(days) + list(days[:4]),
         "open": np.linspace(10, 12, 6).tolist() + np.linspace(5, 6, 4).tolist(),
         "high": np.linspace(11, 13, 6).tolist() + np.linspace(6, 7, 4).tolist(),
         "low": np.linspace(9, 11, 6).tolist() + np.linspace(4, 5, 4).tolist(),
         "close": [10.0, 11.0, 12.0, 11.5, 10.5, 11.0] + [5.0, 5.5, 6.0, 5.8],
         "volume": np.arange(10, dtype=float)})
    ts = pd.to_datetime(["2026-01-05 09:35", "2026-01-05 09:40",
                         "2026-01-06 09:35", "2026-01-06 09:40"] * 2)
    m5 = pd.DataFrame(
        {"symbol": ["A"] * 4 + ["B"] * 4, "datetime": ts,
         "open": [10.0, 10.1, 10.7, 10.9] + [5.0, 5.05, 5.15, 5.25],
         "high": [10.2, 10.3, 10.9, 11.1] + [5.1, 5.2, 5.3, 5.4],
         "low": [9.9, 10.0, 10.6, 10.8] + [4.9, 5.0, 5.1, 5.2],
         "close": [10.0, 10.2, 10.8, 11.0] + [5.0, 5.1, 5.2, 5.3],
         "volume": np.full(8, 100.0)})
    return ParquetData(bars_daily=bars, bars_5min=m5,
                       pools={"all": ["A", "B"], "anchor": ["A"]})


def test_calendar_dual_freq(pdata):
    cal_day = ParquetCalendarProvider(pdata).load_calendar("day")
    cal_m5 = ParquetCalendarProvider(pdata).load_calendar("5min")
    assert len(cal_day) == 6 and len(cal_m5) == 4  # 8 行但唯一时刻 4 个
    assert isinstance(cal_day, np.ndarray)


def test_feature_series_indexed_by_calendar_position(pdata):
    prov = ParquetFeatureProvider(pdata)
    s = prov.feature("A", "$close", 0, 5, "day")
    assert isinstance(s, pd.Series)
    assert list(s.index) == [0, 1, 2, 3, 4, 5]
    assert s.tolist() == [10.0, 11.0, 12.0, 11.5, 10.5, 11.0]
    s2 = prov.feature("B", "$close", 4, 5, "day")
    assert s2.isna().all()  # 缺失标的 → 全 NaN（停牌语义）


def test_instrument_pools(pdata):
    prov = ParquetInstrumentProvider(pdata)
    out = prov.list_instruments({"market": "anchor", "filter_pipe": []},
                                as_list=True)
    assert out == ["A"]
    out2 = prov.list_instruments({"market": ["A", "B"], "filter_pipe": []},
                                 as_list=True)
    assert sorted(out2) == ["A", "B"]


def test_lazy_cache_and_wide(pdata):
    prov = ParquetFeatureProvider(pdata)
    prov.feature("A", "$close", 0, 0, "day")
    assert ("day", "A", "close") in prov._cache
    w = pdata.wide("day", "open")
    assert "A" in w.columns and len(w) == 6


def test_init_and_d_features(pdata):
    init_qlib_parquet(pdata)  # 全局 init；本模块后续测试复用
    from qlib.data import D

    assert len(D.calendar(freq="day")) == 6
    df = D.features(["A"], ["$close"], None, None, freq="day", disk_cache=0)
    assert df["$close"].tolist() == [10.0, 11.0, 12.0, 11.5, 10.5, 11.0]
    # 表达式（契约⑤：Ref(X,N)=X[t-N]）
    de = D.features(["A"], ["$close/Ref($close,2)-1"], None, None,
                    freq="day", disk_cache=0)
    got = de["$close/Ref($close,2)-1"].tolist()
    assert got[0] != got[0]  # NaN（无更早数据）
    assert abs(got[2] - (12.0 / 10.0 - 1)) < 1e-6
    # 契约⑨：DayLast 需 custom_ops 注册，每日末值传播到当日全部 bar
    d5 = D.features(["A"], ["DayLast($close)"], None, None,
                    freq="5min", disk_cache=0)
    assert np.allclose(d5["DayLast($close)"].tolist(), [10.2] * 2 + [11.0] * 2,
                       atol=1e-6)  # float32 精度（契约⑤）


def test_provider_requires_injection():
    with pytest.raises(RuntimeError):
        ParquetCalendarProvider().load_calendar("day")
