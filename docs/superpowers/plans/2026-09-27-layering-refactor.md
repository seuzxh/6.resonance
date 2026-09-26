# 分层重构实施计划（FastAPI 同构 · 方案 B）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `resonance/` 单层包与三个厚 ops 入口重组为 specs/core/domain/dataio/services 五层 + 薄入口，行为逐位等价（OOS 冻结期不改任何参数语义）。

**Architecture:** specs.py（冻结工件，≈schemas，零依赖最内层）→ domain/（纯策略内核）→ dataio/（数据访问）→ services/（用例编排）→ ops/（薄入口组装根）。单向依赖，无环；依赖注入用构造函数传实例。

**Tech Stack:** Python 3.12（conda env `resonance`）、pandas/numpy/pyarrow、pytest（离线）、pip editable install（pyproject.toml 已就位，commit 516d569）。

**Spec:** `docs/superpowers/specs/2026-09-26-layering-refactor-design.md`（十节定稿，含 2026-09-27 复核修订×7）。**执行者须同时读 spec 与本计划。**

## Global Constraints（每个任务隐含遵守）

1. 只用 `conda run -n resonance python …`，禁止系统 Python（CLAUDE.md 硬约束 1）。
2. 所有测试离线可跑：`conda run -n resonance python -m pytest` 在**每个任务结束时必须全绿**。
3. **不改任何数值语义**：specs.py 常量与原 config.py / signal_daily FROZEN / backtest_v41 常量**字面一致**；代码迁移以「剪切-粘贴 + 改 import」为原则，禁止顺手重构逻辑。
4. 已退役指数 700050.TI / 932000.CSI 禁入新实验；`specs.assert_no_retired` 自检保留。
5. 凭证不入仓：refresh_token 仍走全局源/环境变量。
6. 禁止运行 `ops/signal_daily.py` 的 main()（盘中污染风险 + 网络写入）；一切基线/对拍走本计划给出的直调路径。
7. 保留工作区中无关的未提交改动；每个任务恰好一个 commit。
8. 遇到与计划不符的代码事实（如疑似死代码实有引用）：停下修正计划文件并说明，不静默变通。

## 基线事实（2026-09-27 已核）

- 主包 7 模块 1512 行；`v3.py` 635 行（V3Params/V3Signals/V3Backtester/MinuteBarProvider/评分内核/统计混居）。
- `backtest_v41.py:33` `from ops.backtest_v3 import evaluate, load_wide`——**backtest_v3.py 是坏入口但 load_wide 是活依赖**（evaluate 传 `minute_prices=` 必 TypeError；load_wide 被 backtest_v41 main 使用）。删除前必须先安置 load_wide。
- `backtest_v41.py` 顶部 `ANCHORS` 数值字典疑似未被 main 使用（main 内 anchor_rows 硬编码字面量）——T2 grep 核实，无引用则删不入 specs。
- `signal_daily.main()` 链式调用 `ops.publish_site.main`（发布层）；该链保持原样。
- 资金流钩子（entry_gate/exit_grid/post_rank）仅 tests/test_v3.py:453-526 引用。
- tests 五文件各有 `sys.path.insert` 引导（test_v3/test_core/test_minute/test_ifind_bars/test_signal_daily）。

---

### Task 1: 基线存档（零代码改动）

**Files:**
- Create: `outputs/refactor_baseline/`（git-ignored，本地保留至 T11 对拍完成）

**Interfaces:**
- Produces: `outputs/refactor_baseline/` 下四组产物，T11 的对拍基准。

- [ ] **Step 1: 确定基线日（= 缓存最新交易日）**

Run:
```bash
cd /home/zxh/projects/6.resonance && conda run -n resonance python -c "
import pandas as pd
b = pd.read_parquet('data/cache/daily_bars.parquet')
print(b['date'].max())"
```
记为 `<BASE_DATE>`（预期为最近一个已收盘交易日）。后续所有 `--date`/`end` 均用它。

- [ ] **Step 2: 存档 backtest_v41 产物**

Run:
```bash
mkdir -p outputs/refactor_baseline
conda run -n resonance python ops/backtest_v41.py
cp outputs/v4/nav_curves.csv outputs/v4/trades.csv outputs/v4/phase_table.csv outputs/refactor_baseline/
sha256sum outputs/refactor_baseline/*.csv | tee outputs/refactor_baseline/v41.sha256
```
Expected: 三 CSV 复制成功；打印的统计表与 spec §四锚点方向一致（不逐点对齐）。

- [ ] **Step 3: 存档六轨重放信号（离线直调，勿经 main）**

Run:
```bash
conda run -n resonance python - <<'EOF'
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from ops.signal_daily import TRACKS, replay_track
for track, pool in TRACKS.items():
    out = replay_track(track, pool, "BASE_DATE_PLACEHOLDER")
    print(track, len(out["trades"]), float(out["nav_curve"].iloc[-1]))
EOF
cp outputs/oos/trades_*.csv outputs/oos/nav_*.csv outputs/refactor_baseline/
sha256sum outputs/refactor_baseline/trades_*.csv outputs/refactor_baseline/nav_*.csv | tee outputs/refactor_baseline/oos.sha256
```
把 `BASE_DATE_PLACEHOLDER` 替换为 Step 1 的 `<BASE_DATE>`。
**关键检查**：运行输出中不得出现 `[minute] 自愈补采`（出现即缓存有缺口、走了网络——中止，先人工补缓存再重来基线）。

- [ ] **Step 4: 存档 publish 四 JSON（byte 级对拍基准）**

Run:
```bash
conda run -n resonance python ops/publish_site.py --dry-run
mkdir -p outputs/refactor_baseline/site && cp site/public/data/*.json outputs/refactor_baseline/site/
sha256sum outputs/refactor_baseline/site/*.json | tee outputs/refactor_baseline/site_json.sha256
```

- [ ] **Step 5: 全量测试基线**

Run: `conda run -n resonance python -m pytest`
Expected: `45 passed`（记录精确数字，T11 对比）。

- [ ] **Step 6: Commit（仅计划文件勾选状态，若使用；基线产物 git-ignored 不入库）**

```bash
git status --short   # 确认无源码改动
git commit --allow-empty -m "refactor T1: 基线存档（v41/oos 六轨/publish 四 JSON/pytest=45，本地 outputs/refactor_baseline/）"
```

---

### Task 2: specs.py + core/config.py 常量迁移（删旧 config.py）

**Files:**
- Create: `resonance/specs.py`、`resonance/core/__init__.py`、`resonance/core/config.py`
- Delete: `resonance/config.py`
- Modify: `resonance/v3.py`（V3Params 移出、import 改、broad_codes 默认删）、`ops/signal_daily.py`、`ops/backtest_v41.py`、`ops/backtest_v3.py`、`ops/collect.py`、`ops/collect_minute5.py`、`ops/probe.py`、`ops/validate_data.py`、`ops/publish_site.py`、`tests/`（全部 config 引用）
- Test: Create `tests/test_specs.py`

**Interfaces:**
- Consumes: 现有 config.py / signal_daily / backtest_v41 常量（字面搬运）。
- Produces（后续所有任务依赖）:
  - `resonance.specs`: `V3Params`（dataclass，字段与现 v3.py 完全一致含 `with_()`）、`BROAD_INDEX_POOL`、`V41_BROAD_POOL`、`V43_ANCHOR_POOL`、`RETIRED_NO_HF_CODES`、`assert_no_retired(codes, *, context="实验候选")`、`OOS_START`、`POOL13`、`TRACKS`、`NO_HF_COVER`、`FROZEN`、`V41_PHASES`、`V41_END`、`V41_STACK`、`DAILY_CTRL`
  - `resonance.core.config`: `PROJECT_ROOT`、`DATA_DIR`、`CACHE_DIR`、`OUTPUTS_DIR`、`IFIND_TOKEN_URL`、`IFIND_HISTORY_URL`、`IFIND_DATAPOOL_URL`、`IFIND_BASIC_URL`、`IFIND_HF_URL`、`IFIND_TOKEN_FILE`、`REFRESH_TOKEN_PATHS`、`CONCEPT_CODE_RANGE`、`COLLECT_START`、`HD_FIELD_MAP`、`MINUTE_INTERVAL`、`MINUTE_BARS_PER_DAY`

- [ ] **Step 1: grep 核实三个疑似无引用常量**

Run: `grep -rn "BENCHMARK_INDEXES\|BACKTEST_START\|SIGNAL_WINDOW" --include="*.py" ops/ tests/ research/ resonance/ | grep -v __pycache__`
Expected: 仅 config.py 自身与死代码链（metrics/minute/backtest.py、test_core）。若出现在活代码，停下改计划。无引用 → 不迁移（随死代码消亡）。

- [ ] **Step 2: 写失败测试 `tests/test_specs.py`**

```python
"""specs 冻结工件离线测试：参数类、池字面一致性、退役守卫。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from resonance import specs  # noqa: E402


def test_frozen_params_literal():
    p = specs.FROZEN
    assert (p.topk, p.daily_top, p.hl_source, p.cost_bp) == (3, 5, "leader", 10.0)


def test_tracks_and_pools_literal():
    assert list(specs.V43_ANCHOR_POOL) == ["399001.SZ", "399303.SZ", "000688.SH"]
    assert len(specs.V41_BROAD_POOL) == 9 and len(specs.POOL13) == 15
    assert set(specs.TRACKS) == {"D3", "A9", "B13", "C1", "G2", "K5"}
    assert specs.TRACKS["C1"] == ["399001.SZ"]


def test_assert_no_retired():
    with pytest.raises(ValueError, match="已退役"):
        specs.assert_no_retired(["700050.TI"])
    specs.assert_no_retired(["399001.SZ"])  # 不抛


def test_v3params_with():
    p = specs.V3Params().with_(topk=2)
    assert p.topk == 2 and specs.V3Params().topk == 3
```

- [ ] **Step 3: 跑测试确认失败**

Run: `conda run -n resonance python -m pytest tests/test_specs.py -v`
Expected: FAIL（`ModuleNotFoundError: resonance.specs` 或 ImportError）。

- [ ] **Step 4: 创建 `resonance/specs.py`（内容 = 拼装现有字面量，一字不改数值）**

```python
"""冻结规格工件（≈schemas：研究轨与运行轨的唯一接口，零依赖最内层）。

版本 = 本文件中的参数组合工件（git commit 即版本号，晋升门：整体替换不
原地修改）；叙述面在 docs/spec/v43-best-plan.md 与 docs/README.md。
搬运来源：config.py（2026-09-27）、ops/signal_daily.py、ops/backtest_v41.py。
"""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class V3Params:
    """V3 规格族参数模式（字段与语义见 domain/signals.py 模块 docstring）。"""

    leader_window: int = 10
    pos_window: int = 3
    res_window: int = 10
    dd_window: int = 10
    dd_tiers: tuple[float, float] = (0.02, 0.04)
    half_lives: tuple[int, int, int] = (5, 3, 2)
    topk: int = 3
    min_hold: int = 3
    stop_loss: float = 0.05
    cooldown: int = 1
    cost_bp: float = 0.0
    exec_lag: int = 1
    daily_top: int = 0
    minute_bars: int = 24
    hl_source: str = "allA"

    def with_(self, **kw) -> "V3Params":
        return replace(self, **kw)


# ---- 指数池（搬运自 config.py，注释随迁）----
BROAD_INDEX_POOL = {
    "883957.TI": "同花顺全A",
    "700050.TI": "微盘股",  # 已退役（RETIRED_NO_HF_CODES），仅历史复现
    "000680.SH": "科创综指",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
    "000016.SH": "上证50",
    "899050.BJ": "北证50",
    "932000.CSI": "中证2000",  # 已退役（RETIRED_NO_HF_CODES），仅历史复现
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
    "399303.SZ": "国证2000",
    "000015.SH": "红利指数",
}

V41_BROAD_POOL = {
    "883957.TI": "同花顺全A",
    "000001.SH": "上证指数",
    "883417.TI": "大盘股",
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
    "700050.TI": "微盘股",  # 已退役；A9 为预注册冻结轨例外，评价期后移除
}

V43_ANCHOR_POOL = {
    "399001.SZ": "深证成指",
    "399303.SZ": "国证2000",
    "000688.SH": "科创50",
}

# ---- 已退役指数（搬运注释全文）----
RETIRED_NO_HF_CODES = frozenset({"700050.TI", "932000.CSI"})


def assert_no_retired(codes, *, context: str = "实验候选") -> None:
    """新实验入口断言：候选不得包含已退役的 HF 无覆盖指数。"""
    bad = RETIRED_NO_HF_CODES.intersection(codes)
    if bad:
        raise ValueError(
            f"{context} 含已退役指数 {sorted(bad)}：HF 永久无 5min 数据，"
            "2026-09-25 用户指令禁用（specs.RETIRED_NO_HF_CODES）"
        )


# ---- OOS 四轨 + 展示对照轨（搬运自 ops/signal_daily.py，注释随迁）----
OOS_START = "2026-09-22"
POOL13 = ["883957.TI", "700050.TI", "000680.SH", "399006.SZ", "000688.SH", "000016.SH",
          "899050.BJ", "932000.CSI", "000300.SH", "000905.SH", "000852.SH", "399303.SZ",
          "000015.SH", "000001.SH", "399001.SZ"]
TRACKS = {"D3": list(V43_ANCHOR_POOL),
          "A9": list(V41_BROAD_POOL), "B13": POOL13, "C1": ["399001.SZ"],
          "G2": ["399303.SZ"], "K5": ["000688.SH"]}
NO_HF_COVER = {"700050.TI", "932000.CSI"}
FROZEN = V3Params(topk=3, daily_top=5, hl_source="leader", cost_bp=10.0)

# ---- V4.1 复现规格（搬运自 ops/backtest_v41.py）----
V41_PHASES = ["2025-09-22", "2025-09-23", "2025-09-24", "2025-09-25", "2025-09-26"]
V41_END = "2026-09-18"
V41_STACK = V3Params(topk=3, daily_top=5, hl_source="leader")
DAILY_CTRL = V3Params(topk=3, hl_source="leader")
```
注：config.py 原注释块（池/退役的历史依据）在搬运时**全文随迁**，上面为省篇幅缩写；执行者以 config.py 原注释为准。

- [ ] **Step 5: 创建 `resonance/core/config.py`（基础设施 = 原 config.py 删除池/冻结/GPT 参数后的余量）**

```python
"""基础设施配置：端点、凭证路径、目录、采集参数、分钟常量（≈FastAPI core/config）。"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

IFIND_TOKEN_URL = "https://quantapi.51ifind.com/api/v1/get_access_token"
IFIND_HISTORY_URL = "https://quantapi.51ifind.com/api/v1/history_data"
IFIND_DATAPOOL_URL = "https://quantapi.51ifind.com/api/v1/data_pool"
IFIND_BASIC_URL = "https://quantapi.51ifind.com/api/v1/basic_data_service"
IFIND_HF_URL = "https://ft.10jqka.com.cn/api/v1/high_frequency"
IFIND_TOKEN_FILE = Path("/home/zxh/qlib_data/.ifind_token")
REFRESH_TOKEN_PATHS = (
    Path("/home/zxh/qlib_data/scripts/verify_data.py"),
    Path("/home/zxh/qlib_data/scripts/qlib_dumper/instrument_source.py"),
    Path("/home/zxh/qlib_data/scripts/daily_update.py"),
    Path.home() / ".bashrc",
)

CONCEPT_CODE_RANGE = tuple(range(885001, 887000))
COLLECT_START = "2024-10-01"
HD_FIELD_MAP = {
    "pre_close": "pre_close", "open": "open", "high": "high", "low": "low",
    "close": "close", "pct_chg": "pct_chg", "volume": "volume",
    "amt": "amount", "turn": "turnover_ratio",
}

# 分钟采集常量（原 resonance/minute.py，collect_minute5 在用）
MINUTE_INTERVAL = "5"
MINUTE_BARS_PER_DAY = 48
```
并创建空 `resonance/core/__init__.py`。

- [ ] **Step 6: 改 `resonance/v3.py`**

1. 删除 `V3Params` 类定义（45-71 行），改 `from .specs import V3Params`（`from . import config` 一并删除——引擎默认池随下一步消失）。
2. `V3Backtester.__init__` 签名：`broad_codes: list[str] | None = None` 改为 `broad_codes: list[str]`（必填，位置不变），删除函数体内 `list(config.BROAD_INDEX_POOL)` 默认与 missing 警告里的 config 引用（警告逻辑保留，只改 import 源）。
3. 全文件 grep `config.` 确认零残留。

- [ ] **Step 7: 批量改 import（全部消费方）**

| 文件 | 旧 | 新 |
|---|---|---|
| ops/signal_daily.py | `from resonance import config` / `from resonance.v3 import …, V3Params, …` | `from resonance import specs`（TRACKS/FROZEN/OOS_START/POOL13/NO_HF_COVER 改从 specs 引用，本地定义删除）+ `from resonance.core import config`（CACHE_DIR 等）+ `from resonance.specs import V3Params` |
| ops/backtest_v41.py | 同上模式 | `from resonance import specs` + `from resonance.core import config`；**第三节 `V3Backtester(close_all, concepts, params=p.with_(cost_bp=10.0))` 补 `broad_codes=list(specs.BROAD_INDEX_POOL)`** |
| ops/backtest_v3.py | `from resonance import config` | `from resonance.core import config`（本文件 T3 将删，此处最小改） |
| ops/collect.py / collect_minute5.py / probe.py / validate_data.py / publish_site.py | `from resonance import config` | `from resonance.core import config`；publish_site 的 `ANCHORS = config.V43_ANCHOR_POOL` 改 `ANCHORS = specs.V43_ANCHOR_POOL`（加 `from resonance import specs`）；collect_minute5 若从 `resonance.minute` 拿常量改从 `resonance.core.config` 拿 |
| resonance/ifind.py | `from . import config`（若有） | `from .core import config` |
| tests/test_core.py、test_ifind_bars.py、test_minute.py、test_signal_daily.py、test_v3.py | config/v3 引用 | 同模式替换 |

- [ ] **Step 8: 删除 `resonance/config.py`，全量 grep 无残留**

Run: `grep -rn "from resonance import config\|from resonance.config\|resonance\.config" --include="*.py" . | grep -v __pycache__`
Expected: 零输出。

- [ ] **Step 9: 全量测试**

Run: `conda run -n resonance python -m pytest`
Expected: 全绿（46 个：45 + test_specs 新增；钩子用例仍在，T3 才删）。

- [ ] **Step 10: Commit**

```bash
git add -A && git commit -m "refactor T2: specs.py+core/config 常量迁移，删旧 config；V3Params 归位契约层；引擎 broad_codes 改必填（backtest_v41 三节补显式 13 池）"
```

---

### Task 3: 死代码删除 + dataio/cache.py 诞生

**Files:**
- Create: `resonance/dataio/__init__.py`、`resonance/dataio/cache.py`
- Delete: `resonance/metrics.py`、`resonance/minute.py`、`ops/backtest_v3.py`、tests/test_core.py 中 metrics/RotationBacktester 用例、tests/test_minute.py、test_v3.py:453-526 钩子用例
- Modify: `resonance/v3.py`（删钩子三参数 + flow_exit 分支）、`resonance/backtest.py`（删 RotationBacktester 与 np_sqrt，perf_stats 改 `import numpy as np` + `np.sqrt`，暂留原文件至 T6）、`ops/backtest_v41.py`（load_wide 改源）、tests/test_v3.py 引用清理

**Interfaces:**
- Consumes: T2 的 core.config。
- Produces: `resonance.dataio.cache.load_wide() -> tuple[pd.DataFrame, list[str]]`（与原 backtest_v3.load_wide 逐字节同实现）、`append_rows(path, new: pd.DataFrame, subset: list[str]) -> None`（keep="last" 幂等落盘，供 T8 services/daily 使用）。

- [ ] **Step 1: grep 核实死代码零活引用**

Run: `grep -rn "from resonance.metrics\|resonance\.metrics\|make_minute_rank_fn\|minute_resonance_score\|minute_returns\|RotationBacktester\|from ops.backtest_v3\|evaluate(" --include="*.py" ops/ tests/ research/ resonance/ | grep -v __pycache__ | grep -v "resonance/minute.py\|resonance/metrics.py\|resonance/backtest.py\|ops/backtest_v3.py\|test_core\|test_minute"`
Expected: 仅 `ops/backtest_v41.py:33`（load_wide，本任务安置）。`MINUTE_LEGS_PER_DAY/MINUTE_WINDOW_DAYS/MINUTE_WINDOW_BARS/POOL_SIZE` 若仅 minute.py 内部使用则不迁（Step 2 已只迁 collect_minute5 实际引用者，T2 已核实）。

- [ ] **Step 2: 写失败测试（load_wide 契约：合成 parquet → 宽表+概念交集）**

在新建 `tests/test_dataio_cache.py`：

```python
"""dataio/cache 离线测试：load_wide 宽表化与 append_rows 幂等。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from resonance.core import config  # noqa: E402
from resonance.dataio import cache  # noqa: E402


def test_load_wide_pivot_and_intersect(tmp_path, monkeypatch):
    bars = pd.DataFrame({
        "symbol": ["885001.TI", "885001.TI", "000300.SH", "000300.SH"],
        "date": ["2025-01-02", "2025-01-03"] * 2,
        "close": [100.0, 101.0, 200.0, 202.0],
    })
    bars.to_parquet(tmp_path / "daily_bars.parquet", index=False)
    pd.DataFrame({"code": ["885001.TI", "999999.TI"], "name": ["a", "b"]}
                 ).to_csv(tmp_path / "concept_catalog.csv", index=False)
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    close, concepts = cache.load_wide()
    assert concepts == ["885001.TI"]
    assert close.shape == (2, 2) and close.index.dtype.kind == "M"


def test_append_rows_keep_last_idempotent(tmp_path):
    f = tmp_path / "t.parquet"
    base = pd.DataFrame({"symbol": ["A"], "datetime": ["d1"], "close": [1.0]})
    cache.append_rows(f, base, subset=["symbol", "datetime"])
    cache.append_rows(f, pd.DataFrame({"symbol": ["A"], "datetime": ["d1"], "close": [9.0]}),
                      subset=["symbol", "datetime"])
    out = pd.read_parquet(f)
    assert len(out) == 1 and out["close"].iloc[0] == 9.0
```
（测试里那行 `(tmp_path / ...).to_parquet if False else None` 是占位废行——执行时删除。）

- [ ] **Step 3: 跑测试确认失败**

Run: `conda run -n resonance python -m pytest tests/test_dataio_cache.py -v`
Expected: FAIL（`resonance.dataio` 不存在）。

- [ ] **Step 4: 创建 `resonance/dataio/cache.py`（load_wide 逐字节搬运 backtest_v3.py:48-53；append_rows 提炼自 signal_daily 的 keep="last" 写回）**

```python
"""本地缓存读写（≈repositories 本地实现）：parquet 长表 ↔ 宽表。

只存原始宽表；「拼接」不在此层（展示口径在 services/publish，bar 窗
拼接在 domain/minute）。load_wide 搬运自 ops/backtest_v3.py（2026-09-27）。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from resonance.core import config


def load_wide() -> tuple[pd.DataFrame, list[str]]:
    """日线长表 → (收盘宽表 index=Datetime, 概念码交集列表)。"""
    bars = pd.read_parquet(config.CACHE_DIR / "daily_bars.parquet")
    catalog = pd.read_csv(config.DATA_DIR / "concept_catalog.csv")
    concepts = [c for c in catalog["code"] if c in set(bars["symbol"])]
    close_all = bars.pivot(index="date", columns="symbol", values="close").sort_index()
    close_all.index = pd.to_datetime(close_all.index)
    return close_all, concepts


def load_minute_wide() -> pd.DataFrame:
    """5min bar 长表 → 收盘宽表（index=datetime，bar 结束时刻）。"""
    m5 = pd.read_parquet(config.CACHE_DIR / "minute5_bars.parquet")
    m5["datetime"] = pd.to_datetime(m5["datetime"])
    return m5.pivot(index="datetime", columns="symbol", values="close").sort_index()


def append_rows(path: Path, new: pd.DataFrame, subset: list[str]) -> int:
    """keep='last' 幂等落盘：新行覆盖旧键行（2026-09-23 事故教训口径）。返回新增行数。"""
    if new is None or len(new) == 0:
        return 0
    if path.exists():
        old = pd.read_parquet(path)
        out = pd.concat([old, new], ignore_index=True)
    else:
        out = new.copy()
    out = out.drop_duplicates(subset=subset, keep="last").sort_values(subset).reset_index(drop=True)
    out.to_parquet(path, index=False)
    return len(new)
```
注意：`append_rows` 的键序 `sort_values(subset)` 与 signal_daily 原实现 `sort_values(["symbol","date"])` / `["symbol","datetime"]` 一致（调用方传对应 subset）。创建空 `resonance/dataio/__init__.py`。

- [ ] **Step 5: 死代码物理删除**

1. `git rm resonance/metrics.py resonance/minute.py ops/backtest_v3.py`
2. `ops/backtest_v41.py:33` 改 `from resonance.dataio.cache import load_wide`，并删除未用的 `evaluate` 导入。
3. `resonance/v3.py` 删 `entry_gate`/`exit_grid`/`post_rank` 三参数及 `self.exit_grid/self.post_rank/self._gate_idx/self._gate_blocked` 相关状态、run() 内 `flow_exit` 分支与 `gate_missing_days/entry_blocked_days` 计数、`flow_exits` 统计（stats dict 里对应键删除；打印方 ops 里若引用这些键需同步——grep `entry_blocked_days\|gate_missing_days\|flow_exits` 全仓，仅测试与 v3.py 应命中）。
4. `resonance/backtest.py` 删 `RotationBacktester` 与 `np_sqrt`；`perf_stats` 内 `np_sqrt(244)` 改 `(rets.std() * (244 ** 0.5))` 形式或 `import numpy as np` + `np.sqrt`。
5. 删除 tests/test_core.py 中 metrics/RotationBacktester 用例（保留 perf_stats 用例）、删除 tests/test_minute.py、删除 test_v3.py:453-526。

- [ ] **Step 6: 全量测试**

Run: `conda run -n resonance python -m pytest`
Expected: 全绿；总数较 T1 基线下降（死代码+钩子用例移除，新增 dataio 2 例）——记录精确数字。

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "refactor T3: 死代码链退场（metrics/旧minute/RotationBacktester/资金流钩子/backtest_v3）；dataio/cache.py 诞生（load_wide 落户+append_rows 幂等落盘）"
```

---

### Task 4: 拆分 v3.py → domain/

**Files:**
- Create: `resonance/domain/__init__.py`、`resonance/domain/resonance.py`、`resonance/domain/signals.py`、`resonance/domain/minute.py`、`resonance/domain/engine.py`
- Delete: `resonance/v3.py`
- Modify: `ops/backtest_v41.py`、`ops/signal_daily.py`、`ops/publish_site.py`、tests（test_v3.py → 按域拆分）
- Test: `tests/test_domain_resonance.py`、`tests/test_domain_signals.py`、`tests/test_domain_minute.py`、`tests/test_domain_engine.py`（由 test_v3.py 原文搬运，只改 import）

**Interfaces:**
- Consumes: `resonance.specs.V3Params`。
- Produces:
  - `domain/resonance.py`: `RANK_COLS`、`up_resonance_scores_np(y: np.ndarray, X: np.ndarray, half_life: float) -> pd.DataFrame`、`minute_up_resonance(leader_bars: pd.Series, concept_bars: pd.DataFrame) -> pd.DataFrame`
  - `domain/signals.py`: `compound_window`、`dynamic_half_life`、`v3_ranking`、`class V3Signals`（模块 docstring = 原 v3.py 信号层说明全文）
  - `domain/minute.py`: `class MinuteBarProvider`
  - `domain/engine.py`: `class V3Backtester`（broad_codes 必填，T2 已改）

- [ ] **Step 1: 建包与搬运（纯剪切-粘贴，docstring 随函数走）**

- `RANK_COLS` + `up_resonance_scores_np` + `minute_up_resonance` → `domain/resonance.py`（import：numpy/pandas）
- `compound_window` + `dynamic_half_life` + `v3_ranking` + `V3Signals` → `domain/signals.py`（头部放原 v3.py 模块 docstring 的信号层 §1-4 与口径注部分；`from ..specs import V3Params`、`from .resonance import RANK_COLS, up_resonance_scores_np`）
- `MinuteBarProvider` → `domain/minute.py`
- `V3Backtester` + 原 docstring 的执行层 §6/§7 部分 → `domain/engine.py`（`from ..specs import V3Params`、`from .signals import V3Signals`、`from .minute import MinuteBarProvider` 仅供类型、`from .resonance import minute_up_resonance`——engine._final_ranking 用到）
- `resonance/domain/__init__.py` 置空。
- 删除 `resonance/v3.py`。

- [ ] **Step 2: 消费方 import 改写**

| 旧 import | 新 import |
|---|---|
| `from resonance.v3 import MinuteBarProvider, V3Backtester, V3Signals` | `from resonance.domain.minute import MinuteBarProvider` 等，按名拆开 |
| `from resonance.v3 import V3Params` | `from resonance.specs import V3Params`（T2 后本就应已如此） |

涉及：ops/backtest_v41.py、ops/signal_daily.py、ops/publish_site.py（replay_display 内 173 行延迟 import）、tests。

- [ ] **Step 3: 测试拆分搬运**

test_v3.py 现有用例按被测对象归入四个新文件（用例体一字不改，只改顶部 import 与模块 docstring）；删除 test_v3.py。

- [ ] **Step 4: 全量测试 + 残留 grep**

Run: `conda run -n resonance python -m pytest && grep -rn "resonance.v3\|from resonance import v3" --include="*.py" . | grep -v __pycache__`
Expected: 全绿；grep 零输出。

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "refactor T4: v3.py 巨石拆分 domain/{resonance,signals,minute,engine}；测试按域重排"
```

---

### Task 5: dataio 完善（ifind 迁入 + outputs 读侧）

**Files:**
- Create: `resonance/dataio/outputs.py`
- Modify: `resonance/ifind.py` → 移动为 `resonance/dataio/ifind.py`；`ops/collect.py`、`ops/collect_minute5.py`、`ops/probe.py`、`ops/signal_daily.py`、`tests/test_ifind_bars.py`

**Interfaces:**
- Consumes: core.config。
- Produces:
  - `dataio/ifind.py`：原 ifind.py 全部公开名不变（`get_access_token`、`fetch_history_data`、`parse_history_data`、`fetch_minute_bars`、`fetch_minute_close`、`fetch_index_names`、`load_refresh_token`、`IfindError`）
  - `dataio/outputs.py`：`OUT_DIR = config.OUTPUTS_DIR / "oos"`、`read_nav(track: str) -> pd.Series`、`read_trades(track: str) -> pd.DataFrame`

- [ ] **Step 1: 写失败测试 `tests/test_dataio_outputs.py`**

```python
"""dataio/outputs 离线测试：oos 产物读取。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from resonance.core import config  # noqa: E402
from resonance.dataio import outputs  # noqa: E402


def test_read_nav_and_trades(tmp_path, monkeypatch):
    monkeypatch.setattr(outputs, "OUT_DIR", tmp_path)
    nav = pd.Series([1.0, 1.01], index=pd.to_datetime(["2026-09-22", "2026-09-23"]), name="nav")
    nav.index.name = "date"
    nav.to_csv(tmp_path / "nav_D3.csv")
    pd.DataFrame({"date": ["2026-09-22"], "type": ["entry"], "from": [None], "to": ["885001.TI"],
                  "price": [100.0], "nav": [1.0]}).to_csv(tmp_path / "trades_D3.csv", index=False)
    s = outputs.read_nav("D3")
    t = outputs.read_trades("D3")
    assert float(s.iloc[-1]) == 1.01 and t["to"].iloc[0] == "885001.TI"
```
Run: `conda run -n resonance python -m pytest tests/test_dataio_outputs.py -v` → FAIL。

- [ ] **Step 2: 移动 ifind 并创建 outputs.py**

1. `git mv resonance/ifind.py resonance/dataio/ifind.py`，内部 `from . import config`（若有）→ `from ..core import config`。
2. `resonance/dataio/outputs.py`：

```python
"""运行产物读侧（outputs/oos）：runner/发布层/校验共用。"""
from __future__ import annotations

import pandas as pd

from resonance.core import config

OUT_DIR = config.OUTPUTS_DIR / "oos"


def read_nav(track: str) -> pd.Series:
    s = pd.read_csv(OUT_DIR / f"nav_{track}.csv", index_col=0).iloc[:, 0]
    s.index = pd.to_datetime(s.index)
    return s


def read_trades(track: str) -> pd.DataFrame:
    return pd.read_csv(OUT_DIR / f"trades_{track}.csv")
```

3. 消费方 `from resonance.ifind import …` → `from resonance.dataio.ifind import …`（ops/collect.py、collect_minute5.py、probe.py、signal_daily.py、tests/test_ifind_bars.py）。

- [ ] **Step 3: 全量测试 + Commit**

Run: `conda run -n resonance python -m pytest` → 全绿。
```bash
git add -A && git commit -m "refactor T5: ifind 归位 dataio；outputs 读侧落成（read_nav/read_trades）"
```

---

### Task 6: services/performance.py（旧 backtest.py 终删）

**Files:**
- Create: `resonance/services/__init__.py`、`resonance/services/performance.py`
- Delete: `resonance/backtest.py`
- Modify: `ops/signal_daily.py`、`ops/backtest_v41.py`、`ops/publish_site.py`、tests（原 perf_stats 用例迁 `tests/test_services_performance.py`）

**Interfaces:**
- Produces: `perf_stats(nav_curve: pd.Series, benchmark_curve: pd.Series | None = None) -> dict`、`yearly_returns(nav: pd.Series) -> dict[str, float]`（两者实现逐字节搬运，来源 backtest.py 与 v3.py——v3.py 已于 T4 拆分，从 domain/engine 同文件内 yearly_returns 搬走）。

- [ ] **Step 1: 测试先行**：把 test_core.py 中 perf_stats 用例与（若有）yearly_returns 用例迁入 `tests/test_services_performance.py`，import 改 `from resonance.services.performance import perf_stats, yearly_returns`。Run → FAIL。
- [ ] **Step 2: 创建 services/performance.py**（函数体逐字节搬运；`np_sqrt` 不复活，直接 `import numpy as np`）。domain/engine.py 中删除 `yearly_returns`。删除 `resonance/backtest.py`。
- [ ] **Step 3: 消费方改 import**（signal_daily.main 内延迟 import、backtest_v41 两处、publish_site:217 延迟 import）。
- [ ] **Step 4: 全量测试 + Commit**

```bash
git add -A && git commit -m "refactor T6: perf_stats/yearly_returns 归位 services/performance；旧 backtest.py 终删"
```

---

### Task 7: services/backtest.py（backtest_v41 瘦身）

**Files:**
- Create: `resonance/services/backtest.py`
- Modify: `ops/backtest_v41.py`

**Interfaces:**
- Consumes: `specs.V41_STACK/DAILY_CTRL/V41_PHASES/V41_END/V3Params/BROAD_INDEX_POOL`、`domain.{engine,minute}`、`dataio.cache.{load_wide,load_minute_wide}`、`services.performance.perf_stats`。
- Produces: `build_provider() -> MinuteBarProvider`、`run_stack(close_all, concepts, p: V3Params, prov, start: str, cost: float) -> dict`、`stats_row(tag: str, out: dict) -> dict`（三者实现逐字节搬运自 ops/backtest_v41.py:53-87；build_provider 内 parquet 加载改用 `cache.load_minute_wide()`）。

- [ ] **Step 1: 搬运三函数**至 `resonance/services/backtest.py`（含 numpy import；`OUT_DIR` 留在入口）。main 中调用处改 `from resonance.services import backtest as bt_service`。
- [ ] **Step 2: 入口瘦身检查**：backtest_v41.py 只剩 docstring、OUT_DIR、specs import、main（含锚点对照打印与落盘）。
- [ ] **Step 3: 全量测试 + Commit**

```bash
git add -A && git commit -m "refactor T7: 回测复现用例归位 services/backtest；backtest_v41 入口瘦身"
```

---

### Task 8: services/daily.py（signal_daily 瘦身）

**Files:**
- Create: `resonance/services/daily.py`
- Modify: `ops/signal_daily.py`、`tests/test_signal_daily.py`（若 mock 的是模块内函数，改 mock services.daily）

**Interfaces:**
- Consumes: `specs.{TRACKS,FROZEN,OOS_START,NO_HF_COVER,V3Params}`、`dataio.ifind.{fetch_history_data,fetch_minute_bars}`、`dataio.cache.{load_wide,load_minute_wide,append_rows}`、`domain.{signals,engine,minute}`、`services.performance.perf_stats`。
- Produces: `update_daily(end_date: str) -> None`、`topup_minute_for_window(close_all, concepts, pool, end_date) -> int`、`replay_track(track: str, pool: list[str], end_date: str) -> dict`（逐字节搬运，仅两处机械替换：①parquet 写回改 `cache.append_rows`（键 subset=["symbol","date"] / ["symbol","datetime"]，行为等价——keep-last+排序相同）；②`load_wide()`/minute pivot 改调 dataio）。

- [ ] **Step 1: 搬运**（原 signal_daily.py:55-185 三个函数 + OUT_DIR 改从 `dataio.outputs.OUT_DIR` 引用或自算 `config.OUTPUTS_DIR/"oos"`——与 publish 读侧同一常量，必须同源）。
- [ ] **Step 2: 入口瘦身**：ops/signal_daily.py 只剩 docstring、argparse、15:05 硬闸、`update_daily` + 循环 `replay_track` + 打印 + publish 链（函数体从 services 调）。
- [ ] **Step 3: 测试**：tests/test_signal_daily.py 原用例改 import（`from resonance.services import daily`）；若其 monkeypatch `ops.signal_daily.fetch_history_data` 需改 patch `services.daily.fetch_history_data`。全量跑绿。
- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "refactor T8: OOS 每日用例归位 services/daily；signal_daily 入口只剩闸+组装+打印+发布链"
```

---

### Task 9: services/publish.py（publish_site 瘦身）

**Files:**
- Create: `resonance/services/publish.py`
- Modify: `ops/publish_site.py`

**Interfaces:**
- Consumes: `specs.{TRACKS,V43_ANCHOR_POOL,FROZEN}`、`dataio.outputs.{read_nav,read_trades,OUT_DIR}`、`dataio.cache.load_wide`、`domain.{engine,minute}`、`services.performance.perf_stats`、`core.config.{DATA_DIR,OUTPUTS_DIR}`。
- Produces: 原 publish_site.py:47-354 的全部函数逐字节搬运（`_names/_closes/_nav/_anchors_at/_trades/validate/_stitched/_trades_stitched/build_recent/replay_display/_holdings_from_trades/build_nav/build_signals/build_archive`）+ 展示常量（`PUBLISH_LAG/NAV_START/SHOW_TRACKS/NAV_TRACKS/TRACK_NAMES/ANCHORS/SPARK_DAYS/ACT/WD`）。`_nav/_trades` 内部改调 `dataio.outputs.read_nav/read_trades`（等价：原实现即读同路径 CSV）。`replay_display` 内延迟 import 改 `from resonance.domain.minute import MinuteBarProvider` + `from resonance.domain.engine import V3Backtester`；`import ops.signal_daily as sd` → `from resonance import specs as sd`（sd.TRACKS 同键值）。
- 入口保留：argparse/dry-run、JSON 写出 `SITE_DATA`、git add/commit/push。

- [ ] **Step 1: 搬运 + 入口瘦身**（main 留在 ops/publish_site.py，函数调用改为 `from resonance.services import publish as svc`）。
- [ ] **Step 2: dry-run 自证**（不入基线，仅 smoke）：

Run: `conda run -n resonance python ops/publish_site.py --dry-run`
Expected: 正常写出四 JSON；**与 T1 基线 sha256 一致**（此时数据未变——提前等于把 T11 的 publish 对拍做了一次）。

- [ ] **Step 3: 全量测试 + Commit**

```bash
git add site/public/data/*.json   # dry-run 数据若无变化则跳过
git add -A && git commit -m "refactor T9: 站点构建用例归位 services/publish；publish_site 入口只剩参数化+落盘+git"
```

---

### Task 10: editable 安装 + sys.path 引导清除

**Files:**
- Modify: ops×7（signal_daily/backtest_v41/collect/collect_minute5/probe/validate_data/publish_site）、tests 全部、`resonance/__init__.py`

- [ ] **Step 1: 安装**：`conda run -n resonance pip install -e .` → `pip show resonance` 确认。
- [ ] **Step 2: 删除全部 `sys.path.insert` 引导行与对应的 `# noqa: E402`**（grep `sys.path.insert` 全仓清零；`from pathlib import Path` 若因此闲置一并删）。
- [ ] **Step 3: `resonance/__init__.py` docstring 更新**：

```python
"""共振：指数—概念上涨共振策略（V4.3 三锚动选）研究与样本外验证。

分层（docs/superpowers/specs/2026-09-26-layering-refactor-design.md）：
specs=冻结工件 | domain=纯策略内核 | dataio=数据访问 | services=用例编排 | ops=入口。
"""
__version__ = "0.1.0"
```

- [ ] **Step 4: 全量测试 + 入口冒烟**（不触网）：`conda run -n resonance python -m pytest` 全绿；`conda run -n resonance python ops/publish_site.py --dry-run` 正常。
- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "refactor T10: pip install -e . 生效；全仓 sys.path 引导与 E402 退役；包 docstring 对齐分层"
```

---

### Task 11: 等价性对拍（最终验收）

**Files:** 无代码改动（失败则回到对应任务修复后重跑）。

- [ ] **Step 1: 重跑 v41**：`conda run -n resonance python ops/backtest_v41.py` → `sha256sum` 比对 `outputs/refactor_baseline/v41.sha256`（浮点落盘应逐字节一致；若 CSV 含时间戳列导致差异，改用数值对拍脚本逐列 `pd.testing.assert_frame_equal`）。
- [ ] **Step 2: 重跑六轨重放**（同 T1 Step 3 的直调脚本，end=同一 `<BASE_DATE>`）→ 六对 `trades_*.csv/nav_*.csv` 与基线 sha256 比对。
- [ ] **Step 3: publish 对拍**：`ops/publish_site.py --dry-run` → 四 JSON 与 `outputs/refactor_baseline/site_json.sha256` **byte 级**一致。
- [ ] **Step 4: pytest 总数核对**：与 T3 Step 6 记录一致。
- [ ] **Step 5: 通过后 Commit（空提交留痕）**

```bash
git commit --allow-empty -m "refactor T11: 等价性验收通过（v41 sha256 + 六轨 sha256 + publish JSON byte 级一致）"
```

---

### Task 12: 文档同步

**Files:**
- Modify: `CLAUDE.md`、`README.md:10`、`docs/README.md:6-7`、`docs/spec/v43-best-plan.md:5,47`、`docs/ops/oos-validation-design.md:16`

- [ ] **Step 1: 六处 `config.V43_ANCHOR_POOL`/FROZEN 旧位置引用**改指 `resonance/specs.py`（v43-best-plan 仅改交叉引用路径）。
- [ ] **Step 2: CLAUDE.md**：「关键参数位置」节改 `resonance/specs.py`；pytest 计数改 T11 核对值；「常用验证与入口」节加 `pip install -e .`（新环境一步就绪）。
- [ ] **Step 3: docs/README.md** 补映射表一行：代码五层（specs/core/domain/dataio/services）↔ 文档四分（spec/ops/research/data）对应关系。
- [ ] **Step 4: 全量测试 + Commit**

```bash
git add -A && git commit -m "refactor T12: 文档同步——工件位置引用改 specs.py、CLAUDE.md 参数位置/测试计数更新、docs README 补分层映射表"
```

---

## 自审记录（2026-09-27）

1. **Spec 覆盖**：§三目录（T2-T9 逐层落成）、§四迁移表（每行有任务：V3Params→T2、评分内核/信号/minute/engine→T4、统计→T6、ifind→T5、load_wide→T3/T5、daily→T8、backtest→T7、publish→T9、config 拆分→T2、死代码→T3、backtest_v3→T3、文档行→T12、`__init__`→T10）、§五数据流（T8 后成形）、§六 web 层（T9）、§七验收（T1+T11）、§八测试与文档（T3/T4/T10/T12）、§九决策（T2 broad_codes、类名保留）、§十（research/ 零改动，qlib_smoke 不涉）。无缺口。
2. **占位符**：T1 Step 3 的 `BASE_DATE_PLACEHOLDER` 是运行期替换指令非占位。无 TBD/TODO。
3. **类型一致性**：`load_wide() -> tuple[pd.DataFrame, list[str]]` 在 T3 定义、T7/T8/T9 消费一致；`append_rows(path, new, subset) -> int` T3 定义 T8 消费；`read_nav/read_trades` T5 定义 T9 消费；`specs` 常量清单 T2 Interfaces 与 T7/T8/T9 引用一致；`OUT_DIR` 双源问题已消（T8 指定与 dataio.outputs 同源）。
