"""signal_daily runner 数据纪律离线测试（全部 mock 网络，无真实请求）。

覆盖 2026-09-23 盘中运行事故的三处修复：
1. update_daily：补采窗口按活跃 code 最小末日推进 + keep="last" 幂等覆盖
   ——盘中写入的"昨收快照"行必须能被收盘真值修复；
2. topup_minute_for_window：按"当日是否有 bar"判新鲜度——跨日旧 bar 非空
   不算覆盖（事故当日尾盘 24bar 从未被采到）；
3. 盘中硬闸：15:05 前拒绝以当日为终点运行。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

import work.signal_daily as sd


def test_update_daily_overwrites_stale_and_fills_laggard(tmp_path, monkeypatch):
    f = tmp_path / "daily_bars.parquet"
    pd.DataFrame([("AAA", "2026-09-22", 10.0), ("AAA", "2026-09-23", 10.0),   # 09-23 为污染行
                  ("BBB", "2026-09-22", 20.0)], columns=["symbol", "date", "close"]
                 ).to_parquet(f, index=False)
    monkeypatch.setattr(sd.config, "CACHE_DIR", tmp_path, raising=False)

    def fake_fetch(codes, s, e):
        rows = [{"symbol": c, "date": "2026-09-23", "close": 11.5 if c == "AAA" else 21.0}
                for c in codes]
        return [pd.DataFrame(rows)]

    monkeypatch.setattr(sd, "fetch_history_data", fake_fetch)
    sd.update_daily("2026-09-23")

    out = pd.read_parquet(f).set_index(["symbol", "date"])["close"]
    assert out[("AAA", "2026-09-23")] == 11.5, "重采真值必须覆盖污染行（keep=last）"
    assert out[("BBB", "2026-09-23")] == 21.0, "落后 code 必须被补齐（start=min 活跃末日）"
    assert len(out) == 4, "不应产生重复行（AAA/BBB × 09-22/09-23）"


def test_topup_requires_bars_on_the_exact_day(tmp_path, monkeypatch):
    f = tmp_path / "minute5_bars.parquet"
    old = pd.DataFrame({"symbol": ["C1"] * 3 + ["LDR"] * 3,
                        "datetime": pd.to_datetime(
                            ["2026-09-18 14:45", "2026-09-18 14:50", "2026-09-18 14:55"] * 2),
                        "close": [1.0, 1.0, 1.0] * 2})
    old.to_parquet(f, index=False)
    monkeypatch.setattr(sd.config, "CACHE_DIR", tmp_path, raising=False)

    calls = []

    def fake_fetch(codes, s, e, interval="5", day_start="09:30:00"):
        calls.append((codes[0], s))
        return pd.DataFrame({"symbol": [codes[0]] * 2,
                             "datetime": pd.to_datetime([f"{s} 14:50", f"{s} 14:55"]),
                             "close": [1.0, 1.1]})

    monkeypatch.setattr(sd, "fetch_minute_close", fake_fetch)

    idx = pd.bdate_range("2026-09-01", "2026-09-23")
    close_all = pd.DataFrame({"LDR": pd.Series(1.01, index=idx).cumprod(),
                              "C1": pd.Series(1.02, index=idx).cumprod(),
                              "883957.TI": 1.0})  # V3Signals 需全A 列（半衰期基准）
    monkeypatch.setattr(sd, "OOS_START", "2026-09-22")

    n = sd.topup_minute_for_window(close_all, ["C1"], ["LDR"], "2026-09-23")
    fetched = {(c, d) for c, d in calls}
    assert ("C1", "2026-09-22") in fetched, "当日无 bar 必须补采（跨日旧 bar 不算覆盖）"
    assert ("C1", "2026-09-23") in fetched
    assert n >= 2

    calls.clear()
    assert sd.topup_minute_for_window(close_all, ["C1"], ["LDR"], "2026-09-23") == 0
    assert calls == [], "补齐后幂等：当日已有 bar 不再补采"


def test_intraday_guard_blocks_today_before_close(monkeypatch, capsys):
    monkeypatch.setattr(pd.Timestamp, "now",
                        staticmethod(lambda tz=None: pd.Timestamp("2026-09-24 11:29:00")))
    monkeypatch.setattr(sd, "update_daily",
                        lambda d: pytest.fail("盘中运行不得触达采集"))
    argv_backup = sys.argv
    sys.argv = ["signal_daily.py", "--date", "2026-09-24"]
    try:
        rc = sd.main()
    finally:
        sys.argv = argv_backup
    assert rc == 2
    assert "拒绝盘中运行" in capsys.readouterr().out
