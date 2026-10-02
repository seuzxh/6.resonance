"""qlib 路线 A 因子管道（qlib env）：5min 表达式 → 日频特征 → 训练 → 预测与 IC。

冻结的 8 条 5min 表达式（docs/superpowers/plans/2026-09-29-…md T4）在
表达式引擎上评估后，按"每日末 bar"采样为日频值（DayLast 语义的 pandas
等价实现），再 join 桥文件的 F1–F7 因子列。

用法：
    conda run -n qlib python research/qlib_route_a/qlib_pipeline.py
产出：
    outputs/qlib_ml/features.parquet / coverage.md / pred.parquet / ic_report.md
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- 冻结清单 --

FREQ5_EXPRESSIONS = {
    # 尾盘（最后 24 根 bar）动量与全天动量——采样到日末 bar 即日频值
    "TAIL_MOM24": "$close/Ref($close,23)-1",
    "FULL_MOM48": "$close/Ref($close,47)-1",
    # 尾盘波动 vs 全天波动（bar 间收益的滚动标准差）
    "TAIL_VOL24": "Std($close/Ref($close,1)-1,23)",
    "FULL_VOL48": "Std($close/Ref($close,1)-1,47)",
    # 尾盘量能占比（尾盘均量 / 全日均量）
    "TAIL_VRATIO": "Mean($volume,24)/Mean($volume,48)",
    # 收盘价日内位置：(末 bar 收盘 − 日内最低)/(日内最高 − 日内最低)
    "DAY_POS": "(DayLast($close)-DayLast(Min($low,48)))"
               "/(DayLast(Max($high,48))-DayLast(Min($low,48)))",
    # 高价触及度：日内最高相对前收盘的涨幅（捕捉日内冲高）
    "HI_PUMP": "DayLast(Max($high,48))/DayLast(Ref($close,48))-1",
    # 尾盘加速度：最后 12 根 bar 动量 / 之前 12 根 bar 动量
    "TAIL_ACC": "($close/Ref($close,11)-1)/(Ref($close,12)/Ref($close,23)-1)",
}

# 时间切分（冻结，约束 7：按时间切分禁随机）
SEGMENTS = {
    "train": ("2025-09-22", "2026-05-29"),
    "valid": ("2026-06-01", "2026-07-31"),
    "test": ("2026-08-03", "2026-09-23"),
}


# ---------------------------------------------------------------- 特征构建 --

def build_features(data, concepts: list[str], start: str, end: str,
                   bridge_parquet=None) -> pd.DataFrame:
    """D.features@5min 评估全部表达式 → 按日取末 bar（日频化）→ join F1–F7。

    5min 日历非连续（契约⑧）：按日采样天然只在有 bar 的交易日产生行。
    """
    from qlib.data import D

    # 日期字符串归一日边界：end="YYYY-MM-DD" 是 00:00，会把当日全部 bar
    # 切掉（5min 日历从 09:35 起）——统一推到当日 23:59
    if len(str(end)) == 10:
        end = f"{end} 23:59"
    if len(str(start)) == 10:
        start = f"{start} 00:00"
    df = D.features(concepts, list(FREQ5_EXPRESSIONS.values()),
                    start, end, freq="5min", disk_cache=0)
    df.columns = list(FREQ5_EXPRESSIONS)
    day_idx = pd.Index([pd.Timestamp(ts).normalize()
                        for ts in df.index.get_level_values("datetime")])
    feats = df.groupby([day_idx, df.index.get_level_values("instrument")],
                       sort=True).last()  # 每日末 bar = 日频值（DayLast 采样）
    feats.index.names = ["datetime", "instrument"]
    if bridge_parquet is not None:
        f17 = pd.read_parquet(bridge_parquet)
        f17["date"] = pd.to_datetime(f17["date"])
        vals = ["score", "sync", "capture", "half_life"]
        wide17 = f17.pivot_table(index="date", columns="concept", values=vals)
        long17 = (wide17.stack("concept", future_stack=True)
                  .rename_axis(["datetime", "instrument"]))
        feats = feats.join(long17, how="left")
    return feats


def make_labels(data, concepts: list[str], start: str, end: str) -> pd.DataFrame:
    """标签：T+k 收盘相对 T 收盘（收盘对收盘，不含成本——预测目标定义，
    不随 V4.4 成交口径变）。"""
    from qlib.data import D

    # 负向 Ref 会裁掉各自不同长度的末端（k=1/2/5），同一请求里按列拼接
    # 会长度不齐——逐条取数，再按日期和概念对齐。
    # 必须是未来价除以当前价；倒置会把上涨标成负值，与降序选高分相反。
    parts = []
    for k in (1, 2, 5):
        d = D.features(concepts, [f"Ref($close,-{k})/$close-1"], start, end,
                       freq="day", disk_cache=0)
        d.columns = [f"LABEL{k}"]
        parts.append(d)
    return pd.concat(parts, axis=1)


# -------------------------------------------------------------------- 训练 --

def train_model(features: pd.DataFrame, segments: dict):
    """feature/label 两级列头 → from_df → DatasetH → LGBModel（A0 冒烟契约）。"""
    from qlib.contrib.model.gbdt import LGBModel
    from qlib.data.dataset import DatasetH
    from qlib.data.dataset.handler import DataHandlerLP

    # LightGBM 仅单标签：LABEL1 进标签组；LABEL2/5 是评估口径，不进训练
    label_cols = ["LABEL1"]
    data = features.dropna(subset=["LABEL1"]).copy()
    data = data.drop(columns=[c for c in data.columns
                              if str(c).startswith("LABEL") and c != "LABEL1"])
    data.columns = pd.MultiIndex.from_tuples(
        [("label", c) if c in label_cols else ("feature", c)
         for c in data.columns])
    segs = {k: tuple(map(str, v)) for k, v in segments.items()}
    ds = DatasetH(handler=DataHandlerLP.from_df(data), segments=segs)
    model = LGBModel(loss="mse", early_stopping_rounds=30,
                     num_boost_round=60, learning_rate=0.05,
                     num_leaves=8, verbose=-1)
    model.fit(ds, verbose_eval=0)
    return model, ds


def daily_rank_ic(pred: pd.Series, label: pd.Series) -> tuple[float, float, int]:
    """日频 Spearman Rank IC → (均值, t 值, 天数)。"""
    from scipy import stats as sps

    df = pd.concat([pred.rename("p"), label.rename("y")], axis=1).dropna()
    ic = df.groupby(level="datetime").apply(
        lambda g: sps.spearmanr(g["p"], g["y"])[0]).dropna()
    if len(ic) < 3:
        return float("nan"), float("nan"), len(ic)
    if ic.std() > 0:
        t = ic.mean() / (ic.std() / np.sqrt(len(ic)))
    else:
        t = float("inf") if ic.mean() != 0 else float("nan")
    return float(ic.mean()), float(t), len(ic)


# -------------------------------------------------------------------- main --

def main() -> int:
    from research.qlib_route_a.qlib_provider import (
        ParquetData, init_qlib_parquet,
    )

    out_dir = Path(__file__).resolve().parents[2] / "outputs" / "qlib_ml"
    out_dir.mkdir(parents=True, exist_ok=True)
    data = ParquetData.from_cache_dir(
        Path(__file__).resolve().parents[2] / "data" / "cache")
    init_qlib_parquet(data)

    # 5min 覆盖概念集（有任一 bar 者）
    m5_close = data.wide("5min", "close")
    concepts = [c for c in data.pools["concept"]
                if c in m5_close.columns and m5_close[c].notna().any()]
    print(f"[universe] 5min 覆盖概念 {len(concepts)}/{len(data.pools['concept'])}")

    start, end = "2025-09-22", str(pd.Timestamp(data.cals["5min"][-1]).date())
    feats = build_features(data, concepts, start, end,
                           bridge_parquet=out_dir.parent / "qlib_bridge"
                           / "daily_factors.parquet")
    feats.to_parquet(out_dir / "features.parquet")
    print(f"[features] {feats.shape} → features.parquet")

    # 覆盖率报告（10.4.2 日历口径）
    day_cnt = len(feats.index.get_level_values(0).unique())
    per_day = feats.groupby(level=0)[list(FREQ5_EXPRESSIONS)].apply(
        lambda g: g.notna().mean())
    lines = [
        "# 5min 因子覆盖率报告（T4）", "",
        f"- 5min 日历日数（特征实际覆盖日）：{day_cnt}",
        f"- 概念 universe：{len(concepts)}（5min 覆盖；全目录 "
        f"{len(data.pools['concept'])}）",
        f"- 逐因子非 NaN 率（全体均值）：", "",
        "| 因子 | 非 NaN 率 |", "|---|---|",
    ]
    for c in list(FREQ5_EXPRESSIONS):
        lines.append(f"| {c} | {feats[c].notna().mean():.1%} |")
    lines += ["", f"- 头 4 日（2025-09-22~25）为 close-only 历史，OHLCV 类因子"
              " 该段为 NaN（数据清单 §二.2）。",
              f"- F1–F7 桥列非 NaN 率：score {feats['score'].notna().mean():.1%}。"]
    (out_dir / "coverage.md").write_text("\n".join(lines))
    print(f"[coverage] {day_cnt} 日 → coverage.md")

    # 标签 + 训练 + 预测 + IC
    labels = make_labels(data, concepts, start, "2026-09-24")
    full = feats.join(labels, how="left")
    model, ds = train_model(full, SEGMENTS)
    pred = model.predict(ds, segment="test")
    pred.name = "pred"
    pred_df = pred.reset_index()
    pred_df["date"] = pred_df["datetime"].dt.strftime("%Y-%m-%d")
    pred_df[["date", "instrument", "pred"]].rename(
        columns={"instrument": "concept"}).to_parquet(
        out_dir / "pred.parquet", index=False)
    print(f"[pred] test 段 {len(pred_df)} 条 → pred.parquet")

    y_test = ds.prepare("test")["LABEL1"]
    ic_lines = ["# IC 报告（T5）", "",
                "口径：test 段（2026-08-03~09-23）日频 Spearman Rank IC，"
                "对 LABEL1（T+1 收盘对收盘）。样本内、幸存者目录、"
                "单一 regime（L3 措辞边界）。", "",
                "| 口径 | Rank IC 均值 | t 值 | 天数 |", "|---|---|---|---|"]
    m, t, n = daily_rank_ic(pred, y_test)
    ic_lines.append(f"| 模型分 | {m:+.4f} | {t:+.2f} | {n} |")
    test_feats = ds.prepare("test")
    for c in list(FREQ5_EXPRESSIONS) + ["score", "sync", "capture"]:
        if c in test_feats.columns:
            mm, tt, nn = daily_rank_ic(test_feats[c], y_test)
            ic_lines.append(f"| {c} | {mm:+.4f} | {tt:+.2f} | {nn} |")
    (out_dir / "ic_report.md").write_text("\n".join(ic_lines))
    print(f"[ic] 模型分 RankIC {m:+.4f}（t={t:+.2f}, {n} 日）→ ic_report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
