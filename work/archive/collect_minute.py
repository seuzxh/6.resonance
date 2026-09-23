"""分钟K线采集：minute_fetch_list.json 清单内 207 codes × 滚动一年 60min close。

用法：
    conda run -n resonance python work/collect_minute.py

产出：data/cache/minute_bars.parquet（长表 symbol,datetime,close），断点续传。
清单来源：分钟验证窗（2025-09-22 起）日线 Top10 并集（196 概念）+ 有分钟数据的
11 宽基。配额成本 ≈ 199k dataVol（close 单指标）。
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.ifind import MINUTE_CODES_PER_REQUEST, fetch_minute_close  # noqa: E402

FETCH_LIST = config.CACHE_DIR / "minute_fetch_list.json"
OUT_FILE = config.CACHE_DIR / "minute_bars.parquet"
PARTIAL_FILE = config.CACHE_DIR / "minute_bars.partial.parquet"
# 分钟数据滚动留存 ~1 年（-4309），从 2025-09-22 起为当前全部可用历史
MINUTE_START = "2025-09-22"


def main() -> int:
    codes = json.loads(FETCH_LIST.read_text())
    end = dt.date.today().isoformat()
    print(f"[分钟采集] {len(codes)} codes × {MINUTE_START}~{end}（20/请求）")

    done: set[str] = set()
    frames: list[pd.DataFrame] = []
    if PARTIAL_FILE.exists():
        partial = pd.read_parquet(PARTIAL_FILE)
        frames = [partial]
        done = set(partial["symbol"].unique())
        print(f"  断点续存：已有 {len(done)} codes")
    todo = [c for c in codes if c not in done]

    t0 = time.time()
    for k in range(0, len(todo), MINUTE_CODES_PER_REQUEST):
        chunk = todo[k : k + MINUTE_CODES_PER_REQUEST]
        for attempt in range(3):
            try:
                df = fetch_minute_close(chunk, MINUTE_START, end)
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
        pd.concat(frames, ignore_index=True).drop_duplicates(
            subset=["symbol", "datetime"], keep="last"
        ).to_parquet(PARTIAL_FILE, index=False)
        print(f"  {min(k + MINUTE_CODES_PER_REQUEST, len(todo))}/{len(todo)} codes"
              f"（{time.time() - t0:.0f}s）", flush=True)

    bars = pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=["symbol", "datetime"], keep="last"
    )
    bars.to_parquet(OUT_FILE, index=False)
    PARTIAL_FILE.unlink(missing_ok=True)
    cov = bars.groupby("symbol")["datetime"].agg(["min", "max", "count"])
    print(f"[OK] {len(bars)} 行 × {bars['symbol'].nunique()} codes → {OUT_FILE}")
    print(f"  覆盖：{cov['count'].median():.0f} bar/code 中位数，"
          f"{cov['min'].min()} ~ {cov['max'].max()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
