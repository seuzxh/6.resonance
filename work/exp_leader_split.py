"""实验：动态锚·9池按领先指数拆解归因（2026-09-22 用户指令）。

预注册（playbook §五已过）：
- 问题：DYN9 动态锚整体仅排第 11（outputs/exp_anchor），拆开看——每个池
  成员的"领导期间"内，策略的共振选择质量如何？
- 归因口径：日 t 的领导指数 = V3Signals（日线栈参数）当日 leader_idx
  （regime 口径：持仓或空仓收益/成本都计入当日领导者的期间；gate 关闭日
  计 0 收益并单独计数）。锚点相位 2025-01-02→2026-09-18（日线栈相位快速
  收敛，检验力≈1 条路径，沿标注）。
- 判定指标（每成员）：领导天数/闸门通过日；期间策略累计收益 vs 领先指数
  自身同期收益 → **共振超额**（核心：概念选择跑赢被跟随者的幅度）；日均/
  胜率/最大单日损；log 贡献占比（占全期策略 log 收益）。
- 用途：池成员取舍证据（与固定锚实验交叉：成员的固定锚成绩 vs 其领导
  期间的动态表现差异 = 换锚损失的定位）。

用法：
    conda run -n resonance python work/exp_leader_split.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.backtest import perf_stats  # noqa: E402
from resonance.v3 import V3Backtester, V3Params, V3Signals  # noqa: E402
from work.backtest_v3 import load_wide  # noqa: E402

START, END = "2025-01-02", "2026-09-18"
BASE = dict(topk=3, daily_top=0, hl_source="leader", cost_bp=10.0)


def main() -> int:
    close_all, concepts = load_wide()
    pool = list(config.V41_BROAD_POOL)
    names = config.V41_BROAD_POOL

    bt = V3Backtester(close_all, concepts, broad_codes=pool, params=V3Params(**BASE))
    out = bt.run(START, END)
    nav = out["nav_curve"]
    st = perf_stats(nav)
    print(f"DYN9 全期（10bp）：{st['total_return']:+.2%} | 回撤 {st['max_drawdown']:.2%} | "
          f"夏普 {st['sharpe']:.2f}（换仓 {out['stats']['position_changes']}）")

    sig = bt.sig
    cal = close_all.index
    s0 = cal.searchsorted(pd.Timestamp(START))
    s1 = cal.searchsorted(pd.Timestamp(END), side="right")
    days = cal[s0:s1]

    rets = nav.pct_change().fillna(0.0)
    quanA = close_all["883957.TI"].pct_change().fillna(0.0)
    total_log = float(np.log1p(rets.loc[days].to_numpy()).sum())

    rows = []
    for k, code in enumerate(pool):
        mask = np.array([sig.has_leader[i] and pool[sig.leader_idx[i]] == code
                         for i in range(s0, s1)])
        n_days = int(mask.sum())
        if n_days == 0:
            continue
        d = days[mask]
        r = rets.loc[d].to_numpy()
        lead_r = close_all[code].pct_change().fillna(0.0).loc[d].to_numpy()
        gate_ok = sum(1 for i, m in zip(range(s0, s1), mask)
                      if m and sig.gate[i])
        strat_c = float(np.expm1(np.log1p(r).sum()))
        lead_c = float(np.expm1(np.log1p(lead_r).sum()))
        rows.append({
            "leader": names[code], "code": code,
            "领导日": n_days, f"闸门过": gate_ok,
            "策略期间收益": strat_c, "领先自身收益": lead_c,
            "共振超额": strat_c - lead_c,
            "日均": float(r.mean()), "胜率": float((r > 0).mean()),
            "最差日": float(r.min()),
            "log贡献占比": float(np.log1p(r).sum() / total_log) if total_log else np.nan,
        })
    df = pd.DataFrame(rows).sort_values("共振超额", ascending=False).reset_index(drop=True)
    pd.set_option("display.width", 200)
    print("\n== 按领先指数拆解（regime 口径，10bp，全窗）==")
    print(df.to_string(index=False, float_format=lambda v: f"{v:+,.3f}"))
    gate_blocked = int(sum(1 for i in range(s0, s1)
                           if sig.has_leader[i] and not sig.gate[i]))
    print(f"\n闸门关闭日（空仓主因之一）：{gate_blocked} | 全期交易日 {len(days)}")
    outdir = config.OUTPUTS_DIR / "exp_anchor"
    outdir.mkdir(parents=True, exist_ok=True)
    df.to_csv(outdir / "leader_split.csv", index=False)
    print(f"[OK] {outdir}/leader_split.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
