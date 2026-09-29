"""验证轨 P2：信号桥 + 参考引擎重跑 + ReplayBacktester（①段等价）。

三段等价链（docs/research/qlib-validation-plan.md §三）：
  V3Backtester（耦合引擎） ≡① ReplayBacktester（桥文件驱动） ≡② ResonanceStrategy

本模块（resonance env）：
- export_final_rank：逐日最终榜（含 V4.1 分钟重排层）落盘
  outputs/qlib_bridge/final_rank.parquet——①②两段的共同输入；
- ReplayBacktester：吃桥文件 + float32 close/open 矩阵，执行规则照抄
  V3Backtester 的 V4.4 开盘口径（逐条对照 v3.py run() 的 open 分支）；
- run_matrix：全矩阵（3 窗 × 5 相位 × {0,10,30}bp）跑参考引擎与重放，
  执行 G2 逐笔门，产物落 outputs/qlib_bridge/ref_runs/ 与 g2_report.md。

用法：conda run -n resonance python research/qlib_route_a/qlib_bridge_export.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from resonance import config
from resonance.v3 import MinuteBarProvider, V3Backtester, V3Params

STACK = dict(topk=3, daily_top=5, hl_source="leader")  # 冻结栈（spec §五 P3）
MATRIX = [
    ("FULL", ["2024-12-27", "2024-12-30", "2024-12-31", "2025-01-02", "2025-01-03"], "2026-09-18"),
    ("MWIN", ["2025-09-22", "2025-09-23", "2025-09-24", "2025-09-25", "2025-09-26"], "2026-09-18"),
    ("EXT", ["2024-12-27", "2024-12-30", "2024-12-31", "2025-01-02", "2025-01-03"], "2026-09-24"),
]
COSTS = (0.0, 10.0, 30.0)


def load_float32():
    bars = pd.read_parquet(config.CACHE_DIR / "daily_bars.parquet")
    catalog = pd.read_csv(config.DATA_DIR / "concept_catalog.csv")
    close_all = bars.pivot(index="date", columns="symbol", values="close").sort_index()
    open_all = bars.pivot(index="date", columns="symbol", values="open").sort_index()
    for w in (close_all, open_all):
        w.index = pd.to_datetime(w.index)
    close32 = close_all.astype(np.float32)
    open32 = open_all.astype(np.float32)
    concepts = [c for c in catalog["code"] if c in close32.columns]
    m5 = pd.read_parquet(config.CACHE_DIR / "minute5_bars.parquet")
    prov = MinuteBarProvider(m5.pivot(index="datetime", columns="symbol",
                                      values="close").sort_index())
    return close32, open32, concepts, prov


def export_final_rank(close32, concepts, prov) -> pd.DataFrame:
    """逐日最终榜（引擎内部同款调用：V3Signals + _final_ranking）。"""
    p = V3Params(**STACK, exec_price="open")
    bt = V3Backtester(close32, concepts, broad_codes=list(config.V43_ANCHOR_POOL),
                      params=p, minute_bars_provider=prov,
                      open_all=close32)  # open_all 仅构造校验用，榜不含执行
    rows, st = [], {}
    cal = close32.index
    for i in range(len(cal)):
        date = cal[i]
        leader = (bt.broad[bt.sig.leader_idx[i]]
                  if bt.sig.has_leader[i] else None)
        gate = bool(bt.sig.has_leader[i] and bt.sig.gate[i])
        rk = bt._final_ranking(i, date, st) if gate else pd.DataFrame()
        for rank, r in enumerate(rk.itertuples(index=False), start=1):
            rows.append({
                "date": str(pd.Timestamp(date).date()), "rank": rank,
                "concept": r.concept, "score": float(r.score),
                "sync": float(r.sync), "capture": float(r.capture),
                "leader": leader, "gate": gate,
                "half_life": float(bt.sig.hl[i]),
            })
    df = pd.DataFrame(rows)
    out = config.OUTPUTS_DIR / "qlib_bridge" / "final_rank.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    print(f"[bridge] {len(df)} 行 / {df['date'].nunique()} 信号日 → {out}")
    print(f"[bridge] 分钟层计数 {st}")
    return df


class ReplayBacktester:
    """①段：桥文件驱动的重放执行器（规则逐条对照 v3.py run() open 分支）。"""

    def __init__(self, close32, open32, bridge: pd.DataFrame,
                 topk: int = 3, min_hold: int = 3, stop_loss: float = 0.05,
                 cooldown: int = 1, cost_bp: float = 10.0):
        self.close = close32
        self.open = open32
        self.rk = {d: g.sort_values("rank") for d, g in bridge.groupby("date")}
        self.topk, self.min_hold = topk, min_hold
        self.stop_loss, self.cooldown, self.cost = stop_loss, cooldown, cost_bp / 1e4

    def run(self, start, end):
        cal = self.close.index
        s = cal.searchsorted(pd.Timestamp(start))
        e = len(cal) if end is None else cal.searchsorted(pd.Timestamp(end), side="right")
        close_np = self.close.to_numpy(dtype=np.float64)
        open_np = self.open.to_numpy(dtype=np.float64)
        cc = {c: j for j, c in enumerate(self.close.columns)}
        oc = {c: j for j, c in enumerate(self.open.columns)}

        def close_at(i, code):
            v = close_np[i, cc[code]]
            return float(v) if math.isfinite(v) else float("nan")

        def open_at(i, code):
            v = open_np[i, oc[code]]
            return float(v) if math.isfinite(v) else float("nan")

        nav, holding, entry_px, exec_i = 1.0, None, None, None
        pending, block_until = None, -10
        out, trades = {}, []

        def exec_pending(i, date):
            nonlocal nav, holding, entry_px, exec_i, pending, block_until
            act, pending = pending, None

            def overnight(code):
                nonlocal nav
                o, c_prev = open_at(i, code), close_at(i - 1, code)
                if math.isfinite(o) and math.isfinite(c_prev) and c_prev > 0:
                    nav *= o / c_prev

            if act[0] == "buy":
                px = open_at(i, act[1])
                if math.isfinite(px):
                    nav *= 1 - self.cost
                    trades.append((date, "entry", None, act[1], px, nav))
                    holding = act[1]
                    entry_px = close_at(i, act[1]) if math.isfinite(close_at(i, act[1])) else px
                    exec_i = i
            elif act[0] == "switch":
                px_new = open_at(i, act[2])
                if math.isfinite(px_new):
                    overnight(act[1])
                    nav *= (1 - self.cost) ** 2
                    trades.append((date, "switch", act[1], act[2], px_new, nav))
                    holding = act[2]
                    entry_px = close_at(i, act[2]) if math.isfinite(close_at(i, act[2])) else px_new
                    exec_i = i
            elif act[0] == "sell":
                overnight(act[1])
                nav *= 1 - self.cost
                typ = "stop" if act[3] == "stop" else "exit"
                if act[3] == "stop":
                    block_until = i + self.cooldown
                trades.append((date, typ, act[1], None, open_at(i, act[1]), nav))
                holding, entry_px, exec_i = None, None, None

        for i in range(s, e):
            date = cal[i]
            key = str(pd.Timestamp(date).date())
            # 1o) 开盘执行滞后信号
            if pending is not None:
                exec_pending(i, date)
            # 2o) 当日收盘估值（执行日 open→close，其余 close→close）
            if holding is not None and exec_i is not None and i >= exec_i:
                base = (open_at(i, holding) if i == exec_i
                        else close_at(i - 1, holding))
                c_i = close_at(i, holding)
                if math.isfinite(c_i) and math.isfinite(base) and base > 0:
                    nav *= c_i / base
            # 3) 今日收盘信号（消费桥文件当日榜）
            if holding is not None:
                c_i = close_at(i, holding)
                do_rank = exec_i is not None and i - exec_i >= self.min_hold
                if (entry_px is not None and math.isfinite(c_i)
                        and c_i <= entry_px * (1 - self.stop_loss)):
                    pending = ("sell", holding, None, "stop")
                elif do_rank:
                    rk = self.rk.get(key)
                    if rk is None or rk.empty:
                        pending = ("sell", holding, None, "exit")
                    else:
                        top = list(rk["concept"].head(self.topk))
                        if holding not in top:
                            pending = ("switch", holding, top[0], None)
            else:
                if i > block_until:
                    rk = self.rk.get(key)
                    if rk is not None and not rk.empty:
                        pending = ("buy", rk["concept"].iloc[0], None, None)
            out[date] = nav
        return {"nav_curve": pd.Series(out, name="nav"),
                "trades": pd.DataFrame(
                    trades, columns=["date", "type", "from", "to", "price", "nav"])}


def _trades_key(df: pd.DataFrame):
    return [(str(pd.Timestamp(r["date"]).date()), r["type"], r["from"], r["to"],
             float(r["price"])) for _, r in df.iterrows()]


def run_matrix(close32, open32, concepts, prov, bridge) -> int:
    out_dir = config.OUTPUTS_DIR / "qlib_bridge" / "ref_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = [
        "窗口代码：FULL=完整周期窗（2025-01-02 相位族→2026-09-18）、"
        "MWIN=分钟子窗（2025-09-22 相位族→2026-09-18）、EXT=延伸窗（FULL 起→2026-09-24）。", "",
"# G2 报告：ReplayBacktester ≡ V3Backtester（①段等价门）", "",
              "口径：float32 矩阵、V4.4 开盘成交、逐笔交易 100% 一致 + nav 终值"
              "相对差 ≤1e-9。", ""]
    n_pass = n_all = 0
    for wname, phases, end in MATRIX:
        for start in phases:
            for cost in COSTS:
                n_all += 1
                p = V3Params(**STACK, exec_price="open", cost_bp=cost)
                bt = V3Backtester(close32, concepts,
                                  broad_codes=list(config.V43_ANCHOR_POOL),
                                  params=p, minute_bars_provider=prov,
                                  open_all=open32)
                ref = bt.run(start, end)
                rp = ReplayBacktester(close32, open32, bridge, cost_bp=cost)
                rep = rp.run(start, end)
                same = _trades_key(ref["trades"]) == _trades_key(rep["trades"])
                nav_rel = abs(rep["nav_curve"].iloc[-1]
                              / ref["nav_curve"].iloc[-1] - 1)
                ok = same and nav_rel <= 1e-9
                n_pass += ok
                report.append(f"- {wname}/{start}/{int(cost)}bp: "
                              f"{'✅' if ok else '❌'} 逐笔一致={same} "
                              f"nav相对差={nav_rel:.2e} "
                              f"(ref终值 {ref['nav_curve'].iloc[-1]:.4f}, "
                              f"{len(ref['trades'])} 笔)")
                ref["trades"].assign(window=wname, start=start, cost=cost).to_csv(
                    out_dir / f"ref_{wname}_{start}_{int(cost)}bp_trades.csv",
                    index=False)
    report += ["", f"**G2 判定：{n_pass}/{n_all} 配置通过**"
               + ("（逐笔门全过，①段等价成立）" if n_pass == n_all else "（存在不一致，须修复后复跑）")]
    (config.OUTPUTS_DIR / "qlib_bridge" / "g2_report.md").write_text("\n".join(report))
    print(f"[G2] {n_pass}/{n_all} 通过；报告 → outputs/qlib_bridge/g2_report.md")
    return 0 if n_pass == n_all else 1


def main() -> int:
    close32, open32, concepts, prov = load_float32()
    print(f"[data] float32 矩阵 {close32.shape}；概念 {len(concepts)}")
    bridge = export_final_rank(close32, concepts, prov)
    return run_matrix(close32, open32, concepts, prov, bridge)


if __name__ == "__main__":
    raise SystemExit(main())
