"""探测 GPT 会话领先指数的真实规则（探索性，非交付）。

判别锚点：只持最强宽基 = +121.06%；领先频率 微盘26/创业17/科创50 14/上证50 6/
科创综指6/北证50 5/中证2000 5，其余 0。遍历：动量窗口 × 信号日对齐。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.backtest import RotationBacktester, perf_stats  # noqa: E402

ANCHOR_TOTAL = 1.2106
ANCHOR_FREQ = {"微盘股": 26, "创业板指": 17, "科创50": 14, "上证50": 6,
               "科创综指": 6, "北证50": 5, "中证2000": 5}


def load():
    bars = pd.read_parquet(config.CACHE_DIR / "daily_bars.partial.parquet")
    close = bars.pivot(index="date", columns="symbol", values="close").sort_index()
    close.index = pd.to_datetime(close.index)
    return close


def leader_run(close, mom_window, shift, rebal=5):
    """shift: rank_fn 收到的日期 = dates[i-1-shift]。0=信号 T-1（规范）；1=信号 T（未来泄漏）。"""
    broad = list(config.BROAD_INDEX_POOL)

    def rank_fn(asof):
        asof = pd.Timestamp(asof)
        sub = close[broad].loc[:asof]
        if len(sub) < mom_window + 1:
            return pd.DataFrame(columns=["concept", "corr"])
        mom = (sub.iloc[-1] / sub.iloc[-mom_window - 1] - 1).dropna()
        mom = mom.sort_values(ascending=False)
        return pd.DataFrame({"concept": mom.index, "corr": mom.values + 10.0})

    # shift>0 需要把引擎给的 prev 再往前挪 → 包一层日期映射
    if shift > 0:
        base = rank_fn
        dates = close.loc[config.BACKTEST_START:].index

        def rank_fn(asof):
            asof = pd.Timestamp(asof)
            pos = dates.searchsorted(asof)
            return base(dates[pos - shift]) if pos - shift >= 0 else base(asof)

    bt = RotationBacktester(close[broad].loc[config.BACKTEST_START:], rank_fn,
                            rebalance_days=rebal, topk=1)
    out = bt.run()
    # 重建领先频率：直接用 rank 逻辑逐信号日算
    trade_dates = close[broad].loc[config.BACKTEST_START:].index
    freq = {}
    i = 1
    sig_days = []
    while i < len(trade_dates):
        asof = trade_dates[i - 1 - shift] if i - 1 - shift >= 0 else None
        if asof is not None:
            sub = close[broad].loc[:asof]
            if len(sub) >= mom_window + 1:
                mom = (sub.iloc[-1] / sub.iloc[-mom_window - 1] - 1).dropna()
                if len(mom):
                    freq[mom.idxmax()] = freq.get(mom.idxmax(), 0) + 1
                    sig_days.append(str(asof.date()))
        i += rebal
    return out, freq


def main():
    close = load()
    print(f"{'窗口':>4} {'shift':>5} {'总收益':>9} {'夏普':>6} {'回撤':>7}  领先频率Top5")
    for w in (5, 10, 20, 30, 60):
        for shift in (0, 1):
            out, freq = leader_run(close, w, shift)
            st = perf_stats(out["nav_curve"])
            freq_named = {config.BROAD_INDEX_POOL[k]: v for k, v in
                          sorted(freq.items(), key=lambda kv: -kv[1])}
            top5 = list(freq_named.items())[:5]
            mark = " ←" if abs(st["total_return"] - ANCHOR_TOTAL) < 0.15 else ""
            print(f"{w:>4} {shift:>5} {st['total_return']:>8.2%} {st['sharpe']:>6.2f} "
                  f"{st['max_drawdown']:>7.2%}  {top5}{mark}")
    print(f"\n锚点: +121.06% | 频率 {ANCHOR_FREQ}")


if __name__ == "__main__":
    main()
