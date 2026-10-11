"""R2 ruptures 探索：PELT+rbf pen 扫描 + BinSeg 对照 + 事件对齐（§四 R2）。

预注册：docs/research/regime-detection-plan.md §四 R2 冻结细节。
判据 1（断点稳健 ≥70%）/判据 2（每段 ≥40 交易日）/判据 4（事件对齐 ≥3/5）
在本脚本评估（补救动作一次机会由命令行 --min-size 控制）。

用法：conda run -n resonance python research/regime_ruptures.py [--min-size 20|40]
产出：outputs/regime/ruptures_segments_by_pen.csv / ruptures_elbow.csv /
      ruptures_central_segments.csv / event_alignment.csv / ruptures_criteria.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import ruptures as rpt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import regime_common as rc  # noqa: E402


def pelt_breakpoints(X: np.ndarray, pen: float, min_size: int) -> list[int]:
    """PELT+rbf；返回新段首日下标列表（ruptures 的段尾下标 e → 新段首日 = e）。"""
    algo = rpt.Pelt(model="rbf", min_size=min_size, jump=1).fit(X)
    ends = algo.predict(pen=pen)
    return [int(e) for e in ends if e < len(X)]


def min_dist(a: list[int], b: list[int]) -> np.ndarray:
    """a 中每个断点到 b 的最近距离（交易日下标差）；b 为空 → 全 inf。"""
    if not a:
        return np.array([])
    if not b:
        return np.full(len(a), np.inf)
    return np.abs(np.array(a)[:, None] - np.array(b)[None, :]).min(axis=1)


def segment_table(dates: pd.DatetimeIndex, X_raw: pd.DataFrame, bkpts: list[int]) -> pd.DataFrame:
    """断点下标 → 分段表（段长、logret/vol5 均值）。"""
    bounds = [0, *bkpts, len(dates)]
    rows = []
    for i in range(len(bounds) - 1):
        s, e = bounds[i], bounds[i + 1]
        rows.append({
            "seg": i,
            "start": str(dates[s].date()),
            "end": str(dates[e - 1].date()),
            "n_days": e - s,
            "mean_logret": float(X_raw["logret"].iloc[s:e].mean()),
            "mean_vol5": float(X_raw["vol5"].iloc[s:e].mean()),
        })
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-size", type=int, default=rc.MIN_SIZE,
                    help="预注册补救：判据 2 未达标时用 40 重跑一次")
    args = ap.parse_args()

    feats = pd.read_parquet(rc.OUT_DIR / "features.parquet")
    seg_rows, elbow_rows = [], []
    criteria: dict = {"min_size": args.min_size}
    bkpts_cache: dict[tuple[str, int], list[int]] = {}
    per_symbol_feats: dict[str, pd.DataFrame] = {}
    central_segs: dict[str, pd.DataFrame] = {}

    for code in rc.ALL_CODES:
        f = feats[feats["symbol"] == code].set_index("date").sort_index()
        f = f.dropna(subset=["logret", "vol5"])
        per_symbol_feats[code] = f
        X = rc.zscore(f[["logret", "vol5"]].to_numpy())
        dates = f.index

        for pen in rc.RUPTURES_PENS:
            bk = pelt_breakpoints(X, float(pen), args.min_size)
            bkpts_cache[(code, pen)] = bk
            seg_rows.append({"symbol": code, "pen": pen, "n_bkps": len(bk),
                             **{f"bk{i}": str(dates[b].date()) for i, b in enumerate(bk)}})
            elbow_rows.append({"symbol": code, "pen": pen, "n_bkps": len(bk)})
            if pen == rc.CENTRAL_PEN:
                central_segs[code] = segment_table(dates, f, bk)

        # BinSeg 对照：断点数取中心档 PELT 的断点数（结果记入 criteria）
        k = len(bkpts_cache[(code, rc.CENTRAL_PEN)])
        bs = rpt.Binseg(model="rbf", min_size=args.min_size, jump=1).fit(X)
        bs_ends = [int(e) for e in bs.predict(n_bkps=k) if e < len(X)]
        dist = min_dist(bkpts_cache[(code, rc.CENTRAL_PEN)], bs_ends)
        criteria.setdefault("binseg", {})[code] = {
            "n_bkps_binseg": len(bs_ends),
            "match_le_5d": int((dist <= 5).sum()) if len(dist) else 0,
            "n_bkps_pelt": k,
        }

    pd.DataFrame(seg_rows).to_csv(rc.OUT_DIR / "ruptures_segments_by_pen.csv", index=False)
    pd.DataFrame(elbow_rows).to_csv(rc.OUT_DIR / "ruptures_elbow.csv", index=False)
    all_segs = []
    for code, s in central_segs.items():
        s = s.copy()
        s.insert(0, "symbol", code)
        all_segs.append(s)
    pd.concat(all_segs, ignore_index=True).to_csv(
        rc.OUT_DIR / "ruptures_central_segments.csv", index=False)

    # ---- 判据 1：中心档断点对相邻档（pen 3 / 30）的稳健性（主对象全A） ----
    central = bkpts_cache[(rc.MAIN_CODE, rc.CENTRAL_PEN)]
    d3 = min_dist(central, bkpts_cache[(rc.MAIN_CODE, 3)])
    d30 = min_dist(central, bkpts_cache[(rc.MAIN_CODE, 30)])
    stable = np.minimum(d3, d30) <= rc.BKPT_STABLE_DAYS if len(central) else np.array([])
    rate = float(stable.mean()) if len(central) else 0.0
    criteria["criterion_1_pen_stable"] = {
        "pass": bool(rate >= 0.70), "rate": round(rate, 4),
        "stable": int(stable.sum()) if len(central) else 0,
        "total": len(central), "threshold": 0.70,
        "note": "相邻档任一存在 ≤15 交易日对应断点即记稳健",
    }

    # ---- 判据 2：中心档每段 ≥40 交易日（主对象） ----
    segs = central_segs[rc.MAIN_CODE]
    min_len = int(segs["n_days"].min())
    criteria["criterion_2_min_segment"] = {
        "pass": bool(min_len >= 40), "min_len": min_len, "threshold": 40,
    }

    # ---- 判据 4：事件对齐（主对象中心档；三锚描述性） ----
    align_rows = []
    for code in rc.ALL_CODES:
        dates = per_symbol_feats[code].index
        central_c = bkpts_cache[(code, rc.CENTRAL_PEN)]
        for ev, (day, desc) in rc.EVENTS.items():
            ep = rc.event_date_on_calendar(dates, day)
            dmin = int(np.abs(np.array(central_c) - ep).min()) if central_c else 10 ** 9
            align_rows.append({"symbol": code, "event": ev, "date": day, "desc": desc,
                               "min_dist_trading_days": dmin,
                               "aligned": dmin <= rc.EVENT_WINDOW_DAYS})
    align = pd.DataFrame(align_rows)
    align.to_csv(rc.OUT_DIR / "event_alignment.csv", index=False)
    main_align = align[align["symbol"] == rc.MAIN_CODE]
    n_aligned = int(main_align["aligned"].sum())
    criteria["criterion_4_event_alignment"] = {
        "pass": bool(n_aligned >= 3), "aligned": n_aligned, "total": len(rc.EVENTS),
        "threshold": ">=3/5",
        "main_detail": main_align[["event", "min_dist_trading_days", "aligned"]]
        .to_dict(orient="records"),
    }

    rc.OUT_DIR.joinpath("ruptures_criteria.json").write_text(
        json.dumps(criteria, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(criteria, ensure_ascii=False, indent=2))
    print("主对象中心档分段：")
    print(central_segs[rc.MAIN_CODE].to_string(index=False))


if __name__ == "__main__":
    main()
