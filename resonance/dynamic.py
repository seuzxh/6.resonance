"""动态宽基→概念策略（GPT 会话 2026-09-18 主线）。

规则（docs/gpt-session-summary.md §二）：
- 信号日比较 13 宽基近 20 日累计收益取最强（全跌取跌幅最小 = max 自动涵盖）；
- 再在概念目录中取与该指数 20 日 Pearson 相关最高者 → 交给 RotationBacktester
  的 Top5 缓冲 / T+1 / 几何复利引擎执行。

执行时点口径（exec_lag，2026-09-19 复现时经验判定，见
outputs/index_backtest_framework/reproduction_report.md）：
- exec_lag=0：信号 T 收盘 → T 收盘入场（当收盘价成交口径）；
- exec_lag=1：信号 T 收盘 → T+1 收盘入场，T+2 起计收益（严格 T+1 口径；
  GPT 会话锚点 +121.06%/+148.59% 与此口径对齐：领先频率 微盘24/创业15/
  科创50 14 ≈ 锚点 26/17/14，总收益差距 <10pp）。

冷启动变体：信号日早于窗口起点 + 20 个交易日时返回空榜（对应 GPT 会话
数据无 lookback 的情形；本机采集含 2024-10 起 lookback，热启动首日即可出信号）。
"""
from __future__ import annotations

import pandas as pd

from . import config
from .metrics import resonance_rankings


def leader_index(
    close_broad: pd.DataFrame, asof: str, window: int = config.SIGNAL_WINDOW
) -> str | None:
    """信号日 asof 的最强宽基：近 window 日累计收益最大者。

    全跌时 max 自动取跌幅最小者。样本不足（NaN）的指数自动排除。
    """
    sub = close_broad.loc[:asof]
    if len(sub) < window + 1:
        return None
    mom = sub.iloc[-1] / sub.iloc[-window - 1] - 1.0
    mom = mom.dropna()
    if mom.empty:
        return None
    return str(mom.idxmax())


def leader_momentum(
    close_broad: pd.DataFrame, asof: str, window: int = config.SIGNAL_WINDOW
) -> pd.Series:
    """信号日全部宽基的近 window 日累计收益（领先频率统计用）。"""
    sub = close_broad.loc[:asof]
    if len(sub) < window + 1:
        return pd.Series(dtype=float)
    return sub.iloc[-1] / sub.iloc[-window - 1] - 1.0


def make_dynamic_rank_fn(
    close_all: pd.DataFrame,
    concepts: list[str],
    broad_codes: list[str] | None = None,
    window: int = config.SIGNAL_WINDOW,
    cold_start: bool = False,
    backtest_start: str | None = None,
    exec_lag: int = 0,
):
    """构造 RotationBacktester 用的 rank_fn(date)。

    close_all：宽基+概念收盘宽表（同一日历索引，含 lookback）；
    exec_lag：见模块 docstring；引擎传入的 asof 为 d_{i-1}，lag>0 时信号日
    再回移 lag 个交易日（不依赖引擎索引，直接在 close_all 日历上回移）；
    cold_start=True 时，信号日早于 backtest_start + window 个交易日的榜为空。
    """
    if broad_codes is None:
        broad_codes = list(config.BROAD_INDEX_POOL)
    close_broad = close_all[broad_codes]
    returns = close_all.pct_change()
    cal = close_all.index

    cutoff: pd.Timestamp | None = None
    if cold_start:
        assert backtest_start is not None, "cold_start 需要 backtest_start"
        start_pos = cal.searchsorted(pd.Timestamp(backtest_start))
        cutoff = cal[start_pos + window]

    def rank_fn(asof) -> pd.DataFrame:
        asof = pd.Timestamp(asof)
        pos = cal.searchsorted(asof)
        sig_day = cal[max(pos - exec_lag, 0)]  # 执行滞后：信号日回移
        leader = leader_index(close_broad, sig_day, window)
        rank_fn.leader_history[str(asof.date())] = leader  # 领先频率统计（含 None）
        if cutoff is not None and sig_day < cutoff:
            return pd.DataFrame(columns=["concept", "corr"])
        if leader is None:
            return pd.DataFrame(columns=["concept", "corr"])
        return resonance_rankings(returns, leader, concepts, window, asof=sig_day)

    rank_fn.leader_history: dict[str, str | None] = {}
    return rank_fn
