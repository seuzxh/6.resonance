"""探索性挖掘：Alpha158 因子库在 5min 数据上的有效性扫描（2026-09-29 用户指令）。

性质：**未预注册的探索性挖掘**——157 条 Alpha158 内置表达式（剔除 VWAP0：
本项目 5min avg_price 为成分股均价量级，禁作指数 vwap，见 data-inventory
§二.2）在 5min 频评估、按每日末 bar 采样为日频，对 T+1/T+2/T+5 概念收益
做日频 Spearman Rank IC。

多重比较纪律：157 因子 × α=0.05 期望约 8 个假阳性；报告 Bonferroni 阈值
（t ≥ 2.58→α≈0.01 单侧近似；157 检验 Bonferroni |t| 需 ≳3.5）。全部为
样本内、单一 regime、幸存者目录（L3 上界措辞，不作采纳依据）。

用法：conda run -n qlib python research/qlib_route_a/qlib_explore_alpha158.py
产出：outputs/qlib_ml/exploratory_alpha158.md + alpha158_ic.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from research.qlib_route_a.qlib_pipeline import SEGMENTS, daily_rank_ic, make_labels
from research.qlib_route_a.qlib_provider import ParquetData, init_qlib_parquet

START, END = "2025-09-26", "2026-09-23"   # 跳过头 4 日 close-only 段
BATCH = 40                                 # 概念分批（控内存：每批约 300MB）


def alpha158_fields() -> tuple[list[str], list[str]]:
    from qlib.contrib.data.handler import Alpha158

    exprs, names = Alpha158.get_feature_config(object.__new__(Alpha158))
    pairs = [(str(e), str(n)) for e, n in zip(exprs, names)
             if "$vwap" not in str(e) and "$factor" not in str(e)]
    return [p[0] for p in pairs], [p[1] for p in pairs]


def family_of(name: str) -> str:
    import re

    m = re.match(r"^[A-Z]+", name)
    return m.group(0) if m else name


def main() -> int:
    from qlib.data import D

    root = Path(__file__).resolve().parents[2]
    data = ParquetData.from_cache_dir(root / "data" / "cache")
    init_qlib_parquet(data)
    m5_close = data.wide("5min", "close")
    concepts = [c for c in data.pools["concept"]
                if c in m5_close.columns and m5_close[c].notna().any()]
    exprs, names = alpha158_fields()
    print(f"[universe] {len(concepts)} 概念 × {len(names)} 因子；窗 {START}~{END}")

    parts = []
    for k in range(0, len(concepts), BATCH):
        chunk = concepts[k: k + BATCH]
        df = D.features(chunk, exprs, f"{START} 00:00", f"{END} 23:59",
                        freq="5min", disk_cache=0)
        df.columns = names
        day_idx = pd.Index([pd.Timestamp(ts).normalize()
                            for ts in df.index.get_level_values("datetime")])
        daily = df.groupby([day_idx, df.index.get_level_values("instrument")],
                           sort=True).last()
        daily.index.names = ["datetime", "instrument"]
        parts.append(daily)
        print(f"  批 {k // BATCH + 1}/{(len(concepts) + BATCH - 1) // BATCH} 完成"
              f"（{len(chunk)} 概念 → 累计 {sum(len(p) for p in parts)} 行）", flush=True)
    feats = pd.concat(parts)
    del parts

    labels = make_labels(data, concepts, START, "2026-09-24")
    full = feats.join(labels, how="left").sort_index()  # 批间 concat 后按索引排序，否则 MultiIndex 切片报 Unsorted
    t0, t1 = SEGMENTS["test"]
    test = full.loc[t0: t1]

    rows = []
    for n in names:
        mF, tF, dF = daily_rank_ic(full[n], full["LABEL1"])
        mT, tT, dT = daily_rank_ic(test[n], test["LABEL1"])
        rows.append({"name": n, "family": family_of(n),
                     "ic_full": mF, "t_full": tF, "days_full": dF,
                     "ic_test": mT, "t_test": tT})
    tbl = pd.DataFrame(rows).sort_values("t_full", ascending=False)
    out_csv = root / "outputs" / "qlib_ml" / "alpha158_ic.csv"
    tbl.to_csv(out_csv, index=False)

    # 顶部因子的衰减（LABEL2/L5，全窗）
    top = tbl.head(20)["name"].tolist()
    decay = {}
    for n in top:
        decay[n] = [daily_rank_ic(full[n], full[f"LABEL{k}"])[0]
                    for k in (1, 2, 5)]

    lines = [
        "# 探索性挖掘：Alpha158 × 5min（未预注册）", "",
        f"universe：5min 覆盖概念 {len(concepts)} 个；因子 157 条（剔除 VWAP0）；"
        "全窗样本内日频 Rank IC（2025-09-26~2026-09-23，"
        f"{tbl['days_full'].max():.0f} 日量级）+ test 段（2026-08-03~09-23）一致性。",
        "**多重比较警示**：157 检验 × α=0.05 期望约 8 个假阳性；Bonferroni 门槛"
        " |t|≳3.5。样本内、单一 regime、幸存者目录（L3，不作采纳依据）。", "",
        "## Top 20（按全窗 t 值降序）", "",
        "| 因子 | 族 | 全窗IC | 全窗t | test段IC | test段t | 衰减 L1→L2→L5 |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for _, r in tbl.head(20).iterrows():
        d = decay.get(r["name"], [float("nan")] * 3)
        lines.append(f"| {r['name']} | {r['family']} | {r['ic_full']:+.4f} "
                     f"| {r['t_full']:+.2f} | {r['ic_test']:+.4f} "
                     f"| {r['t_test']:+.2f} | "
                     f"{d[0]:+.3f}→{d[1]:+.3f}→{d[2]:+.3f} |")

    fam = (tbl.groupby("family")
           .agg(n=("name", "size"),
                t_max=("t_full", lambda s: s.abs().max()),
                hit=("t_full", lambda s: int((s.abs() > 3.5).sum())))
           .sort_values("t_max", ascending=False).head(12))
    lines += ["", "## 族聚合（按族内最大 |t| 排序）", "",
              "| 族 | 因子数 | 最大\\|t\\| | \\|t\\|>3.5 个数 |", "|---|---:|---:|---:|"]
    for fam_name, r in fam.iterrows():
        lines.append(f"| {fam_name} | {int(r['n'])} | {r['t_max']:.2f} "
                     f"| {int(r['hit'])} |")

    lines += ["", "## 冻结族对照", "",
              "TAIL_MOM24（本项目自定义尾盘动量，test 段）：+0.156 / t=+2.41。"]
    out = root / "outputs" / "qlib_ml" / "exploratory_alpha158.md"
    out.write_text("\n".join(lines))
    print(tbl.head(20).to_string(index=False, float_format=lambda v: f"{v:+.3f}"))
    print(f"\n[OK] → {out} / {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
