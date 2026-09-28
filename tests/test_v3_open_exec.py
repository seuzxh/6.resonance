"""V4.4 开盘成交口径测试（resonance env；合成数据，离线）。

语义合同（docs/spec/v44-open-exec-plan.md §一）：
1. 成交价 = T+1 开盘价；成交当日计 open→close 收益；
2. 后续日 close→close；卖出/换仓旧仓吃隔夜段 open(i)/close(i−1)；
3. 止损判定基准 = 入场日**收盘**价（非成交价）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from resonance.v3 import V3Backtester, V3Params


def _synthetic(n_days: int = 40):
    """1 锚 + 2 概念：锚稳定上涨（闸门恒过），概念 1 跟随、概念 2 更强。"""
    days = pd.bdate_range("2026-01-05", periods=n_days)
    rng = np.random.default_rng(11)
    anchor = pd.Series(np.linspace(100, 130, n_days), index=days)
    c1 = anchor * (1.0 + 0.002 * np.arange(n_days))
    c2 = pd.Series(np.linspace(100, 150, n_days), index=days)
    close = pd.DataFrame({"ANCHOR": anchor, "C1": c1, "C2": c2, "ALLA": anchor})
    gap = pd.DataFrame(rng.normal(0, 0.001, (n_days, 4)),
                       index=days, columns=close.columns)
    open_ = close.shift(1) * (1 + gap)
    open_.iloc[0] = close.iloc[0]
    return close, open_


def _run(close, open_, exec_price, params=None):
    p = (params or V3Params(topk=3, hl_source="leader", cost_bp=10.0)).with_(exec_price=exec_price)
    bt = V3Backtester(close, ["C1", "C2"], broad_codes=["ANCHOR"],
                      allA_code="ALLA", params=p,
                      open_all=open_ if exec_price == "open" else None)
    return bt.run(close.index[12], close.index[-1])


def _prev_day(close, d):
    return close.index[close.index.get_loc(d) - 1]


def test_close_mode_fills_at_close():
    close, open_ = _synthetic()
    tr = _run(close, open_, "close")["trades"]
    assert len(tr) >= 1
    for _, t in tr.iterrows():
        code = t["to"] or t["from"]
        assert abs(t["price"] - close.at[t["date"], code]) < 1e-9


def test_open_mode_fills_at_open():
    close, open_ = _synthetic()
    tr = _run(close, open_, "open")["trades"]
    assert len(tr) >= 1
    for _, t in tr.iterrows():
        code = t["to"] or t["from"]
        assert abs(t["price"] - open_.at[t["date"], code]) < 1e-9, \
            f"{t['date']} 成交价应为开盘价"


def test_open_mode_nav_identity():
    """逐日手工复算 nav：执行日 open→close、其余日 close/close、事件成本、
    卖出/换仓隔夜段。允许 1e-9 容差。"""
    close, open_ = _synthetic()
    out = _run(close, open_, "open")
    nav = out["nav_curve"]
    trades = {row["date"]: row for _, row in out["trades"].iterrows()}
    holding = exec_day = None
    cost = 0.0
    expect = 1.0
    for d, cur in nav.items():
        prev = _prev_day(close, d)
        if d in trades:
            t = trades[d]
            cost = (1 - 1e-3) ** 2 if t["type"] == "switch" else (1 - 1e-3)  # 10bp = 1e-3
            if t["type"] in ("exit", "stop"):
                frm = t["from"]
                expect *= open_.at[d, frm] / close.at[prev, frm]
            holding = t["to"]
            exec_day = d
            expect *= cost
        if holding is not None and d >= exec_day:
            base = (open_.at[d, holding] if d == exec_day
                    else close.at[prev, holding])
            expect *= close.at[d, holding] / base
        assert abs(cur - expect) < 1e-9, f"{d}: nav {cur} ≠ 复算 {expect}"


def test_stop_base_is_entry_close_not_fill_open():
    """入场后崩塌触发止损：开盘口径下判定基准是入场日收盘价。"""
    close, open_ = _synthetic()
    # 单日急跌至入场收盘×0.94：介于"入场收盘×0.95"与"入场开盘×0.95"之间——
    # 只有以收盘为基准才会触发（区分成交价基准的判别性设计；缓慢阴跌会被
    # 排名通道先行换出，到不了止损）。
    entry_day = close.index[13]          # 合成数据下首个执行日（确定性）
    crash_day = close.index[18]
    close.at[crash_day, "C2"] = close.at[entry_day, "C2"] * 0.94
    open_.at[crash_day, "C2"] = close.at[crash_day, "C2"]
    out = _run(close, open_, "open",
               V3Params(topk=3, hl_source="leader", stop_loss=0.05))
    stops = out["stops"]
    assert len(stops) >= 1, "崩塌场景应触发止损"
    tr_entry = out["trades"].iloc[0]
    entry_close = close.at[tr_entry["date"], tr_entry["to"]]
    sig_d = stops.iloc[0]["signal_date"]
    held = stops.iloc[0]["code"]
    assert close.at[sig_d, held] <= entry_close * 0.95 + 1e-9, \
        "触发日持仓收盘应低于入场收盘×(1−5%)（基准=入场收盘）"
