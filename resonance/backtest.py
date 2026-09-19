"""指数轮动回测引擎（最小核心）：Top5 缓冲 + T+1 计收益 + 几何复利。

规则迁移自 GPT 会话已确认口径（2026-09-18）：
- 每 REBALANCE_DAYS 个交易日检查一次排名（信号窗口 = 20 日相关度）；
- 单持仓；首次买入当前第 1 名；
- 原持仓仍在 Top5 内 → 续持；跌出 Top5 → 更换为当前第 1 名，无合格候选则空仓；
- T 日收盘产生信号，T+1 起计入新标的收益（无未来函数）；
- 区间收益按几何复利衔接：NAV_k = NAV_{k-1} × (1 + R_k)。

指数收益研究口径：费率/滑点为 0，结果不代表指数可直接成交。
"""
from __future__ import annotations

import pandas as pd

from . import config


class RotationBacktester:
    def __init__(
        self,
        close: pd.DataFrame,
        rank_fn,
        rebalance_days: int = config.REBALANCE_DAYS,
        topk: int = config.TOPK_BUFFER,
    ):
        """close: 收盘价宽表；rank_fn(date) → 当日概念排名 DataFrame[concept, corr]（降序）。"""
        self.close = close
        self.rank_fn = rank_fn
        self.rebalance_days = rebalance_days
        self.topk = topk

    def run(self) -> dict:
        rets = self.close.pct_change()
        dates = self.close.index
        nav = 1.0
        holding: str | None = None
        holding_until = -1  # 下一次可检查日的位置
        records = []

        for i, date in enumerate(dates):
            # --- 调仓检查（收盘信号，次日生效）---
            if i >= holding_until and i > 0:
                prev = dates[i - 1]
                ranking = self.rank_fn(prev)
                if ranking is None or ranking.empty:
                    new_holding = None
                    top_set: set[str] = set()
                else:
                    top_set = set(ranking["concept"].head(self.topk))
                    first = ranking["concept"].iloc[0]
                    corr1 = float(ranking["corr"].iloc[0])
                    new_holding = holding if holding in top_set else (first if corr1 > 0 else None)

                if new_holding != holding:
                    records.append(
                        {"date": date, "action": "switch" if new_holding else "clear",
                         "from": holding, "to": new_holding}
                    )
                    holding = new_holding
                holding_until = i + self.rebalance_days

            # --- 当日计收益（T+1：调仓当日已按新持仓收盘价起算，收益从下一日 pct_change 来）---
            if holding is not None and i > 0:
                r = rets.at[date, holding]
                if pd.notna(r):
                    nav *= 1.0 + float(r)
            records.append({"date": date, "action": "eod", "holding": holding, "nav": nav})

        curve = pd.Series(
            [r["nav"] for r in records if r["action"] == "eod"],
            index=[r["date"] for r in records if r["action"] == "eod"],
        )
        switches = [r for r in records if r["action"] in ("switch", "clear")]
        return {"nav_curve": curve, "switches": switches, "final_nav": nav}


def perf_stats(nav_curve: pd.Series, benchmark_curve: pd.Series | None = None) -> dict:
    """净值曲线 → 收益/回撤/夏普等统计。"""
    rets = nav_curve.pct_change().dropna()
    n_years = max(len(rets) / 244.0, 1e-9)
    total = float(nav_curve.iloc[-1] / nav_curve.iloc[0] - 1)
    ann = float((1 + total) ** (1 / n_years) - 1) if total > -1 else -1.0
    vol = float(rets.std() * np_sqrt(244))
    sharpe = float(rets.mean() / rets.std() * np_sqrt(244)) if rets.std() > 0 else 0.0
    dd = float((nav_curve / nav_curve.cummax() - 1).min())
    stats = {
        "total_return": total,
        "annualized_return": ann,
        "annualized_vol": vol,
        "sharpe": sharpe,
        "max_drawdown": dd,
    }
    if benchmark_curve is not None:
        stats["benchmark_return"] = float(benchmark_curve.iloc[-1] / benchmark_curve.iloc[0] - 1)
        stats["excess_wealth"] = float(nav_curve.iloc[-1] / benchmark_curve.iloc[-1] - 1)
    return stats


def np_sqrt(n: float) -> float:
    import math

    return math.sqrt(n)
