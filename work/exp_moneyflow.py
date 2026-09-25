"""moneyflow-gate 实验（docs/moneyflow-gate-plan.md §四修订版）。

分阶段：
    stage=rank   本地提取窗内逐日「日线 Top5」（V4.3 三锚栈信号层）→ 导出并集码表；
    stage=event  构建资金特征 → 事件研究（Top1 未来5日收益 × 资金分组）；
    stage=back   回测臂 base/gateA/gateB/A∧B × τ 网格 × 双窗口 5 相位 → 判据表。

用法：
    conda run -n resonance python work/exp_moneyflow.py rank
    conda run -n resonance python work/exp_moneyflow.py event
    conda run -n resonance python work/exp_moneyflow.py back
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.backtest import perf_stats  # noqa: E402
from resonance.v3 import MinuteBarProvider, V3Backtester, V3Params, yearly_returns  # noqa: E402
from work.backtest_v3 import load_wide  # noqa: E402

OUT_DIR = config.OUTPUTS_DIR / "exp_moneyflow"
CACHE = config.CACHE_DIR

# V4.3 生产栈（冻结）：三锚 + V4.2 全参数
V43_PARAMS = dict(topk=3, daily_top=5, hl_source="leader", minute_bars=24,
                  stop_mode="close", exec_lag=1)
POOL = list(config.V43_ANCHOR_POOL)
END = "2026-09-18"
FLOW_START = pd.Timestamp("2025-09-25")          # 接口滚动 1 年最早 bar 日
WIN_MAIN = ("2025-09-29", ["2025-09-29", "2025-09-30", "2025-10-09", "2025-10-10", "2025-10-13"])
WIN_REF = ("2025-09-22", ["2025-09-22", "2025-09-23", "2025-09-24", "2025-09-25", "2025-09-26"])
TAUS = [-0.01, -0.005, 0.0, 0.005, 0.01]        # τ 网格（off=对照臂）
COSTS = (10.0, 0.0, 30.0)


def build_provider() -> MinuteBarProvider:
    m5 = pd.read_parquet(CACHE / "minute5_bars.parquet")
    m5["datetime"] = pd.to_datetime(m5["datetime"])
    return MinuteBarProvider(m5.pivot(index="datetime", columns="symbol", values="close").sort_index())


# ---------------------------------------------------------------- 排名提取 --
def daily_rankings(close, concepts, prov):
    """窗内逐日（V4.3 信号层）日线 Top5 + 最终榜（分钟重排）→ 长表。

    排名是数据 ≤T 的确定函数，与回测路径无关（相位/持仓不影响榜单）。
    """
    bt = V3Backtester(close, concepts, broad_codes=POOL, params=V3Params(**V43_PARAMS),
                      minute_bars_provider=prov)
    cal = close.index
    s = cal.searchsorted(pd.Timestamp("2025-09-22"))
    e = cal.searchsorted(pd.Timestamp(END), side="right")
    rows = []
    st: dict = {}
    for i in range(s, e):
        date = cal[i]
        daily = bt.sig.ranking(i)
        leader = bt.broad[bt.sig.leader_idx[i]] if bt.sig.has_leader[i] else None
        final = bt._final_ranking(i, date, st)
        for rank, c in enumerate(daily["concept"].head(5), start=1):
            rows.append({"date": date, "leader": leader, "kind": "daily",
                         "rank": rank, "concept": c})
        for rank, c in enumerate(final["concept"].head(5), start=1):
            rows.append({"date": date, "leader": leader, "kind": "final",
                         "rank": rank, "concept": c})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 资金特征 --
def concept_flow_features(path=CACHE / "moneyflow_concept.parquet"):
    """概念/指数 60min 金额 → 日频 net_big / gross_big / g3 长表。"""
    df = pd.read_parquet(path)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df["date"] = df["datetime"].dt.normalize()
    g = df.groupby(["symbol", "date"]).agg(
        buy=("active_buy_large_amt", "sum"),
        buy2=("active_buy_main_amt", "sum"),
        sell=("active_sell_large_amt", "sum"),
        sell2=("active_sell_main_amt", "sum"),
    ).reset_index()
    g["net_big"] = (g["buy"] + g["buy2"] - g["sell"] - g["sell2"])
    g["gross_big"] = (g["buy"] + g["buy2"] + g["sell"] + g["sell2"]) / 2.0
    g["g1"] = g["net_big"] / g["gross_big"].replace(0, np.nan)
    g = g.sort_values(["symbol", "date"]).reset_index(drop=True)
    g["net3"] = g.groupby("symbol")["net_big"].transform(
        lambda s: s.rolling(3, min_periods=3).sum())
    g["gross3"] = g.groupby("symbol")["gross_big"].transform(
        lambda s: s.rolling(3, min_periods=3).sum())
    g["g3"] = g["net3"] / g["gross3"].replace(0, np.nan)
    return g


def gate_series(features: pd.DataFrame, rank_top1: pd.DataFrame, key: str,
                tau: float, cal: pd.DatetimeIndex) -> pd.Series:
    """按日构建 entry_gate：key='top1'（概念自身 g3）或 'allA'（全A g3）。

    数据缺失日直通（True），由引擎计 gate_missing_days。
    """
    if key == "allA":
        f = features[features["symbol"] == "883957.TI"].set_index("date")["g3"]
        gate = pd.Series(True, index=cal)
        for d in cal:
            if d in f.index and pd.notna(f.loc[d]):
                gate.loc[d] = bool(f.loc[d] > tau)
        return gate
    # top1：每日最终榜第 1 名概念的 g3
    piv = features.pivot(index="date", columns="symbol", values="g3")
    gate = pd.Series(True, index=cal)
    for _, row in rank_top1.iterrows():
        d = pd.Timestamp(row["date"]).normalize()
        if d in piv.index and row["concept"] in piv.columns:
            v = piv.loc[d, row["concept"]]
            if pd.notna(v):
                gate.loc[d] = bool(v > tau)
    return gate


# ---------------------------------------------------------------- 主入口 --
def main(stage: str) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    close, concepts = load_wide()
    prov = build_provider()

    if stage == "rank":
        rk = daily_rankings(close, concepts, prov)
        rk.to_parquet(OUT_DIR / "daily_rankings.parquet", index=False)
        union = sorted(set(rk.loc[rk["kind"] == "daily", "concept"]))
        top1u = sorted(set(rk.loc[(rk["kind"] == "final") & (rk["rank"] == 1), "concept"]))
        pd.Series(union, name="code").to_csv(OUT_DIR / "flow_codes_daily_top5.csv", index=False)
        n_days = rk[rk["kind"] == "daily"]["date"].nunique()
        print(f"[rank] 榜单日 {n_days}；日线Top5并集 {len(union)} 码；最终Top1并集 {len(top1u)} 码")
        print(f"[rank] 并集已导出 {OUT_DIR / 'flow_codes_daily_top5.csv'}")
        return 0

    if stage == "event":
        run_event(close, concepts, prov)
        return 0

    if stage == "back":
        run_back(close, concepts, prov)
        return 0

    if stage == "r2":
        run_r2(close, concepts, prov)
        return 0

    if stage == "r3":
        run_r3(close, concepts, prov)
        return 0

    if stage == "winrate":
        run_winrate(close, concepts, prov)
        return 0

    if stage == "p1p2":
        run_p1p2(close, concepts, prov)
        return 0

    if stage == "null":
        run_null(close, concepts, prov)
        return 0

    if stage == "r5":
        run_r5(close, concepts, prov)
        return 0

    raise SystemExit(f"未知 stage: {stage}")


def make_ret_post(close, window: int, q: int):
    """R5：最终榜按 w 日收益降级最差 q 个（healthy-first）。"""
    codes_all = close.columns
    retw = close[codes_all] / close[codes_all].shift(window) - 1.0

    def f(rk, date):
        d = pd.Timestamp(date).normalize()
        if d not in retw.index:
            return rk
        row = retw.loc[d]
        vals = {}
        for c in rk["concept"]:
            if c in row.index and pd.notna(row[c]):
                vals[c] = float(row[c])
        if len(vals) <= q:
            return rk
        worst = set(sorted(vals, key=vals.get)[:q])
        rk = rk.copy()
        rk["_h"] = [c not in worst for c in rk["concept"]]
        rk["_r"] = np.arange(len(rk))
        return rk.sort_values(["_h", "_r"], ascending=[False, True]) \
                .drop(columns=["_h", "_r"]).reset_index(drop=True)
    return f


def run_r5(close, concepts, prov) -> None:
    """R5：w∈{2,3,5}×q=2 + w=3×q=1，判据见 plan §八.4。"""
    from resonance.v3 import V3Backtester

    def bt_run(post, start, cost):
        bt = V3Backtester(close, concepts, broad_codes=POOL,
                          params=V3Params(**V43_PARAMS).with_(cost_bp=cost),
                          minute_bars_provider=prov, post_rank=post)
        return bt.run(start, END)

    def perf(out):
        st = perf_stats(out["nav_curve"])
        yr = yearly_returns(out["nav_curve"])
        return dict(total=st["total_return"], dd=st["max_drawdown"], sharpe=st["sharpe"],
                    y2025=yr.get("2025", np.nan), y2026=yr.get("2026", np.nan),
                    changes=out["stats"]["position_changes"],
                    stops=out["stats"]["stop_count"])

    arms = [("base", None, None)] + [
        (f"W{w}_q{q}", w, q) for w, q in ((2, 2), (3, 2), (5, 2), (3, 1))]
    rows = []
    for win_name, (start0, phases) in (("主窗", WIN_MAIN), ("副窗", WIN_REF)):
        for label, w, q in arms:
            post = make_ret_post(close, w, q) if w else None
            for cost in COSTS:
                per = [perf(bt_run(post, s, cost)) for s in phases]
                mm = pd.DataFrame(per).median()
                rows.append({"win": win_name, "arm": label, "cost": f"{int(cost)}bp", **mm.to_dict()})
    tab = pd.DataFrame(rows)
    tab.to_csv(OUT_DIR / "r5_grid.csv", index=False)
    for win_name in ("主窗", "副窗"):
        sub = tab[(tab["win"] == win_name) & (tab["cost"] == "10bp")]
        print(f"\n===== R5 {win_name}（10bp，5 相位中位）=====")
        print(sub.drop(columns=["win", "cost"]).to_string(index=False, float_format=lambda x: f"{x:+.3f}"))
    sub = tab[(tab["win"] == "主窗") & (tab["cost"] == "10bp")]
    base = sub[sub["arm"] == "base"].iloc[0]
    d = sub[sub["arm"] != "base"].copy()
    d["Δtotal"] = d["total"] - base["total"]
    d["Δdd"] = d["dd"] - base["dd"]
    d.to_csv(OUT_DIR / "r5_judgement.csv", index=False)
    print("\n===== R5 判据 Δ（主窗 10bp）=====")
    print(d[["arm", "Δtotal", "Δdd", "changes", "stops"]].to_string(index=False, float_format=lambda x: f"{x:+.4f}"))

    # 机制诊断：QM(W3_q2) 与 S+2.0 交易序列重合度（主窗相位1）
    F = build_p1p2_features(close)
    piv_g3 = F.pivot(index="date", columns="symbol", values="g3")
    t_qm = bt_run(make_ret_post(close, 3, 2), WIN_MAIN[1][0], 10.0)["trades"]
    t_s2 = bt_run(make_flow_post(piv_g3, 0.02), WIN_MAIN[1][0], 10.0)["trades"]
    same = sum(1 for _, a in t_qm.iterrows()
               if ((t_s2["date"] == a["date"]) & (t_s2["type"] == a["type"])
                   & (t_s2["to"] == a["to"])).any())
    print(f"\n机制诊断：QM(W3_q2) 与 S+2.0 交易序列重合 {same}/{len(t_qm)}")


def run_null(close, concepts, prov) -> None:
    """置换零假设（plan §八.3）：池内打乱特征值 → 同规则 Δ 零分布。

    两个家族各 16 种子：tau 制（S+2.0 规则）与分位制（q=2 规则）。
    """
    import random as _random
    from resonance.v3 import V3Backtester

    F = build_p1p2_features(close)
    rk = pd.read_parquet(OUT_DIR / "daily_rankings.parquet")
    fin = rk[rk["kind"] == "final"][["date", "concept"]]
    day_pool = fin.groupby("date")["concept"].apply(list).to_dict()
    piv_g3 = F.pivot(index="date", columns="symbol", values="g3")

    def shuffled_piv(seed):
        rng = _random.Random(seed)
        rows = []
        for d, pool in day_pool.items():
            vals = [piv_g3.loc[d, c] if (d in piv_g3.index and c in piv_g3.columns) else np.nan
                    for c in pool]
            keep = [v for v in vals if pd.notna(v)]
            rng.shuffle(keep)
            it = iter(keep)
            for c, v in zip(pool, vals):
                rows.append({"date": d, "symbol": c,
                             "g3": next(it) if pd.notna(v) else np.nan})
        return pd.DataFrame(rows).pivot(index="date", columns="symbol", values="g3")

    def bt_total(post, start="2025-09-29", cost=10.0):
        bt = V3Backtester(close, concepts, broad_codes=POOL,
                          params=V3Params(**V43_PARAMS).with_(cost_bp=cost),
                          minute_bars_provider=prov, post_rank=post)
        return perf_stats(bt.run(start, END)["nav_curve"])["total_return"]

    base_t = bt_total(None)
    print(f"base total = {base_t:+.3f}")
    out_rows = []
    for family in ("tau2", "q2"):
        deltas = []
        for seed in range(16):
            sp = shuffled_piv(seed * 7 + 1)
            if family == "tau2":
                post = make_flow_post(sp, 0.02)
            else:
                # 分位制的随机版：随机特征值（长表直传，make_quantile_post 内部自透视）
                rnd = shuffled_piv(seed * 13 + 3).stack().rename("rnd").reset_index()
                rnd.columns = ["date", "symbol", "rnd"]
                post = make_quantile_post(rnd, "rnd", 2)
            t = bt_total(post)
            deltas.append(t - base_t)
            out_rows.append({"family": family, "seed": seed, "delta": t - base_t})
        d = pd.Series(deltas)
        print(f"[{family}] 置换 Δ 零分布: 均值{d.mean():+.3f} 中位{d.median():+.3f} "
              f"min{d.min():+.3f} max{d.max():+.3f} P90{d.quantile(.9):+.3f}")
    pd.DataFrame(out_rows).to_csv(OUT_DIR / "null_dist.csv", index=False)
    # 真实 S+2.0 对照
    real = bt_total(make_flow_post(piv_g3, 0.02))
    real_q2 = bt_total(make_quantile_post(F, "g3", 2))
    print(f"真实 Δ：tau制 S+2.0 = {real - base_t:+.3f} | 分位制 QF_q2 = {real_q2 - base_t:+.3f}")


def build_p1p2_features(close, rank_path=None):
    """R4 特征表：(date, concept) → g3 / ret3 / resid3 / xl3。

    resid3：扩展窗 pooled OLS（g3 ~ a + b·ret3，仅用日期 < T 样本拟合）。
    """
    feats = concept_flow_features()
    # xl3：特大单净占比（concept_flow_features 已含 buy/sell 分档合计的原料）
    df = pd.read_parquet(CACHE / "moneyflow_concept.parquet")
    df["datetime"] = pd.to_datetime(df["datetime"])
    df["date"] = df["datetime"].dt.normalize()
    gg = df.groupby(["symbol", "date"]).agg(
        b_l=("active_buy_large_amt", "sum"), s_l=("active_sell_large_amt", "sum"),
    ).reset_index()
    gg["net_xl"] = gg["b_l"] - gg["s_l"]
    gg["gross_xl"] = (gg["b_l"] + gg["s_l"]) / 2.0
    gg = gg.sort_values(["symbol", "date"])
    gg["net_xl3"] = gg.groupby("symbol")["net_xl"].transform(
        lambda s: s.rolling(3, min_periods=3).sum())
    gg["gross_xl3"] = gg.groupby("symbol")["gross_xl"].transform(
        lambda s: s.rolling(3, min_periods=3).sum())
    gg["xl3"] = gg["net_xl3"] / gg["gross_xl3"].replace(0, np.nan)

    g3 = feats[["symbol", "date", "g3"]]
    xl = gg[["symbol", "date", "xl3"]]
    # ret3：概念 3 日复合收益（显式长表，避免 MultiIndex 层级名对齐问题）
    codes = sorted(set(feats["symbol"]))
    r3w = close[codes] / close[codes].shift(3) - 1.0
    ret3 = r3w.stack().reset_index()
    ret3.columns = ["date", "symbol", "ret3"]
    F = g3.merge(xl, on=["symbol", "date"], how="outer") \
          .merge(ret3, on=["symbol", "date"], how="outer")
    F = F.dropna(subset=["g3", "ret3"]).sort_values("date").reset_index(drop=True)
    # 扩展窗残差（仅过去样本拟合；前缀和 O(n) 实现）
    X = F["ret3"].to_numpy(dtype=float)
    Y = F["g3"].to_numpy(dtype=float)
    dates_arr = F["date"].to_numpy()
    n_less = np.searchsorted(dates_arr, dates_arr, side="left")  # 每行：日期严格小于该行的样本数
    cx = np.cumsum(X); cy = np.cumsum(Y); cxx = np.cumsum(X * X); cxy = np.cumsum(X * Y)
    resid = np.full(len(F), np.nan)
    for i in range(len(F)):
        n = int(n_less[i])
        if n < 40:
            continue
        sx, sy, sxx, sxy = cx[n - 1], cy[n - 1], cxx[n - 1], cxy[n - 1]
        xm, ym = sx / n, sy / n
        denom = sxx - n * xm * xm
        if denom <= 1e-18:
            continue
        b = (sxy - n * xm * ym) / denom
        a = ym - b * xm
        resid[i] = Y[i] - (a + b * X[i])
    F["resid3"] = resid
    return F


def make_quantile_post(ftable: pd.DataFrame, feat: str, q: int):
    """R4 分位重排 post_rank：有特征候选中特征最差 q 个降级（healthy-first）。"""
    piv = ftable.pivot(index="date", columns="symbol", values=feat)

    def f(rk, date):
        d = pd.Timestamp(date).normalize()
        vals = {}
        for c in rk["concept"]:
            v = piv.loc[d, c] if (d in piv.index and c in piv.columns) else np.nan
            if pd.notna(v):
                vals[c] = float(v)
        if len(vals) <= q:
            return rk
        worst = set(sorted(vals, key=vals.get)[:q])
        rk = rk.copy()
        rk["_h"] = [c not in worst for c in rk["concept"]]
        rk["_r"] = np.arange(len(rk))
        return rk.sort_values(["_h", "_r"], ascending=[False, True]) \
                .drop(columns=["_h", "_r"]).reset_index(drop=True)
    return f


def run_p1p2(close, concepts, prov) -> None:
    """R4：QF/QR/QM/QX × q∈{1,2}（plan §八.2 预注册，全臂上报）。"""
    F = build_p1p2_features(close)
    # 特征间相关结构（事件研究补充）
    rk = pd.read_parquet(OUT_DIR / "daily_rankings.parquet")
    top5 = rk[rk["kind"] == "daily"][["date", "concept"]]
    m = top5.merge(F, left_on=["concept", "date"], right_on=["symbol", "date"],
                   how="inner")
    corrs = m.groupby("date").apply(
        lambda g: g["g3"].corr(g["ret3"], method="spearman")
        if g["g3"].nunique() > 2 and g["ret3"].nunique() > 2 else np.nan).dropna()
    print(f"日度 Top5 内 Spearman(g3,ret3)：中位 {corrs.median():+.2f} "
          f"(P25 {corrs.quantile(.25):+.2f}, P75 {corrs.quantile(.75):+.2f}, n={len(corrs)}日)")
    fw = {}
    cal = close.index
    for _, row in m.iterrows():
        d = pd.Timestamp(row["date"]).normalize()
        i = cal.searchsorted(d)
        if i + 5 < len(cal) and row["concept"] in close.columns:
            c1, c5 = close.iloc[i + 1][row["concept"]], close.iloc[i + 5][row["concept"]]
            if pd.notna(c1) and pd.notna(c5) and c1 > 0:
                fw[(row["concept"], d)] = c5 / c1 - 1.0
    m["fwd5"] = [fw.get((c, pd.Timestamp(d).normalize()), np.nan)
                 for c, d in zip(m["concept"], m["date"])]
    for feat, name in (("g3", "g3"), ("ret3", "ret3"), ("resid3", "resid3"), ("xl3", "xl3")):
        v = m[feat]
        ok = v.notna() & m["fwd5"].notna()
        # 按特征>0/<0 分组（g3/xl3/resid3 的 0 有意义；ret3 用中位）
        thr = 0.0 if feat != "ret3" else float(m.loc[ok, feat].median())
        a = m.loc[ok & (m[feat] > thr), "fwd5"]
        b = m.loc[ok & (m[feat] <= thr), "fwd5"]
        print(f"[事件研究] {name}: >{'中位' if thr else 0} n={len(a)} 均值{a.mean():+.4f} 胜率{(a>0).mean():.1%}"
              f" | ≤ n={len(b)} 均值{b.mean():+.4f} 胜率{(b>0).mean():.1%} | 差{a.mean()-b.mean():+.4f}")

    from resonance.v3 import V3Backtester

    def bt_run(post, start, cost):
        bt = V3Backtester(close, concepts, broad_codes=POOL,
                          params=V3Params(**V43_PARAMS).with_(cost_bp=cost),
                          minute_bars_provider=prov, post_rank=post)
        return bt.run(start, END)

    def perf(out):
        st = perf_stats(out["nav_curve"])
        yr = yearly_returns(out["nav_curve"])
        return dict(total=st["total_return"], dd=st["max_drawdown"], sharpe=st["sharpe"],
                    y2025=yr.get("2025", np.nan), y2026=yr.get("2026", np.nan),
                    changes=out["stats"]["position_changes"],
                    stops=out["stats"]["stop_count"])

    arms = [("base", None, None)]
    for feat, name in (("g3", "QF"), ("resid3", "QR"), ("ret3", "QM"), ("xl3", "QX")):
        for q in (1, 2):
            arms.append((f"{name}_q{q}", feat, q))
    rows = []
    for win_name, (start0, phases) in (("主窗", WIN_MAIN), ("副窗", WIN_REF)):
        for label, feat, q in arms:
            post = make_quantile_post(F, feat, q) if feat else None
            for cost in COSTS:
                per = [perf(bt_run(post, s, cost)) for s in phases]
                mm = pd.DataFrame(per).median()
                rows.append({"win": win_name, "arm": label, "cost": f"{int(cost)}bp", **mm.to_dict()})
    tab = pd.DataFrame(rows)
    tab.to_csv(OUT_DIR / "r4_grid.csv", index=False)
    for win_name in ("主窗", "副窗"):
        sub = tab[(tab["win"] == win_name) & (tab["cost"] == "10bp")]
        print(f"\n===== R4 {win_name}（10bp，5 相位中位）=====")
        print(sub.drop(columns=["win", "cost"]).to_string(index=False, float_format=lambda x: f"{x:+.3f}"))
    sub = tab[(tab["win"] == "主窗") & (tab["cost"] == "10bp")]
    base = sub[sub["arm"] == "base"].iloc[0]
    d = sub[sub["arm"] != "base"].copy()
    d["Δtotal"] = d["total"] - base["total"]
    d["Δdd"] = d["dd"] - base["dd"]
    d.to_csv(OUT_DIR / "r4_judgement.csv", index=False)
    print("\n===== R4 判据 Δ（主窗 10bp）=====")
    print(d[["arm", "Δtotal", "Δdd", "changes", "stops"]].to_string(index=False, float_format=lambda x: f"{x:+.4f}"))


def make_flow_post(piv, tau):
    """R3 选择面 post_rank：g3>τ 健康组稳定前置。"""
    def f(rk, date):
        d = pd.Timestamp(date).normalize()
        h = []
        for c in rk["concept"]:
            v = piv.loc[d, c] if (d in piv.index and c in piv.columns) else np.nan
            h.append(True if pd.isna(v) else bool(v > tau))
        rk = rk.copy()
        rk["_h"] = h
        rk["_r"] = np.arange(len(rk))
        return rk.sort_values(["_h", "_r"], ascending=[False, True]) \
                .drop(columns=["_h", "_r"]).reset_index(drop=True)
    return f


def _episodes(out: dict) -> pd.DataFrame:
    """持仓连续段 → 逐笔回合（收益取 nav 段比，含成本）。"""
    hold = out["holdings"]
    nav = out["nav_curve"]
    codes = hold["holding"].ffill().fillna("")
    runs, cur, d0 = [], None, None
    prev = ""
    for d, c in codes.items():
        if c != prev:
            if prev != "":
                runs.append((cur, d0, prev_d))
            cur, d0 = c, d
            prev = c
        prev_d = d
    if prev != "":
        runs.append((cur, d0, prev_d))
    rows = []
    cal = nav.index
    for code, d0, d1 in runs:
        i0 = cal.searchsorted(pd.Timestamp(d0))
        base_nav = nav.iloc[i0 - 1] if i0 > 0 else nav.iloc[0]
        rows.append({"concept": code, "start": d0, "end": d1,
                     "days": len(nav.loc[d0:d1]),
                     "ret": float(nav.loc[d1] / base_nav - 1.0)})
    return pd.DataFrame(rows)


def run_winrate(close, concepts, prov) -> None:
    """胜率视角：base vs S+2/S+2.5 的逐笔回合与逐持仓日（主窗 5 相位中位）。"""
    feats = concept_flow_features()
    piv = feats.pivot(index="date", columns="symbol", values="g3")
    from resonance.v3 import V3Backtester

    def bt_run(post, start):
        bt = V3Backtester(close, concepts, broad_codes=POOL,
                          params=V3Params(**V43_PARAMS).with_(cost_bp=10.0),
                          minute_bars_provider=prov, post_rank=post)
        return bt.run(start, END)

    arms = [("base", None), ("S+2.0", 0.02), ("S+2.5", 0.025)]
    rows = []
    for label, tau in arms:
        post = make_flow_post(piv, tau) if tau is not None else None
        ep_stats, day_stats, tots = [], [], []
        for s in WIN_MAIN[1]:
            out = bt_run(post, s)
            ep = _episodes(out)
            won = ep[ep["ret"] > 0]
            lost = ep[ep["ret"] <= 0]
            ep_stats.append({
                "n": len(ep), "win%": len(won) / len(ep) if len(ep) else np.nan,
                "avg_win": won["ret"].mean() if len(won) else np.nan,
                "avg_loss": lost["ret"].mean() if len(lost) else np.nan,
                "max_loss": ep["ret"].min(),
                "avg_days": ep["days"].mean(),
            })
            nav = out["nav_curve"]
            hold = out["holdings"]["holding"].notna()
            daily = nav.pct_change()[hold].dropna()
            day_stats.append({"held_days": len(daily),
                              "day_win%": (daily > 0).mean() if len(daily) else np.nan,
                              "day_median": daily.median()})
            st = perf_stats(nav)
            tots.append(st["total_return"])
        e = pd.DataFrame(ep_stats).median()
        d_ = pd.DataFrame(day_stats).median()
        rows.append({"arm": label, "rounds": e["n"], "胜率(回合)": e["win%"],
                     "均盈": e["avg_win"], "均亏": e["avg_loss"],
                     "盈亏比": abs(e["avg_win"] / e["avg_loss"]) if e["avg_loss"] else np.nan,
                     "最差单笔": e["max_loss"], "均持有日": e["avg_days"],
                     "持仓日": d_["held_days"], "胜率(日)": d_["day_win%"],
                     "日收益中位": d_["day_median"], "总收益": np.median(tots)})
    tab = pd.DataFrame(rows)
    tab.to_csv(OUT_DIR / "winrate.csv", index=False)
    print(tab.to_string(index=False, float_format=lambda x: f"{x:+.4f}"))
    # 逐笔明细（主窗相位1）导出
    for label, tau in arms:
        post = make_flow_post(piv, tau) if tau is not None else None
        ep = _episodes(bt_run(post, WIN_MAIN[1][0]))
        ep.to_csv(OUT_DIR / f"episodes_{label.replace('+','')}.csv", index=False)


def run_r3(close, concepts, prov) -> None:
    """R3 选择面（plan §R3 预注册）：最终榜按 g3>τ_s 稳定重排（健康组在前）。

    止损规则：任一 τ_s 中位 Δ≥+2pp 且改写决策 ≥8 例 → 候选；否则整线关闭。
    """
    feats = concept_flow_features()
    piv = feats.pivot(index="date", columns="symbol", values="g3")

    def make_post(tau):
        def f(rk, date):
            d = pd.Timestamp(date).normalize()
            h = []
            for c in rk["concept"]:
                v = piv.loc[d, c] if (d in piv.index and c in piv.columns) else np.nan
                h.append(True if pd.isna(v) else bool(v > tau))
            rk = rk.copy()
            rk["_h"] = h
            rk["_r"] = np.arange(len(rk))
            rk = rk.sort_values(["_h", "_r"], ascending=[False, True])
            return rk.drop(columns=["_h", "_r"]).reset_index(drop=True)
        return f

    def perf(out):
        st = perf_stats(out["nav_curve"])
        yr = yearly_returns(out["nav_curve"])
        s = out["stats"]
        return dict(total=st["total_return"], dd=st["max_drawdown"], sharpe=st["sharpe"],
                    y2025=yr.get("2025", np.nan), y2026=yr.get("2026", np.nan),
                    changes=s["position_changes"], stops=s["stop_count"])

    def bt_run(post, start, cost):
        bt = V3Backtester(close, concepts, broad_codes=POOL,
                          params=V3Params(**V43_PARAMS).with_(cost_bp=cost),
                          minute_bars_provider=prov, post_rank=post)
        return bt.run(start, END)

    rows = []
    for win_name, (start0, phases) in (("主窗", WIN_MAIN), ("副窗", WIN_REF)):
        for tau_label, tau in (("S0", 0.0), ("S+3", 0.03), ("S+5", 0.05)):
            for cost in COSTS:
                per = [perf(bt_run(make_post(tau) if tau is not None else None, s, cost))
                       for s in phases]
                m = pd.DataFrame(per).median()
                rows.append({"win": win_name, "arm": tau_label, "cost": f"{int(cost)}bp", **m.to_dict()})
        for cost in COSTS:  # base（与 R1/R2 同参重算，作同窗对照）
            per = [perf(bt_run(None, s, cost)) for s in phases]
            m = pd.DataFrame(per).median()
            rows.append({"win": win_name, "arm": "base", "cost": f"{int(cost)}bp", **m.to_dict()})
    tab = pd.DataFrame(rows)
    tab.to_csv(OUT_DIR / "r3_grid.csv", index=False)
    for win_name in ("主窗", "副窗"):
        sub = tab[(tab["win"] == win_name) & (tab["cost"] == "10bp")]
        print(f"\n===== R3 {win_name}（10bp，5 相位中位）=====")
        print(sub.drop(columns=["win", "cost"]).to_string(index=False, float_format=lambda x: f"{x:+.3f}"))
    sub = tab[(tab["win"] == "主窗") & (tab["cost"] == "10bp")]
    base = sub[sub["arm"] == "base"].iloc[0]
    d = sub[sub["arm"] != "base"].copy()
    d["Δtotal"] = d["total"] - base["total"]
    d["Δdd"] = d["dd"] - base["dd"]
    d.to_csv(OUT_DIR / "r3_judgement.csv", index=False)
    print("\n===== R3 判据 Δ（主窗 10bp）=====")
    print(d[["arm", "Δtotal", "Δdd", "changes", "stops"]].to_string(index=False, float_format=lambda x: f"{x:+.4f}"))
    # 改写决策计数（止损规则第二条件）：主窗相位1，入场/换仓目标 ≠ 基线的笔数
    b0 = bt_run(None, WIN_MAIN[1][0], 10.0)["trades"]
    for tau_label, tau in (("S0", 0.0), ("S+3", 0.03), ("S+5", 0.05)):
        t0 = bt_run(make_post(tau), WIN_MAIN[1][0], 10.0)["trades"]
        diffs = 0
        for _, tr in t0.iterrows():
            m = b0[(b0["date"] == tr["date"]) & (b0["type"] == tr["type"])]
            if len(m) == 0 or m.iloc[0]["to"] != tr["to"] or m.iloc[0]["from"] != tr["from"]:
                diffs += 1
        print(f"改写决策数（主窗相位1，{tau_label}）: {diffs}")


def run_r2(close, concepts, prov) -> None:
    """R2 hard gate（plan §R2 预注册）：检查日持有概念 g3 ≤ τ_h → 退现金。

    附 L1 挤占判别：flow_exit 触发日与基线同概念退出的间隔分布。
    """
    feats = concept_flow_features()
    piv = feats.pivot(index="date", columns="symbol", values="g3")
    piv = piv.reindex(close.index.normalize().union(piv.index)).reindex(columns=close.columns)
    cal = close.index
    grids = {f"H{int(t*1000)}": piv.le(t) & piv.notna() for t in (-0.02, -0.04, -0.06)}

    def perf(out):
        st = perf_stats(out["nav_curve"])
        yr = yearly_returns(out["nav_curve"])
        s = out["stats"]
        return dict(total=st["total_return"], dd=st["max_drawdown"], sharpe=st["sharpe"],
                    y2025=yr.get("2025", np.nan), y2026=yr.get("2026", np.nan),
                    changes=s["position_changes"], stops=s["stop_count"],
                    flow_exits=s.get("flow_exits", 0))

    def bt_run(grid, start, cost):
        bt = V3Backtester(close, concepts, broad_codes=POOL,
                          params=V3Params(**V43_PARAMS).with_(cost_bp=cost),
                          minute_bars_provider=prov, exit_grid=grid)
        return bt.run(start, END)

    rows, base_trades_by_win = [], {}
    for win_name, (start0, phases) in (("主窗", WIN_MAIN), ("副窗", WIN_REF)):
        base_runs = [bt_run(None, s, 10.0) for s in phases]
        base_trades_by_win[win_name] = base_runs
        for cost in COSTS:
            for label, grid in [(f"{c}@{int(cost)}bp", None) for c in ("base",)] + \
                    [(f"H{int(t*1000)}@{int(cost)}bp", grids[f"H{int(t*1000)}"])
                     for t in (-0.02, -0.04, -0.06)]:
                per = [perf(bt_run(grid, s, cost)) for s in phases]
                m = pd.DataFrame(per).median()
                rows.append({"win": win_name, "arm": label.split("@")[0], "cost": f"{int(cost)}bp",
                             **m.to_dict()})
    tab = pd.DataFrame(rows)
    tab.to_csv(OUT_DIR / "r2_grid.csv", index=False)
    for win_name in ("主窗", "副窗"):
        sub = tab[(tab["win"] == win_name) & (tab["cost"] == "10bp")]
        print(f"\n===== R2 {win_name}（10bp，5 相位中位）=====")
        print(sub.drop(columns=["win", "cost"]).to_string(index=False, float_format=lambda x: f"{x:+.3f}"))
    # Δ 主窗
    sub = tab[(tab["win"] == "主窗") & (tab["cost"] == "10bp")]
    base = sub[sub["arm"] == "base"].iloc[0]
    d = sub[sub["arm"] != "base"].copy()
    d["Δtotal"] = d["total"] - base["total"]
    d["Δdd"] = d["dd"] - base["dd"]
    d.to_csv(OUT_DIR / "r2_judgement.csv", index=False)
    print("\n===== R2 判据 Δ（主窗 10bp）=====")
    print(d[["arm", "Δtotal", "Δdd", "flow_exits", "stops", "changes"]].to_string(
        index=False, float_format=lambda x: f"{x:+.4f}"))

    # L1 挤占判别：主窗相位1 的 flow_exit 触发日 vs 基线同概念退出间隔
    h = bt_run(grids["H-20"], WIN_MAIN[1][0], 10.0)
    ft = h["trades"]
    fe = [t for _, t in ft.iterrows() if t.get("type") in ("exit", "stop")
          and t.get("reason", None) == "flow_exit"] if "reason" in ft.columns else []
    bt_runs = base_trades_by_win["主窗"]
    bt0 = bt_runs[0]
    bt0_sell = bt0["trades"][bt0["trades"]["type"].isin(["exit", "stop", "switch"])]
    gaps = []
    for t in fe:
        same = bt0_sell[(bt0_sell["from"] == t["from"]) & (bt0_sell["date"] >= t["date"])]
        if len(same):
            gap = (pd.Timestamp(same.iloc[0]["date"]) - pd.Timestamp(t["date"])).days
            gaps.append(gap)
    print(f"\nL1 挤占判别（主窗相位1，H-2%）：flow_exit {len(fe)} 次；"
          f"基线同概念随后退出间隔(日): {sorted(gaps)}（0=同日，已挤占）")


def run_event(close, concepts, prov) -> None:
    """事件研究：全候选日 Top1 未来5日收益 × {Top1 自身 g3, 全A g3, 情绪 g3} 分组。"""
    rk = pd.read_parquet(OUT_DIR / "daily_rankings.parquet")
    top1 = rk[(rk["kind"] == "final") & (rk["rank"] == 1)].copy()
    feats_c = concept_flow_features()
    feats_h = concept_flow_features(CACHE / "moneyflow_hourly.parquet")

    # 未来 5 日收益（Top1 概念，T+1 收盘可成交口径：T+1→T+5 持有收益）
    fw = {}
    cal = close.index
    for _, row in top1.iterrows():
        d = pd.Timestamp(row["date"]).normalize()
        i = cal.searchsorted(d)
        if i + 5 < len(cal) and row["concept"] in close.columns:
            c1, c5 = close.iloc[i + 1][row["concept"]], close.iloc[i + 5][row["concept"]]
            if pd.notna(c1) and pd.notna(c5) and c1 > 0:
                fw[d] = c5 / c1 - 1.0
    top1["fwd5"] = top1["date"].map(lambda d: fw.get(pd.Timestamp(d).normalize(), np.nan))

    piv_c = feats_c.pivot(index="date", columns="symbol", values="g3")
    for name, table, code in (("g3_top1", feats_c, None), ("g3_allA", feats_h, "883957.TI"),
                              ("g3_sent", feats_h, "883404.TI")):
        vals = []
        for _, row in top1.iterrows():
            d = pd.Timestamp(row["date"]).normalize()
            if code is not None:
                f = table[table["symbol"] == code].set_index("date")["g3"]
                vals.append(f.get(d, np.nan))
            else:
                if d in piv_c.index and row["concept"] in piv_c.columns:
                    vals.append(piv_c.loc[d, row["concept"]])
                else:
                    vals.append(np.nan)
        top1[name] = vals

    rows = []
    for col in ("g3_top1", "g3_allA", "g3_sent"):
        v = top1[col]
        ok = v.notna() & top1["fwd5"].notna()
        pos = top1.loc[ok & (v > 0), "fwd5"]
        neg = top1.loc[ok & (v <= 0), "fwd5"]
        rows.append({
            "指标": col, "样本": int(ok.sum()), "覆盖": f"{ok.mean():.0%}",
            "g3>0 样本": len(pos), "g3>0 均值": pos.mean(), "g3>0 中位": pos.median(),
            "g3>0 胜率": (pos > 0).mean() if len(pos) else np.nan,
            "g3≤0 样本": len(neg), "g3≤0 均值": neg.mean(), "g3≤0 中位": neg.median(),
            "g3≤0 胜率": (neg > 0).mean() if len(neg) else np.nan,
            "差(正−负)": (pos.mean() - neg.mean()) if len(pos) and len(neg) else np.nan,
        })
    tab = pd.DataFrame(rows)
    print(tab.to_string(index=False, float_format=lambda x: f"{x:+.4f}"))
    tab.to_csv(OUT_DIR / "event_study.csv", index=False)
    top1.drop(columns=[]).to_parquet(OUT_DIR / "event_rows.parquet", index=False)
    # 分位数参考（网格校准）
    for col in ("g3_top1", "g3_allA"):
        q = top1[col].dropna().quantile([0.1, 0.25, 0.5, 0.75, 0.9])
        print(f"\n{col} 分位:", {f"{k:.0%}": f"{v:+.3f}" for k, v in q.items()})


def run_back(close, concepts, prov) -> None:
    """回测臂 × τ × 窗口 × 相位（判据：主窗 5 相位中位 Δ vs base）。"""
    rk = pd.read_parquet(OUT_DIR / "daily_rankings.parquet")
    top1 = rk[(rk["kind"] == "final") & (rk["rank"] == 1)]
    feats_c = concept_flow_features()
    feats_h = concept_flow_features(CACHE / "moneyflow_hourly.parquet")
    cal = close.index

    def bt_run(gate, start, cost):
        bt = V3Backtester(close, concepts, broad_codes=POOL,
                          params=V3Params(**V43_PARAMS).with_(cost_bp=cost),
                          minute_bars_provider=prov, entry_gate=gate)
        return bt.run(start, END)

    def perf(out):
        st = perf_stats(out["nav_curve"])
        yr = yearly_returns(out["nav_curve"])
        s = out["stats"]
        return dict(total=st["total_return"], dd=st["max_drawdown"], sharpe=st["sharpe"],
                    y2025=yr.get("2025", np.nan), y2026=yr.get("2026", np.nan),
                    changes=s["position_changes"], stops=s["stop_count"],
                    blocked=s.get("entry_blocked_days", 0), missing=s.get("gate_missing_days", 0))

    gates: dict[tuple[str, float], pd.Series | None] = {("base", 0.0): None}
    for tau in TAUS:
        gates[("A", tau)] = gate_series(feats_c, top1, "top1", tau, cal)
        gates[("B", tau)] = gate_series(feats_h, top1, "allA", tau, cal)
        ga, gb = gates[("A", tau)], gates[("B", tau)]
        gates[("AB", tau)] = ga & gb

    rows = []
    for win_name, (start, phases) in (("主窗", WIN_MAIN), ("副窗", WIN_REF)):
        for (arm, tau), gate in gates.items():
            for cost in COSTS:
                per = [perf(bt_run(gate, s, cost)) for s in phases]
                df = pd.DataFrame(per)
                m = df.median()
                rows.append({"win": win_name, "arm": arm, "tau": tau,
                             "cost": f"{int(cost)}bp",
                             "total": m["total"], "dd": m["dd"], "sharpe": m["sharpe"],
                             "y2025": m["y2025"], "y2026": m["y2026"],
                             "changes": m["changes"], "stops": m["stops"],
                             "blocked": m["blocked"], "missing": m["missing"]})
        # 只打 10bp 汇总
    tab = pd.DataFrame(rows)
    tab.to_csv(OUT_DIR / "backtest_grid.csv", index=False)
    for win_name in ("主窗", "副窗"):
        sub = tab[(tab["win"] == win_name) & (tab["cost"] == "10bp")]
        print(f"\n===== {win_name}（10bp，5 相位中位）=====")
        print(sub.drop(columns=["win", "cost"]).to_string(
            index=False, float_format=lambda x: f"{x:+.3f}"))
    # Δ vs base（主窗 10bp 判据表）
    sub = tab[(tab["win"] == "主窗") & (tab["cost"] == "10bp")]
    base = sub[sub["arm"] == "base"].iloc[0]
    sub = sub[sub["arm"] != "base"].copy()
    sub["Δtotal"] = sub["total"] - base["total"]
    sub["Δdd"] = sub["dd"] - base["dd"]
    sub.to_csv(OUT_DIR / "judgement_table.csv", index=False)
    print("\n===== 判据 Δ（主窗 10bp）=====")
    print(sub[["arm", "tau", "Δtotal", "Δdd", "blocked", "missing"]].to_string(
        index=False, float_format=lambda x: f"{x:+.4f}"))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "rank"))
