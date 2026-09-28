"""冒烟3：ParquetProvider 三接口注入 qlib 数据层（免 bin），表达式引擎跑日线与 5min。

注入机制（pyqlib 0.9.7 已核实）：
  qlib.init 的 kwargs 进 C 后触发 config.register() → register_all_wrappers(C)
  → init_instance_by_config(C.calendar_provider / feature_provider /
  instrument_provider)，dict 配置支持 {"class":..., "module_path":...}。
  expression_provider 留默认（LocalExpressionProvider 只做表达式编排，
  叶子经 FeatureD 回到我们的 parquet provider）。
  provider_uri 仅被 init 做存在性检查 + 潜在缓存写入 → 指向 data/cache
  并显式关闭 expression_cache/dataset_cache。
"""
from pathlib import Path

import numpy as np
import pandas as pd
import qlib
from qlib.data.data import CalendarProvider, FeatureProvider, InstrumentProvider

CACHE = Path("/home/zxh/projects/6.resonance/data/cache")

# ---- parquet 载入：日历 × 宽表 ----
daily = pd.read_parquet(CACHE / "daily_bars.parquet",
                        columns=["symbol", "date", "open", "high", "low", "close", "volume"])
daily["date"] = pd.to_datetime(daily["date"])
m5 = pd.read_parquet(CACHE / "minute5_bars.parquet", columns=["symbol", "datetime", "close"])
m5["datetime"] = pd.to_datetime(m5["datetime"])

CALS = {
    "day": pd.DatetimeIndex(sorted(daily["date"].unique())).to_numpy(dtype=object),
    "5min": pd.DatetimeIndex(sorted(m5["datetime"].unique())).to_numpy(dtype=object),
}
DAILY_WIDE = {f: daily.pivot(index="date", columns="symbol", values=f).sort_index()
              for f in ("open", "high", "low", "close", "volume")}
M5_WIDE = {"close": m5.pivot(index="datetime", columns="symbol", values="close").sort_index()}
POOLS = {"all": sorted(daily["symbol"].unique()),
         "anchor_v43": ["399001.SZ", "399303.SZ", "000688.SH"]}
print(f"[data] day 日历 {len(CALS['day'])} 日（{CALS['day'][0]}~{CALS['day'][-1]}）| "
      f"5min 日历 {len(CALS['5min'])} bar（{CALS['5min'][0]}~{CALS['5min'][-1]}）| "
      f"{len(POOLS['all'])} 标的")


# ---- 三接口 provider ----
class ParquetCalendarProvider(CalendarProvider):
    def load_calendar(self, freq, future=False):
        return CALS[freq]  # 基类 calendar() 负责按起止切片


class ParquetInstrumentProvider(InstrumentProvider):
    def instruments(self, market="all", filter_pipe=None, start_time=None, end_time=None):
        return {"market": market, "filter_pipe": filter_pipe or []}

    def list_instruments(self, instruments, start_time=None, end_time=None,
                         freq="day", as_list=False):
        market = instruments["market"]
        insts = list(market) if isinstance(market, (list, tuple, set)) \
            else list(POOLS.get(market, POOLS["all"]))
        cal = CALS[freq]
        s = pd.Timestamp(start_time) if start_time is not None else cal[0]
        e = pd.Timestamp(end_time) if end_time is not None else cal[-1]
        out = {i: [(s, e)] for i in insts}
        return list(out) if as_list else out


class ParquetFeatureProvider(FeatureProvider):
    """feature(instrument, field, start_index, end_index, freq) → 日历下标切片的 ndarray。

    数组对齐全量日历（缺失处 NaN），按 (freq, 标的, 字段) 惰性缓存。
    """

    def __init__(self):
        self._cache = {}

    def _arr(self, instrument, field, freq):
        key = (freq, instrument, field)
        if key not in self._cache:
            wide = (DAILY_WIDE if freq == "day" else M5_WIDE).get(field)
            if wide is None or instrument not in wide.columns:
                arr = np.full(len(CALS[freq]), np.nan)
            else:
                arr = wide[instrument].reindex(CALS[freq]).to_numpy(dtype=np.float64)
            self._cache[key] = arr
        return self._cache[key]

    def feature(self, instrument, field, start_index, end_index, freq):
        arr = self._arr(instrument, str(field)[1:], freq)
        # 契约（base.py Expression.load）：返回 pd.Series，索引=日历下标，
        # 上游会设置 series.name 并用 .loc[start:end] 切片
        return pd.Series(arr[start_index: end_index + 1],
                         index=np.arange(start_index, end_index + 1))


# ---- 注入 ----
qlib.init(
    provider_uri=str(CACHE),  # 仅满足存在性检查；我们的 provider 不读它
    region="cn",
    expression_cache=None, dataset_cache=None,  # 禁缓存写入，防污染 data/cache
    calendar_provider={"class": "ParquetCalendarProvider", "module_path": "__main__"},
    instrument_provider={"class": "ParquetInstrumentProvider", "module_path": "__main__"},
    feature_provider={"class": "ParquetFeatureProvider", "module_path": "__main__"},
)
from qlib.data import D  # init 之后导入

# ---- 验证 ----
codes = ["399001.SZ", "399303.SZ", "000688.SH"]

# A) 日历
n_day = len(D.calendar(freq="day"))
n_m5 = len(D.calendar(freq="5min"))
assert (n_day, n_m5) == (len(CALS["day"]), len(CALS["5min"])), (n_day, n_m5)
print(f"[A] D.calendar day={n_day} 5min={n_m5} ✓")

# B) 日线叶子字段 vs parquet
df = D.features(codes, ["$close"], "2025-01-01", "2026-09-18", freq="day", disk_cache=0)
ref = DAILY_WIDE["close"][codes].loc["2025-01-01":"2026-09-18"]
got = df["$close"].unstack("instrument")[codes]
pd.testing.assert_frame_equal(got, ref, check_freq=False, check_names=False,
                              check_dtype=False)  # qlib 表达式链路统一 float32
print(f"[B] 日线 $close {df.shape} 与 parquet 逐元素相等 ✓")

# C) 日线表达式 vs pandas（注意 qlib Ref(X,N)=X[t-N]，pct_change(10) 的等价式是 close/Ref-1）
expr = "$close/Ref($close,10)-1"
de = D.features(codes, [expr], "2025-01-01", "2026-09-18", freq="day", disk_cache=0)
pe = DAILY_WIDE["close"][codes].loc["2025-01-01":"2026-09-18"].pct_change(10)
got = de[expr].unstack("instrument")[codes]
mask = pe.notna().to_numpy()  # pandas 切片口径首 10 日 NaN；qlib 扩展窗自带 lookback、有值
diff = np.nanmax(np.abs(got.to_numpy()[mask] - pe.to_numpy()[mask]))
assert np.allclose(got.to_numpy()[mask], pe.to_numpy()[mask],
                   rtol=1e-4, atol=5e-7), f"max|diff|={diff}"  # float32 精度容差
print(f"[C] 表达式 {expr} 与 pandas pct_change(10) 一致（max|diff|={diff:.1e}；"
      f"qlib 扩展窗使窗口头部也有值，pandas 切片口径为 NaN——按后者掩码对比）✓")

# D) 5min 叶子与表达式（窗口按 5min 日历动态取末 5 日——按需矩阵采集使 5min
#    日历非连续（243 天），固定日期窗可能整日无 bar，如 2026-09-18）
tail5 = CALS["5min"][-240:]
s5, e5 = pd.Timestamp(tail5[0]).strftime("%Y-%m-%d %H:%M"), pd.Timestamp(tail5[-1]).strftime("%Y-%m-%d %H:%M")
d5 = D.features(["885907.TI"], ["$close", "Mean($close,48)"], s5, e5,
                freq="5min", disk_cache=0)
r5 = M5_WIDE["close"]["885907.TI"].reindex(CALS["5min"]).loc[s5:e5]
assert len(d5) == len(r5) == 240, (len(d5), len(r5))
assert np.allclose(d5["$close"].droplevel("instrument").to_numpy(), r5.to_numpy(),
                   equal_nan=True)
mref = r5.rolling(48).mean().dropna()
mgot = d5["Mean($close,48)"].droplevel("instrument").loc[mref.index]
assert np.allclose(mgot.to_numpy(), mref.to_numpy(), rtol=1e-4, atol=5e-7)
print(f"[D] 5min $close {len(d5)} bar（{s5}~{e5}）与 Mean($close,48) 与 pandas rolling 一致 ✓"
      f"（注：5min 日历非连续，采集矩阵决定；885907 在缺 bar 时刻为 NaN 填充）")

print("\n[SMOKE PASS] ParquetProvider 注入成立：qlib 数据层+表达式引擎直读 parquet，"
      "day/5min 双频、叶子/表达式均与 pandas 对拍一致，全程零 bin。")
