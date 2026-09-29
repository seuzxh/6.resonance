"""rank 因子族探索（docs/research/rank-factors-plan.md 预注册，2026-09-29）。

13 条冻结因子（9 新信息 + 4 对照）：截面位次 / 时序分位 / 位次迁移。
日频、纯 pandas（resonance env 即可跑，不经 qlib 表达式引擎；评价复用
qlib_pipeline 的 daily_rank_ic/SEGMENTS——纯 pandas 依赖）。

用法：conda run -n resonance python research/qlib_route_a/qlib_explore_rank.py
产出：outputs/qlib_ml/exploratory_rank.md + rank_ic.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from resonance import config
from research.qlib_route_a.qlib_pipeline import SEGMENTS, daily_rank_ic
from research.qlib_route_a.qlib_provider import ParquetData

START, END = "2025-09-26", "2026-09-23"   # 与 alpha158 扫描同窗（跳过头 4 日）


def xr(df: pd.DataFrame) -> pd.DataFrame:
    """当日截面百分位位次（全概念池内，平均法并列）。"""
    return df.rank(axis=1, pct=True)


def tsr(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """rolling(n) 时序分位（自身历史内的百分位）。"""
    return df.rolling(n).rank(pct=True)


def to_long(df: pd.DataFrame, name: str) -> pd.Series:
    s = df.stack(future_stack=True).rename(name)
    s.index.names = ["datetime", "instrument"]
    return s


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    data = ParquetData.from_cache_dir(root / "data" / "cache")

    m5_close = data.wide("5min", "close")
    concepts = [c for c in data.pools["concept"]
                if c in m5_close.columns and m5_close[c].notna().any()]
    config.assert_no_retired(concepts, context="rank 因子 universe")
    print(f"[universe] 5min 覆盖概念 {len(concepts)}；窗 {START}~{END}")

    close = data.wide("day", "close")[concepts]
    amount = data.wide("day", "amount")[concepts]

    mom5, mom10, mom20 = (close.pct_change(k) for k in (5, 10, 20))
    vol20 = close.pct_change().rolling(20).std()
    amt20 = amount.rolling(20).mean()

    xr_mom5, xr_mom20, xr_mom10 = xr(mom5), xr(mom20), xr(mom10)
    factors: dict[str, pd.Series] = {
        # ---- 新信息族 ----
        "TSR_C20": to_long(tsr(close, 20), "TSR_C20"),
        "TSR_C60": to_long(tsr(close, 60), "TSR_C60"),
        "TSR_MOM10_60": to_long(tsr(mom10, 60), "TSR_MOM10_60"),
        "TSR_VOL20_60": to_long(tsr(vol20, 60), "TSR_VOL20_60"),
        "DR_MOM5": to_long(xr_mom5.diff(1), "DR_MOM5"),
        "DR_MOM20": to_long(xr_mom20.diff(1), "DR_MOM20"),
        "RANKGAP_20_5": to_long(xr_mom20 - xr_mom5, "RANKGAP_20_5"),
        # ---- 对照组（不计入多重比较候选）----
        "XR_MOM10": to_long(xr_mom10, "XR_MOM10"),
        "XR_VOL20": to_long(xr(vol20), "XR_VOL20"),
        "XR_AMT20": to_long(xr(amt20), "XR_AMT20"),
    }

    # score 位次族（桥文件；仅信号日非空 → 位次在当日候选集内）
    bridge = pd.read_parquet(root / "outputs" / "qlib_bridge" / "daily_factors.parquet")
    bridge["date"] = pd.to_datetime(bridge["date"])
    score = bridge.pivot_table(index="date", columns="concept", values="score")
    rank_col = bridge.pivot_table(index="date", columns="concept", values="rank")
    score = score.reindex(columns=concepts)
    xr_score = xr(score)
    factors["DR_SCORE"] = to_long(xr_score.diff(1), "DR_SCORE")
    factors["XR_GAP_SCORE_MOM5"] = to_long(xr_score - xr_mom5.reindex(
        columns=score.columns), "XR_GAP_SCORE_MOM5")
    factors["BR_RANK"] = to_long(rank_col.reindex(columns=concepts), "BR_RANK")

    # 标签：T+k 收盘对收盘（与 make_labels 同口径，纯 pandas 自算）
    labels = {}
    for k in (1, 2, 5):
        lab = close.shift(-k) / close - 1
        s = to_long(lab, f"LABEL{k}")
        labels[f"LABEL{k}"] = s

    # 组装与窗口切片（lookback 用全历史，这里才切窗）
    wide = pd.concat(factors, axis=1)
    full = wide.join(pd.DataFrame(labels), how="left").sort_index()
    full = full.loc[START:END]
    t0, t1 = SEGMENTS["test"]
    test = full.loc[t0:t1]

    rows = []
    for n in factors:
        cov = full[n].notna().mean()
        mF, tF, dF = daily_rank_ic(full[n], full["LABEL1"])
        mT, tT, dT = daily_rank_ic(test[n], test["LABEL1"])
        decay = [daily_rank_ic(full[n], full[f"LABEL{k}"])[0] for k in (1, 2, 5)]
        rows.append({"name": n, "cov": cov, "ic_full": mF, "t_full": tF,
                     "days_full": dF, "ic_test": mT, "t_test": tT,
                     "L1": decay[0], "L2": decay[1], "L5": decay[2]})
    tbl = pd.DataFrame(rows).sort_values("t_full", key=lambda s: s.abs(),
                                         ascending=False)
    out_csv = root / "outputs" / "qlib_ml" / "rank_ic.csv"
    tbl.to_csv(out_csv, index=False)

    # 对照校验（判据 3，2026-09-29 修订）：截面位次化不改变 Spearman IC
    # ——内部不变性校验。原「XR_MOM10≈ROC10」为预注册口径错误：alpha158
    # 扫描的 ROC10 是 5min 表达式（日末前 10 根 bar 动量），与日线 10 日
    # 动量不可比（plan §四.3 已同步修订）。
    checks = []
    xr10 = float(tbl[tbl["name"] == "XR_MOM10"]["ic_full"].iloc[0])
    raw10 = daily_rank_ic(to_long(mom10, "mom10").loc[START:END], full["LABEL1"])[0]
    checks.append(("XR_MOM10≈原始MOM10（不变性）", abs(xr10 - raw10) < 0.005,
                   f"xr={xr10:+.4f} raw={raw10:+.4f}"))
    score_ic = daily_rank_ic(to_long(score, "score").loc[START:END],
                             full["LABEL1"])[0]
    br_ic = float(tbl[tbl["name"] == "BR_RANK"]["ic_full"].iloc[0])
    checks.append(("BR_RANK≈−score", abs(br_ic + score_ic) < 0.005,
                   f"br={br_ic:+.4f} score={score_ic:+.4f}"))

    lines = [
        "# rank 因子族探索（预注册执行，rank-factors-plan.md）", "",
        f"universe：5min 覆盖概念 {len(concepts)}；全窗 {START}~{END} + test 段"
        f"（{t0}~{t1}）日频 Spearman Rank IC。", "多重比较：9 条新信息族 "
        "Bonferroni |t|≥2.89（3.5 严线并列）；对照组不计候选。样本内、"
        "单一 regime、幸存者目录（L3 上界措辞，不作采纳依据）。", "",
        "| 因子 | 覆盖率 | 全窗IC | 全窗t | test IC | test t | L1→L2→L5 | 过线 |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    new9 = {"TSR_C20", "TSR_C60", "TSR_MOM10_60", "TSR_VOL20_60", "DR_MOM5",
            "DR_MOM20", "RANKGAP_20_5", "DR_SCORE", "XR_GAP_SCORE_MOM5"}
    for _, r in tbl.iterrows():
        passed = ""
        if r["name"] in new9:
            ok = (abs(r["t_full"]) >= 2.89 and r["ic_test"] * r["ic_full"] > 0
                  and abs(r["t_test"]) >= 1.5 and r["L5"] * r["L1"] > 0)
            passed = "✅候选" if ok else "—"
        lines.append(
            f"| {r['name']}{'（对照）' if r['name'] not in new9 else ''} "
            f"| {r['cov']:.1%} | {r['ic_full']:+.4f} | {r['t_full']:+.2f} "
            f"| {r['ic_test']:+.4f} | {r['t_test']:+.2f} "
            f"| {r['L1']:+.3f}→{r['L2']:+.3f}→{r['L5']:+.3f} | {passed} |")
    lines += ["", "## 对照校验（判据 3）", ""]
    for name, ok, detail in checks:
        lines.append(f"- {'✅' if ok else '❌'} {name}：{detail}")
    out = root / "outputs" / "qlib_ml" / "exploratory_rank.md"
    out.write_text("\n".join(lines))
    print(tbl.to_string(index=False, float_format=lambda v: f"{v:+.3f}"))
    for name, ok, detail in checks:
        print(f"[{'OK' if ok else 'FAIL'}] {name}: {detail}")
    print(f"[OK] → {out} / {out_csv}")
    return 0 if all(ok for _, ok, _ in checks) else 2


if __name__ == "__main__":
    raise SystemExit(main())
