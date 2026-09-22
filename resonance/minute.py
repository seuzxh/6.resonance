"""分钟共振精选：日线 Top10 概念池内用分钟K线收盘相关度二次排序。

设计见 docs/minute-resonance-design.md §二（v3）：
- 第一层（日线，短窗）：领先指数 = 13 宽基近 w 日累计收益最强（w∈{2..5} 扫描，
  v1/v2 为 20 日）；全概念池按与领先指数近 w 日 Pearson 相关降序取 Top10；
- 第二层（分钟，v3=近 3 日 5min）：Top10 内按与领先指数的分钟共振分降序——
  最近 3 个交易日的 5min bar 日内相邻收益（每日 47 腿，剔隔夜）Pearson 相关
  （141 样本点；v2 为 20 日 940 点）；
- 降级：池内无分钟数据的概念排在分钟可评者之后（按日线序）；领先指数无分钟
  数据（700050 微盘股 / 932000 中证2000）→ 整体退化为纯日线排名；信号日早于
  分钟数据留存起点（2025-09-25）→ 同样退化为纯日线；
- 最终排名喂 RotationBacktester（Top5 缓冲 / corr1>0 门槛 / exec_lag）。

5min 数据按 (code,day) 需求矩阵裁剪采集——见 work/collect_minute5.py。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config
from .metrics import resonance_rankings

MINUTE_INTERVAL = "5"        # 5min bar（v2 起；60min 版数据保留作对照）
MINUTE_BARS_PER_DAY = 48     # 09:35..11:30, 13:05..15:00（bar 结束时刻）
MINUTE_LEGS_PER_DAY = 47     # 日内相邻收益腿数（首 bar 无前值）
MINUTE_WINDOW_DAYS = 3       # v3：近 3 日（v2 为 20 日）
MINUTE_WINDOW_BARS = MINUTE_WINDOW_DAYS * MINUTE_LEGS_PER_DAY  # 141
POOL_SIZE = 10               # 日线 Top10 范围池

# --- v4：极值时刻弹性共振 ---
EXTREME_MIN_BARS = 1         # 极值窗口最短 1 bar = 5 分钟
EXTREME_MAX_BARS = 4         # 最长 4 bar = 20 分钟（用户口径 5~20min）
DEAD_DAY_SPREAD = 1e-3       # |R_up|+|R_dn| < 0.1% 视为死日，弹性不计


def extreme_window(legs, min_bars: int = EXTREME_MIN_BARS,
                   max_bars: int = EXTREME_MAX_BARS) -> dict[str, object]:
    """单日指数腿序列（array-like，日内相邻 5min 收益）→ 最大涨/跌窗口。

    窗口长度 ∈ [min_bars, max_bars] bar（5~20 分钟）；返回闭区间腿下标与
    对应区间收益。{"up": (i,j), "dn": (i,j), "r_up": float, "r_dn": float}
    """
    v = np.asarray(legs, dtype=float)
    n = len(v)
    best_up = best_dn = (0, min_bars - 1)
    r_up, r_dn = -np.inf, np.inf
    csum = np.concatenate([[0.0], np.cumsum(v)])
    for length in range(min_bars, max_bars + 1):
        sums = csum[length:] - csum[: n - length + 1]
        i_up, i_dn = int(np.argmax(sums)), int(np.argmin(sums))
        if sums[i_up] > r_up:
            r_up, best_up = float(sums[i_up]), (i_up, i_up + length - 1)
        if sums[i_dn] < r_dn:
            r_dn, best_dn = float(sums[i_dn]), (i_dn, i_dn + length - 1)
    return {"up": best_up, "dn": best_dn, "r_up": r_up, "r_dn": r_dn}


def elasticity_table(minute_rets: pd.DataFrame, leaders: list[str],
                     min_bars: int = EXTREME_MIN_BARS,
                     max_bars: int = EXTREME_MAX_BARS,
                     use_down: bool = True) -> dict[str, pd.DataFrame]:
    """逐日逐领导指数计算全概念池的当日弹性 e（v4 核心预计算）。

    use_down=True（v4 双窗）：e = (C_up − C_dn) / (|R_up| + |R_dn|)，概念在
    领导当日 5~20min 极值涨/跌两窗的同向响应比。
    use_down=False（v4b 只涨窗，用户口径"只考虑关联涨幅"）：e = C_up / R_up，
    概念在领导当日最大涨幅窗的单位跟涨弹性；R_up < 0.1% 视为死日。
    领导当日数据不足或死日 → 整行缺失。
    返回 {leader: DataFrame(日 × 分钟覆盖代码)}。
    """
    grouped = dict(tuple(minute_rets.groupby(minute_rets.index.normalize())))
    days = sorted(grouped)
    codes = [c for c in minute_rets.columns if c not in leaders]
    tables: dict[str, pd.DataFrame] = {}
    for leader in leaders:
        if leader not in minute_rets.columns:
            continue
        rows = {}
        for day in days:
            g = grouped[day]
            lead_legs = g[leader].reset_index(drop=True)
            if lead_legs.notna().sum() < MINUTE_LEGS_PER_DAY * 0.9:
                continue
            ext = extreme_window(lead_legs.fillna(0.0).to_numpy(), min_bars, max_bars)
            i_up, j_up = ext["up"]
            if use_down:
                spread = abs(ext["r_up"]) + abs(ext["r_dn"])
                if spread < DEAD_DAY_SPREAD:
                    continue
                i_dn, j_dn = ext["dn"]
                resp = (g[codes].iloc[i_up : j_up + 1].sum()
                        - g[codes].iloc[i_dn : j_dn + 1].sum())
            else:
                spread = ext["r_up"]
                if spread < DEAD_DAY_SPREAD:
                    continue
                resp = g[codes].iloc[i_up : j_up + 1].sum()
            rows[day] = resp / spread
        tables[leader] = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    return tables


def make_elasticity_rank_fn(
    close_all: pd.DataFrame,
    concepts: list[str],
    tables: dict[str, pd.DataFrame],
    broad_codes: list[str],
    window: int,
    exec_lag: int = 1,
    pool_size: int = POOL_SIZE,
    agg_days: int = 1,
    daily_corr: dict[str, pd.DataFrame] | None = None,
):
    """v4 rank_fn：日线 Top10 池 → 池内按近 agg_days 日弹性中位数降序。

    concepts 应传分钟覆盖概念集、broad_codes 应传有分钟数据的领导指数
    （"不考虑无 min 的情况"——由调用方收窄，本函数不再做降级分支：
    领导无弹性表时直接返回日线榜）。
    """
    from .metrics import resonance_rankings

    returns = close_all.pct_change()
    close_broad = close_all[broad_codes]
    cal = close_all.index
    stats = {"calls": 0, "minute_active": 0, "pick_changed": 0}

    def rank_fn(asof) -> pd.DataFrame:
        asof = pd.Timestamp(asof)
        pos = cal.searchsorted(asof)
        sig_day = cal[max(pos - exec_lag, 0)]
        sub = close_broad.loc[:sig_day]
        if len(sub) < window + 1:
            return pd.DataFrame(columns=["concept", "corr"])
        mom = (sub.iloc[-1] / sub.iloc[-window - 1] - 1.0).dropna()
        if mom.empty:
            return pd.DataFrame(columns=["concept", "corr"])
        leader = str(mom.idxmax())
        if daily_corr is not None:
            mat = daily_corr.get(leader)
            if mat is None or sig_day not in mat.index:
                return pd.DataFrame(columns=["concept", "corr"])
            row = mat.loc[sig_day].dropna()
            if row.empty:
                return pd.DataFrame(columns=["concept", "corr"])
            daily = pd.DataFrame({"concept": row.index, "corr": row.values})
        else:
            daily = resonance_rankings(returns, leader, concepts, window, asof=sig_day)
        if daily.empty:
            return daily
        stats["calls"] += 1

        tab = tables.get(leader)
        if tab is None or sig_day not in tab.index:
            return daily
        med = tab.loc[:sig_day].tail(agg_days).median(skipna=True)
        pool = daily.head(pool_size)
        scored = [{"concept": c, "corr": float(med[c])}
                  for c in pool["concept"] if c in med.index and pd.notna(med[c])]
        if not scored:
            return daily
        stats["minute_active"] += 1
        scored.sort(key=lambda r: -r["corr"])
        scored_set = {s["concept"] for s in scored}
        unscored = [{"concept": c, "corr": float(r["corr"])}
                    for c, r in zip(pool["concept"], pool["corr"]) if c not in scored_set]
        final = pd.DataFrame(scored + unscored)[["concept", "corr"]]
        if final["concept"].iloc[0] != pool["concept"].iloc[0]:
            stats["pick_changed"] += 1
        return final

    rank_fn.stats = stats
    return rank_fn


def minute_returns(bars_long: pd.DataFrame) -> pd.DataFrame:
    """分钟长表 [symbol, datetime, close] → 日内收益宽表。

    index=datetime（剔每日首 bar 后剩余 3 腿/日），columns=symbol。
    隔夜/午休跨段：10:30→11:30、11:30→14:00、14:00→15:00 三腿均保留
    （小时 bar 研究惯例），跨日 15:00→次日10:30 剔除。
    """
    wide = bars_long.pivot(index="datetime", columns="symbol", values="close").sort_index()
    day = wide.index.normalize()
    rets = wide.groupby(day).pct_change()
    return rets.dropna(how="all")


def minute_resonance_score(
    minute_rets: pd.DataFrame, concept: str, leader: str,
    asof: pd.Timestamp, window_bars: int = MINUTE_WINDOW_BARS,
) -> float:
    """截至 asof（含 asof 当日 15:00 bar）的分钟共振分。样本不足返回 NaN。"""
    sub = minute_rets.loc[:asof + pd.Timedelta(hours=23)]
    if len(sub) < window_bars:
        return float("nan")
    x = sub[concept].iloc[-window_bars:]
    y = sub[leader].iloc[-window_bars:]
    if x.notna().sum() < window_bars or y.notna().sum() < window_bars:
        return float("nan")
    return float(x.corr(y))


def make_minute_rank_fn(
    close_all: pd.DataFrame,
    concepts: list[str],
    minute_rets: pd.DataFrame,
    broad_codes: list[str] | None = None,
    window: int = config.SIGNAL_WINDOW,
    exec_lag: int = 1,
    pool_size: int = POOL_SIZE,
    window_bars: int = MINUTE_WINDOW_BARS,
    daily_corr: dict[str, pd.DataFrame] | None = None,
):
    """构造 RotationBacktester 的 rank_fn：日线 Top10 池 + 分钟共振精选。

    minute_rets 覆盖的代码即可评分钟分；covered 判定 = 列存在且信号日样本足
    （window_bars 腿 = 完整分钟窗口）。
    daily_corr：可选的预计算日线相关 {leader: DataFrame(date×concept)}——批量
    回测（w 扫描 × 多相位）时注入以避免每次信号日重算全池滚动相关；
    缺省时内部调 resonance_rankings 逐日计算。
    """
    if broad_codes is None:
        broad_codes = list(config.BROAD_INDEX_POOL)
    close_broad = close_all[broad_codes]
    returns = close_all.pct_change()
    cal = close_all.index
    stats = {"calls": 0, "minute_active": 0, "pick_changed": 0}

    def _daily_ranking(leader: str, sig_day: pd.Timestamp) -> pd.DataFrame | None:
        if daily_corr is not None:
            mat = daily_corr.get(leader)
            if mat is None or sig_day not in mat.index:
                return None
            row = mat.loc[sig_day].dropna()
            if row.empty:
                return None
            row = row.sort_values(ascending=False)  # 与 resonance_rankings 同序（降序）
            return pd.DataFrame({"concept": row.index, "corr": row.values})
        return resonance_rankings(returns, leader, concepts, window, asof=sig_day)

    def rank_fn(asof) -> pd.DataFrame:
        asof = pd.Timestamp(asof)
        pos = cal.searchsorted(asof)
        sig_day = cal[max(pos - exec_lag, 0)]
        # --- 第一层：日线 ---
        sub = close_broad.loc[:sig_day]
        if len(sub) < window + 1:
            return pd.DataFrame(columns=["concept", "corr"])
        mom = (sub.iloc[-1] / sub.iloc[-window - 1] - 1.0).dropna()
        if mom.empty:
            return pd.DataFrame(columns=["concept", "corr"])
        leader = str(mom.idxmax())
        daily = _daily_ranking(leader, sig_day)
        if daily is None or daily.empty:
            return pd.DataFrame(columns=["concept", "corr"])
        stats["calls"] += 1

        # --- 第二层：分钟共振（仅当领先指数可评）---
        end = sig_day + pd.Timedelta(hours=23)
        sub_mr = minute_rets.loc[:end] if leader in minute_rets.columns else None
        leader_ok = sub_mr is not None and len(sub_mr) >= window_bars
        if not leader_ok:
            return daily  # 降级：纯日线排名

        pool = daily.head(pool_size)
        scored: list[dict] = []
        unscored: list[dict] = []
        for _, row in pool.iterrows():
            c = row["concept"]
            if c in sub_mr.columns:
                m = minute_resonance_score(sub_mr, c, leader, sig_day, window_bars)
                if pd.notna(m):
                    scored.append({"concept": c, "corr": m, "daily_corr": row["corr"]})
                    continue
            unscored.append({"concept": c, "corr": row["corr"], "daily_corr": row["corr"]})
        if not scored:
            return daily
        stats["minute_active"] += 1
        scored.sort(key=lambda r: -r["corr"])
        final = pd.DataFrame(scored + unscored)[["concept", "corr"]]
        if final["concept"].iloc[0] != pool["concept"].iloc[0]:
            stats["pick_changed"] += 1
        return final

    rank_fn.stats = stats
    return rank_fn
