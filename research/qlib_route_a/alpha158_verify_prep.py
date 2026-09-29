"""A158-V1 预备（qlib env）：三特征日频化 + LGBM 训练 + 预测落盘。

预注册设计：docs/research/alpha158-verify-plan.md。特征：
  BETA20  = Slope($close,20)/$close（尾盘稳健动量，正候选）
  RSQR20  = Rsquare($close,20)（趋势质量，正候选）
  CNTP30  = Mean($close>Ref($close,1),30)（短窗涨 bar 占比，负候选，
            保留原方向——树模型无需翻转，重排仅在 C 臂用 BETA20）

用法：conda run -n qlib python research/qlib_route_a/alpha158_verify_prep.py
产出：outputs/qlib_ml/alpha158_features.parquet / pred_a158_3f.parquet
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd

from research.qlib_route_a.qlib_pipeline import SEGMENTS, make_labels, train_model
from research.qlib_route_a.qlib_provider import ParquetData, init_qlib_parquet

EXPR = {
    "BETA20": "Slope($close,20)/$close",
    "RSQR20": "Rsquare($close,20)",
    "CNTP30": "Mean($close>Ref($close,1),30)",
}
START, END = "2025-09-26", "2026-09-23"


def main() -> int:
    from qlib.data import D

    root = Path(__file__).resolve().parents[2]
    data = ParquetData.from_cache_dir(root / "data" / "cache")
    init_qlib_parquet(data)
    m5_close = data.wide("5min", "close")
    concepts = [c for c in data.pools["concept"]
                if c in m5_close.columns and m5_close[c].notna().any()]

    df = D.features(concepts, list(EXPR.values()), f"{START} 00:00",
                    f"{END} 23:59", freq="5min", disk_cache=0)
    df.columns = list(EXPR)
    day_idx = pd.Index([pd.Timestamp(ts).normalize()
                        for ts in df.index.get_level_values("datetime")])
    feats = df.groupby([day_idx, df.index.get_level_values("instrument")],
                       sort=True).last()
    feats.index.names = ["datetime", "instrument"]
    feats = feats.sort_index()
    out_f = root / "outputs" / "qlib_ml" / "alpha158_features.parquet"
    feats.to_parquet(out_f)
    print(f"[features] {feats.shape} → {out_f}")

    labels = make_labels(data, concepts, START, "2026-09-24")
    full = feats.join(labels, how="left").sort_index()
    model, ds = train_model(full, SEGMENTS)
    pred = model.predict(ds, segment="test")
    pred.name = "pred"
    pred_df = pred.reset_index()
    pred_df["date"] = pred_df["datetime"].dt.strftime("%Y-%m-%d")
    out_p = root / "outputs" / "qlib_ml" / "pred_a158_3f.parquet"
    pred_df[["date", "instrument", "pred"]].rename(
        columns={"instrument": "concept"}).to_parquet(out_p, index=False)
    print(f"[pred] test 段 {len(pred_df)} 条 → {out_p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
