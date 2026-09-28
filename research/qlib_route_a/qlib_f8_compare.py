"""X3：模型分替换 F8 重排的 5 相位预注册对照（resonance env，V4.4 开盘口径）。

预注册判据（docs/superpowers/plans/2026-09-29-…md T6，playbook §一）：
以 test 段 5 相位中位对照现行 F8 栈（同窗同参数同 10bp、exec_price=open）：
成功 = 中位提升 >0 且模型版 ≥F8 版的相位 ≥4/5；无效 = |中位差| ≤2pp 或
方向不一致；反效 = 中位下降 >2pp；另做孤峰形态检查（L2）。

用法：conda run -n resonance python research/qlib_route_a/qlib_f8_compare.py
产出：outputs/qlib_ml/x3_phases.csv（相位×配置统计）+ 判决打印
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from resonance import config
from resonance.backtest import perf_stats
from resonance.v3 import MinuteBarProvider, V3Backtester, V3Params

END = "2026-09-23"          # 5min 数据末（test 段自然终点）
COST_BP = 10.0
EXEC = "open"               # V4.4 口径（用户 2026-09-29 裁决）
PHASES = ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"]


def make_model_post_rank(pred: pd.DataFrame):
    """post_rank 钩子：镜像 F8' 降级链（spec §四.2）。

    当日日线 Top5 中有模型分的概念按模型分降序重排、无分的剔除
    （计 model_excluded）；可评 <2 回退日线原序（计 model_fallback_sparse）。
    引擎合同：返回表列只需 concept+score；回退时返回原榜。
    """
    stats = {"model_days": 0, "model_excluded": 0,
             "model_fallback_sparse": 0}
    tbl = {(d, c): p for d, c, p in zip(pred["date"], pred["concept"],
                                        pred["pred"])}

    def post_rank(ranking: pd.DataFrame, date) -> pd.DataFrame:
        key_day = str(pd.Timestamp(date).date())
        top = ranking.head(5)
        scored = [(float(tbl[(key_day, r.concept)]), r.concept)
                  for r in top.itertuples(index=False)
                  if (key_day, r.concept) in tbl]
        stats["model_days"] += 1
        stats["model_excluded"] += len(top) - len(scored)
        if len(scored) < 2:
            stats["model_fallback_sparse"] += 1
            return ranking  # 回退：整个日线榜原序
        scored.sort(reverse=True)
        # 与 F8 语义一致：重排成功时最终榜只含可评分概念（被剔除者出局）
        return pd.DataFrame({"concept": [c for _, c in scored],
                             "score": [s for s, _ in scored]})

    return post_rank, stats


def main() -> int:
    bars = pd.read_parquet(config.CACHE_DIR / "daily_bars.parquet")
    catalog = pd.read_csv(config.DATA_DIR / "concept_catalog.csv")
    close_all = bars.pivot(index="date", columns="symbol", values="close").sort_index()
    open_all = bars.pivot(index="date", columns="symbol", values="open").sort_index()
    for w in (close_all, open_all):
        w.index = pd.to_datetime(w.index)
    concepts = [c for c in catalog["code"] if c in close_all.columns]
    m5 = pd.read_parquet(config.CACHE_DIR / "minute5_bars.parquet")
    prov = MinuteBarProvider(m5.pivot(index="datetime", columns="symbol",
                                      values="close").sort_index())
    pred = pd.read_parquet(config.OUTPUTS_DIR / "qlib_ml" / "pred.parquet")

    rows = []
    for tag in ("A_f8_baseline", "B_model_rerank"):
        for start in PHASES:
            if tag == "A_f8_baseline":
                p = V3Params(topk=3, daily_top=5, hl_source="leader",
                             exec_price=EXEC, cost_bp=COST_BP)
                bt = V3Backtester(close_all, concepts,
                                  broad_codes=list(config.V43_ANCHOR_POOL),
                                  params=p, minute_bars_provider=prov,
                                  open_all=open_all)
                st_extra = {}
            else:
                post_rank, st_extra = make_model_post_rank(pred)
                p = V3Params(topk=3, hl_source="leader",
                             exec_price=EXEC, cost_bp=COST_BP)
                bt = V3Backtester(close_all, concepts,
                                  broad_codes=list(config.V43_ANCHOR_POOL),
                                  params=p, post_rank=post_rank,
                                  open_all=open_all)
            out = bt.run(start, END)
            st = perf_stats(out["nav_curve"])
            rows.append({
                "tag": tag, "start": start,
                "total": st["total_return"], "dd": st["max_drawdown"],
                "sharpe": st["sharpe"],
                "changes": out["stats"]["position_changes"],
                "stops": out["stats"]["stop_count"],
                **{f"m_{k}": v for k, v in st_extra.items()},
            })
    df = pd.DataFrame(rows)
    out_csv = config.OUTPUTS_DIR / "qlib_ml" / "x3_phases.csv"
    df.to_csv(out_csv, index=False)

    print(df.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    print("\n===== 5 相位中位对照（10bp，V4.4 开盘口径）=====")
    med = {}
    for tag in ("A_f8_baseline", "B_model_rerank"):
        sub = df[df["tag"] == tag]
        med[tag] = (float(np.median(sub["total"])),
                    float(np.median(sub["dd"])),
                    float(np.median(sub["sharpe"])))
        print(f"[{tag}] 总收益 {med[tag][0]:+.2%} | 回撤 {med[tag][1]:.2%} | "
              f"夏普 {med[tag][2]:.2f}")
    diff_pp = (med["B_model_rerank"][0] - med["A_f8_baseline"][0]) * 100
    win = int(sum(b >= a for b, a in zip(
        df[df["tag"] == "B_model_rerank"]["total"],
        df[df["tag"] == "A_f8_baseline"]["total"])))
    verdict = ("success" if (win >= 4 and diff_pp > 0)
               else "harmful" if diff_pp <= -2 else "neutral")
    print(f"\n===== 预注册判决：{verdict.upper()} =====")
    print(f"中位差 {diff_pp:+.2f}pp | 模型版 ≥ F8 版相位 {win}/5")
    print(f"（判据：成功=中位差>0 且 ≥4/5；反效=中位差≤−2pp；无效=其余。"
          f"措辞边界：样本内上界、test 段 36 日单一 regime、幸存者目录、"
          f"指数不可交易。）")
    print(f"\n[OK] 落盘 {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
