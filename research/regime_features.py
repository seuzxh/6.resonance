"""R1 数据对齐：缓存日线 → 特征表与覆盖审计（regime-detection §四 R1）。

预注册：docs/research/regime-detection-plan.md §三。窗口 2021-12-01~2026-09-18
截断；特征 logret + vol5（含当日，无未来函数）。

用法：conda run -n resonance python research/regime_features.py
产出：outputs/regime/features.parquet / feature_audit.json
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import regime_common as rc  # noqa: E402


def main() -> None:
    rc.OUT_DIR.mkdir(parents=True, exist_ok=True)
    closes = rc.load_daily_closes()

    feats: list[pd.DataFrame] = []
    audit: dict[str, dict] = {}
    union_dates: pd.DatetimeIndex | None = None
    for code, close in closes.items():
        f = rc.build_features(close)
        f = f.reset_index().rename(columns={"index": "date"})
        f.insert(0, "symbol", code)
        feats.append(f)
        if union_dates is None:
            union_dates = close.index
        else:
            union_dates = union_dates.union(close.index)
        audit[code] = {
            "rows": int(len(f)),
            "first": str(close.index.min().date()),
            "last": str(close.index.max().date()),
            "logret_na": int(f["logret"].isna().sum()),
            "vol5_na": int(f["vol5"].isna().sum()),
        }

    # 相对五标的并集日历的缺日审计
    for code, close in closes.items():
        audit[code]["missing_vs_union"] = int(len(union_dates) - len(close.index))

    parquet = rc.OUT_DIR / "features.parquet"
    all_f = pd.concat(feats, ignore_index=True)
    all_f.to_parquet(parquet, index=False)

    src = rc.config.CACHE_DIR / "daily_bars.parquet"
    digest = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
    rc.OUT_DIR.joinpath("feature_audit.json").write_text(
        json.dumps(
            {
                "research_window": [rc.RESEARCH_START, rc.RESEARCH_END],
                "daily_bars_sha256_16": digest,
                "n_rows_total": int(len(all_f)),
                "per_symbol": audit,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("R1 完成：", len(all_f), "行 ×", len(closes), "标的")
    for code, a in audit.items():
        print(f"  {code}: {a['rows']} 行 {a['first']}~{a['last']} "
              f"vol5缺失 {a['vol5_na']} 缺日 {a['missing_vs_union']}")


if __name__ == "__main__":
    main()
