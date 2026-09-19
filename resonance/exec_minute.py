"""分钟级执行层优化：盘中交易时点重算 + 盘中追踪止损重模拟。

设计见 docs/minute-exec-design.md。信号层（20日相关、5日调仓、单持仓、
Top5 缓冲、exec_lag=1、几何复利）完全不变，只替换执行价格：

1. rerun_trade_times（候选 A）：复用日线基线引擎的持仓路径与调仓时刻表，
   把调仓日 τ（= 信号日 T 的次日）的成交价从收盘改为 τ 日盘中时点 t。
   同侧同时点（先卖后买），无杠杆。总收益比基线 = Π_τ (P_t/C)_旧 × (C/P_t)_新。
2. run_with_intraday_stop（候选 B）：全路径重模拟（止损会改变后续持仓），
   持仓期内逐 5min bar 维护入场以来最高 mark，mark < 峰值×(1−x) 触发止损，
   按触发 bar 收盘卖出 → 空仓至下一个原定调仓检查日。mode='close' 为仅
   收盘 mark 的剂量对照（同规则日线粒度）。

无未来数据：成交价取时点 t ≤ 当日 15:00，信号来自 T 收盘；引擎首个调仓的
exec_lag 边界塌缩日（τ 即信号日）强制收盘成交。缺失降级：该 code 当日无
5min 数据 → 退化为日线收盘口径并计数。

指数收益研究口径：零费率/滑点，指数不可直接交易；执行时点优化隐含"可按
分钟价成交"的更强假设，结论须带此标注。
"""
from __future__ import annotations

import math

import pandas as pd

TRADE_TIMES = ("open", "09:35", "10:00", "10:30", "11:30", "13:05", "14:00", "14:30", "15:00")


def build_minute_wide(bars_long: pd.DataFrame) -> pd.DataFrame:
    """5min 长表 [symbol, datetime, close] → 宽表（index=完整 datetime）。"""
    return bars_long.pivot(index="datetime", columns="symbol", values="close").sort_index()


class MinutePrices:
    """执行价供给：'open'→日线开盘价；'15:00'→日线收盘；其余→当日该时点 5min bar 收盘。"""

    def __init__(self, close_wide: pd.DataFrame, open_wide: pd.DataFrame, minute_wide: pd.DataFrame):
        self.close_wide = close_wide
        self.open_wide = open_wide
        self.minute_wide = minute_wide
        self.degraded = 0          # 请求盘中价但缺数据、退化为收盘的次数
        self._day_slices: dict[str, pd.DataFrame] | None = None

    # --- 基础取价 ---
    def _at(self, wide: pd.DataFrame, key, code: str) -> float:
        if code not in wide.columns or key not in wide.index:
            return float("nan")
        v = wide.at[key, code]
        return float(v) if pd.notna(v) else float("nan")

    def close_at(self, day: pd.Timestamp, code: str) -> float:
        return self._at(self.close_wide, day, code)

    def day_slice(self, day: pd.Timestamp) -> pd.DataFrame | None:
        """当日 5min 宽表切片（48 行），无则 None。惰性构建按日索引。"""
        if self._day_slices is None:
            idx = self.minute_wide.index
            self._day_slices = {
                str(k.date()): g for k, g in self.minute_wide.groupby(idx.normalize())
            }
        return self._day_slices.get(str(pd.Timestamp(day).date()))

    def price(self, code: str, day: pd.Timestamp, t: str) -> float:
        """code 在 day 日时点 t 的可成交价（缺数据退化收盘；nan=完全无价）。"""
        if t == "open":
            v = self._at(self.open_wide, day, code)
        elif t == "15:00":
            v = self.close_at(day, code)
        else:
            v = self._at(self.minute_wide, pd.Timestamp(f"{day.date()} {t}"), code)
        if math.isfinite(v):
            return v
        fb = self.close_at(day, code)
        if math.isfinite(fb):
            self.degraded += 1
            return fb
        return float("nan")

    def day_marks(self, code: str, day: pd.Timestamp) -> list[tuple[pd.Timestamp, float]]:
        sub = self.day_slice(day)
        if sub is None or code not in sub.columns:
            return []
        col = sub[code]
        return [(ts, float(p)) for ts, p in col.items() if pd.notna(p) and math.isfinite(float(p))]


def _reconstruct_holding(days: list[pd.Timestamp], switches: list[dict]):
    """switches（date=首个收益日 i, from, to）→ 每日收益归属持仓 与 τ（成交日）→i 映射。"""
    sw_by_date = {s["date"]: s for s in switches}
    held: dict[pd.Timestamp, str | None] = {}
    trade_of: dict[pd.Timestamp, pd.Timestamp] = {}
    cur = None
    for i, d in enumerate(days):
        if d in sw_by_date:
            cur = sw_by_date[d]["to"]
            if i > 0:
                trade_of[days[i - 1]] = d
        held[d] = cur
    return held, trade_of


def rerun_trade_times(bt_out: dict, prices: MinutePrices, t: str) -> dict:
    """候选 A：基线持仓路径 + 成交时点 t 的净值重算（bt_out 来自基线引擎同窗运行）。"""
    curve = bt_out["nav_curve"]
    days = list(curve.index)
    held, trade_of = _reconstruct_holding(days, bt_out["switches"])

    nav, anchor = 1.0, None
    out_curve, log_ratios, switches_log = {}, [], []
    for d in days:
        f = 1.0
        if d in trade_of:
            sw = next(s for s in bt_out["switches"] if s["date"] == trade_of[d])
            old, new = sw["from"], sw["to"]
            # 引擎首个调仓的 exec_lag 边界（τ=窗口首日=信号日）→ 强制收盘，防未来数据
            t_eff = "15:00" if d == days[0] else t
            p_old = prices.price(old, d, t_eff) if old is not None else None
            p_new = prices.price(new, d, t_eff) if new is not None else None
            if old is not None and anchor is not None and math.isfinite(anchor) and anchor > 0 \
                    and p_old is not None and math.isfinite(p_old) and p_old > 0:
                f *= p_old / anchor
            if new is not None and p_new is not None and math.isfinite(p_new) and p_new > 0:
                c_new = prices.close_at(d, new)
                f *= c_new / p_new if math.isfinite(c_new) and c_new > 0 else 1.0
            if t_eff != "15:00" and old is not None and new is not None:
                c_old, c_new = prices.close_at(d, old), prices.close_at(d, new)
                if all(math.isfinite(x) and x > 0 for x in (p_old, p_new, c_old, c_new)):
                    lr = math.log(p_old / c_old) + math.log(c_new / p_new)
                    log_ratios.append(lr)
                    switches_log.append({"date": d, "old": old, "new": new, "log_ratio": lr})
            if new is not None:
                c = prices.close_at(d, new)
                anchor = c if math.isfinite(c) and c > 0 else p_new
            else:
                anchor = None
        elif (h := held[d]) is not None:
            c = prices.close_at(d, h)
            if anchor is not None and math.isfinite(c) and c > 0 and anchor > 0:
                f = c / anchor
                anchor = c
            # 收盘 NaN：引擎同款跳过（nav 不动）
        nav *= f
        out_curve[d] = nav
    return {
        "nav_curve": pd.Series(out_curve),
        "log_ratios": log_ratios,
        "switches_log": switches_log,
        "degraded": prices.degraded,
    }


def run_with_intraday_stop(
    close_df: pd.DataFrame,
    prices: MinutePrices,
    rank_fn,
    rebalance_days: int,
    topk: int,
    stop_pct: float,
    mode: str = "minute",
    gate_fn=None,
) -> dict:
    """候选 B：全路径重模拟 + 追踪止损（mode='minute' 5min 粒度 / 'close' 收盘对照）。

    调仓检查逻辑逐条镜像 resonance.backtest.RotationBacktester.run（Top5 缓冲 /
    corr1>0 门槛 / 检查网格），入场价 = 上一交易日收盘（exec_lag=1 基线口径），
    仅"当日计收益"替换为逐 mark 的止损路径。

    gate_fn(asof)→bool（缺省自动取 rank_fn.gate_fn 属性）：False = 不开新仓
    ——已有持仓仍在 Top5 内则续持，跌出 Top5 或空仓时保持/转为空仓（"不开仓"
    语义，不强制平仓续持中的仓位）。rebalance_days=1 即逐日滚动检查。
    """
    assert mode in ("minute", "close")
    dates = list(close_df.index)
    if gate_fn is None:
        gate_fn = getattr(rank_fn, "gate_fn", None)

    nav, holding, anchor, peak, holding_until = 1.0, None, None, None, -1
    out_curve, records, stop_events = {}, [], []
    stats = {"stops": 0, "flat_days": 0, "degraded_days": 0, "mark_days": 0, "gate_blocked": 0}
    degraded_log: list[tuple[str, pd.Timestamp]] = []

    for i, date in enumerate(dates):
        # --- 调仓检查（收盘信号，次日生效；与基线引擎一致）---
        if i >= holding_until and i > 0:
            ranking = rank_fn(dates[i - 1])
            if ranking is None or ranking.empty:
                new_holding, top_set = None, set()
            else:
                top_set = set(ranking["concept"].head(topk))
                first = ranking["concept"].iloc[0]
                corr1 = float(ranking["corr"].iloc[0])
                if gate_fn is not None and not bool(gate_fn(dates[i - 1])):
                    gate_ok = False
                    stats["gate_blocked"] += 1
                    new_holding = holding if holding in top_set else None
                else:
                    new_holding = holding if holding in top_set else (first if corr1 > 0 else None)
            if new_holding != holding:
                records.append({"date": date, "action": "switch" if new_holding else "clear",
                                "from": holding, "to": new_holding})
                holding = new_holding
                if holding is not None:
                    anchor = prices.close_at(dates[i - 1], holding)  # 入场价 = T+1 收盘
                    peak = anchor if math.isfinite(anchor) and anchor > 0 else None
                else:
                    anchor, peak = None, None
            holding_until = i + rebalance_days

        # --- 当日收益段（含止损路径）---
        f = 1.0
        if holding is not None and i > 0 and anchor is not None \
                and math.isfinite(anchor) and anchor > 0:
            if mode == "minute":
                marks = prices.day_marks(holding, date)
                if not marks:
                    stats["degraded_days"] += 1
                    degraded_log.append((holding, date))
                    c = prices.close_at(date, holding)
                    marks = [(date, c)] if math.isfinite(c) else []
            else:
                c = prices.close_at(date, holding)
                marks = [(date, c)] if math.isfinite(c) else []
            stats["mark_days"] += 1
            stopped = False
            for ts, p in marks:
                if peak is None or p > peak:
                    peak = p
                if peak is not None and peak > 0 and p < peak * (1.0 - stop_pct):
                    f = p / anchor
                    stop_events.append({"date": date, "time": getattr(ts, "time", lambda: None)(),
                                        "code": holding, "day_ret": f - 1.0})
                    holding, anchor, peak, stopped = None, None, None, True
                    stats["stops"] += 1
                    break
            if not stopped and holding is not None:
                c = prices.close_at(date, holding)
                if math.isfinite(c) and c > 0:
                    f = c / anchor
                    anchor = c
                    peak = c if peak is None else max(peak, c)
                # 收盘 NaN：引擎同款跳过
        elif holding is None and i > 0:
            stats["flat_days"] += 1
        nav *= f
        out_curve[date] = nav

    return {"nav_curve": pd.Series(out_curve), "switches": records, "stop_events": stop_events,
            "stats": stats, "degraded": prices.degraded, "degraded_log": degraded_log}
