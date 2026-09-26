"""数据采集：概念目录快照 + 13 宽基指数与全部概念指数日线落盘。

用法：
    conda run -n resonance python ops/collect.py            # 全量
    conda run -n resonance python ops/collect.py --catalog-only

产出（均 gitignored）：
    data/concept_catalog.csv        概念目录快照（code, name, snapshot_date）
    data/cache/daily_bars.parquet   日线长表（symbol, date, 9 字段），可断点续存
    data/cache/daily_bars.partial.parquet   采集中间态（完成后删除）

口径：history_data CPS=0；10 codes/请求（客户端自动分块，这里显式分块以便断点）。
区间 2024-10-01（config.COLLECT_START）→ 今日；为 2025-01-01 回测留 20/60 日
信号窗口预热 lookback。
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.ifind import (  # noqa: E402
    CODES_PER_REQUEST,
    fetch_history_data,
    fetch_index_names,
)

CATALOG_FILE = config.DATA_DIR / "concept_catalog.csv"
BARS_FILE = config.CACHE_DIR / "daily_bars.parquet"
PARTIAL_FILE = config.CACHE_DIR / "daily_bars.partial.parquet"


def collect_catalog() -> pd.DataFrame:
    """枚举 885/886 段候选 → basic_data_service 取简称 → 目录快照 CSV。"""
    candidates = [f"{n}.TI" for n in config.CONCEPT_CODE_RANGE]
    print(f"[1/2] 概念目录：枚举 {len(candidates)} 个候选代码 ...")
    names = fetch_index_names(candidates)
    snapshot = dt.date.today().isoformat()
    catalog = pd.DataFrame(
        {"code": sorted(names), "name": [names[c] for c in sorted(names)]}
    )
    catalog["snapshot_date"] = snapshot
    CATALOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    catalog.to_csv(CATALOG_FILE, index=False)
    n885 = catalog["code"].str.startswith("885").sum()
    n886 = catalog["code"].str.startswith("886").sum()
    print(f"[OK] 概念目录 {len(catalog)} 个（885 段 {n885} + 886 段 {n886}），"
          f"快照日 {snapshot} → {CATALOG_FILE}")
    return catalog


def collect_bars(codes: list[str]) -> pd.DataFrame:
    """逐 10-code 块拉日线，断点续存（partial parquet），完成合并落盘。"""
    end = dt.date.today().isoformat()
    print(f"[2/2] 日线采集：{len(codes)} codes × {config.COLLECT_START}~{end} ...")

    done: set[str] = set()
    frames: list[pd.DataFrame] = []
    if PARTIAL_FILE.exists():
        partial = pd.read_parquet(PARTIAL_FILE)
        frames = [partial]
        done = set(partial["symbol"].unique())
        print(f"     断点续存：已有 {len(done)} codes，跳过")

    todo = [c for c in codes if c not in done]
    t0 = time.time()
    for k in range(0, len(todo), CODES_PER_REQUEST):
        chunk = todo[k : k + CODES_PER_REQUEST]
        for attempt in range(3):
            try:
                got = fetch_history_data(chunk, config.COLLECT_START, end)
                break
            except Exception as e:  # noqa: BLE001 — 网络/网关抖动重试
                if attempt == 2:
                    raise
                print(f"     块 {chunk[0]}~ 重试 {attempt + 1}: {e}")
                time.sleep(5 * (attempt + 1))
        df = pd.concat(got, ignore_index=True)
        got_codes = set(df["symbol"].unique())
        missing = set(chunk) - got_codes
        if missing:
            print(f"     [WARN] 块内 {sorted(missing)} 无任何行情（新上市/停牌段代码）")
        frames.append(df)
        # 断点续存：每块完成即写 partial
        pd.concat(frames, ignore_index=True).to_parquet(PARTIAL_FILE, index=False)
        n_done = min(k + CODES_PER_REQUEST, len(todo))
        rate = n_done / max(time.time() - t0, 1e-9)
        print(f"     {n_done}/{len(todo)} codes（{rate:.1f} codes/s）", flush=True)

    bars = pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=["symbol", "date"], keep="last"
    )
    bars.to_parquet(BARS_FILE, index=False)
    PARTIAL_FILE.unlink(missing_ok=True)
    print(f"[OK] 日线 {len(bars)} 行 × {bars['symbol'].nunique()} codes → {BARS_FILE}")
    return bars


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog-only", action="store_true", help="只刷概念目录")
    args = ap.parse_args()

    if CATALOG_FILE.exists() and not args.catalog_only:
        catalog = pd.read_csv(CATALOG_FILE)
        print(f"[1/2] 概念目录已有（{len(catalog)} 个，快照日 "
              f"{catalog['snapshot_date'].iloc[0]}），复用；--catalog-only 强制刷新")
    else:
        catalog = collect_catalog()

    if args.catalog_only:
        return 0

    universe = list(config.BROAD_INDEX_POOL) + list(catalog["code"])
    print(f"     采集 universe = 13 宽基 + {len(catalog)} 概念 = {len(universe)} codes")
    bars = collect_bars(universe)

    # 汇总
    wide_dates = bars.groupby("symbol")["date"].agg(["min", "max", "count"])
    print("\n===== 采集汇总 =====")
    print(f"行数 {len(bars)} | codes {bars['symbol'].nunique()} | "
          f"日期 {bars['date'].min()} ~ {bars['date'].max()}")
    for code, name in list(config.BROAD_INDEX_POOL.items())[:3]:
        row = wide_dates.loc[code]
        print(f"  {name}({code}): {row['min']} ~ {row['max']}，{row['count']} 日")
    print(f"  概念日均行数: {wide_dates.loc[catalog['code']].mean():.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
