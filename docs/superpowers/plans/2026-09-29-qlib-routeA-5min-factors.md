# qlib 路线 A 扩展轨（5min 因子 + 模型训练 + F8 替换对照）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实施已定稿的路线 A 扩展轨——ParquetProvider 生产化、5min 因子
表达式管道、LGBModel 时间切分训练、模型分替换 F8 重排的 5 相位预注册对照。

**Architecture:** 三接口 provider（Calendar/Instrument/Feature）把
data/cache 的 parquet 注入 qlib 数据层（免 bin、qlib.init 正常使用）；
因子计算走表达式引擎（含官方高频算子 DayLast，经 custom_ops 注册）；
两环境分工——qlib env 负责因子与训练，resonance env 负责 F1–F7 导出
与回测对照，outputs/ 下的 parquet 文件是两环境唯一契约。

**Tech Stack:** pyqlib 0.9.7（锁版本）｜lightgbm 4.6｜pandas 2.3.3
（qlib env）与 3.0.5（resonance env）｜parquet。

**Spec:** [docs/research/qlib-validation-plan.md](../../research/qlib-validation-plan.md)
——§十 是本计划的直接规格（路线 A 定稿），§四 是因子定义，
§二 与 §10.1 是已实证的九条集成契约（三个冒烟脚本
`research/qlib_route_a/qlib_{,ml_,provider_}smoke.py` 是契约的活证据，实施时可运行复核）。

## Global Constraints（每个任务默认继承）

1. **环境**：qlib 侧代码只在 `conda run -n qlib` 下运行；resonance 侧只在
   `conda run -n resonance` 下运行；禁止系统 Python。例外依据：用户
   2026-09-28 选定路线 A（spec §九.4），CLAUDE.md 例外注记在 Task 7 落盘。
2. **版本锁**：所有 qlib 侧入口先断言 `qlib.__version__ == "0.9.7"`。
3. **禁写数据目录**：`qlib.init` 必传 `expression_cache=None,
   dataset_cache=None`；`provider_uri` 指向 `data/cache/`（仅为通过
   存在性检查），任何代码不得写 `data/`。
4. **算子注册**：官方高频算子（DayLast 等）默认未注册，init 时必须
   `custom_ops=[DayLast]`（契约⑨，2026-09-29 实测）。
5. **离线测试**：单测用合成数据，不读 `data/cache`、不联网；qlib 相关
   测试文件顶部 `pytest.importorskip("qlib")`（resonance env 全量跑时
   自动跳过）。qlib env 下只跑指定文件，不跑全量：
   `conda run -n qlib python -m pytest tests/qlib_route_a/test_qlib_provider.py -v`。
6. **退役码**：700050.TI / 932000.CSI 不进任何池（入口自检
   `config.assert_no_retired`）。
7. **时点纪律**：特征全部为 T 日收盘可得量；标签从 T+1 起算
   （`Ref($close,-1)`，收盘对收盘——预测目标定义，不随成交口径变）；
   训练/验证/测试按时间切分，禁止随机切分。**成交口径 = V4.4 的
   T+1 开盘价**（用户 2026-09-29 裁决，spec/v44-open-exec-plan.md）：
   X3（T6）两组方案均须在引擎 `exec_price="open"` 模式下运行——该引擎
   改动属于 v44 spec 的任务，是 T6 的前置依赖。
8. **措辞与判据**：结论按 L3 上界措辞、幸存者目录与指数不可交易标注；
   收益对照只取 5 相位中位；判据已预注册于 Task 6（playbook §一）。
9. **每任务一个 commit**；所有产出的文档与注释遵循仓库 AGENTS.md 行文
   约束（完整句子、首现括注、禁止电报体）。

## File Structure（分解决策）

所有 qlib 路线 A 的代码文件集中在专目录 `research/qlib_route_a/`（用户
2026-09-29 指令；目录命名说明见其 README——**禁止命名为 `qlib/`**，
仓库根在 sys.path 时会遮蔽 pyqlib 包）。三个冒烟脚本已迁入该目录。
`research` 与 `research.qlib_route_a` 均为命名空间包（无 `__init__.py`，
Python 3.3+ 语义），仓库根入 sys.path 后
`from research.qlib_route_a.qlib_provider import …` 即可导入。

```text
research/qlib_route_a/          # qlib 路线 A 专目录（收口即整体清理）
├── README.md                   # 目录说明与命名禁忌
├── qlib_smoke.py               # 冒烟①（已在位）
├── qlib_ml_smoke.py            # 冒烟②（已在位）
├── qlib_provider_smoke.py      # 冒烟③（已在位）
├── qlib_provider.py            # T1+T2：ParquetData 容器 + 三接口 provider
│                               #   + init_qlib_parquet() 注入助手（qlib env）
├── qlib_factor_export.py       # T3：F1–F7 日线因子导出（resonance env）
├── qlib_pipeline.py            # T4+T5：5min 因子表达式 → 特征矩阵 →
│                               #   DatasetH → LGBModel → 预测与 IC（qlib env）
└── qlib_f8_compare.py          # T6：post_rank 钩子的 5 相位对照（resonance env）
tests/qlib_route_a/             # qlib 路线 A 测试专目录（用户 2026-09-29 指令，
│                               #   与 research/qlib_route_a/ 对称；pytest 递归收集）
├── test_qlib_provider.py       # T1+T2 测试（qlib env；合成数据）
├── test_qlib_factor_export.py  # T3 测试（resonance env；合成数据）
├── test_qlib_pipeline.py       # T4+T5 测试（qlib env；合成数据）
└── test_qlib_f8_compare.py     # T6 测试（resonance env；合成数据）
outputs/qlib_bridge/daily_factors.parquet   # T3 → T4/T6 的跨环境契约
outputs/qlib_ml/features.parquet           # T4 产物
outputs/qlib_ml/pred.parquet               # T5 → T6 的跨环境契约
outputs/qlib_ml/{coverage.md, ic_report.md, x3_phases.csv, report.md}
```

跨环境契约的 schema 在各任务的 Interfaces 块里精确给出——两个环境的
实施者只通过这些 schema 交互。

---

### Task 1: ParquetData 容器与三接口 provider（生产版）

**Files:**
- Create: `research/qlib_route_a/qlib_provider.py`
- Test: `tests/qlib_route_a/test_qlib_provider.py`

**Interfaces:**
- Consumes: 无（首个任务）。冒烟脚本 `research/qlib_route_a/qlib_provider_smoke.py`
  是实现参考（已验证 exit=0），本任务把它提炼成可注入、可测试的类。
- Produces:
  - `ParquetData(bars_daily: pd.DataFrame, bars_5min: pd.DataFrame,
    pools: dict[str, list[str]])`——两份长表（列为
    symbol/date|datetime/字段…）+ 池映射；属性
    `.cals: dict[str, np.ndarray]`（day 与 5min 全量日历）、
    `.wides: dict[tuple[str, str], pd.DataFrame]`（(freq, 字段)→宽表）。
  - `ParquetCalendarProvider(data: ParquetData)`，方法
    `load_calendar(freq, future=False) -> np.ndarray`。
  - `ParquetInstrumentProvider(data: ParquetData)`，方法
    `instruments(market, filter_pipe, start_time, end_time) -> dict`、
    `list_instruments(instruments, start_time, end_time, freq, as_list)`。
  - `ParquetFeatureProvider(data: ParquetData)`，方法
    `feature(instrument, field, start_index, end_index, freq)
    -> pd.Series`（索引=日历下标——契约③）。
  - `ParquetData.from_cache_dir(cache_dir: Path) -> ParquetData`
    工厂（读真实 daily_bars/minute5_bars/concept_catalog 与
    `config.V43_ANCHOR_POOL`，仅供脚本入口用，测试不用）。

- [ ] **Step 1: 写失败测试（合成数据，覆盖日历/叶子/NaN/池四断言）**

```python
# tests/qlib_route_a/test_qlib_provider.py
"""qlib provider 合成数据测试（qlib env 运行；resonance env 自动跳过）。"""
import numpy as np
import pandas as pd
import pytest

pytest.importorskip("qlib")

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research.qlib_route_a.qlib_provider import (  # noqa: E402
    ParquetCalendarProvider, ParquetData, ParquetFeatureProvider,
    ParquetInstrumentProvider,
)


@pytest.fixture(scope="module")
def pdata() -> ParquetData:
    days = pd.date_range("2026-01-05", periods=6, freq="B")
    bars = pd.DataFrame(
        {"symbol": ["A"] * 6 + ["B"] * 4,
         "date": list(days) + list(days[:4]),
         "close": [10.0, 11.0, 12.0, 11.5, 10.5, 11.0] + [5.0, 5.5, 6.0, 5.8],
         "volume": np.arange(10, dtype=float)})
    # 5min：两日 × 各 4 根 bar（合成小网格即可验证双频契约）
    ts = pd.to_datetime(["2026-01-05 09:35", "2026-01-05 09:40",
                         "2026-01-06 09:35", "2026-01-06 09:40"] * 2)
    m5 = pd.DataFrame(
        {"symbol": ["A"] * 4 + ["B"] * 4, "datetime": ts,
         "close": [10.0, 10.2, 10.8, 11.0] + [5.0, 5.1, 5.2, 5.3]})
    return ParquetData(bars_daily=bars, bars_5min=m5,
                       pools={"all": ["A", "B"], "anchor": ["A"]})


def test_calendar_dual_freq(pdata):
    cal_day = ParquetCalendarProvider(pdata).load_calendar("day")
    cal_m5 = ParquetCalendarProvider(pdata).load_calendar("5min")
    assert len(cal_day) == 6 and len(cal_m5) == 8
    assert isinstance(cal_day, np.ndarray)


def test_feature_series_indexed_by_calendar_position(pdata):
    prov = ParquetFeatureProvider(pdata)
    s = prov.feature("A", "$close", 0, 5, "day")
    assert isinstance(s, pd.Series)
    assert list(s.index) == [0, 1, 2, 3, 4, 5]
    assert s.tolist() == [10.0, 11.0, 12.0, 11.5, 10.5, 11.0]
    # 缺失标的 → 全 NaN（与停牌语义一致）
    s2 = prov.feature("B", "$close", 4, 5, "day")
    assert s2.isna().all()


def test_instrument_pools(pdata):
    prov = ParquetInstrumentProvider(pdata)
    out = prov.list_instruments({"market": "anchor", "filter_pipe": []},
                                as_list=True)
    assert out == ["A"]


def test_lazy_cache_hits(pdata):
    prov = ParquetFeatureProvider(pdata)
    prov.feature("A", "$close", 0, 0, "day")
    assert ("day", "A", "close") in prov._cache
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n qlib python -m pytest tests/qlib_route_a/test_qlib_provider.py -v`
Expected: FAIL（`ModuleNotFoundError: research.qlib_route_a.qlib_provider`）

- [ ] **Step 3: 实现 research/qlib_route_a/qlib_provider.py（数据层部分）**

```python
"""ParquetProvider：把 data/cache parquet 注入 qlib 数据层（免 bin）。

实现依据 docs/research/qlib-validation-plan.md §10.1 的九条契约，
三个冒烟脚本（research/qlib_route_a/qlib_*_smoke.py）是契约的活证据。
本文件前半为数据层（ParquetData 与三接口），后半为 init_qlib_parquet()
注入助手（Task 2 增加）。只在 conda env `qlib` 下运行。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


class ParquetData:
    """parquet 长表 → 双频日历 + (freq, 字段) 宽表 + 池映射的纯容器。

    bars_daily 列：symbol/date/open/high/low/close/volume（date 可为
    字符串，构造时统一转 Timestamp——契约 E-7）。
    bars_5min 列：symbol/datetime/<任意字段>（现库九指标直读）。
    缺失 (标的, 时刻) 在宽表中为 NaN（=停牌语义）。
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
                self.wides[key] = pd.DataFrame(
                    index=self.cals[freq])  # 全空 → 全 NaN
            else:
                self.wides[key] = (long[field].unstack("symbol")
                                   .reindex(self.cals[freq]))
        return self.wides[key]

    @classmethod
    def from_cache_dir(cls, cache_dir) -> "ParquetData":
        """从 data/cache 构造（仅脚本入口用；测试用合成数据）。"""
        from pathlib import Path
        cache_dir = Path(cache_dir)
        d = pd.read_parquet(cache_dir / "daily_bars.parquet")
        m = pd.read_parquet(cache_dir / "minute5_bars.parquet")
        catalog = pd.read_csv(cache_dir.parent / "concept_catalog.csv")
        import resonance.config as cfg  # noqa: PLC0415
        pools = {
            "all": sorted(d["symbol"].unique()),
            "concept": [c for c in catalog["code"]
                        if c in set(d["symbol"])],
            "anchor_v43": list(cfg.V43_ANCHOR_POOL),
        }
        cfg.assert_no_retired(pools["anchor_v43"], context="qlib provider 池")
        return cls(bars_daily=d, bars_5min=m, pools=pools)
```

provider 三类（同文件追加）：

```python
from qlib.data.data import (  # noqa: E402
    CalendarProvider, FeatureProvider, InstrumentProvider,
)


class ParquetCalendarProvider(CalendarProvider):
    def __init__(self, data: ParquetData):
        self.data = data

    def load_calendar(self, freq, future=False):
        return self.data.cals[freq]  # 基类 calendar() 负责切片


class ParquetInstrumentProvider(InstrumentProvider):
    def __init__(self, data: ParquetData):
        self.data = data

    def instruments(self, market="all", filter_pipe=None,
                    start_time=None, end_time=None):
        return {"market": market, "filter_pipe": filter_pipe or []}

    def list_instruments(self, instruments, start_time=None, end_time=None,
                         freq="day", as_list=False):
        market = instruments["market"]
        insts = (list(market) if isinstance(market, (list, tuple, set))
                 else list(self.data.pools.get(market, self.data.pools["all"])))
        cal = self.data.cals[freq]
        s = pd.Timestamp(start_time) if start_time is not None else cal[0]
        e = pd.Timestamp(end_time) if end_time is not None else cal[-1]
        out = {i: [(s, e)] for i in insts}
        return list(out) if as_list else out


class ParquetFeatureProvider(FeatureProvider):
    def __init__(self, data: ParquetData):
        self.data = data
        self._cache: dict[tuple[str, str, str], np.ndarray] = {}

    def _arr(self, instrument, field, freq) -> np.ndarray:
        key = (freq, instrument, field)
        if key not in self._cache:
            wide = self.data.wide(freq, field)
            if instrument not in wide.columns:
                arr = np.full(len(self.data.cals[freq]), np.nan)
            else:
                arr = wide[instrument].to_numpy(dtype=np.float64)
            self._cache[key] = arr
        return self._cache[key]

    def feature(self, instrument, field, start_index, end_index, freq):
        arr = self._arr(instrument, str(field)[1:], freq)
        # 契约③：返回以日历下标为索引的 pd.Series（上游设 series.name）
        return pd.Series(arr[start_index: end_index + 1],
                         index=np.arange(start_index, end_index + 1))
```

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n qlib python -m pytest tests/qlib_route_a/test_qlib_provider.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add research/qlib_route_a/qlib_provider.py tests/qlib_route_a/test_qlib_provider.py
git commit -m "qlib路线A-T1: ParquetData容器与三接口provider（合成数据测试4项过）"
```

---

### Task 2: init_qlib_parquet() 注入助手（custom_ops + 版本断言）

**Files:**
- Modify: `research/qlib_route_a/qlib_provider.py`（文件末尾追加）
- Modify: `tests/qlib_route_a/test_qlib_provider.py`（追加集成测试）

**Interfaces:**
- Consumes: Task 1 的三个 provider 类。
- Produces: `init_qlib_parquet(data: ParquetData, provider_uri: str
  | None = None) -> None`——执行 qlib.init 并注入；之后 `qlib.data.D`
  可直接用。副作用是全局的（一个进程只 init 一次）。

- [ ] **Step 1: 追加失败测试（init 后 D.calendar / D.features /
  DayLast 各一断言）**

```python
# tests/qlib_route_a/test_qlib_provider.py 追加
def test_init_and_d_features(pdata):
    import qlib
    from qlib.contrib.ops.high_freq import DayLast
    from research.qlib_route_a.qlib_provider import init_qlib_parquet
    init_qlib_parquet(pdata)          # 全局 init；本模块后续测试复用
    from qlib.data import D
    assert len(D.calendar(freq="day")) == 6
    df = D.features(["A"], ["$close"], None, None, freq="day", disk_cache=0)
    assert df["$close"].tolist() == [10.0, 11.0, 12.0, 11.5, 10.5, 11.0]
    d5 = D.features(["A"], ["DayLast($close)"], None, None,
                    freq="5min", disk_cache=0)
    # 两日各 4 根 bar，DayLast 把每日末值传播到当日全部 bar
    assert d5["DayLast($close)"].tolist() == [10.2] * 2 + [11.0] * 2
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n qlib python -m pytest tests/qlib_route_a/test_qlib_provider.py -v`
Expected: 新测试 FAIL（`init_qlib_parquet` 未定义）

- [ ] **Step 3: 实现注入助手（文件末尾追加）**

```python
def init_qlib_parquet(data: ParquetData, provider_uri=None) -> None:
    """qlib.init + 三 provider + 高频算子注册（契约①④⑨，全局一次性）。

    provider_uri 只为通过 init 的存在性检查，默认指向 data/cache；
    缓存写入显式关闭（约束 3）。
    """
    import qlib
    assert qlib.__version__ == "0.9.7", (
        f"pyqlib 版本 {qlib.__version__} ≠ 0.9.7，桩/覆写点未经核验，"
        "升级前先重跑 research/qlib_route_a/qlib_provider_smoke.py")
    from qlib.contrib.ops.high_freq import DayLast
    from pathlib import Path
    here = str(Path(__file__).resolve())
    prov = {"class": None, "module_path": here}  # class 由下方填入
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
    # 把数据实例挂到 provider 单例上（init_instance_by_config 无参构造）
    import qlib.data.data as qdd
    qdd.Cal.provider.data = data
    qdd.Inst.provider.data = data
    qdd.FeatureD.provider.data = data
```

> 注入后挂数据的实现说明：`init_instance_by_config` 以无参构造 provider，
> 所以类必须接受无参构造。把 Task 1 三个类的 `__init__(self, data)` 改为
> `__init__(self, data=None)`，data 为 None 时留空、由本函数注入
> （`self.data` 属性在 feature/calendar 调用前必须已注入，否则抛出
> 明确错误信息）。同时删除测试 fixture 中直接传 data 的构造差异——
> 两处构造方式等价。

- [ ] **Step 4: 按 Step 3 注释调整 Task 1 三个类的构造签名
  （`data: ParquetData | None = None`，调用前校验已注入）**

- [ ] **Step 5: 运行确认通过（含 Task 1 的 4 项不回归）**

Run: `conda run -n qlib python -m pytest tests/qlib_route_a/test_qlib_provider.py -v`
Expected: 5 PASS

- [ ] **Step 6: 真数据复核（不进 CI，人工步骤）**

Run: `conda run -n qlib python research/qlib_route_a/qlib_provider_smoke.py`
Expected: exit=0（冒烟仍绿，证明生产版与冒烟契约一致）

- [ ] **Step 7: Commit**

```bash
git add research/qlib_route_a/qlib_provider.py tests/qlib_route_a/test_qlib_provider.py
git commit -m "qlib路线A-T2: init_qlib_parquet注入助手（custom_ops注册DayLast+版本断言+真数据冒烟复核）"
```

---

### Task 3: F1–F7 日线因子导出（resonance env）

**Files:**
- Create: `research/qlib_route_a/qlib_factor_export.py`
- Test: `tests/qlib_route_a/test_qlib_factor_export.py`

**Interfaces:**
- Consumes: `resonance.v3.V3Signals`（预计算信号缓存）、
  `resonance.config.V43_ANCHOR_POOL`。
- Produces: `outputs/qlib_bridge/daily_factors.parquet`，列为
  `date(str YYYY-MM-DD), concept(str), rank(int), score, sync, capture,
  leader(str), gate(bool), half_life(float)`——rank 为当日 F6 榜名次，
  闸门失败/无候选日零行。T4 与 T6 依赖此 schema。

- [ ] **Step 1: 写失败测试（合成矩阵，验证 schema 与闸门日零行）**

```python
# tests/qlib_route_a/test_qlib_factor_export.py
"""F1–F7 导出测试（resonance env；合成数据，离线）。"""
import numpy as np
import pandas as pd

from resonance.v3 import V3Params
from research.qlib_route_a.qlib_factor_export import export_factors


def _synthetic_close() -> pd.DataFrame:
    """3 锚 + 2 概念 × 40 日：锚 A 持续领涨且闸门多数日通过。"""
    days = pd.bdate_range("2026-01-05", periods=40)
    a = pd.Series(np.linspace(100, 140, 40), index=days)   # 锚A 强动量
    b = pd.Series(np.linspace(100, 105, 40), index=days)
    c = pd.Series(np.linspace(100, 101, 40), index=days)
    c1 = a * 1.01 + np.sin(np.arange(40))                  # 概念1 跟随A
    c2 = pd.Series(100.0, index=days)                      # 概念2 横盘
    return pd.DataFrame({"ANCHORA": a, "ANCHORB": b, "ANCHORC": c,
                         "CON1": c1, "CON2": c2})


def test_export_schema_and_gate_days():
    close = _synthetic_close()
    df = export_factors(close, concepts=["CON1", "CON2"],
                        broad_codes=["ANCHORA", "ANCHORB", "ANCHORC"],
                        params=V3Params())
    cols = {"date", "concept", "rank", "score", "sync", "capture",
            "leader", "gate", "half_life"}
    assert cols <= set(df.columns)
    # 概念2 三日复合恒为 0 → 不满足 >0，永不入榜
    assert "CON2" not in set(df["concept"])
    # 前 10 日无完整共振窗 → 首个信号日不早于第 11 日
    dates = sorted(df["date"].unique())
    assert dates[0] >= str(close.index[10].date())
    # 有信号日：leader=动量最强锚、score 降序
    d0 = df[df["date"] == dates[0]]
    assert d0["leader"].iloc[0] == "ANCHORA"
    assert list(d0["score"]) == sorted(d0["score"], reverse=True)
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n resonance python -m pytest tests/qlib_route_a/test_qlib_factor_export.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 export_factors（复用 V3Signals，不重写公式）**

```python
"""F1–F7 日线因子导出（resonance env）→ outputs/qlib_bridge/daily_factors.parquet。

因子定义见 docs/research/qlib-validation-plan.md §四.1；本脚本只是把
resonance.v3.V3Signals 的逐日榜落盘为跨环境契约文件，公式零重写。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd

from resonance import config
from resonance.v3 import V3Params, V3Signals


def export_factors(close_all: pd.DataFrame, concepts: list[str],
                   broad_codes: list[str],
                   params: V3Params | None = None) -> pd.DataFrame:
    config.assert_no_retired(broad_codes, context="因子导出锚池")
    sig = V3Signals(close_all, concepts, broad_codes,
                    "883957.TI", params or V3Params())
    rows = []
    for i in range(len(close_all)):
        if not sig.has_leader[i] or not sig.gate[i]:
            continue  # 闸门失败/无候选日零行
        rk = sig.ranking(i)
        for rank, r in enumerate(rk.itertuples(index=False), start=1):
            rows.append({
                "date": str(close_all.index[i].date()),
                "concept": r.concept, "rank": rank,
                "score": float(r.score), "sync": float(r.sync),
                "capture": float(r.capture),
                "leader": broad_codes[sig.leader_idx[i]],
                "gate": True,
                "half_life": float(sig.hl[i]),
            })
    return pd.DataFrame(rows)


def main() -> int:
    """真数据导出：装载 → 导出 → 落盘（装载三行与 ops/backtest_v3.py
    的 load_wide 同款：read_parquet daily_bars → pivot close → 目录∩bars）。

    broad_codes 取 config.V43_ANCHOR_POOL；allA 固定 883957.TI（须在
    close_all 列中）；落盘 outputs/qlib_bridge/daily_factors.parquet，
    打印行数/信号日数/概念数。
    """
    bars = pd.read_parquet(config.CACHE_DIR / "daily_bars.parquet")
    catalog = pd.read_csv(config.DATA_DIR / "concept_catalog.csv")
    close_all = bars.pivot(index="date", columns="symbol",
                           values="close").sort_index()
    close_all.index = pd.to_datetime(close_all.index)
    concepts = [c for c in catalog["code"] if c in close_all.columns]
    df = export_factors(close_all, concepts,
                        broad_codes=list(config.V43_ANCHOR_POOL))
    out = config.OUTPUTS_DIR / "qlib_bridge" / "daily_factors.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    print(f"[OK] {len(df)} 行 / {df['date'].nunique()} 信号日 / "
          f"{df['concept'].nunique()} 概念 → {out}")
    return 0

- [ ] **Step 4: 补全 main()（按上注三行装载 + to_parquet + 打印）并
  运行测试确认通过**

Run: `conda run -n resonance python -m pytest tests/qlib_route_a/test_qlib_factor_export.py -v`
Expected: PASS

- [ ] **Step 5: 真数据导出（人工步骤，产出契约文件）**

Run: `conda run -n resonance python research/qlib_route_a/qlib_factor_export.py`
Expected: 打印约 7 万行级、约 390 信号日（2022-01 起），文件落在
`outputs/qlib_bridge/daily_factors.parquet`

- [ ] **Step 6: Commit**

```bash
git add research/qlib_route_a/qlib_factor_export.py tests/qlib_route_a/test_qlib_factor_export.py
git commit -m "qlib路线A-T3: F1-F7因子导出（V3Signals复用零重写，跨环境契约daily_factors.parquet）"
```

---

### Task 4: 5min 因子表达式清单冻结与特征管道

**Files:**
- Create: `research/qlib_route_a/qlib_pipeline.py`
- Test: `tests/qlib_route_a/test_qlib_pipeline.py`

**Interfaces:**
- Consumes: Task 2 的 `init_qlib_parquet`；Task 3 的
  `outputs/qlib_bridge/daily_factors.parquet`。
- Produces:
  - `FREQ5_EXPRESSIONS: dict[str, str]`（冻结的 8 条 5min 表达式）；
  - `build_features(data: ParquetData, concepts: list[str],
    start: str, end: str) -> pd.DataFrame`——日频特征矩阵，索引
    (datetime, instrument)，列 = 8 个 5min 因子 + F1–F7 七列（leader
    编码为 category、gate 为 bool），无标签；
  - `outputs/qlib_ml/features.parquet` 与 `outputs/qlib_ml/coverage.md`。

- [ ] **Step 1: 冻结表达式清单（写入 research/qlib_route_a/qlib_pipeline.py 顶部）**

```python
FREQ5_EXPRESSIONS = {
    # 尾盘（最后24根bar）动量与全天动量——采样到日末 bar 即日频值
    "TAIL_MOM24": "$close/Ref($close,23)-1",
    "FULL_MOM48": "$close/Ref($close,47)-1",
    # 尾盘波动 vs 全天波动（bar 间收益的滚动标准差）
    "TAIL_VOL24": "Std($close/Ref($close,1)-1,23)",
    "FULL_VOL48": "Std($close/Ref($close,1)-1,47)",
    # 尾盘量能占比（尾盘均量 / 全日均量）
    "TAIL_VRATIO": "Mean($volume,24)/Mean($volume,48)",
    # 收盘价日内位置：(末bar收盘-日内最低)/(日内最高-日内最低)
    "DAY_POS": "(DayLast($close)-DayLast(Min($low,48)))"
               "/(DayLast(Max($high,48))-DayLast(Min($low,48)))",
    # 高价触及度：日内最高相对前收盘的涨幅（捕捉日内冲高）
    "HI_PUMP": "DayLast(Max($high,48))/DayLast(Ref($close,48))-1",
    # 尾盘加速度：最后12根bar动量 / 之前12根bar动量
    "TAIL_ACC": "($close/Ref($close,11)-1)/(Ref($close,12)/Ref($close,23)-1)",
}
```

- [ ] **Step 2: 写失败测试（合成 5min 数据手算对照）**

```python
# tests/qlib_route_a/test_qlib_pipeline.py
"""5min 因子管道测试（qlib env；合成数据）。"""
import numpy as np
import pandas as pd
import pytest

pytest.importorskip("qlib")

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research.qlib_route_a.qlib_provider import ParquetData, init_qlib_parquet
from research.qlib_route_a.qlib_pipeline import FREQ5_EXPRESSIONS, build_features


@pytest.fixture(scope="module")
def env():
    """两日 × 48bar × 1 概念 + 3 锚（日线），价格线性递增可手算。"""
    bars5 = []
    for day, base in (("2026-01-05", 100.0), ("2026-01-06", 110.0)):
        for k in range(48):
            ts = pd.Timestamp(f"{day} {(9,35+k) if k < 24 else (13,5+k-24):02d}:{(k%2)*30:02d}")
            px = base + 0.1 * k
            bars5.append({"symbol": "CON1", "datetime": ts, "open": px,
                          "high": px + 0.5, "low": px - 0.5, "close": px,
                          "volume": 1000.0 + 10 * k})
    m5 = pd.DataFrame(bars5)
    days = pd.bdate_range("2025-12-01", periods=30)
    d = pd.DataFrame({"symbol": ["A"] * 30,
                      "date": days,
                      "open": np.linspace(100, 130, 30),
                      "high": np.linspace(101, 131, 30),
                      "low": np.linspace(99, 129, 30),
                      "close": np.linspace(100, 130, 30),
                      "volume": np.full(30, 500.0)})
    pdata = ParquetData(bars_daily=d, bars_5min=m5,
                        pools={"all": ["A", "CON1"]})
    init_qlib_parquet(pdata)
    return pdata


def test_expressions_match_pandas(env, tmp_path):
    # F1–F7 桥文件不存在时 build_features 只返回 5min 因子（join 为空）
    feats = build_features(env, concepts=["CON1"], start="2026-01-05",
                           end="2026-01-06", bridge_parquet=None)
    assert list(feats.index.get_level_values(0).unique()) == [
        pd.Timestamp("2026-01-05"), pd.Timestamp("2026-01-06")]
    # TAIL_MOM24 手算：第二日末 bar 收盘 = 110+0.1*47=114.7，
    # 23 根前 = 110+0.1*24=112.4 → 114.7/112.4-1
    got = feats.loc[(pd.Timestamp("2026-01-06"), "CON1"), "TAIL_MOM24"]
    assert abs(float(got) - (114.7 / 112.4 - 1)) < 1e-5
```

- [ ] **Step 3: 运行确认失败**

Run: `conda run -n qlib python -m pytest tests/qlib_route_a/test_qlib_pipeline.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 4: 实现 build_features（表达式评估 + 采样到日频 + 桥 join）**

```python
def build_features(data, concepts: list[str], start: str, end: str,
                   bridge_parquet=None) -> pd.DataFrame:
    """D.features@5min 评估全部表达式 → 按日取末 bar（日频化）→ join F1–F7。

    5min 日历非连续（契约⑧）：按日采样天然只在有 bar 的交易日产生行。
    """
    from qlib.data import D
    exprs = list(FREQ5_EXPRESSIONS.values())
    df = D.features(concepts, exprs, start, end, freq="5min", disk_cache=0)
    df.columns = list(FREQ5_EXPRESSIONS)
    day_idx = pd.Index([pd.Timestamp(ts).normalize()
                        for ts in df.index.get_level_values("datetime")])
    feats = df.groupby([day_idx, df.index.get_level_values("instrument")],
                       sort=True).last()      # 每日末 bar = 日频值
    feats.index.names = ["datetime", "instrument"]
    if bridge_parquet is not None:
        f17 = pd.read_parquet(bridge_parquet)
        f17["date"] = pd.to_datetime(f17["date"])
        f17 = f17.pivot_table(index="date", columns="concept",
                              values=["score", "sync", "capture",
                                      "half_life"])
        # join 到 feats 索引（宽表重塑为 (datetime,instrument) 长面）
        long17 = (f17.stack("concept", future_stack=True)
                    .rename_axis(["datetime", "instrument"]))
        feats = feats.join(long17, how="left")
    return feats
```

- [ ] **Step 5: 运行确认通过**

Run: `conda run -n qlib python -m pytest tests/qlib_route_a/test_qlib_pipeline.py -v`
Expected: PASS

- [ ] **Step 6: main() 真数据产出特征与覆盖率报告（人工步骤）**

main() 逻辑（同文件追加）：`ParquetData.from_cache_dir` → 概念池取
5min 覆盖码（`data.wide("5min","close").notna().any()` 过滤）→
build_features(start="2025-09-22", end=5min 日历末, bridge_parquet=
`outputs/qlib_bridge/daily_factors.parquet`) → 落盘
`outputs/qlib_ml/features.parquet`；coverage.md 内容按日统计：
非 NaN 概念数、8 因子各自的 NaN 率、5min 日历天数（预期 243 天附近）。

Run: `conda run -n qlib python research/qlib_route_a/qlib_pipeline.py`
Expected: features.parquet 约 243 日 × ~390 概念行级；coverage.md 落盘

- [ ] **Step 7: Commit**

```bash
git add research/qlib_route_a/qlib_pipeline.py tests/qlib_route_a/test_qlib_pipeline.py
git commit -m "qlib路线A-T4: 5min因子表达式冻结(8条)+特征管道+覆盖率报告"
```

---

### Task 5: 标签、时间切分训练与 IC 报告

**Files:**
- Modify: `research/qlib_route_a/qlib_pipeline.py`（追加训练段）
- Modify: `tests/qlib_route_a/test_qlib_pipeline.py`（追加）

**Interfaces:**
- Consumes: Task 4 的 `build_features` 与 `outputs/qlib_ml/features.parquet`。
- Produces:
  - `make_labels(data, concepts, start, end) -> pd.DataFrame`——列
    `LABEL1/LABEL2/LABEL5`（T+1/T+2/T+5 收益，day 频，
    `$close/Ref($close,-k)-1` 表达式）；
  - `train_model(features: pd.DataFrame, segments: dict) -> LGBModel`
    （内部组装 feature/label 两级列头 → DataHandlerLP.from_df →
    DatasetH → LGBModel，沿 A0 冒烟已验证路径）；
  - `outputs/qlib_ml/pred.parquet`（列 date, concept, pred——T6 的输入
    契约）与 `outputs/qlib_ml/ic_report.md`（因子与模型分的
    T+1/T+2/T+5 Rank IC 表）。
  - 时间切分冻结值：train 2025-09-22~2026-05-29、valid
    2026-06-01~2026-07-31、test 2026-08-03~数据末（时间切分，约束 7）。

- [ ] **Step 1: 写失败测试（合成特征 + 线性可学关系）**

```python
# tests/qlib_route_a/test_qlib_pipeline.py 追加
def test_train_predict_on_synthetic(tmp_path, monkeypatch):
    from research.qlib_route_a.qlib_pipeline import make_labels, train_model
    rng = np.random.default_rng(7)
    days = pd.bdate_range("2026-01-05", periods=60)
    idx = pd.MultiIndex.from_product(
        [days, ["C1", "C2", "C3"]], names=["datetime", "instrument"])
    X = pd.DataFrame(rng.normal(size=(len(idx), 3)),
                     index=idx, columns=["A", "B", "C"])
    X["LABEL1"] = (0.5 * X["A"] - 0.3 * X["B"]
                   + rng.normal(scale=0.01, size=len(idx)))
    segments = {"train": ("2026-01-05", "2026-02-27"),
                "valid": ("2026-03-02", "2026-03-31"),
                "test":  ("2026-04-01", "2026-03-31")}  # 空 test 允许
    model = train_model(X, segments)
    assert model is not None
```

> 注：train_model 对空 test 段（end<start）应跳过评估不报错；
  make_labels 单测断言 LABEL1 的首行无 NaN 且与 pandas shift(-1) 一致。

- [ ] **Step 2: 运行确认失败 → Step 3: 实现**

```python
def make_labels(data, concepts, start, end) -> pd.DataFrame:
    from qlib.data import D
    exprs = {f"LABEL{k}": f"$close/Ref($close,-{k})-1"
             for k in (1, 2, 5)}
    df = D.features(concepts, list(exprs.values()), start, end,
                    freq="day", disk_cache=0)
    df.columns = list(exprs)
    return df


def train_model(features: pd.DataFrame, segments: dict):
    import lightgbm as lgb  # noqa: F401
    from qlib.contrib.model.gbdt import LGBModel
    from qlib.data.dataset import DatasetH
    from qlib.data.dataset.handler import DataHandlerLP
    data = features.dropna(subset=["LABEL1"]).copy()
    data.columns = pd.MultiIndex.from_tuples(
        [("feature", c) if c not in ("LABEL1", "LABEL2", "LABEL5")
         else ("label", c) for c in data.columns])
    segs = {k: tuple(map(str, v)) for k, v in segments.items()}
    ds = DatasetH(handler=DataHandlerLP.from_df(data), segments=segs)
    model = LGBModel(loss="mse", early_stopping_rounds=30,
                     num_boost_round=60, learning_rate=0.05,
                     num_leaves=8, verbose=-1)
    model.fit(ds, verbose_eval=0)
    return model, ds
```

> Recorder 说明：路线 A 下是真实 qlib.init，`R.log_metrics` 可用，
> 无需 A0 冒烟里的置空桩。

- [ ] **Step 4: 运行确认通过**

Run: `conda run -n qlib python -m pytest tests/qlib_route_a/test_qlib_pipeline.py -v`
Expected: 全部 PASS

- [ ] **Step 5: main() 真数据训练与落盘（人工步骤）**

main() 追加：读 features.parquet → join make_labels → 冻结切分 →
train_model → test 段 predict → 落盘 pred.parquet（date/concept/pred）
→ ic_report.md（8 因子 + F6/sync/capture + 模型分各自对 LABEL1/2/5 的
日频 Rank IC 均值与 t 值，措辞按 L3）。

Run: `conda run -n qlib python research/qlib_route_a/qlib_pipeline.py`
Expected: pred.parquet 落盘（test 段行数打印）；ic_report.md 落盘

- [ ] **Step 6: Commit**

```bash
git add research/qlib_route_a/qlib_pipeline.py tests/qlib_route_a/test_qlib_pipeline.py
git commit -m "qlib路线A-T5: 标签表达式+时间切分LGBModel训练+预测与IC报告落盘"
```

---

### Task 6: F8 替换的 5 相位预注册对照（resonance env）

**Files:**
- Create: `research/qlib_route_a/qlib_f8_compare.py`
- Test: `tests/qlib_route_a/test_qlib_f8_compare.py`

**Interfaces:**
- Consumes: `resonance.v3.V3Backtester`（含 `post_rank` 研究钩子）、
  Task 5 的 `outputs/qlib_ml/pred.parquet`、Task 3 的
  `daily_factors.parquet`、`ops/backtest_v3.py:load_wide`。
- Produces: `outputs/qlib_ml/x3_phases.csv`（相位×配置的收益/回撤/
  换仓/降级计数）与判决结论（打印 + 写入 report.md 的段落）。

**预注册判据（先冻结再跑，playbook §一）**：以 test 段 5 相位中位对照
现行 F8 栈（同窗同参数同 10bp）：成功 = 中位提升 >0 且模型版 ≥F8 版的
相位 ≥4/5；无效 = |中位差| ≤2pp 或方向不一致；反效 = 中位下降 >2pp；
另做孤峰形态检查（L2）。**评分口径：总收益、最大回撤、夏普（244 年化）。**

- [ ] **Step 1: 写失败测试（post_rank 钩子三分支：重排/剔除/回退）**

```python
# tests/qlib_route_a/test_qlib_f8_compare.py
"""F8 替换钩子测试（resonance env；合成数据）。"""
import pandas as pd

from research.qlib_route_a.qlib_f8_compare import make_model_post_rank


def _ranking():
    return pd.DataFrame({
        "concept": ["C1", "C2", "C3", "C4", "C5"],
        "score": [5.0, 4.0, 3.0, 2.0, 1.0]})


def test_reorder_by_model_score():
    pred = pd.DataFrame({"date": ["2026-01-05"] * 3,
                         "concept": ["C3", "C1", "C4"],
                         "pred": [0.9, 0.5, 0.1]})
    fn, stats = make_model_post_rank(pred)
    out = fn(_ranking(), pd.Timestamp("2026-01-05"))
    # 与 F8 语义一致：最终榜只含可评分概念，按模型分降序
    assert list(out["concept"]) == ["C3", "C1", "C4"]
    assert stats["model_days"] == 1 and stats["model_excluded"] == 2


def test_fallback_when_sparse():
    pred = pd.DataFrame({"date": ["2026-01-05"],
                         "concept": ["C1"], "pred": [0.3]})
    fn, stats = make_model_post_rank(pred)
    out = fn(_ranking(), pd.Timestamp("2026-01-05"))
    assert list(out["concept"]) == ["C1", "C2", "C3", "C4", "C5"]  # 原序
    assert stats["model_fallback_sparse"] == 1
```

- [ ] **Step 2: 运行确认失败 → Step 3: 实现钩子**

```python
def make_model_post_rank(pred: pd.DataFrame):
    """返回 (post_rank_fn, stats)。镜像 F8' 降级链（spec §四.2）：

    当日日线 Top5 中有模型分的概念按模型分重排、无分的剔除
    （计 model_excluded）；可评 <2 回退日线原序（计 model_fallback_sparse）。
    """
    stats = {"model_days": 0, "model_excluded": 0,
             "model_fallback_sparse": 0}
    tbl = {(d, c): p for d, c, p in zip(pred["date"], pred["concept"],
                                        pred["pred"])}

    def post_rank(ranking: pd.DataFrame, date) -> pd.DataFrame:
        key_day = str(pd.Timestamp(date).date())
        top = ranking.head(5).copy()
        scored = [(float(tbl[(key_day, r.concept)]), r.concept)
                  for r in top.itertuples(index=False)
                  if (key_day, r.concept) in tbl]
        stats["model_days"] += 1
        stats["model_excluded"] += len(top) - len(scored)
        if len(scored) < 2:
            stats["model_fallback_sparse"] += 1
            return ranking  # 回退：整个日线榜原序（含 sync/capture 列）
        scored.sort(reverse=True)
        # 与 F8 语义一致：重排成功时最终榜只含可评分概念（被剔除者出局），
        # 分钟层 minute_up_resonance 的返回表同样只含可评分概念
        return pd.DataFrame({"concept": [c for _, c in scored],
                             "score": [s for s, _ in scored]})

    return post_rank, stats
```

> 注：`V3Backtester` 的 `post_rank` 钩子签名是
  `post_rank(final_ranking, date) → ranking`（v3.py:439-441），返回表
  列只需 concept+score（引擎只消费这两列做排序与 topk 缓冲）。运行
  配置 A（F8 基线）= 冻结全栈 `V3Params(topk=3, daily_top=5,
  hl_source="leader")` + 真实 MinuteBarProvider；配置 B（模型版）=
  `V3Params(topk=3, hl_source="leader", exec_price="open")`
  （daily_top=0，无分钟层）+ `post_rank=钩子`；配置 A 同样带
  `exec_price="open"`（两组方案同口径，V4.4）。两配置同窗、同 5 相位（test 段前 5 个交易日起点）、
  成本 10bp，经 `ops/backtest_v3.py:evaluate` 出统计。

- [ ] **Step 4: 运行测试确认通过**

Run: `conda run -n resonance python -m pytest tests/qlib_route_a/test_qlib_f8_compare.py -v`
Expected: PASS

- [ ] **Step 5: main() 跑 5 相位对照并落盘（人工步骤；跑前把判据段
  原样粘贴进输出报告——预注册自证）**

Run: `conda run -n resonance python research/qlib_route_a/qlib_f8_compare.py`
Expected: x3_phases.csv 两配置 × 5 相位行级 + 中位对照打印 + 判决

- [ ] **Step 6: Commit**

```bash
git add research/qlib_route_a/qlib_f8_compare.py tests/qlib_route_a/test_qlib_f8_compare.py
git commit -m "qlib路线A-T6: F8替换post_rank钩子+5相位预注册对照（判据先冻结）"
```

---

### Task 7: 报告、文档与收尾

**Files:**
- Create: `outputs/qlib_ml/report.md`
- Modify: `CLAUDE.md`（qlib env 例外注记 + 常用入口两行）
- Modify: `docs/research/qlib-validation-plan.md`（§10.5 X 状态勾选）
- Modify: `research/README.md`（在册实验状态更新）

- [ ] **Step 1: 写 outputs/qlib_ml/report.md**——汇总 coverage.md、
  ic_report.md、x3_phases.csv 的结论段；每条收益结论带五要素
  （成本/相位/窗口/基准/样本内外）与 L3 措辞边界（单一 regime、
  幸存者目录、指数不可交易、5min 日历非连续 243 天）。

- [ ] **Step 2: CLAUDE.md 硬约束 1 追加例外注记**（引用户 2026-09-28
  选定路线 A 的决策与 spec §九），常用入口追加：

```bash
conda run -n qlib python -m pytest tests/qlib_route_a/test_qlib_provider.py tests/qlib_route_a/test_qlib_pipeline.py -v
conda run -n qlib python research/qlib_route_a/qlib_pipeline.py      # 特征+训练+IC
conda run -n resonance python research/qlib_route_a/qlib_f8_compare.py  # X3 五相位对照
```

- [ ] **Step 3: 全量回归**：

Run: `conda run -n resonance python -m pytest -q`
Expected: 全绿（qlib 相关文件 SKIP 不计失败）；再跑
`conda run -n qlib python -m pytest tests/qlib_route_a/test_qlib_provider.py
tests/qlib_route_a/test_qlib_pipeline.py -v` 全绿。

- [ ] **Step 4: spec 与在册状态更新**（X0'–X3 打勾或标注实际产出），
  按 AGENTS.md 完成前校验清单过一遍产出文档。

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/research/qlib-validation-plan.md research/README.md outputs/qlib_ml/report.md
git commit -m "qlib路线A-T7: 报告收尾+qlib env例外注记+X状态更新"
```

---

## Self-Review 记录

1. **规格覆盖**：spec §10.2 组件（provider/pipeline/factor 两轨）→
   T1–T5；§10.3 判据与落点 → T6（post_rank 替换 F8，预注册判据冻结
   于任务头）；§10.4 五条边界 → T4 coverage 与 T7 报告措辞；
   §10.5 里程碑 X0'→T1/T2、X1→T3/T4、X2→T5、X3→T6。**未覆盖项**：
   F1–F7 的 custom_ops 算子化（spec 标注"X2 期可选"）——按 YAGNI 不入
   本计划，需要时另开任务；walk-forward 训练（spec 未要求）同。
2. **占位符扫描**：无 TBD/TODO；T3 Step 3 的 main() 与 T4 Step 6 的
   main() 以"实现注意"给出完整逻辑描述与装载三行——执行者按注落地，
   属实现指引而非占位。
3. **类型一致性**：`ParquetData` 构造在 T1 为 `__init__(data)`、T2 改
   `data=None`（T2 Step 4 显式处理并回归 T1 测试）；`daily_factors.
   parquet` schema 在 T3 Interfaces 与 T4 Step 4 join 代码一致
   （date/concept/score/sync/capture/half_life）；`pred.parquet`
   schema 在 T5 Interfaces 与 T6 钩子 `tbl` 构造一致。
