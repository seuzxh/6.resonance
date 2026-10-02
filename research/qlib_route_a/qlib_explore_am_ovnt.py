"""探索性诊断：上午盘（MORN）与隔夜/隔日（GAP/XTAIL）5min 因子族（2026-09-29 用户指令）。

性质：**未预注册的探索性分析**——样本内 IC 诊断，不作采纳依据（L3 上界
措辞；9 因子族多重比较未校正）。与 8 条冻结因子同口径对比：
test 段（2026-08-03~09-23）日频 Spearman Rank IC，标签 LABEL1/2/5
（T+k 收盘对收盘、不含成本），universe=5min 覆盖概念 389 个。

网格约定：48 bar/日（0..23 上午、24..47 下午），日频采样点=当日末 bar。
表达式均在末 bar 处取值，全部为 T 日 15:00 可得量（无未来数据）。

用法：conda run -n qlib python research/qlib_route_a/qlib_explore_am_ovnt.py
产出：outputs/qlib_ml/exploratory_am_ovnt.md（+控制台表格）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from research.qlib_route_a.qlib_pipeline import SEGMENTS, daily_rank_ic, make_labels
from research.qlib_route_a.qlib_provider import ParquetData, init_qlib_parquet

# ---------------------------------------------------------------- 候选集 --
EXPR_5MIN = {
    # 上午盘（bar 0..23）
    "MORN_MOM24": "Ref($close,24)/Ref($close,47)-1",          # 上午盘内动量（11:30 对 09:35）
    "MORN_VOL24": "Std(Ref($close/Ref($close,1)-1,24),23)",   # 上午盘内波动（近似）
    "MORN_VRATIO": "Mean(Ref($volume,24),24)/Mean($volume,48)",  # 上午量能占比
    # 隔夜 / 隔日（T−1 信息）
    "OVNT_GAP": "Ref($close,47)/Ref($close,48)-1",            # 隔夜跳空（今首 bar 对昨末 bar）
    "XTAIL_MOM24": "Ref($close,48)/Ref($close,71)-1",         # 昨日尾盘（下午）动量
    "XTAIL_MOM48": "Ref($close,48)/Ref($close,95)-1",         # 昨日全天动量
    "XTAIL_VRATIO": "Mean(Ref($volume,48),24)/Mean(Ref($volume,48),48)",  # 昨日尾盘量能占比
    # 日内结构
    "PMAM_ROT": "($close/Ref($close,23)-1)-(Ref($close,24)/Ref($close,47)-1)",  # 下午−上午相对强度
    "GAP_REL": "(Ref($close,24)/Ref($close,47)-1)-(Ref($close,47)/Ref($close,48)-1)",  # 上午对隔夜的增量
}
EXPR_DAY = {"OVNT_GAP_D": "$open/Ref($close,1)-1"}  # 日线口径隔夜跳空（精确）

REF_LINE = "尾盘动量旧对照使用倒置标签，方向已失效；2026-10-03后须按正向标签重算"


def main() -> int:
    from qlib.data import D

    root = Path(__file__).resolve().parents[2]
    data = ParquetData.from_cache_dir(root / "data" / "cache")
    init_qlib_parquet(data)
    m5_close = data.wide("5min", "close")
    concepts = [c for c in data.pools["concept"]
                if c in m5_close.columns and m5_close[c].notna().any()]
    start, end = "2025-09-26", "2026-09-23"  # 跳过头 4 日 close-only 段
    print(f"[universe] {len(concepts)} 概念；窗 {start}~{end}")

    df = D.features(concepts, list(EXPR_5MIN.values()), f"{start} 00:00",
                    f"{end} 23:59", freq="5min", disk_cache=0)
    df.columns = list(EXPR_5MIN)
    day_idx = pd.Index([pd.Timestamp(ts).normalize()
                        for ts in df.index.get_level_values("datetime")])
    feats = df.groupby([day_idx, df.index.get_level_values("instrument")],
                       sort=True).last()
    feats.index.names = ["datetime", "instrument"]

    dd = D.features(concepts, list(EXPR_DAY.values()), start, end,
                    freq="day", disk_cache=0)
    dd.columns = list(EXPR_DAY)
    feats = feats.join(dd, how="left")

    labels = make_labels(data, concepts, start, "2026-09-24")
    full = feats.join(labels, how="left")
    t0, t1 = SEGMENTS["test"]
    test = full.loc[t0: t1]

    rows = []
    for c in list(EXPR_5MIN) + list(EXPR_DAY):
        r = {"factor": c}
        for lab in ("LABEL1", "LABEL2", "LABEL5"):
            m, t, n = daily_rank_ic(test[c], test[lab])
            r[f"ic_{lab[-1]}"] = m
            r[f"t_{lab[-1]}"] = t
            if lab == "LABEL1":
                r["days"] = n
        rows.append(r)
    tbl = pd.DataFrame(rows)

    lines = [
        "# 探索性诊断：上午盘与隔夜/隔日 5min 因子（未预注册）",
        "",
        "口径：test 段（2026-08-03~09-23）日频 Spearman Rank IC；标签",
        "LABEL_k = T+k 收盘对收盘（不含成本）；universe = 5min 覆盖概念"
        f" {len(concepts)} 个；全链 float32。",
        "**样本内、单一 regime、9 因子族多重比较未校正（L3 上界措辞，"
        "不作采纳依据）**。对照基准——" + REF_LINE,
        "", "| 因子 | IC(L1) | t(L1) | IC(L2) | IC(L5) | 天数 |", "|---|---:|---:|---:|---:|---:|",
    ]
    for _, r in tbl.iterrows():
        lines.append(f"| {r['factor']} | {r['ic_1']:+.4f} | {r['t_1']:+.2f} "
                     f"| {r['ic_2']:+.4f} | {r['ic_5']:+.4f} | {int(r['days'])} |")
    out = root / "outputs" / "qlib_ml" / "exploratory_am_ovnt.md"
    out.write_text("\n".join(lines))
    print(tbl.to_string(index=False, float_format=lambda v: f"{v:+.4f}"))
    print(f"\n[OK] → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
