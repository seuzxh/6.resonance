"""采集概念指数资金流（moneyflow-gate，日线Top5并集 149 码）。

口径（plan §零修订）：4 金额指标（特大/大 × 主动买/卖）× Interval=60 ×
2025-09-20→采集日；10 码/请求（dataVol≈39k<50k），共 ~15 请求。

用法：
    conda run -n resonance python work/collect_concepts_moneyflow.py
产出：data/cache/moneyflow_concept.parquet + 零覆盖码清单
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
INDICATORS = ["active_buy_large_amt", "active_sell_large_amt",
              "active_buy_main_amt", "active_sell_main_amt"]
START, END = "2025-09-20 09:15:00", "2026-09-25 15:15:00"
CHUNK = 10


def fetch_chunk(codes: list[str]) -> pd.DataFrame:
    access = get_access_token()
    payload = {
        "codes": ",".join(codes), "indicators": ",".join(INDICATORS),
        "starttime": START, "endtime": END,
        "functionpara": {"Fill": "Original", "Interval": "60"},
    }
    r = requests.post(HF_URL, json=payload,
                      headers={"Content-Type": "application/json",
                               "access_token": access}, timeout=300)
    data = r.json()
    ec = str(data.get("errorcode"))
    if ec not in ("0", "None", ""):
        raise RuntimeError(f"errorcode={ec}: {data.get('errmsg')}")
    rows = []
    for t in data.get("tables") or []:
        code = t.get("thscode")
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
    codes = pd.read_csv(config.OUTPUTS_DIR / "exp_moneyflow" / "flow_codes_daily_top5.csv")["code"].tolist()
    print(f"待采 {len(codes)} 码")
    frames, nocover = [], []
    for i in range(0, len(codes), CHUNK):
        chunk = codes[i:i + CHUNK]
        df = fetch_chunk(chunk)
        got = set(df["symbol"]) if len(df) else set()
        for c in chunk:
            sub = df[df["symbol"] == c] if len(df) else pd.DataFrame()
            if not len(sub) or sub[INDICATORS].abs().sum().sum() == 0:
                nocover.append(c)
        frames.append(df)
        print(f"  [{i // CHUNK + 1}/{(len(codes) + CHUNK - 1) // CHUNK}] {chunk[0]}…{chunk[-1]} "
              f"rows={len(df)}")
        time.sleep(0.8)
    out = pd.concat(frames, ignore_index=True)
    out["datetime"] = pd.to_datetime(out["datetime"])
    out = out[out[INDICATORS].abs().sum(axis=1) > 0]  # 剔全零行（休码/交易所段无谓行）
    out = out.sort_values(["symbol", "datetime"]).reset_index(drop=True)
    path = config.CACHE_DIR / "moneyflow_concept.parquet"
    out.to_parquet(path, index=False)
    print(f"[OK] {len(out)} rows，覆盖码 {out['symbol'].nunique()} → {path}")
    print(f"零覆盖码 {len(nocover)}: {nocover}")
    (config.OUTPUTS_DIR / "exp_moneyflow").mkdir(parents=True, exist_ok=True)
    pd.Series(nocover, name="code").to_csv(
        config.OUTPUTS_DIR / "exp_moneyflow" / "nocover_concepts.csv", index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
