"""qlib 回测适配层（验证轨 P1）：parquet 行情直接驱动 qlib 回测框架。

实现依据 docs/research/qlib-validation-plan.md §二 E-1~E-8 契约
（冒烟① research/qlib_route_a/qlib_smoke.py 为活证据）。只在 conda env
``qlib``（pyqlib 0.9.7）下运行。

组件：
- build_quote(bars_daily) → 标准 quote_df（datetime/instrument 两级索引，
  $open…$volume float32、$factor=1.0、$change，date 字符串强制 to_datetime）；
- StubCal：交易日历桩（契约 E-2：np.ndarray + 末尾哨兵日）；
- ParquetExchange：覆写 get_quote_from_qlib（契约 E-3/E-4：codes 传列表、
  尾部语义 trade_w_adj_price/_update_limit 照抄）；
- run_backtest：单入口（契约 E-1/E-5/E-6/E-8：C 四键注入、基准传
  pd.Series、手动装配 CommonInfrastructure、产出解析）。

产出语义（E-8 补充，探针实证）：portfolio_df 的 return 列为不含费口径、
费用单列在 cost/total_cost；但两列的逐日组合重建与账户终值存在 ~1e-5
量级的内部口径细节——**E-Gate 对拍以逐笔成交与账户终值为准**，不以
组合列重建公式为准。
"""
from __future__ import annotations

import bisect

import numpy as np
import pandas as pd

QUOTE_FIELDS = ["$open", "$high", "$low", "$close", "$volume",
                "$factor", "$change"]


def build_quote(bars_daily: pd.DataFrame) -> pd.DataFrame:
    """日线长表 → qlib 标准 quote_df（E-7：date 字符串强制 Timestamp）。"""
    d = bars_daily.copy()
    d["date"] = pd.to_datetime(d["date"])
    d = d.rename(columns={"symbol": "instrument", "date": "datetime"})
    d = d.set_index(["datetime", "instrument"]).sort_index()
    q = pd.DataFrame(index=d.index)
    for f in ("open", "high", "low", "close", "volume"):
        q[f"${f}"] = d[f].astype(np.float32)
    q["$factor"] = np.float32(1.0)
    q["$change"] = q.groupby(level="instrument")["$close"].pct_change()
    return q[QUOTE_FIELDS]


class StubCal:
    """契约 E-2：qlib.backtest.utils.Cal 的 parquet 日历桩。

    照抄 CalendarCalendar 的 calendar/locate_index 语义；末尾补 1 日
    哨兵仅作末步排他上界，不参与行情。
    """

    def __init__(self, cal: pd.DatetimeIndex):
        self._cal = cal.to_numpy(dtype=object)  # 断言 np.ndarray 用
        self._cal = np.append(self._cal, cal[-1] + pd.Timedelta(days=1))
        self._idx = {d: i for i, d in enumerate(self._cal)}

    def _get_calendar(self, freq, future=False):
        assert freq == "day", f"仅日线回测，收到 {freq}"
        return self._cal, self._idx

    def calendar(self, freq, future=False):
        return self._get_calendar(freq, future)[0]

    def locate_index(self, start_time, end_time, freq, future=False):
        start_time, end_time = pd.Timestamp(start_time), pd.Timestamp(end_time)
        calendar, calendar_index = self._get_calendar(freq=freq, future=future)
        if start_time not in calendar_index:
            start_time = calendar[bisect.bisect_left(calendar, start_time)]
        start_index = calendar_index[start_time]
        if end_time not in calendar_index:
            end_time = calendar[bisect.bisect_right(calendar, end_time) - 1]
        end_index = calendar_index[end_time]
        return start_time, end_time, start_index, end_index


def install_stub_calendar(trade_dates: pd.DatetimeIndex) -> None:
    import qlib.backtest.utils as btest_utils

    btest_utils.Cal = StubCal(trade_dates)


def install_config() -> None:
    """契约 E-1：全局 C 四键（研究模式：不取整手/无涨跌停/收盘买默认）。"""
    from qlib import config as qconf

    qconf.C["trade_unit"] = None
    qconf.C["limit_threshold"] = None
    qconf.C["deal_price"] = "$close"
    qconf.C["region"] = "cn"


def make_exchange(quote: pd.DataFrame, codes: list[str], start, end,
                  deal_price: str = "$open", cost_bp: float = 10.0):
    from qlib.backtest.exchange import Exchange

    class ParquetExchange(Exchange):
        def get_quote_from_qlib(self):  # 契约 E-3：绕开 D.features
            cols = list(self.all_fields)
            have = [c for c in cols if c in quote.columns]
            self.quote_df = quote.loc[:, have].copy()
            for c in cols:
                if c not in self.quote_df.columns:
                    self.quote_df[c] = (np.float32(1.0) if c == "$factor"
                                        else np.float32("nan"))
            for attr in ("buy_price", "sell_price"):
                pstr = getattr(self, attr)
                if self.quote_df[pstr].isna().any():
                    self.logger.warning(f"{pstr} 字段含 NaN。")
            # $factor 恒 1.0（不复权研究口径）→ 正常价格模式；再按原方法
            # 语义更新停牌/涨跌停标记（NaN 收盘 = 停牌）
            self.trade_w_adj_price = False
            self._update_limit(self.limit_threshold)

    return ParquetExchange(
        freq="day", start_time=str(pd.Timestamp(start).date()),
        end_time=str(pd.Timestamp(end).date()),
        codes=list(codes),  # 契约 E-4：列表，避免 D.instruments
        deal_price=deal_price, limit_threshold=None, volume_threshold=None,
        trade_unit=None, open_cost=cost_bp / 1e4, close_cost=cost_bp / 1e4,
        min_cost=0.0)


def run_backtest(quote: pd.DataFrame, trade_dates: pd.DatetimeIndex,
                 benchmark_ret: pd.Series, strategy, start, end,
                 codes: list[str] | None = None,
                 deal_price: str = "$open", cost_bp: float = 10.0):
    """契约 E-6/E-8：装配 CommonInfrastructure → backtest_loop → portfolio_df。

    strategy 为已构造的 BaseStrategy 实例（调用方负责 reset_common_infra
    之外的装配，本函数代做）。返回 (portfolio_df, exchange)。
    """
    install_config()
    install_stub_calendar(trade_dates)
    from qlib.backtest import backtest_loop
    from qlib.backtest.account import Account
    from qlib.backtest.utils import CommonInfrastructure

    codes = codes or sorted(quote.index.get_level_values("instrument").unique())
    exch = make_exchange(quote, codes, start, end, deal_price, cost_bp)
    account = Account(init_cash=1_000_000.0, freq="day",
                      benchmark_config={"benchmark": benchmark_ret})
    infra = CommonInfrastructure(trade_account=account, trade_exchange=exch)
    from qlib.backtest.executor import SimulatorExecutor

    executor = SimulatorExecutor(
        time_per_step="day", start_time=str(pd.Timestamp(start).date()),
        end_time=str(pd.Timestamp(end).date()), common_infra=infra,
        generate_portfolio_metrics=True, verbose=False)
    strategy.reset_common_infra(infra)  # 契约 E-6：手动装配
    port, _ = backtest_loop(str(pd.Timestamp(start).date()),
                            str(pd.Timestamp(end).date()), strategy, executor)
    portfolio_df = port["1day"][0]
    return portfolio_df, exch, account
