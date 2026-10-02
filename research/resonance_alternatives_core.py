"""替代机制研究规则；conda resonance；设计见同名预注册。"""
from collections import Counter
from copy import copy
from types import SimpleNamespace

import numpy as np
import pandas as pd

from resonance.v3 import RANK_COLS, V3Backtester, minute_up_resonance


def raw_momentum(close):
    return (close / close.shift(10) - 1).where(close.rolling(11).count() == 11)


def adjusted_momentum(close, window):
    returns = close.pct_change(fill_method=None)
    volatility = returns.rolling(window, min_periods=window).std(ddof=1).clip(lower=.0001)
    return raw_momentum(close) / volatility


def best_schedule(scores):
    values = scores.to_numpy(float)
    values = np.where(np.isfinite(values), values, -np.inf)
    return pd.Series([scores.columns[row.argmax()] if np.isfinite(row).any() else None
                      for row in values], index=scores.index, dtype=object)


def capped_schedule(scores, cap):
    if not 0 < cap <= 1:
        raise ValueError("cap must be in (0,1]")
    quota = int(np.floor(cap * 60 + 1e-12))
    history = []
    for row in scores.to_numpy(float):
        counts = Counter(history[-59:])
        eligible = [j for j, c in enumerate(scores.columns)
                    if np.isfinite(row[j]) and counts[c] < quota]
        chosen = max(eligible, key=lambda j: row[j]) if eligible else None
        history.append(scores.columns[chosen] if chosen is not None else None)
    return pd.Series(history, index=scores.index, dtype=object)


def mean_ranking(rankings):
    if not rankings or any(r.empty for r in rankings):
        return pd.DataFrame(columns=RANK_COLS)
    if len(rankings) == 1:
        return rankings[0].copy()
    common = sorted(set.intersection(*(set(r.concept) for r in rankings)))
    if not common:
        return pd.DataFrame(columns=RANK_COLS)
    values = np.mean([r.set_index("concept").loc[common, RANK_COLS[1:]].to_numpy(float)
                      for r in rankings], axis=0)
    result = pd.DataFrame(values, columns=RANK_COLS[1:])
    result.insert(0, "concept", common)
    return result.sort_values(["score", "concept"], ascending=[False, True]).reset_index(drop=True)


def consensus_final(daily, anchors, date, provider, stats):
    if daily.empty:
        return daily
    top = daily.head(5)
    stats["minute_layer_days"] = 1
    leaders = {c: provider.window_span(c, date, 24) for c in anchors}
    if any(s.empty for s in leaders.values()):
        stats["minute_fallback_leader"] = 1
        return top
    stats["audit_stale_leader_window"] = sum(s.index[-1].normalize() != date
                                               for s in leaders.values())
    concept_bars = {c: provider.window_span(c, date, 24) for c in top.concept}
    concept_bars = {c: s for c, s in concept_bars.items() if not s.empty}
    stats["minute_excluded"] = len(top) - len(concept_bars)
    stats["audit_stale_concept_windows"] = sum(s.index[-1].normalize() != date
                                                for s in concept_bars.values())
    if len(concept_bars) < 2:
        stats["minute_fallback_sparse"] = 1
        return top
    wide = pd.DataFrame(concept_bars)
    result = mean_ranking([minute_up_resonance(s, wide) for s in leaders.values()])
    if result.empty:
        stats["minute_fallback_sparse"] = 1
        return top
    return result


def run_cached(engine, ranks, counts, start, end, cost):
    bt = copy(engine)
    bt.p = bt.p.with_(cost_bp=float(cost))

    def final(i, date, stats):
        for key, value in counts[i].items():
            stats[key] = stats.get(key, 0) + value
        return ranks[i]

    bt._final_ranking = final
    return bt.run(start, end)


class ConsensusBank:
    """多锚榜单缓存；原五指数领先者保持闸门与诊断标签，不冒充贡献权重。"""
    def __init__(self, engines, primary, count, daily_ranks):
        self.primary = primary
        calendar = primary.close.index
        codes = list(engines)
        momentum = raw_momentum(primary.close[codes])
        self.weights = pd.DataFrame(0., index=calendar, columns=codes)
        self.ranks, self.counts = [], []
        for i, date in enumerate(calendar):
            stats = {}
            ranked = momentum.iloc[i].dropna().sort_values(ascending=False, kind="stable")
            chosen = []
            if primary.sig.gate[i]:
                chosen = [c for c in ranked.index[:count] if not daily_ranks[c][i].empty]
            if not chosen:
                daily = pd.DataFrame(columns=RANK_COLS)
                stats["selection_no_contributors"] = 1
            else:
                self.weights.loc[date, chosen] = 1 / len(chosen)
                stats["selection_contributor_count"] = len(chosen)
                if len(chosen) == 1:
                    stats["selection_single_contributor"] = 1
                daily = mean_ranking([daily_ranks[c][i] for c in chosen])
            if primary.p.daily_top > 0:
                final = consensus_final(daily, chosen, date, primary.mbp, stats)
            else:
                final = daily
            self.ranks.append(final)
            self.counts.append(stats)

    def run(self, start, end, cost):
        return run_cached(self.primary, self.ranks, self.counts, start, end, cost)

    def contribution_stats(self, start, end):
        weights = self.weights.loc[start:end]
        active = weights.sum(axis=1) > 0
        return {"max_mean_contribution": float(weights.mean().max()),
                "max_conditional_contribution": float(weights[active].mean().max()) if active.any() else 0.,
                "max_rolling_contribution": float((weights.rolling(60, min_periods=1).sum() / 60).max().max()),
                "single_contributor_days": int(weights.max(axis=1).eq(1).sum()),
                "no_contributor_days": int((~active).sum())}
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
