"""F1–F7 日线因子导出（resonance env）→ outputs/qlib_bridge/daily_factors.parquet。

因子定义见 docs/research/qlib-validation-plan.md §四.1；本脚本只是把
resonance.v3.V3Signals 的逐日榜落盘为跨环境契约文件，公式零重写。
用法：conda run -n resonance python research/qlib_route_a/qlib_factor_export.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd

from resonance import config
from resonance.v3 import V3Params, V3Signals


def export_factors(close_all: pd.DataFrame, concepts: list[str],
                   broad_codes: list[str],
                   params: V3Params | None = None,
                   allA_code: str = "883957.TI") -> pd.DataFrame:
    """逐日全序日线榜 → 长表（闸门失败/无候选日零行）。

    列：date(str) / concept / rank / score / sync / capture / leader /
    gate(bool) / half_life(float)——T4 特征 join 与 T6 对照的契约 schema。
    """
    config.assert_no_retired(broad_codes, context="因子导出锚池")
    sig = V3Signals(close_all, concepts, broad_codes,
                    allA_code, params or V3Params())
    rows = []
    for i in range(len(close_all)):
        if not sig.has_leader[i] or not sig.gate[i]:
            continue
        rk = sig.ranking(i)
        for rank, r in enumerate(rk.itertuples(index=False), start=1):
            rows.append({
                "date": str(close_all.index[i].date()),
                "concept": r.concept, "rank": rank,
                "score": float(r.score), "sync": float(r.sync),
                "capture": float(r.capture),
                "leader": broad_codes[sig.leader_idx[i]],
                "gate": True,
                "half_life": float(sig.hl[i]),
            })
    return pd.DataFrame(rows)


def main() -> int:
    bars = pd.read_parquet(config.CACHE_DIR / "daily_bars.parquet")
    catalog = pd.read_csv(config.DATA_DIR / "concept_catalog.csv")
    close_all = bars.pivot(index="date", columns="symbol",
                           values="close").sort_index()
    close_all.index = pd.to_datetime(close_all.index)
    concepts = [c for c in catalog["code"] if c in close_all.columns]
    df = export_factors(close_all, concepts,
                        broad_codes=list(config.V43_ANCHOR_POOL))
    out = config.OUTPUTS_DIR / "qlib_bridge" / "daily_factors.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    print(f"[OK] {len(df)} 行 / {df['date'].nunique()} 信号日 / "
          f"{df['concept'].nunique()} 概念 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
