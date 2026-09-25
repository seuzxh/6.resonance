"""采集指数高频资金类指标（60min bar，moneyflow-gate 探索）。

口径（docs/moneyflow-gate-plan.md §3.1，预注册）：
- codes：三锚 + 情绪指数 + 全A + 中证1000（国证2000 代理）+ 国证2000（留零取证）；
- 12 指标（主动/被动 × 特大/大单金额 + 内外盘 + 涨跌家数）；
- Interval=60 → 4 bar/日，dataVol ≈ 11.8k/码·年 < MaxPoints 50k → 每码 1 请求；
- starttime 2025-09-20（接口滚动 1 年，最早 ~09-25，更早静默截断）。

用法：
    conda run -n resonance python work/collect_moneyflow.py
产出：data/cache/moneyflow_hourly.parquet + 控制台覆盖审计
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import requests  # noqa: E402

from resonance import config  # noqa: E402
from resonance.ifind import get_access_token  # noqa: E402

HF_URL = "https://quantapi.51ifind.com/api/v1/high_frequency"

CODES = [
    "399001.SZ",   # 深证成指（锚）
    "000688.SH",   # 科创50（锚）
    "399303.SZ",   # 国证2000（锚，预期零覆盖，留取证）
    "883404.TI",   # 同花顺情绪指数
    "883957.TI",   # 同花顺全A
    "000852.SH",   # 中证1000（国证2000 资金代理候选）
]

INDICATORS = [
    "buyVolume", "sellVolume", "raise_num", "fall_num",
    "active_buy_large_amt", "active_sell_large_amt",
    "active_buy_main_amt", "active_sell_main_amt",
    "possitive_buy_large_amt", "possitive_sell_large_amt",
    "possitive_buy_main_amt", "possitive_sell_main_amt",
]

START = "2025-09-20 09:15:00"
END = "2026-09-25 15:15:00"


def fetch_one(code: str) -> pd.DataFrame:
    access = get_access_token()
    payload = {
        "codes": code, "indicators": ",".join(INDICATORS),
        "starttime": START, "endtime": END,
        "functionpara": {"Fill": "Original", "Interval": "60"},
    }
    r = requests.post(HF_URL, json=payload,
                      headers={"Content-Type": "application/json",
                               "access_token": access}, timeout=300)
    data = r.json()
    ec = str(data.get("errorcode"))
    if ec not in ("0", "None", ""):
        raise RuntimeError(f"{code} errorcode={ec}: {data.get('errmsg')}")
    rows = []
    for t in data.get("tables") or []:
        tbl = t.get("table") or {}
        times = t.get("time") or []
        for k, ts in enumerate(times):
            row = {"symbol": code, "datetime": ts}
            for ind in INDICATORS:
                v = (tbl.get(ind) or [None] * len(times))[k]
                row[ind] = None if v in (None, "") else float(v)
            rows.append(row)
    return pd.DataFrame(rows)


def main() -> int:
    frames = []
    for code in CODES:
        df = fetch_one(code)
        n_days = df["datetime"].str[:10].nunique() if len(df) else 0
        zero_flag = ""
        if len(df):
            amt_cols = ["active_buy_large_amt", "active_sell_large_amt",
                        "active_buy_main_amt", "active_sell_main_amt"]
            tot = df[amt_cols].abs().sum().sum()
            if tot == 0:
                zero_flag = "  ← 金额全零（无覆盖）"
        print(f"{code}: rows={len(df)} days={n_days}"
              f"{' 首日=' + df['datetime'].min() if len(df) else ''}{zero_flag}")
        frames.append(df)
        time.sleep(0.6)
    out = pd.concat(frames, ignore_index=True)
    out["datetime"] = pd.to_datetime(out["datetime"])
    out = out.sort_values(["symbol", "datetime"]).reset_index(drop=True)
    path = config.CACHE_DIR / "moneyflow_hourly.parquet"
    out.to_parquet(path, index=False)
    print(f"[OK] {len(out)} rows → {path}")
    # 覆盖审计：逐码首末日 / 金额零日数
    for code in CODES:
        sub = out[out["symbol"] == code]
        if not len(sub):
            print(f"audit {code}: 空"); continue
        day = sub.assign(d=sub["datetime"].dt.strftime("%Y-%m-%d")).groupby("d")
        net = (day["active_buy_large_amt"].sum() + day["active_buy_main_amt"].sum()
               - day["active_sell_large_amt"].sum() - day["active_sell_main_amt"].sum())
        print(f"audit {code}: {net.index.min()}→{net.index.max()} "
              f"{len(net)}日 净额>0日数={(net > 0).sum()} 零值日数={(net == 0).sum()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
