"""分钟K线采集：minute_fetch_list.json 清单内 207 codes × 滚动一年 60min。

用法：
    conda run -n resonance python work/collect_minute.py               # 增量（新代码）
    conda run -n resonance python work/collect_minute.py --dry-run     # 只报预估 dataVol
    conda run -n resonance python work/collect_minute.py --backfill    # 全指标回补
    conda run -n resonance python work/collect_minute.py --backfill --codes 885311.TI

清单来源：分钟验证窗（2025-09-22 起）日线 Top10 并集（196 概念）+ 有分钟数据的
11 宽基。close 单指标口径 ≈ 199k dataVol；2026-09-26 全指标扩展（用户指令：
9 指标直接取接口，不做本地推导）：4bar/日×9 ≈ 36 dataVol/code·日，全量回补
207 codes ≈ 1.8M dataVol——历史 close-only 行默认不重采，--backfill 显式回补，
回补行以全指标覆盖旧行（keep=last）。留存窗口外的最早日期接口不可回取。

产出：data/cache/minute_bars.parquet（长表 symbol,datetime + 9 指标列；
历史行新列可为 NaN），断点续存。数据集属时点择时探索（已证伪封存），仅作
归档与后续研究复用，不接生产链路。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.ifind import (  # noqa: E402
    MINUTE_CODES_PER_REQUEST, MINUTE_INDICATOR_COLS, fetch_minute_bars,
)

FETCH_LIST = config.CACHE_DIR / "minute_fetch_list.json"
OUT_FILE = config.CACHE_DIR / "minute_bars.parquet"
PARTIAL_FILE = config.CACHE_DIR / "minute_bars.partial.parquet"
# 分钟数据滚动留存 ~1 年（-4309），从 2025-09-22 起为当前全部可用历史
MINUTE_START = "2025-09-22"
FULL_COLS = ["symbol", "datetime", *MINUTE_INDICATOR_COLS]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true",
                    help="对 close-only 历史 code 重采全指标")
    ap.add_argument("--codes", default="", help="仅处理这些代码（逗号分隔）")
    ap.add_argument("--dry-run", action="store_true", help="只统计请求与预估 dataVol")
    args = ap.parse_args()

    codes = json.loads(FETCH_LIST.read_text())
    if args.codes:
        codes = [c for c in codes if c in set(args.codes.split(","))]
    end = dt.date.today().isoformat()
    est_days = len(pd.bdate_range(MINUTE_START, end))
    print(f"[分钟采集] {len(codes)} codes × {MINUTE_START}~{end}"
          f"（{MINUTE_CODES_PER_REQUEST}/请求，全指标 9 列）")

    # 断点：OUT/PARTIAL 已含的 code；backfill 口径下仅"已带全指标"的 code 算
    # 完成（OUT 也读入 frames，重采行才能以 keep=last 覆盖旧行）
    done_full: set[str] = set()
    frames: list[pd.DataFrame] = []
    for f in (OUT_FILE, PARTIAL_FILE):
        if f.exists():
            df = pd.read_parquet(f)
            frames.append(df)
            if "open" in df.columns and df["open"].notna().any():
                done_full |= set(df.loc[df["open"].notna(), "symbol"].unique())
    done_any: set[str] = set()
    for df in frames:
        done_any |= set(df["symbol"].unique())
    if frames and not args.backfill:
        print(f"  断点续存：已有 {len(done_any)} codes（全指标 {len(done_full)}）")
    done = done_full if args.backfill else done_any
    if args.dry_run:
        n_codes = len([c for c in codes if c not in done])
        print(f"[DRY] 需采 {n_codes} codes × {est_days} 交易日 × 4bar×9指标"
              f" ≈ {n_codes * est_days * 36:,} dataVol")
        return 0

    todo = [c for c in codes if c not in done]

    t0 = time.time()
    for k in range(0, len(todo), MINUTE_CODES_PER_REQUEST):
        chunk = todo[k : k + MINUTE_CODES_PER_REQUEST]
        for attempt in range(3):
            try:
                df = fetch_minute_bars(chunk, MINUTE_START, end, interval="60")
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    raise
                print(f"  块 {chunk[0]}~ 重试 {attempt + 1}: {e}")
                time.sleep(5 * (attempt + 1))
        got = set(df["symbol"].unique())
        empty = [c for c in chunk if c not in got]
        if empty:
            print(f"  [WARN] {empty} 无分钟数据（覆盖率变化）")
        frames.append(df)
        bars = pd.concat(frames, ignore_index=True).drop_duplicates(
            subset=["symbol", "datetime"], keep="last"
        )
        for col in FULL_COLS[2:]:
            if col not in bars.columns:
                bars[col] = float("nan")
        bars[FULL_COLS].to_parquet(PARTIAL_FILE, index=False)
        print(f"  {min(k + MINUTE_CODES_PER_REQUEST, len(todo))}/{len(todo)} codes"
              f"（{time.time() - t0:.0f}s）", flush=True)

    bars = pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=["symbol", "datetime"], keep="last"
    )
    for col in FULL_COLS[2:]:
        if col not in bars.columns:
            bars[col] = float("nan")
    bars = bars[FULL_COLS].sort_values(["symbol", "datetime"]).reset_index(drop=True)
    bars.to_parquet(OUT_FILE, index=False)
    PARTIAL_FILE.unlink(missing_ok=True)
    cov = bars.groupby("symbol")["datetime"].agg(["min", "max", "count"])
    print(f"[OK] {len(bars)} 行 × {bars['symbol'].nunique()} codes → {OUT_FILE}")
    print(f"  覆盖：{cov['count'].median():.0f} bar/code 中位数，"
          f"{cov['min'].min()} ~ {cov['max'].max()}")
    print(f"  全指标行 {int(bars['open'].notna().sum()):,} / {len(bars):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
