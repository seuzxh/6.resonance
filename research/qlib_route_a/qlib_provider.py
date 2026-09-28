"""ParquetProvider：把 data/cache parquet 注入 qlib 数据层（免 bin）。

实现依据 docs/research/qlib-validation-plan.md §10.1 的九条契约，
三个冒烟脚本（research/qlib_route_a/qlib_*_smoke.py）是契约的活证据。
本模块只在 conda env ``qlib``（pyqlib 0.9.7）下运行；跨环境数据契约是
outputs/ 下的 parquet 文件。

组件：
- ParquetData：长表 → 双频日历 + (freq, 字段) 宽表 + 池映射的纯容器；
- ParquetCalendarProvider / ParquetInstrumentProvider / ParquetFeatureProvider：
  qlib 数据层三接口（init_instance_by_config 无参构造，data 由
  init_qlib_parquet 注入到 provider 单例上）；
- init_qlib_parquet：qlib.init + 三 provider + DayLast 算子注册 + 版本断言。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


class ParquetData:
    """parquet 长表 → 双频日历 + (freq, 字段) 宽表 + 池映射的纯容器。

    bars_daily 列：symbol/date/open/high/low/close/volume（date 可为
    字符串，构造时统一转 Timestamp——契约 E-7）。
    bars_5min 列：symbol/datetime/<任意字段>（现库九指标直取，09-26 前
    的历史行为 close-only，OHLCV 为 NaN）。
    缺失 (标的, 时刻) 在宽表中为 NaN——与 qlib 的停牌语义一致。
    """

    def __init__(self, bars_daily: pd.DataFrame, bars_5min: pd.DataFrame,
                 pools: dict[str, list[str]]):
        self.pools = pools
        d = bars_daily.copy()
        d["date"] = pd.to_datetime(d["date"])
        m = bars_5min.copy()
        m["datetime"] = pd.to_datetime(m["datetime"])
        self.cals = {
            "day": pd.DatetimeIndex(sorted(d["date"].unique()))
                     .to_numpy(dtype=object),
            "5min": pd.DatetimeIndex(sorted(m["datetime"].unique()))
                      .to_numpy(dtype=object),
        }
        self._long = {"day": d.set_index(["date", "symbol"]),
                      "5min": m.set_index(["datetime", "symbol"])}
        self.wides: dict[tuple[str, str], pd.DataFrame] = {}

    def wide(self, freq: str, field: str) -> pd.DataFrame:
        """(freq, 字段) → 宽表（行=该频日历，列=标的；惰性构建缓存）。"""
        key = (freq, field)
        if key not in self.wides:
            long = self._long[freq]
            if field not in long.columns:
                self.wides[key] = pd.DataFrame(index=self.cals[freq])
            else:
                self.wides[key] = (long[field].unstack("symbol")
                                   .reindex(self.cals[freq]))
        return self.wides[key]

    @classmethod
    def from_cache_dir(cls, cache_dir) -> "ParquetData":
        """从 data/cache 构造（仅脚本入口用；测试用合成数据）。

        池映射：all=日线全部标的；concept=概念目录∩日线；anchor_v43=
        config.V43_ANCHOR_POOL（入口自检退役码）。
        """
        import sys
        from pathlib import Path

        cache_dir = Path(cache_dir)
        root = Path(__file__).resolve().parents[2]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from resonance import config  # noqa: PLC0415

        d = pd.read_parquet(cache_dir / "daily_bars.parquet")
        m = pd.read_parquet(cache_dir / "minute5_bars.parquet")
        catalog = pd.read_csv(cache_dir.parent / "concept_catalog.csv")
        pools = {
            "all": sorted(d["symbol"].unique()),
            "concept": [c for c in catalog["code"]
                        if c in set(d["symbol"])],
            "anchor_v43": list(config.V43_ANCHOR_POOL),
        }
        config.assert_no_retired(pools["anchor_v43"], context="qlib provider 池")
        return cls(bars_daily=d, bars_5min=m, pools=pools)


# ---------------------------------------------------------------- providers --

def _require(data) -> "ParquetData":
    if data is None:
        raise RuntimeError("provider 尚未注入数据——请先调用 init_qlib_parquet(data)")
    return data


from qlib.data.data import (  # noqa: E402
    CalendarProvider, FeatureProvider, InstrumentProvider,
)


class ParquetCalendarProvider(CalendarProvider):
    """契约②：只需 load_calendar 返回全量日历数组，基类负责切片。"""

    def __init__(self, data: ParquetData | None = None):
        self.data = data

    def load_calendar(self, freq, future=False):
        return _require(self.data).cals[freq]


class ParquetInstrumentProvider(InstrumentProvider):
    """契约②：list_instruments 返回 {标的: [(起, 止)]}。"""

    def __init__(self, data: ParquetData | None = None):
        self.data = data

    def instruments(self, market="all", filter_pipe=None,
                    start_time=None, end_time=None):
        return {"market": market, "filter_pipe": filter_pipe or []}

    def list_instruments(self, instruments, start_time=None, end_time=None,
                         freq="day", as_list=False):
        data = _require(self.data)
        market = instruments["market"]
        insts = (list(market) if isinstance(market, (list, tuple, set))
                 else list(data.pools.get(market, data.pools["all"])))
        cal = data.cals[freq]
        s = pd.Timestamp(start_time) if start_time is not None else cal[0]
        e = pd.Timestamp(end_time) if end_time is not None else cal[-1]
        out = {i: [(s, e)] for i in insts}
        return list(out) if as_list else out


class ParquetFeatureProvider(FeatureProvider):
    """契约③：feature() 返回以日历下标为索引的 pd.Series。

    Expression.load 会对返回值设置 series.name 并用 .loc[start:end] 切片，
    传裸 ndarray 会直接报错（冒烟三实际踩过的问题）。
    """

    def __init__(self, data: ParquetData | None = None):
        self.data = data
        self._cache: dict[tuple[str, str, str], np.ndarray] = {}

    def _arr(self, instrument, field, freq) -> np.ndarray:
        key = (freq, instrument, field)
        if key not in self._cache:
            wide = _require(self.data).wide(freq, field)
            if instrument not in wide.columns:
                arr = np.full(len(_require(self.data).cals[freq]), np.nan)
            else:
                arr = wide[instrument].to_numpy(dtype=np.float64)
            self._cache[key] = arr
        return self._cache[key]

    def feature(self, instrument, field, start_index, end_index, freq):
        arr = self._arr(instrument, str(field)[1:], freq)
        # 契约⑩（右边界钳制）：负向 Ref 的扩展窗会把 end_index 推到日历
        # 末端之外（标签表达式），numpy 切片截短后必须同步截短索引，否则
        # 数据/索引长度不齐直接 ValueError（真数据管道实际踩过）
        lo = max(int(start_index), 0)
        hi = min(int(end_index), len(arr) - 1)
        if hi < lo:
            return pd.Series(dtype=float)
        return pd.Series(arr[lo: hi + 1], index=np.arange(lo, hi + 1))


# ------------------------------------------------------------------- init ----

def init_qlib_parquet(data: ParquetData, provider_uri=None) -> None:
    """qlib.init + 三 provider + DayLast 算子注册（契约①④⑨，全局一次性）。

    provider_uri 只为通过 init 的存在性检查，默认指向 data/cache；
    expression_cache/dataset_cache 显式关闭（禁写数据目录——全局约束 3）。
    """
    import qlib
    from pathlib import Path

    assert qlib.__version__ == "0.9.7", (
        f"pyqlib 版本 {qlib.__version__} ≠ 0.9.7，桩/覆写点未经核验，"
        "升级前先重跑 research/qlib_route_a/qlib_provider_smoke.py")
    from qlib.contrib.ops.high_freq import DayLast

    here = str(Path(__file__).resolve())
    prov = {"module_path": here}
    qlib.init(
        provider_uri=provider_uri or str(
            Path(__file__).resolve().parents[2] / "data" / "cache"),
        region="cn",
        expression_cache=None, dataset_cache=None,
        custom_ops=[DayLast],
        calendar_provider={**prov, "class": "ParquetCalendarProvider"},
        instrument_provider={**prov, "class": "ParquetInstrumentProvider"},
        feature_provider={**prov, "class": "ParquetFeatureProvider"},
    )
    import qlib.data.data as qdd

    # init_instance_by_config 无参构造 provider，此处用带数据的实例
    # 重新注册到 Wrapper 单例上（Wrapper 持有 _provider 并做属性委托）
    qdd.Cal.register(ParquetCalendarProvider(data))
    qdd.Inst.register(ParquetInstrumentProvider(data))
    qdd.FeatureD.register(ParquetFeatureProvider(data))
