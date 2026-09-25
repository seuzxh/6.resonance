"""V3 引擎离线测试（全部合成数据，无网络）。

场景构造：bdate 日历，warmup 12 日使首信号日（i11）满足全部窗口回看。
LDR 恒 +1%/日（领先且闸门常开）、LDR2 恒 −1%/日、ALLA 恒平（回撤 0 →
半衰期 5）；概念以恒定日收益构造，用 +5% 突增/−6% 崩塌控制榜单与止损。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import math

import numpy as np

import pandas as pd
import pytest

from resonance.v3 import (
    V3Backtester,
    V3Params,
    compound_window,
    dynamic_half_life,
    up_resonance_scores,
    v3_ranking,
    yearly_returns,
)

BROAD = ["LDR", "LDR2"]
CONCEPTS = ["C_GOOD", "C_MID", "C_NEWC", "C_ALT"]


def mk_close(schedules: dict[str, list[float]], n: int) -> pd.DataFrame:
    """每 code 一条日收益序列（长度 n，不足取末值重复）→ 收盘宽表。"""
    idx = pd.bdate_range("2025-01-01", periods=n)
    data = {}
    for code in set(BROAD + ["ALLA"] + CONCEPTS):
        sched = schedules.get(code, [0.0])
        rets = [sched[min(i, len(sched) - 1)] for i in range(n)]
        data[code] = (1 + pd.Series(rets)).cumprod().to_list()
    return pd.DataFrame(data, index=idx)


def run_v3(close, start_idx, **param_kw):
    p = V3Params(**param_kw)
    bt = V3Backtester(close, CONCEPTS, broad_codes=BROAD, allA_code="ALLA", params=p)
    return bt.run(close.index[start_idx])


# ---------------------------------------------------------------- 信号层 --

def test_up_resonance_scores_hand_computed():
    leader = pd.Series([0.01, 0.02])
    concepts = pd.DataFrame({"A": [0.01, 0.01], "B": [-0.01, 0.02]})
    out = up_resonance_scores(leader, concepts, half_life=1)
    # w = [0.5, 1.0]（age 1, 0）
    a = out[out["concept"] == "A"].iloc[0]
    b = out[out["concept"] == "B"].iloc[0]
    assert a["sync"] == pytest.approx(1.0)
    assert b["sync"] == pytest.approx(2 / 3)
    assert a["capture"] == pytest.approx(0.015 / 0.025)
    assert b["capture"] == pytest.approx(0.02 / 0.025)
    assert a["score"] == pytest.approx(math.sqrt(0.6))
    assert b["score"] == pytest.approx(2 / 3 * math.sqrt(0.8))
    assert out["concept"].iloc[0] == "A"  # 分数降序


def test_up_resonance_scores_clip_and_empty():
    leader = pd.Series([0.01, 0.01])
    concepts = pd.DataFrame({"BIG": [0.10, 0.10]})  # 捕获率 10 → clip 2
    out = up_resonance_scores(leader, concepts, half_life=5)
    assert out["score"].iloc[0] == pytest.approx(math.sqrt(2.0))
    # 无上涨日 → 空表
    empty = up_resonance_scores(pd.Series([-0.01, 0.0]), concepts, 5)
    assert empty.empty


def test_dynamic_half_life_tiers():
    idx = pd.bdate_range("2025-01-01", periods=11)
    flat = pd.Series(100.0, index=idx)
    assert dynamic_half_life(flat, 10, 10, (0.02, 0.04), (5, 3, 2)) == 5
    dip = pd.Series([100 * (1 - 0.01 * i) for i in range(11)], index=idx)  # ~10% 回撤
    assert dynamic_half_life(dip, 10, 10, (0.02, 0.04), (5, 3, 2)) == 2
    mid = pd.Series([100, 100, 100, 97, 97, 97, 97, 97, 97, 97, 97], index=idx)  # 3%
    assert dynamic_half_life(mid, 10, 10, (0.02, 0.04), (5, 3, 2)) == 3


def test_compound_window_nan_excluded():
    idx = pd.bdate_range("2025-01-01", periods=5)
    close = pd.DataFrame({"X": [100, 101, 102, 103, 104],
                          "Y": [100, 102, None, 103, 104]}, index=idx)
    out = compound_window(close, ["X", "Y"], 2, 4)
    assert out["X"] == pytest.approx(104 / 102 - 1)
    assert math.isnan(out["Y"])


def test_v3_ranking_gate_fail_and_insufficient():
    n = 14
    close = mk_close({"LDR": [0.01] * n, "C_GOOD": [0.012] * n}, n)
    rets = close.pct_change()
    p = V3Params()
    # 回看不足 → 空
    assert v3_ranking(rets, close, CONCEPTS, BROAD, "ALLA", 5, p).empty
    # 正常 → 非空，leader=LDR
    info = {}
    out = v3_ranking(rets, close, CONCEPTS, BROAD, "ALLA", 12, p, info)
    assert info["leader"] == "LDR" and info["gate"] and not out.empty
    assert out["concept"].iloc[0] == "C_GOOD"
    # 闸门失败（LDR 近 3 日下跌）→ 空表
    sched = {"LDR": [0.01] * 12 + [-0.03, -0.03], "C_GOOD": [0.012] * n}
    close2 = mk_close(sched, n)
    info2 = {}
    out2 = v3_ranking(close2.pct_change(), close2, CONCEPTS, BROAD, "ALLA", 13, p, info2)
    assert out2.empty and not info2["gate"]


# ---------------------------------------------------------------- 引擎 --

def test_entry_timing_and_t2_accrual():
    """i11 信号 → i12 收盘买入 → i13 起计收益（T+1 成交、T+2 起算）。"""
    n = 20
    close = mk_close({"LDR": [0.01] * n, "C_GOOD": [0.012] * n,
                      "C_MID": [0.005] * n, "C_NEWC": [0.005] * n,
                      "C_ALT": [0.005] * n}, n)
    out = run_v3(close, 11)
    trades = out["trades"]
    assert len(trades) == 1 and trades.iloc[0]["type"] == "entry"
    entry = trades.iloc[0]
    assert entry["to"] == "C_GOOD"
    assert entry["date"] == close.index[12]
    nav = out["nav_curve"]
    assert nav.iloc[0] == pytest.approx(1.0)          # i11：信号日无动作
    assert nav.loc[close.index[12]] == pytest.approx(1.0)  # i12：成交日不计收益
    assert nav.loc[close.index[13]] == pytest.approx(1.012)  # i13：首个收益日
    st = out["stats"]
    assert st["entries"] == 1 and st["position_changes"] == 1


def test_min_hold_blocks_early_switch():
    """跌出 Top3 发生在检查权解锁前 → 最短持有期内不换仓。"""
    n = 22
    # i13 起 C_MID/C_NEWC/C_ALT 三家 +5% 突增 → i13 起榜单前三为突增三家
    sched = {
        "LDR": [0.01] * n,
        "C_GOOD": [0.012] * n,
        "C_MID": [0.005] * 13 + [0.05] * (n - 13),
        "C_NEWC": [0.005] * 13 + [0.05] * (n - 13),
        "C_ALT": [0.005] * 13 + [0.05] * (n - 13),
    }
    close = mk_close(sched, n)
    out = run_v3(close, 11)
    trades = out["trades"]
    # 入场 i12；首次检查信号最早 i15（i15−exec_i12=3），换仓执行最早 i16
    assert trades.iloc[0]["type"] == "entry" and trades.iloc[0]["date"] == close.index[12]
    sw = trades[trades["type"] == "switch"]
    assert len(sw) == 1
    assert sw.iloc[0]["date"] == close.index[16]
    assert sw.iloc[0]["from"] == "C_GOOD"
    # 三家同分 → 并列按代码升序，C_ALT 为第 1 名
    assert sw.iloc[0]["to"] == "C_ALT"


def test_topk_buffer_keeps_rank2():
    """持仓降至第 2（仍在 Top3）→ 续持不换仓。"""
    n = 22
    sched = {
        "LDR": [0.01] * n,
        "C_GOOD": [0.012] * n,
        "C_MID": [0.005] * 14 + [0.05] * (n - 14),   # 仅一家超越
        "C_NEWC": [0.005] * n, "C_ALT": [0.005] * n,
    }
    close = mk_close(sched, n)
    out = run_v3(close, 11)
    trades = out["trades"]
    assert len(trades) == 1  # 只有入场，无换仓


def test_stop_loss_and_cooldown():
    """−6% 崩塌 → 次日收盘止损卖出；冷静 1 日后 i20 信号、i21 再入场。"""
    n = 26
    sched = {
        "LDR": [0.01] * n,
        "C_GOOD": [0.012] * n,
        "C_MID": [0.005] * 13 + [0.05] * (n - 13),
        "C_NEWC": [0.005] * 13 + [0.05] * (n - 13),
        # i13..i16 +5% 四日（i16 换仓入场）→ i17 单日 −6%（自入场价直接击穿）
        "C_ALT": [0.005] * 13 + [0.05] * 4 + [-0.06] + [0.01] * (n - 18),
    }
    close = mk_close(sched, n)
    out = run_v3(close, 11)
    trades = out["trades"]
    # i16 换仓 C_ALT（入场价=close_ALT(i16)）；i17 崩塌 −6% 止损信号，i18 收盘执行；
    # entry_block_until=19（含）→ i20 信号、i21 再入场（C_ALT 3日复合<0 出局 → C_MID）
    types = list(trades["type"])
    assert types == ["entry", "switch", "stop", "entry"]
    assert trades.iloc[2]["date"] == close.index[18]
    assert trades.iloc[2]["from"] == "C_ALT"
    assert trades.iloc[3]["date"] == close.index[21]
    assert trades.iloc[3]["to"] == "C_MID"
    assert out["stats"]["stop_count"] == 1
    # 止损触发价：close_ALT(i17) ≤ 入场价×0.95
    entry_px = float(close["C_ALT"].iloc[16])
    assert float(close["C_ALT"].iloc[17]) <= entry_px * 0.95


def test_gate_fail_exits_to_cash():
    """持仓检查日闸门失败（领先指数近 3 日下跌）→ 退出至现金。"""
    n = 22
    sched = {
        "LDR": [0.01] * 15 + [-0.03] * (n - 15),
        "C_GOOD": [0.012] * n, "C_MID": [0.005] * n,
        "C_NEWC": [0.005] * n, "C_ALT": [0.005] * n,
    }
    close = mk_close(sched, n)
    out = run_v3(close, 11)
    trades = out["trades"]
    # i15 起 −3%：首个下跌日即令 3 日复合转负 → i15 闸门失败（恰为首个检查日），
    # 退出信号 i15 收盘 → i16 收盘卖出
    assert list(trades["type"]) == ["entry", "exit"]
    assert trades.iloc[1]["date"] == close.index[16]
    assert out["stats"]["flat_days"] > 0


def test_gate_fail_while_flat_blocks_entry():
    n = 18
    sched = {"LDR": [-0.02] * n, "C_GOOD": [0.012] * n,
             "C_MID": [0.005] * n, "C_NEWC": [0.005] * n, "C_ALT": [0.005] * n}
    close = mk_close(sched, n)
    out = run_v3(close, 11)
    assert out["trades"].empty
    assert out["stats"]["gate_fail_days"] > 0


def test_costs_double_side_on_switch():
    n = 22
    sched = {
        "LDR": [0.01] * n,
        "C_GOOD": [0.012] * n,
        "C_MID": [0.005] * 13 + [0.05] * (n - 13),
        "C_NEWC": [0.005] * 13 + [0.05] * (n - 13),
        "C_ALT": [0.005] * 13 + [0.05] * (n - 13),
    }
    close = mk_close(sched, n)
    out0 = run_v3(close, 11, cost_bp=0)
    out1 = run_v3(close, 11, cost_bp=10)
    nav0, nav1 = out0["nav_curve"], out1["nav_curve"]
    i12, i15, i16 = close.index[12], close.index[15], close.index[16]
    # 买入单边：成交日 nav ×(1−0.001)
    assert nav1.loc[i12] == pytest.approx(nav0.loc[i12] * 0.999)
    # 换仓双边：i16 再 ×(1−0.001)²（持有路径两版本在 i13..i15 相同比例）
    r16_1 = nav1.loc[i16] / nav1.loc[i15]
    r16_0 = nav0.loc[i16] / nav0.loc[i15]
    assert r16_1 == pytest.approx(r16_0 * 0.999 ** 2)


def test_no_lookahead():
    """修改未来数据不影响已有路径（信号只用 ≤T 数据）。"""
    n = 24
    base = {
        "LDR": [0.01] * n, "C_GOOD": [0.012] * n, "C_MID": [0.005] * n,
        "C_NEWC": [0.005] * n, "C_ALT": [0.005] * n,
    }
    close_a = mk_close(base, n)
    alt = dict(base)
    alt["C_GOOD"] = [0.012] * 16 + [-0.30] * (n - 16)   # i16 起崩塌
    close_b = mk_close(alt, n)
    out_a = run_v3(close_a, 11)
    out_b = run_v3(close_b, 11)
    cut = close_a.index[15]
    pd.testing.assert_series_equal(out_a["nav_curve"].loc[:cut],
                                   out_b["nav_curve"].loc[:cut])


def test_stop_mode_minute_exits_intraday():
    """minute 模式：触发 bar 收盘即时离场（当日收益 = mark/前收）。"""

    class FakeMarks:
        def __init__(self, close, marks):
            self.close = close
            self.marks = marks  # {date: {code: [(ts, price), ...]}}

        def day_marks(self, code, date):
            return self.marks.get(date, {}).get(code, [])

    n = 22
    sched = {
        "LDR": [0.01] * n,
        "C_GOOD": [0.012] * n, "C_MID": [0.005] * n,
        "C_NEWC": [0.005] * n, "C_ALT": [0.005] * n,
    }
    close = mk_close(sched, n)
    # i13 盘中 mark = 前收×0.90（−10%，击穿 5% 止损）→ 当日以 mark 价离场
    d13 = close.index[13]
    prev_close = float(close["C_GOOD"].iloc[12])
    mark = prev_close * 0.90
    marks = {d13: {"C_GOOD": [(d13 + pd.Timedelta(hours=10), mark)]}}
    bt = V3Backtester(close, CONCEPTS, broad_codes=BROAD, allA_code="ALLA",
                      params=V3Params(stop_mode="minute"),
                      minute_prices=FakeMarks(close, marks))
    out = bt.run(close.index[11])
    trades = out["trades"]
    # i13 盘中止损离场；entry_block_until=14（含）→ i15 信号、i16 再入场 C_GOOD
    assert list(trades["type"]) == ["entry", "stop", "entry"]
    assert trades.iloc[1]["date"] == d13
    assert trades.iloc[1]["price"] == pytest.approx(mark)
    assert trades.iloc[2]["date"] == close.index[16]
    assert trades.iloc[2]["to"] == "C_GOOD"
    nav13 = out["nav_curve"].loc[d13]
    assert nav13 == pytest.approx(0.90)   # nav = mark/前收，无成本
    assert out["stats"]["intraday_exits"] == 1


def test_yearly_returns():
    idx = pd.to_datetime(["2025-12-29", "2025-12-30", "2025-12-31",
                          "2026-01-01", "2026-01-02"])
    nav = pd.Series([1.0, 1.1, 1.2, 1.32, 1.4], index=idx)
    yr = yearly_returns(nav)
    assert yr["2025"] == pytest.approx(0.2)
    assert yr["2026"] == pytest.approx(1.4 / 1.2 - 1)


# ------------------------------------------------ V4.1 分钟重排层（v4 §7） --

from resonance.v3 import MinuteBarProvider, minute_up_resonance  # noqa: E402


def test_minute_up_resonance_hand_computed():
    idx = pd.date_range("2026-01-05 13:05", periods=4, freq="5min")
    leader = pd.Series([100, 102, 101, 103], index=idx)          # rets +2%,−1%,+2%
    concepts = pd.DataFrame({
        "A": [100, 101, 102, 103],   # 领先上涨bar均涨，捕获低
        "B": [100, 103, 100, 101],   # 均涨，捕获高
        "C": [100, 99, 98, 97],      # 领先上涨bar均跌
    }, index=idx)
    out = minute_up_resonance(leader, concepts)
    a, b, c = (out[out["concept"] == k].iloc[0] for k in "ABC")
    assert a["sync"] == pytest.approx(1.0) and b["sync"] == pytest.approx(1.0)
    assert c["sync"] == pytest.approx(0.0) and c["score"] == pytest.approx(0.0)
    # 捕获率 = Σmax(概念bar收益,0)/Σmax(领先bar收益,0)（全窗正部，§7.2）
    den = 0.02 + 103 / 101 - 1
    assert a["capture"] == pytest.approx((0.01 + 102 / 101 - 1 + 103 / 102 - 1) / den, abs=1e-6)
    assert b["capture"] == pytest.approx((0.03 + 0.0 + 0.01) / den, abs=1e-6)
    assert b["score"] > a["score"]                  # 同同步率，高捕获胜
    assert list(out["concept"]) == ["B", "A", "C"]


def test_minute_bar_provider_window():
    day = pd.Timestamp("2026-01-05")
    idx = pd.DatetimeIndex(  # 真实 48 bar 日历：上午 09:35~11:30 + 下午 13:05~15:00
        list(pd.date_range("2026-01-05 09:35", "2026-01-05 11:30", freq="5min"))
        + list(pd.date_range("2026-01-05 13:05", "2026-01-05 15:00", freq="5min")))
    wide = pd.DataFrame({"X": [float(v) for v in range(48)],
                         "Y": [100.0] * 10 + [None] * 38}, index=idx)  # Y 仅 10 bar
    prov = MinuteBarProvider(wide)
    w = prov.window("X", day, 24)
    assert len(w) == 24 and w.index[-1].hour == 15
    assert prov.window("Y", day, 24).empty        # 不足 24 根 → 空（降级）


class _FakeMinute:
    """可控分钟供给：bars_map[(day, code)] → Series；未登记返回空。"""

    def __init__(self, bars_map):
        self.bars_map = bars_map

    def window(self, code, day, n):
        return self.bars_map.get((pd.Timestamp(day).date(), code), pd.Series(dtype=float))

    def window_span(self, code, end_day, n):
        return self.window(code, end_day, n)  # 假供给：单日即窗，跨日语义退化


def _bars(vals, day="2025-01-14"):
    idx = pd.date_range(f"{day} 13:05", periods=len(vals), freq="5min")
    return pd.Series(vals, index=idx)


def test_v41_two_layer_entry_picks_minute_rank1():
    """日线 #1 ≠ 分钟 #1 时，入场买分钟 #1（V4.1 纯分钟重排）。"""
    n = 16
    close = mk_close({"LDR": [0.01] * n, "C_GOOD": [0.012] * n,
                      "C_MID": [0.005] * n, "C_NEWC": [0.005] * n,
                      "C_ALT": [0.005] * n}, n)
    sig_day = close.index[11]                       # 信号日
    bm = {}
    d = sig_day.date()
    bm[(d, "LDR")] = _bars([100, 101, 102, 103])
    bm[(d, "C_GOOD")] = _bars([100, 100.5, 101, 101.5])   # 分钟弱
    bm[(d, "C_MID")] = _bars([100, 102, 104, 106])        # 分钟最强
    bm[(d, "C_NEWC")] = _bars([100, 99, 98, 97])
    bm[(d, "C_ALT")] = _bars([100, 99, 98, 97])
    p = V3Params(daily_top=5, topk=2, minute_bars=4)
    bt = V3Backtester(close, CONCEPTS, broad_codes=BROAD, allA_code="ALLA",
                      params=p, minute_bars_provider=_FakeMinute(bm))
    out = bt.run(close.index[11])
    trades = out["trades"]
    assert list(trades["type"]) == ["entry"]
    assert trades.iloc[0]["to"] == "C_MID"          # 分钟第 1，非日线第 1（C_GOOD）
    assert out["stats"]["minute_layer_days"] >= 1


def test_v41_leader_minute_missing_falls_back_daily():
    """领先指数缺分钟 bar → 回退日线原序（计数上报）。"""
    n = 16
    close = mk_close({"LDR": [0.01] * n, "C_GOOD": [0.012] * n,
                      "C_MID": [0.005] * n, "C_NEWC": [0.005] * n,
                      "C_ALT": [0.005] * n}, n)
    sig_day = close.index[11].date()
    bm = {(sig_day, c): _bars([100, 101, 102, 103]) for c in CONCEPTS}
    # 领先 LDR 未登记 → 空
    p = V3Params(daily_top=5, topk=2, minute_bars=4)
    bt = V3Backtester(close, CONCEPTS, broad_codes=BROAD, allA_code="ALLA",
                      params=p, minute_bars_provider=_FakeMinute(bm))
    out = bt.run(close.index[11])
    trades = out["trades"]
    assert trades.iloc[0]["to"] == "C_GOOD"         # 日线第 1
    assert out["stats"]["minute_fallback_leader"] >= 1


def test_v41_top2_buffer_on_final_ranking():
    """Top2 缓冲作用于分钟最终排名：持仓分钟第 2 → 续持。"""
    n = 20
    close = mk_close({"LDR": [0.01] * n, "C_GOOD": [0.012] * n,
                      "C_MID": [0.005] * n, "C_NEWC": [0.005] * n,
                      "C_ALT": [0.005] * n}, n)
    bm = {}
    for i in range(11, n):                          # 每个信号日同构 bar
        d = close.index[i].date()
        bm[(d, "LDR")] = _bars([100, 101, 102, 103], str(d))
        bm[(d, "C_GOOD")] = _bars([100, 102, 104, 106], str(d))   # 分钟第 1
        bm[(d, "C_MID")] = _bars([100, 100.5, 101, 101.5], str(d))
        bm[(d, "C_NEWC")] = _bars([100, 99, 98, 97], str(d))
        bm[(d, "C_ALT")] = _bars([100, 99, 98, 97], str(d))
    # 日线第 1 = C_GOOD 且分钟第 1 = C_GOOD → 入场 C_GOOD；
    # 其后各日分钟排名不变 → 持仓恒在最终 Top2 内 → 无换仓
    p = V3Params(daily_top=5, topk=2, minute_bars=4)
    bt = V3Backtester(close, CONCEPTS, broad_codes=BROAD, allA_code="ALLA",
                      params=p, minute_bars_provider=_FakeMinute(bm))
    out = bt.run(close.index[11])
    assert len(out["trades"]) == 1 and out["trades"].iloc[0]["to"] == "C_GOOD"


# ---------------------------------------------- 2026-09-22 优化指令（V4.2） --

def test_hl_source_leader():
    """hl_source='leader'：半衰期按当日领先指数自身回撤定档（全A 仅对照）。"""
    from resonance.v3 import V3Signals
    n = 30
    # ALLA 恒平（回撤0→h=5）；LDR 前段涨后段崩 ~10%（回撤→h=2）
    sched = {
        "LDR": [0.01] * 20 + [-0.02] * 10,
        "LDR2": [-0.05] * n,   # 远弱于崩塌中的 LDR，保证 LDR 仍为领先指数
        "ALLA": [0.0] * n,
        "C_GOOD": [0.012] * n, "C_MID": [0.005] * n,
        "C_NEWC": [0.005] * n, "C_ALT": [0.005] * n,
    }
    close = mk_close(sched, n)
    sig_allA = V3Signals(close, CONCEPTS, BROAD, "ALLA", V3Params(hl_source="allA"))
    sig_leader = V3Signals(close, CONCEPTS, BROAD, "ALLA", V3Params(hl_source="leader"))
    i = n - 1
    assert sig_allA.has_leader[i] and sig_allA.leader_idx[i] == BROAD.index("LDR")
    assert sig_allA.hl[i] == 5.0        # 全A 无回撤 → 最稳档
    assert sig_leader.hl[i] == 2.0      # LDR 深回撤 → 最快档


def test_window_span_cross_day():
    """window_span：跨日拼接取末 N 根；不足 N → 空。"""
    days = ["2026-01-05", "2026-01-06"]

    def day_idx(d):
        return pd.DatetimeIndex(
            list(pd.date_range(f"{d} 09:35", f"{d} 11:30", freq="5min"))
            + list(pd.date_range(f"{d} 13:05", f"{d} 15:00", freq="5min")))

    wide = pd.concat([
        pd.DataFrame({"X": [float(v) for v in range(48)],
                      "Y": [100.0] * 48}, index=day_idx(days[0])),
        pd.DataFrame({"X": [float(v) for v in range(48, 96)],
                      "Y": [200.0] * 24 + [None] * 24}, index=day_idx(days[1])),
    ])
    prov = MinuteBarProvider(wide)
    w96 = prov.window_span("X", pd.Timestamp(days[1]), 96)
    assert len(w96) == 96
    assert w96.index[0] == wide.index[0] and w96.index[-1] == wide.index[-1]
    assert prov.window_span("X", pd.Timestamp(days[0]), 96).empty   # 首日不足
    assert prov.window_span("Y", pd.Timestamp(days[1]), 96).empty   # Y 次日缺半日
    w48 = prov.window_span("X", pd.Timestamp(days[1]), 48)
    assert len(w48) == 48 and w48.index[0].date() == pd.Timestamp(days[1]).date()


# --------------------------------------- entry_gate 研究钩子（moneyflow-gate）--
def test_entry_gate_blocks_entries_soft():
    """全 False 闸门：无入场（nav 恒 1）；持仓侧不受影响由 soft 语义保证。"""
    close = mk_close({"LDR": [0.01] * 12 + [0.01], "C_GOOD": [0.01]}, 14)
    gate = pd.Series(False, index=close.index)
    bt = V3Backtester(close, CONCEPTS, broad_codes=BROAD, allA_code="ALLA",
                      params=V3Params(), entry_gate=gate)
    out = bt.run(close.index[11])
    assert out["stats"]["entries"] == 0
    assert out["stats"]["entry_blocked_days"] > 0
    assert out["stats"]["gate_missing_days"] == 0
    assert (out["nav_curve"] == 1.0).all()


def test_entry_gate_missing_dates_pass_through():
    """闸门索引未覆盖的日期直通（计数 gate_missing_days），行为与无闸门一致。"""
    close = mk_close({"LDR": [0.01] * 12 + [0.01], "C_GOOD": [0.01]}, 14)
    base = V3Backtester(close, CONCEPTS, broad_codes=BROAD, allA_code="ALLA",
                        params=V3Params()).run(close.index[11])
    sparse = pd.Series(True, index=close.index[12:])  # 首个信号日缺失
    gated = V3Backtester(close, CONCEPTS, broad_codes=BROAD, allA_code="ALLA",
                         params=V3Params(), entry_gate=sparse).run(close.index[11])
    assert gated["stats"]["gate_missing_days"] > 0
    assert gated["stats"]["entries"] == base["stats"]["entries"]
    assert list(gated["nav_curve"]) == pytest.approx(list(base["nav_curve"]))


def test_entry_gate_true_preserves_baseline():
    """全 True 闸门 = 基线逐位一致。"""
    close = mk_close({"LDR": [0.01] * 12 + [0.01], "C_GOOD": [0.01]}, 14)
    kw = dict(concepts=CONCEPTS, broad_codes=BROAD, allA_code="ALLA", params=V3Params())
    base = V3Backtester(close, **kw).run(close.index[11])
    gated = V3Backtester(close, entry_gate=pd.Series(True, index=close.index), **kw).run(close.index[11])
    assert list(gated["nav_curve"]) == pytest.approx(list(base["nav_curve"]))
    assert gated["stats"]["entries"] == base["stats"]["entries"]
    assert gated["stats"]["entry_blocked_days"] == 0


def test_exit_grid_flow_exit():
    """exit_grid 全 True：每个检查日都退出（flow_exit 计数≥1），不留仓。"""
    close = mk_close({"LDR": [0.01] * 12 + [0.01], "C_GOOD": [0.01]}, 18)
    grid = pd.DataFrame(True, index=close.index, columns=CONCEPTS)
    bt = V3Backtester(close, CONCEPTS, broad_codes=BROAD, allA_code="ALLA",
                      params=V3Params(), exit_grid=grid)
    out = bt.run(close.index[11])
    assert out["stats"].get("flow_exits", 0) >= 1
    sells = out["trades"][out["trades"]["type"].isin(["exit", "stop"])]
    assert len(sells) >= 1


def test_exit_grid_nan_and_missing_no_effect():
    """exit_grid 全 NaN（触发面空）= 基线逐位一致。"""
    close = mk_close({"LDR": [0.01] * 12 + [0.01], "C_GOOD": [0.01]}, 18)
    kw = dict(concepts=CONCEPTS, broad_codes=BROAD, allA_code="ALLA", params=V3Params())
    base = V3Backtester(close, **kw).run(close.index[11])
    grid = pd.DataFrame(np.nan, index=close.index, columns=CONCEPTS)
    gated = V3Backtester(close, exit_grid=grid, **kw).run(close.index[11])
    assert list(gated["nav_curve"]) == pytest.approx(list(base["nav_curve"]))
    assert gated["stats"].get("flow_exits", 0) == 0


def test_post_rank_identity_and_reorder():
    """post_rank=None=基线；恒等函数=基线；反转函数应改变选择。"""
    close = mk_close({"LDR": [0.01] * 12 + [0.01],
                      "C_GOOD": [0.01], "C_MID": [0.005]}, 14)
    kw = dict(concepts=CONCEPTS, broad_codes=BROAD, allA_code="ALLA", params=V3Params())
    base = V3Backtester(close, **kw).run(close.index[11])
    ident = V3Backtester(close, post_rank=lambda rk, d: rk, **kw).run(close.index[11])
    assert list(ident["nav_curve"]) == pytest.approx(list(base["nav_curve"]))

    def rev(rk, d):
        return rk.iloc[::-1].reset_index(drop=True)

    flipped = V3Backtester(close, post_rank=rev, **kw).run(close.index[11])
    b_first = base["trades"][base["trades"]["type"] == "entry"]["to"].iloc[0]
    f_first = flipped["trades"][flipped["trades"]["type"] == "entry"]["to"].iloc[0]
    assert b_first != f_first or list(flipped["nav_curve"]) != pytest.approx(list(base["nav_curve"]))
