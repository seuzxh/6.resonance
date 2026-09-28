"""验证轨 P3：ResonanceStrategy——V3 事件循环在 qlib 框架的移植（②段等价）。

三段等价链（docs/research/qlib-validation-plan.md §三）：
  V3Backtester ≡① ReplayBacktester（P2） ≡② ResonanceStrategy（本模块）

语义（对照 spec §五 P3 与 v3.py run() 的 open 分支）：
- 决策在 T 日生成、当日开盘成交（deal_price="$open"）；桥文件是 T−1 日
  收盘的信号 → 策略在 T 日消费 T−1 日的榜 = 引擎 exec_lag=1 等价；
- 止损判定用 T−1 日收盘（信号日）对入场日收盘基准；min_hold/冷却期以
  窗口相对步数计（差值与引擎全日历下标差值同构）；
- 换仓拆一卖一买两笔订单（成本次序 = 双边）；E-Gate 对拍锚 = 逐笔成交
  （日期/标的/方向/价格）+ 账户终值参考（组合列重建不作准，E-8 备注）。

用法：conda run -n qlib python research/qlib_route_a/qlib_equivalence.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from research.qlib_route_a.qlib_harness import build_quote, run_backtest

MATRIX = [
    ("FULL", ["2024-12-27", "2024-12-30", "2024-12-31", "2025-01-02", "2025-01-03"], "2026-09-18"),
    ("MWIN", ["2025-09-22", "2025-09-23", "2025-09-24", "2025-09-25", "2025-09-26"], "2026-09-18"),
    ("EXT", ["2024-12-27", "2024-12-30", "2024-12-31", "2025-01-02", "2025-01-03"], "2026-09-24"),
]
COSTS = (0.0, 10.0, 30.0)


class ResonanceStrategy:
    """qlib BaseStrategy 移植。impl 为 qlib 侧策略实例，本类持全部状态。"""

    def __init__(self, bridge: pd.DataFrame, quote: pd.DataFrame,
                 topk: int = 3, min_hold: int = 3, stop_loss: float = 0.05,
                 cooldown: int = 1, cost_bp: float = 10.0):
        from qlib.strategy.base import BaseStrategy

        self.rk = {d: g.sort_values("rank") for d, g in bridge.groupby("date")}
        self.quote = quote
        self.topk, self.min_hold = topk, min_hold
        self.stop_loss, self.cooldown = stop_loss, cooldown
        self.cost = cost_bp / 1e4
        self.days_seen: list[pd.Timestamp] = []
        self.holding = None
        self.entry_px = None          # 止损基准 = 入场日收盘价
        self.exec_step = None         # 窗口相对步数（买入口）
        self.block_until = -10
        self.trades = []              # (date, type, from, to, price)

        outer = self

        class _Impl(BaseStrategy):
            def generate_trade_decision(self, execute_trade_day=None):
                return outer.step(self.trade_calendar, self.trade_position)

        self.impl = _Impl()

    # ---- 行情查询 ----
    def _px(self, day, code, field="$open"):
        try:
            v = self.quote.loc[(pd.Timestamp(day).normalize(), code), field]
            return float(v) if pd.notna(v) else float("nan")
        except KeyError:
            return float("nan")

    def _held_amount(self, pos):
        p = pos.position.get(self.holding)
        return float(p["amount"]) if p else 0.0

    # ---- 每日决策 ----
    def step(self, cal, position):
        step_idx = cal.get_trade_step()
        t_start, t_end = cal.get_step_time(step_idx)
        today = pd.Timestamp(t_start).normalize()
        self.days_seen.append(today)
        i = len(self.days_seen) - 1                    # 窗口相对步数
        prev_day = self.days_seen[i - 1] if i >= 1 else None
        rk_prev = (self.rk.get(str(prev_day.date()))
                   if prev_day is not None else None)

        from qlib.backtest.decision import Order, TradeDecisionWO

        pos = position
        cash = float(pos.position.get("cash", 0.0))
        orders = []
        o_today = self._px(today, self.holding) if self.holding else float("nan")

        if self.holding is not None:
            c_sig = self._px(prev_day, self.holding, "$close")
            do_rank = (self.exec_step is not None
                       and (i - 1) - self.exec_step >= self.min_hold)
            if (self.entry_px is not None and math.isfinite(c_sig)
                    and c_sig <= self.entry_px * (1 - self.stop_loss)):
                orders.append(Order(stock_id=self.holding,
                                    amount=self._held_amount(pos),
                                    start_time=t_start, end_time=t_end,
                                    direction=Order.SELL))
                self.trades.append((today, "stop", self.holding, None, o_today))
                self.holding, self.entry_px, self.exec_step = None, None, None
                # 引擎阻塞信号日 S ≤ 止损执行日+cooldown；本策略在 T=S+1
                # 消费 S 榜 → 等价条件是 i ≥ 执行日+cooldown+2
                self.block_until = i + self.cooldown + 1
            elif do_rank:
                if rk_prev is None or rk_prev.empty:
                    orders.append(Order(stock_id=self.holding,
                                        amount=self._held_amount(pos),
                                        start_time=t_start, end_time=t_end,
                                        direction=Order.SELL))
                    self.trades.append((today, "exit", self.holding, None, o_today))
                    self.holding, self.entry_px, self.exec_step = None, None, None
                else:
                    top = list(rk_prev["concept"].head(self.topk))
                    if self.holding not in top:
                        new = top[0]
                        px_new = self._px(today, new)
                        held_amt = self._held_amount(pos)
                        est_cash = held_amt * o_today * (1 - self.cost)
                        amt_new = est_cash / px_new * (1 - 1e-9) if px_new > 0 else 0.0
                        orders.append(Order(stock_id=self.holding, amount=held_amt,
                                            start_time=t_start, end_time=t_end,
                                            direction=Order.SELL))
                        orders.append(Order(stock_id=new, amount=amt_new,
                                            start_time=t_start, end_time=t_end,
                                            direction=Order.BUY))
                        self.trades.append((today, "switch", self.holding, new, px_new))
                        self.holding = new
                        self.entry_px = self._px(today, new, "$close")
                        self.exec_step = i
        else:
            if i > self.block_until and rk_prev is not None and not rk_prev.empty:
                code = rk_prev["concept"].iloc[0]
                px = self._px(today, code)
                if px > 0:
                    orders.append(Order(stock_id=code,
                                        amount=cash / px * (1 - 1e-9),
                                        start_time=t_start, end_time=t_end,
                                        direction=Order.BUY))
                    self.trades.append((today, "entry", None, code, px))
                    self.holding = code
                    self.entry_px = self._px(today, code, "$close")
                    self.exec_step = i
        return TradeDecisionWO(orders, self.impl)


def _ref_key(csv: Path):
    df = pd.read_csv(csv)
    return [(str(pd.Timestamp(d).date()), t,
             None if pd.isna(f) else f, None if pd.isna(to) else to, float(p))
            for d, t, f, to, p in zip(df["date"], df["type"], df["from"],
                                      df["to"], df["price"])]


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    bridge = pd.read_parquet(root / "outputs/qlib_bridge/final_rank.parquet")
    bars = pd.read_parquet(root / "data/cache/daily_bars.parquet")
    codes = sorted(set(bridge["concept"]) | set(bridge["leader"].dropna()))
    quote = build_quote(bars[bars["symbol"].isin(codes)])
    trade_days = pd.DatetimeIndex(sorted(quote.index.get_level_values(0).unique()))

    report = ["# E-Gate 报告：ResonanceStrategy(qlib) ≡ 参考引擎（②段）", "",
              "对拍锚：逐笔成交（日期/类型/标的/价格 float32 相等）；账户终值"
              "列参考（换仓金额含 1e-9 估算边际）。", ""]
    n_pass = n_all = 0
    for wname, phases, end in MATRIX:
        for start in phases:
            for cost in COSTS:
                n_all += 1
                bench = pd.Series(0.0, index=trade_days)
                strat = ResonanceStrategy(bridge, quote, cost_bp=cost)
                pdf, exch, account = run_backtest(
                    quote, trade_days, bench, strat.impl, start, end,
                    codes=codes, deal_price="$open", cost_bp=cost)
                ref_csv = (root / "outputs/qlib_bridge/ref_runs/"
                           f"ref_{wname}_{start}_{int(cost)}bp_trades.csv")
                # 价格按 float32 精度比较（ref 经 CSV 往返，十进制表示
                # 有尾位漂移；两端源头同为 float32 矩阵）
                got = [(str(pd.Timestamp(d).date()), t, f, to, np.float32(p))
                       for d, t, f, to, p in strat.trades]
                ref = None
                if ref_csv.exists():
                    ref = [(d, t, f, to, np.float32(p))
                           for d, t, f, to, p in _ref_key(ref_csv)]
                same = ref is not None and got == ref
                n_pass += same
                final_v = float(account.current_position.position.get(
                    "now_account_value", float("nan")))
                report.append(
                    f"- {wname}/{start}/{int(cost)}bp: {'✅' if same else '❌'} "
                    f"逐笔一致={same}（qlib {len(got)} vs ref "
                    f"{len(ref) if ref else 'NA'} 笔；账户终值 {final_v:,.0f}）")
    report += ["", f"**E-Gate 逐笔门：{n_pass}/{n_all}**"
               + ("——②段等价成立" if n_pass == n_all else "——存在不一致，须排查")]
    out = root / "outputs/qlib_bridge/e_gate_report.md"
    out.write_text("\n".join(report))
    print(f"[E-Gate] {n_pass}/{n_all}；报告 → {out}")
    return 0 if n_pass == n_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
