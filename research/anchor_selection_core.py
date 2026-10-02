"""动态选锚实验支持代码；使用 conda resonance 环境，设计见同名实验文档。"""
from __future__ import annotations

from collections import Counter
from copy import copy
from types import SimpleNamespace

import numpy as np
import pandas as pd

from resonance.v3 import RANK_COLS, V3Backtester


def quality_scores(nav, active, lookback=60, min_active=10):
    """扣费区间收益减去同窗最大回撤幅度；不足观察天数返回缺失值。"""
    if not nav.index.equals(active.index) or not nav.columns.equals(active.columns):
        raise ValueError("nav and active must align")
    if not nav.index.is_monotonic_increasing or nav.index.has_duplicates:
        raise ValueError("dates must be strictly increasing")
    if lookback < 1 or min_active < 1 or min_active > lookback:
        raise ValueError("invalid observation window")
    if (nav <= 0).any().any():
        raise ValueError("nav must be positive")
    returns = nav / nav.shift(lookback) - 1
    drawdown = nav.rolling(lookback + 1).apply(
        lambda x: float(np.min(x / np.maximum.accumulate(x) - 1)), raw=True)
    observed = active.astype(float).rolling(lookback).sum() >= min_active
    return (returns + drawdown).where(observed)


def select_schedule(nav, active, start, lookback=60, penalty=0.0,
                    occupancy_window=60, min_active=10, margin=0.01,
                    threshold=0.4, scores=None):
    """周首个交易日收盘选锚；仅用当日及以前已实现的纸面表现。

    占用分母固定为 occupancy_window，开始前计空缺；当天选择不进入当天惩罚。
    正质量但扣分后为负的候选仍合格，惩罚不是资格闸门或硬占用上限。
    """
    if penalty < 0 or margin < 0 or occupancy_window < 1:
        raise ValueError("invalid selection controls")
    if scores is None:
        scores = quality_scores(nav, active, lookback, min_active)
    if not scores.index.equals(nav.index) or not scores.columns.equals(nav.columns):
        raise ValueError("scores must align with nav")
    cols = list(nav.columns)
    data = scores.to_numpy(dtype=float)
    result = []
    current = None
    previous_week = None
    history = []
    start = pd.Timestamp(start)
    for i, date in enumerate(nav.index):
        eligible = np.isfinite(data[i]) & (data[i] > 0)
        week = date.to_period("W-FRI")
        review = date >= start and week != previous_week
        counts = Counter(history[-occupancy_window:])
        penalties = np.array([penalty * max(counts[c] / occupancy_window - threshold, 0)
                              for c in cols])
        adjusted = np.where(eligible, data[i] - penalties, -np.inf)
        if review:
            previous_week = week
            if eligible.any():
                # 固定候选顺序破同分，不按事后表现调整顺序。
                winner = cols[int(np.argmax(adjusted))]
                if current not in cols or not eligible[cols.index(current)]:
                    current = winner
                elif adjusted[cols.index(winner)] > adjusted[cols.index(current)] + margin:
                    current = winner
            else:
                current = None
        anchor = current if date >= start else None
        result.append({"anchor": anchor, "review": review,
                       "eligible_count": int(eligible.sum()),
                       "quality": float(data[i, cols.index(anchor)]) if anchor else np.nan,
                       "penalty": float(penalties[cols.index(anchor)]) if anchor else 0.0})
        if date >= start:
            history.append(anchor)
    return pd.DataFrame(result, index=nav.index)


class RankingBank:
    """缓存原引擎各固定锚榜单，再将预定选锚序列交给同一个原引擎执行。

    不重写事件循环；原持仓、入场收盘止损基准、最短持有及成本全部由原引擎维护。
    """
    def __init__(self, engines: dict[str, V3Backtester]):
        if not engines:
            raise ValueError("empty engines")
        self.engines = engines
        self.codes = list(engines)
        self.template = next(iter(engines.values()))
        self.calendar = self.template.close.index
        self.empty = pd.DataFrame(columns=RANK_COLS)
        self.ranks = {}
        self.counts = {}
        for code, engine in engines.items():
            if not engine.close.index.equals(self.calendar) or engine.broad != [code]:
                raise ValueError("single-anchor engines must share calendar")
            ranks, counts = [], []
            for i, date in enumerate(self.calendar):
                stats = {}
                ranks.append(engine._final_ranking(i, date, stats))
                if stats.get("minute_layer_days") and not stats.get("minute_fallback_leader"):
                    leader_bars = engine.mbp.window_span(code, date, engine.p.minute_bars)
                    if len(leader_bars) and leader_bars.index[-1].normalize() != date:
                        stats["audit_stale_leader_window"] = 1
                    for concept in ranks[-1]["concept"]:
                        bars = engine.mbp.window_span(concept, date, engine.p.minute_bars)
                        if len(bars) and bars.index[-1].normalize() != date:
                            stats["audit_stale_concept_windows"] = stats.get(
                                "audit_stale_concept_windows", 0) + 1
                counts.append(stats)
            self.ranks[code] = ranks
            self.counts[code] = counts

    def run(self, schedule, start, end, cost):
        if not schedule.index.equals(self.calendar):
            raise ValueError("schedule must cover exact calendar")
        unknown = set(schedule.dropna()) - set(self.codes)
        if unknown:
            raise ValueError(f"unknown anchors: {unknown}")
        bt = copy(self.template)
        bt.p = bt.p.with_(cost_bp=float(cost))
        bt.broad = self.codes
        values = schedule.to_numpy(dtype=object)
        n = len(values)
        leader_idx = np.zeros(n, dtype=int)
        has_leader = np.zeros(n, dtype=bool)
        gate = np.zeros(n, dtype=bool)
        hl = np.full(n, 5.0)
        for i, code in enumerate(values):
            if pd.isna(code):
                continue
            source = self.engines[code].sig
            leader_idx[i] = self.codes.index(code)
            has_leader[i] = source.has_leader[i]
            gate[i] = source.gate[i]
            hl[i] = source.hl[i]
        bt.sig = SimpleNamespace(leader_idx=leader_idx, has_leader=has_leader,
                                 gate=gate, hl=hl)

        def final_ranking(i, date, stats):
            code = values[i]
            if pd.isna(code):
                stats["selection_no_anchor"] = stats.get("selection_no_anchor", 0) + 1
                return self.empty
            for key, count in self.counts[code][i].items():
                stats[key] = stats.get(key, 0) + count
            return self.ranks[code][i]

        bt._final_ranking = final_ranking
        return bt.run(start, end)


def occupancy_stats(schedule, window=60):
    """全部评估交易日为分母，空缺保留；另报有锚日的条件占用率。"""
    values = list(schedule)
    counts = schedule.value_counts()
    maximum = float(counts.max() / len(values)) if len(counts) else 0.0
    conditional = float(counts.max() / counts.sum()) if len(counts) else 0.0
    longest = run = switches = 0
    previous = None
    for value in values:
        value = None if pd.isna(value) else value
        if value is not None:
            run = run + 1 if value == previous else 1
            longest = max(longest, run)
        else:
            run = 0
        if value != previous:
            switches += 1
        previous = value
    maximum_rolling = 0.0
    for code in counts.index:
        rolling = schedule.eq(code).astype(float).rolling(window, min_periods=1).sum() / window
        maximum_rolling = max(maximum_rolling, float(rolling.max()))
    return {"max_occupancy": maximum, "max_conditional_occupancy": conditional,
            "max_rolling_occupancy": maximum_rolling,
            "longest_anchor_run": longest, "anchor_transitions": switches,
            "no_anchor_days": int(schedule.isna().sum())}
