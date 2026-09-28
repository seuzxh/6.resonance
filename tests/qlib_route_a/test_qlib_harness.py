"""回测适配层测试（qlib env；合成数据）。"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("qlib")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research.qlib_route_a.qlib_harness import (  # noqa: E402
    build_quote, run_backtest,
)


def _bars(n=6):
    days = pd.bdate_range("2026-01-05", periods=n)
    return pd.DataFrame({
        "symbol": ["A"] * n + ["B"] * n,
        "date": list(days) * 2,
        "open": np.linspace(10, 12, n).tolist() + np.linspace(5, 7, n).tolist(),
        "high": np.linspace(11, 13, n).tolist() + np.linspace(6, 8, n).tolist(),
        "low": np.linspace(9, 11, n).tolist() + np.linspace(4, 6, n).tolist(),
        "close": np.linspace(10, 12, n).tolist() + np.linspace(5, 7, n).tolist(),
        "volume": np.full(2 * n, 100.0)})


def test_build_quote_d_gate():
    bars = _bars()
    q = build_quote(bars)
    # D-Gate-1：$close ≡ float32 pivot（含结构）
    close_ref = (bars.pivot(index="date", columns="symbol", values="close")
                 .astype(np.float32))
    got = q["$close"].unstack("instrument")[["A", "B"]]
    np.testing.assert_allclose(got.to_numpy(), close_ref.to_numpy())
    assert (q["$factor"] == np.float32(1.0)).all()
    # $change = 收盘 pct（首行 NaN）
    ch = q.xs("A", level="instrument")["$change"]
    assert np.isnan(ch.iloc[0])
    assert abs(ch.iloc[1] - (10.4 / 10.0 - 1)) < 1e-6
    # date 字符串已被转为 Timestamp 索引
    assert isinstance(q.index.get_level_values(0)[0], pd.Timestamp)


def test_run_backtest_probe_strategy_open_fill():
    """自定义探针策略：day2 开盘全仓买 A、末日开盘卖出——验证适配层的
    $open 成交、双边成本与逐日收益记账（P3 ResonanceStrategy 的 API 路径）。
    """
    from qlib.backtest.decision import Order, TradeDecisionWO
    from qlib.strategy.base import BaseStrategy

    class Probe(BaseStrategy):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def generate_trade_decision(self, execute_trade_day=None):
            self.calls += 1
            step = self.trade_calendar.get_trade_step()
            start, end = self.trade_calendar.get_step_time(step)
            if self.calls == 2:  # day2 开盘全仓买 A（现金 1e6，成本 10bp）
                amount = 1_000_000.0 / (10.4 * 1.001) * (1 - 1e-9)  # 防 float 舍入现金不足
                o = Order(stock_id="A", amount=amount, start_time=start,
                          end_time=end, direction=Order.BUY)
                return TradeDecisionWO([o], self)
            if self.calls == 6:  # 末日开盘清仓
                o = Order(stock_id="A", amount=1_000_000.0 / (10.4 * 1.001) * (1 - 1e-9),
                          start_time=start, end_time=end,
                          direction=Order.SELL)
                return TradeDecisionWO([o], self)
            return TradeDecisionWO([], self)

    bars = _bars(6)
    q = build_quote(bars)
    days = pd.bdate_range("2026-01-05", periods=6)
    bench = pd.Series(0.0, index=days)
    pdf, _, account = run_backtest(q, days, bench, Probe(), days[0], days[-1])
    assert len(pdf) == 6
    rets = pdf["return"].astype(float).tolist()
    costs = pdf["total_cost"].astype(float).tolist()
    a = [10.0, 10.4, 10.8, 11.2, 11.6, 12.0]  # A 的 open=close 序列
    assert rets[0] == 0.0                                  # day1 空仓
    assert abs(rets[1]) < 1e-9                             # day2 open 买，不含费收益=0
    for k in range(2, 6):
        assert abs(rets[k] - (a[k] / a[k - 1] - 1)) < 1e-6  # close→close（day6 open 卖）
    # 费用列：day2 买侧成本 ≈ 1e6×10bp×仓位；day6 卖侧再计一次
    assert abs(costs[1] - 999.000961) < 1e-2, costs[1]
    assert costs[5] > costs[4]                              # 卖出日成本再增
    # 账户终值（E-Gate 对拍锚之一）：金额×末日出价×(1−卖侧成本)±找零
    final_value = float(account.current_position.position["now_account_value"])
    expect_final = 96057.78826942289 * (1 - 1e-9) * 12.0 * 0.999
    assert abs(final_value - expect_final) < 0.5, (final_value, expect_final)
    # 注：portfolio_df 的 return/cost 列组合重建含费净值存在内部口径细节，
    # E-Gate 以逐笔成交与账户终值为准（harness docstring 已备注）。
