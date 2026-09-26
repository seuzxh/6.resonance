"""冒烟：parquet 行情直接驱动 pyqlib 0.9.7 回测（不 qlib.init、不转 bin）。

证明链：
  1) daily_bars.parquet → quote_df（$close 等字段）
  2) Cal 桩替换（qlib.backtest.utils.Cal）→ 交易日历来自 parquet
  3) ParquetExchange 覆写 get_quote_from_qlib → 绕开 D.features
  4) TopkDropoutStrategy(signal=DataFrame) + SimulatorExecutor + backtest_loop
  5) 产出 portfolio_metrics
"""
import numpy as np
import pandas as pd

import qlib.backtest.utils as btest_utils
from qlib import config as qconf

# 全局 C 缺键会炸（Exchange 构造路径上的 C 读取）→ 注入研究模式四键
qconf.C["trade_unit"] = None        # 指数研究口径：不取整手
qconf.C["limit_threshold"] = None   # 无涨跌停约束（概念指数不可直接交易）
qconf.C["deal_price"] = "$close"    # 收盘成交（与自研引擎口径一致）
qconf.C["region"] = "cn"

# --- 1) parquet → quote_df ---
bars = pd.read_parquet(
    "/home/zxh/projects/6.resonance/data/cache/daily_bars.parquet",
    columns=["symbol", "date", "open", "high", "low", "close", "volume"],
)
codes = ["399001.SZ", "000688.SH", "885907.TI", "883957.TI"]
bars = bars[bars["symbol"].isin(codes)]
bars = bars[(bars["date"] >= "2026-06-01") & (bars["date"] <= "2026-09-18")]
bars = bars.copy()
bars["date"] = pd.to_datetime(bars["date"])  # parquet 里 date 是字符串，qlib 需 Timestamp
df = bars.rename(columns={"symbol": "instrument", "date": "datetime"}).set_index(["datetime", "instrument"])
q = pd.DataFrame(index=df.index)
for f in ("open", "high", "low", "close", "volume"):
    q[f"${f}"] = df[f].astype(np.float32)
q["$factor"] = 1.0
q["$change"] = q.groupby(level="instrument")["$close"].pct_change()
trade_dates = pd.DatetimeIndex(sorted(q.index.get_level_values("datetime").unique()))
print(f"[data] {len(q)} rows x {len(codes)} codes, {trade_dates[0]} ~ {trade_dates[-1]}, {len(trade_dates)} days")

# --- 2) Cal 桩（照抄 qlib.data.data.CalendarCalendar 的 calendar/locate_index 语义）---
import bisect

class _StubCal:
    def __init__(self, cal: pd.DatetimeIndex):
        # 末尾补 1 日哨兵：get_step_time 的排他上界要读 calendar[end+1]（不参与行情）
        self._cal = np.append(cal.to_numpy(dtype=object), cal[-1] + pd.Timedelta(days=1))
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

btest_utils.Cal = _StubCal(trade_dates)

# --- 3) ParquetExchange ---
from qlib.backtest.exchange import Exchange

class ParquetExchange(Exchange):
    def get_quote_from_qlib(self):
        cols = list(self.all_fields)
        have = [c for c in cols if c in q.columns]
        self.quote_df = q.loc[:, have].copy()
        for c in cols:
            if c not in self.quote_df.columns:
                self.quote_df[c] = 1.0 if c == "$factor" else np.nan
        for attr in ("buy_price", "sell_price"):
            pstr = getattr(self, attr)
            if self.quote_df[pstr].isna().any():
                self.logger.warning(f"{pstr} field data contains nan.")
        # $factor 恒 1.0（不复权研究口径）→ 正常价格模式；再按原方法语义更新停牌/涨跌停标记
        self.trade_w_adj_price = False
        self._update_limit(self.limit_threshold)

exch = ParquetExchange(
    freq="day", start_time=str(trade_dates[0]), end_time=str(trade_dates[-1]),
    codes=codes, deal_price="$close", limit_threshold=None, volume_threshold=None,
    trade_unit=None, open_cost=0.001, close_cost=0.001, min_cost=0.0,
)
print(f"[exchange] quote_df {exch.quote_df.shape}, fields={exch.all_fields}")

# --- 4) infra + strategy + executor ---
from qlib.backtest.account import Account
from qlib.backtest.utils import CommonInfrastructure
from qlib.backtest.executor import SimulatorExecutor
from qlib.backtest import backtest_loop
from qlib.contrib.strategy import TopkDropoutStrategy

# 基准：同花顺全A 日收益序列（parquet 直出，绕开 D.features）
bench_close = q["$close"].unstack("instrument")["883957.TI"]
bench_ret = bench_close.pct_change().fillna(0.0)
account = Account(init_cash=1_000_000.0, freq="day",
                  benchmark_config={"benchmark": bench_ret}, port_metr_enabled=True)
infra = CommonInfrastructure(trade_account=account, trade_exchange=exch)

# 信号：10 日动量（收盘价 DataFrame，(datetime, instrument) MultiIndex）
close_wide = q["$close"].unstack("instrument")
score = close_wide.pct_change(10).stack()
score.name = "score"
score = score.to_frame()

strat = TopkDropoutStrategy(topk=1, n_drop=1, signal=score, hold_thresh=1, forbid_all_trade_at_limit=False)
executor = SimulatorExecutor(
    time_per_step="day", start_time=str(trade_dates[0]), end_time=str(trade_dates[-1]),
    common_infra=infra, generate_portfolio_metrics=True, verbose=False,
)
strat.reset_common_infra(infra)  # 手动装配（高层 backtest() 平时自动做）
port_met, _ = backtest_loop(str(trade_dates[0]), str(trade_dates[-1]), strat, executor)
print(f"[OK] portfolio metrics type={type(port_met).__name__}, keys={list(port_met)}")
acc = port_met["1day"]
for j, el in enumerate(acc):
    print(f"  [{j}] {type(el).__name__}", end="")
    if hasattr(el, "columns"):
        print(f" {el.shape} cols={list(el.columns)}", end="")
    print()
pm = next(el for el in acc if hasattr(el, "columns") and "return" in el.columns)
print(pm.tail(3)[["return", "total_turnover", "total_cost"]].to_string())
print("\n[SMOKE PASS] parquet → qlib 回测全链路无 bin、无 qlib.init")
