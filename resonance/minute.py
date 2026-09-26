"""分钟共振精选：日线 Top10 概念池内用分钟K线收盘相关度二次排序。

设计见 docs/research/minute-resonance-design.md §二（v3）：
- 第一层（日线，短窗）：领先指数 = 13 宽基近 w 日累计收益最强（w∈{2..5} 扫描，
  v1/v2 为 20 日）；全概念池按与领先指数近 w 日 Pearson 相关降序取 Top10；
- 第二层（分钟，v3=近 3 日 5min）：Top10 内按与领先指数的分钟共振分降序——
  最近 3 个交易日的 5min bar 日内相邻收益（每日 47 腿，剔隔夜）Pearson 相关
  （141 样本点；v2 为 20 日 940 点）；
- 降级：池内无分钟数据的概念排在分钟可评者之后（按日线序）；领先指数无分钟
  数据（700050 微盘股 / 932000 中证2000）→ 整体退化为纯日线排名；信号日早于
  分钟数据留存起点（2025-09-25）→ 同样退化为纯日线；
- 最终排名喂 RotationBacktester（Top5 缓冲 / corr1>0 门槛 / exec_lag）。

5min 数据按 (code,day) 需求矩阵裁剪采集——见 ops/collect_minute5.py。
"""
from __future__ import annotations

import pandas as pd

from . import config
from .metrics import resonance_rankings

MINUTE_INTERVAL = "5"        # 5min bar（v2 起；60min 版数据保留作对照）
MINUTE_BARS_PER_DAY = 48     # 09:35..11:30, 13:05..15:00（bar 结束时刻）
MINUTE_LEGS_PER_DAY = 47     # 日内相邻收益腿数（首 bar 无前值）
MINUTE_WINDOW_DAYS = 3       # v3：近 3 日（v2 为 20 日）
MINUTE_WINDOW_BARS = MINUTE_WINDOW_DAYS * MINUTE_LEGS_PER_DAY  # 141
POOL_SIZE = 10               # 日线 Top10 范围池


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
