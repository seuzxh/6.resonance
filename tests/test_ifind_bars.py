"""fetch_minute_bars 多指标解析与请求切分的离线测试（mock _post，无网络）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from resonance import ifind  # noqa: E402


def _resp(tables):
    return {"errorcode": 0, "tables": tables}


def test_multi_indicator_parse_and_snake_case(monkeypatch):
    """9 指标 → snake_case 列；空值转 NaN；全空 bar 跳行。"""
    tables = [{
        "thscode": "885311.TI",
        "time": ["2026-09-23 14:55", "2026-09-23 15:00", "2026-09-23 15:05"],
        "table": {
            "open": [10.0, "", 10.5], "high": [10.2, 10.3, None],
            "low": [9.9, 10.0, 9.8], "close": [10.1, 10.25, 10.4],
            "avgPrice": [10.05, 10.15, 10.2], "volume": [100, 200, 300],
            "amount": [1005.0, 2030.0, 3060.0],
            "change": [0.1, 0.15, 0.2], "changeRatio": [1.0, 1.5, 2.0],
        },
    }]
    # 第三根 bar 全字段空/None → 跳行
    tables[0]["table"] = {k: (v[:2] + [None]) for k, v in tables[0]["table"].items()}
    monkeypatch.setattr(ifind, "_post", lambda url, payload, timeout=120: _resp(tables))
    df = ifind.fetch_minute_bars(["885311.TI"], "2026-09-23", "2026-09-23")
    assert list(df.columns) == ["symbol", "datetime", *ifind.MINUTE_INDICATOR_COLS]
    assert len(df) == 2, "全空 bar 必须跳行"
    r0 = df.iloc[0]
    assert r0["open"] == 10.0 and r0["avg_price"] == 10.05 and r0["change_ratio"] == 1.0
    import math
    assert math.isnan(df.iloc[1]["open"]), '空串必须转 NaN'


def test_request_split_by_points_budget(monkeypatch):
    """单 code 一年 5min×9 指标超 45k 点 → 必须拆多次请求且日期连续覆盖。"""
    calls = []

    def fake_post(url, payload, timeout=120):
        calls.append(payload)
        return _resp([])

    monkeypatch.setattr(ifind, "_post", fake_post)
    monkeypatch.setattr(ifind.time, "sleep", lambda s: None)
    ifind.fetch_minute_bars(["A.TI"], "2025-09-22", "2026-09-26")
    assert len(calls) >= 2, "370 天×48×9=160k 点必须切分（45k/请求上限）"
    # 日期无缝衔接：请求 i 的 starttime > 请求 i-1 的 endtime，且首尾覆盖全区间
    starts = [p["starttime"].split()[0] for p in calls]
    ends = [p["endtime"].split()[0] for p in calls]
    assert starts[0] == "2025-09-22" and ends[-1] == "2026-09-26"
    for s, prev_e in zip(starts[1:], ends[:-1]):
        assert s > prev_e
    assert all(p["indicators"].count(",") == 8 for p in calls)
    assert all(p["functionpara"]["Interval"] == "5" for p in calls)


def test_fetch_minute_close_compat():
    """fetch_minute_bars 单指标路径：列与请求参数与旧口径兼容。"""
    payload_seen = []

    def fake_post(url, payload, timeout=120):
        payload_seen.append(payload)
        return _resp([{"thscode": "X.TI", "time": ["2026-09-23 15:00"],
                       "table": {"close": [10.0]}}])

    orig_post = ifind._post
    ifind._post = fake_post
    try:
        df = ifind.fetch_minute_close(["X.TI"], "2026-09-23", "2026-09-23")
    finally:
        ifind._post = orig_post
    assert list(df.columns) == ["symbol", "datetime", "close"]
    assert df.iloc[0]["close"] == 10.0
    assert payload_seen[0]["indicators"] == "close"
    assert payload_seen[0]["functionpara"]["Interval"] == "60"


def test_empty_response_returns_typed_columns(monkeypatch):
    monkeypatch.setattr(ifind, "_post", lambda url, payload, timeout=120: _resp([]))
    df = ifind.fetch_minute_bars(["NO.TI"], "2026-09-23", "2026-09-23")
    assert df.empty
    assert list(df.columns) == ["symbol", "datetime", *ifind.MINUTE_INDICATOR_COLS]
