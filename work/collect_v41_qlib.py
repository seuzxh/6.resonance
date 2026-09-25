"""V4.1 日线补采（qlib 本地链路）：上证指数 000001.SH / 深证成指 399001.SZ。

背景：V4.1 指数池新增两指数；iFinD 月度配额耗尽（-4318），改走本地 qlib 库
~/.qlib/qlib_data/cn_data（独立数据链路，项目已在 work/validate_data.py 中
建立对照口径）。对齐依据：4 个双重存在宽基（399006/000300/000905/000852）
滑动对齐全部给出 b0=1152、相对误差中位 ≤0.9bp，且两新代码 bin_len=1632 与
之一致（同一批 dump）→ 沿用 b0=1152。

用法：
    conda run -n resonance python work/collect_v41_qlib.py
输出：追加至 data/cache/daily_bars.parquet（幂等：已存在则跳过）。
pre_close/pct_chg 由 close 推导，turnover_ratio 置 NaN（v3/v4 信号仅用 close）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402

QLIB_DIR = Path.home() / ".qlib/qlib_data/cn_data"
NEW_CODES = {"000001.SH": "sh000001", "399001.SZ": "sz399001"}
# bin 头约定（由 4 指数滑动对齐交叉验证 + 值级核对推得）：bin[0] = 起始日历
# 下标，真实数据为 bin[1:]。amount 字段 dump 损坏（仅 1 值）→ 置 NaN，不用。
SPAN = ("2024-10-08", "2026-09-18")


def _read_bin(qc: str, field: str, cal: list[str]) -> pd.Series:
    arr = np.fromfile(QLIB_DIR / "features" / qc / f"{field}.day.bin", dtype="<f")
    start = int(arr[0])
    vals = arr[1:]
    if start + len(vals) > len(cal):  # 头损坏或越界 → 弃用
        return pd.Series(dtype=float)
    return pd.Series(vals, index=pd.Index(cal[start : start + len(vals)]))


def load_qlib_daily() -> pd.DataFrame:
    cal = (QLIB_DIR / "calendars/day.txt").read_text().split()
    frames = []
    for ifind_code, qc in NEW_CODES.items():
        df = pd.DataFrame({f: _read_bin(qc, f, cal)
                           for f in ("open", "high", "low", "close", "volume")})
        df["amount"] = np.nan
        df = df.loc[SPAN[0] : SPAN[1]].dropna(subset=["close"])
        assert 400 < len(df) < 500, f"{ifind_code} 行数异常：{len(df)}"
        df["symbol"] = ifind_code
        df["date"] = df.index.astype(str)
        df["pre_close"] = df["close"].shift(1)
        df["pct_chg"] = (df["close"] / df["pre_close"] - 1.0) * 100.0
        df["turnover_ratio"] = np.nan
        frames.append(df.reset_index(drop=True))
    return pd.concat(frames, ignore_index=True)


def main() -> int:
    f = config.CACHE_DIR / "daily_bars.parquet"
    bars = pd.read_parquet(f)
    todo = {k: v for k, v in NEW_CODES.items() if k not in set(bars["symbol"])}
    if not todo:
        print("[OK] 两指数日线已存在，跳过")
        return 0
    new = load_qlib_daily()
    new = new[list(bars.columns)]
    for _, r in new.groupby("symbol"):
        c = r["symbol"].iloc[0]
        print(f"[{c}] {len(r)} 行 {r['date'].min()}~{r['date'].max()} "
              f"close {r['close'].iloc[0]:.0f}->{r['close'].iloc[-1]:.0f} "
              f"区间[{r['close'].min():.0f},{r['close'].max():.0f}]")
    out = pd.concat([bars, new[new["symbol"].isin(todo)]], ignore_index=True)
    out.to_parquet(f, index=False)
    print(f"[OK] daily_bars 写回：{out['symbol'].nunique()} codes × {len(out)} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
