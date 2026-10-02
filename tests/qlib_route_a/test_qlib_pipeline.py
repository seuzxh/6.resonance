"""5min 因子管道测试（qlib env；合成数据）。"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("qlib")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research.qlib_route_a.qlib_pipeline import (  # noqa: E402
    FREQ5_EXPRESSIONS, build_features, daily_rank_ic, make_labels,
)
from research.qlib_route_a.qlib_provider import (  # noqa: E402
    ParquetData, init_qlib_parquet,
)


@pytest.fixture(scope="module")
def env():
    """两日 × 48 bar × 1 概念（价格线性递增可手算）+ 30 日日线。"""
    bars5 = []
    for day, base in (("2026-01-05", 100.0), ("2026-01-06", 110.0)):
        for k in range(48):
            hh, mm = (9, 35 + k) if k < 24 else (13, 5 + k - 24)
            ts = pd.Timestamp(f"{day} {hh:02d}:{mm:02d}:00")
            px = base + 0.1 * k
            bars5.append({"symbol": "CON1", "datetime": ts, "open": px,
                          "high": px + 0.5, "low": px - 0.5, "close": px,
                          "volume": 1000.0 + 10 * k})
    m5 = pd.DataFrame(bars5)
    days = pd.bdate_range("2025-12-01", periods=30)
    d = pd.DataFrame({"symbol": ["A"] * 30, "date": days,
                      "open": np.linspace(100, 130, 30),
                      "high": np.linspace(101, 131, 30),
                      "low": np.linspace(99, 129, 30),
                      "close": np.linspace(100, 130, 30),
                      "volume": np.full(30, 500.0)})
    pdata = ParquetData(bars_daily=d, bars_5min=m5,
                        pools={"all": ["A", "CON1"]})
    init_qlib_parquet(pdata)
    return pdata


def test_expressions_match_pandas(env):
    feats = build_features(env, concepts=["CON1"], start="2026-01-05",
                           end="2026-01-06", bridge_parquet=None)
    assert list(feats.index.get_level_values(0).unique()) == [
        pd.Timestamp("2026-01-05"), pd.Timestamp("2026-01-06")]
    # TAIL_MOM24 手算：第二日末 bar 收盘 = 110+0.1*47=114.7，
    # 23 根前 = 110+0.1*24=112.4 → 114.7/112.4-1
    got = feats.loc[(pd.Timestamp("2026-01-06"), "CON1"), "TAIL_MOM24"]
    assert abs(float(got) - (114.7 / 112.4 - 1)) < 1e-5
    # DAY_POS 手算：第二日 low=109.5（首 bar）、high=115.2（末 bar）、
    # 末 bar 收盘 114.7 → (114.7−109.5)/(115.2−109.5)=0.91228
    got_pos = feats.loc[(pd.Timestamp("2026-01-06"), "CON1"), "DAY_POS"]
    assert abs(float(got_pos) - (114.7 - 109.5) / (115.2 - 109.5)) < 1e-5


def test_labels_future_ref(env):
    lab = make_labels(env, concepts=["A"], start="2025-12-01",
                      end="2025-12-31")
    closes = env.wide("day", "close")["A"]
    manual = closes.shift(-1) / closes - 1
    got = lab["LABEL1"].droplevel("instrument")
    both = pd.concat([got.rename("g"), manual.rename("m")], axis=1).dropna()
    assert len(both) >= 10
    assert np.allclose(both["g"], both["m"], atol=1e-6)
    # 首日100，随后每个交易日增加30/29：未来上涨必须给出正标签。
    first = lab.loc[("A", pd.Timestamp("2025-12-01"))]
    assert float(first["LABEL1"]) == pytest.approx(0.010344827586, abs=1e-6)
    assert float(first["LABEL2"]) == pytest.approx(0.020689655172, abs=1e-6)
    assert float(first["LABEL5"]) == pytest.approx(0.051724137931, abs=1e-6)


def test_rank_ic_perfect_signal():
    idx = pd.MultiIndex.from_product(
        [pd.bdate_range("2026-01-05", periods=10), ["C1", "C2", "C3"]],
        names=["datetime", "instrument"])
    rng = np.random.default_rng(3)
    label = pd.Series(rng.normal(size=len(idx)), index=idx)
    m, t, n = daily_rank_ic(label, label)
    assert n == 10 and abs(m - 1.0) < 1e-9 and np.isinf(t)
