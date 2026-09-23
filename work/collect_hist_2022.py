"""历史扩展采集：2022-01→2024-12 验证窗的日线（2026-09-23 用户指令）。

范围：2021-12-01（预留 10 日 lookback + 相位回看）→ 2024-10-07（缓存起始
2024-10-08 前一日），覆盖 529 概念 + 全部曾入池指数（三锚/9池/13池/情绪指数）
+ 同花顺全A。长区间实测可单请求完成（~690 行/代码）；断点续传按 code×区间
幂等，写回 daily_bars.parquet 去重。

用法：
    conda run -n resonance python work/collect_hist_2022.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.ifind import fetch_history_data  # noqa: E402

START, END = "2021-12-01", "2024-10-07"
EXTRA_INDEXES = [
    "399001.SZ", "399303.SZ", "000688.SH",          # V4.3 三锚
    "883957.TI", "000001.SH", "883417.TI", "883404.TI",
    "000300.SH", "000905.SH", "000852.SH", "399006.SZ", "700050.TI",
    "000680.SH", "000016.SH", "899050.BJ", "932000.CSI", "000015.SH",
]


def main() -> int:
    f = config.CACHE_DIR / "daily_bars.parquet"
    bars = pd.read_parquet(f)
    catalog = pd.read_csv(config.DATA_DIR / "concept_catalog.csv")
    codes = sorted(set(catalog["code"]) | set(EXTRA_INDEXES) | set(bars["symbol"]))
    # 已含该区间的 code 跳过（幂等）
    have = bars[bars["date"] < "2024-10-08"]["symbol"].unique()
    todo = [c for c in codes if c not in set(have)]
    print(f"[需求] {len(codes)} codes，待采 {len(todo)}（区间 {START}~{END}）")
    if not todo:
        return 0
    frames = []
    for k in range(0, len(todo), 10):
        chunk = todo[k : k + 10]
        for attempt in range(3):
            try:
                frames.extend(fetch_history_data(chunk, START, END))
                break
            except Exception as ex:  # noqa: BLE001
                print(f"  批 {chunk[0]}~ 重试 {attempt + 1}: {str(ex)[:110]}")
                time.sleep(3.0)
        else:
            print(f"  批 {chunk[0]}~ 三次失败，跳过（重跑可续）")
            continue
        if (k // 10 + 1) % 10 == 0:
            print(f"  … {k + 10}/{len(todo)} codes")
        time.sleep(0.5)
    if frames:
        new = pd.concat(frames, ignore_index=True)
        out = pd.concat([bars, new], ignore_index=True)
        out = out.drop_duplicates(subset=["symbol", "date"]).sort_values(
            ["symbol", "date"]).reset_index(drop=True)
        out.to_parquet(f, index=False)
        print(f"[写回] +{len(new)} 行（总 {len(out)}）")
        got = set(new["symbol"])
        miss = [c for c in todo if c not in got]
        print(f"[核验] 采到 {len(got)} codes；未采到 {len(miss)}：{miss[:10]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
