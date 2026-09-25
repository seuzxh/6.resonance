"""high_frequency 资金类指标探测（2026-09-25，新探索：指数共振 × 资金流）。

目的（探测期，省配额，≤8 次请求）：
1. 端点/参数验证：quantapi.51ifind.com/api/v1/high_frequency（用户示例）与
   ft.10jqka.com.cn（既有 close 口径）双端点对照；
2. 覆盖：三锚（399001.SZ/399303.SZ/000688.SH，交易所指数）与同花顺指数
   （883404.TI/883957.TI）是否返回资金类数据（文档分组名"同花顺指数专有
   指标"提示可能仅 TI 段有值——必须实测）；
3. 语义：Interval=5 bar 值是 bar 内流量还是开盘累计（日內单调性判别）；
4. 历史 depth：单指标 Interval=60 逐步回拨起始日（-4309 = 越界）。

用法：
    conda run -n resonance python work/probe_moneyflow.py
凭证：走 resonance.ifind.get_access_token（缓存 /home/zxh/qlib_data/.ifind_token）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

from resonance import config  # noqa: E402
from resonance.ifind import get_access_token  # noqa: E402

QUANTAPI_HF = "https://quantapi.51ifind.com/api/v1/high_frequency"
FT_HF = config.IFIND_HF_URL

# 探测集：主力(=大单)金额买卖、特大金额买卖、涨跌家数、内外盘、总委托
PROBE_IND = ("buyVolume,sellVolume,trans_num,raise_num,fall_num,"
             "total_delegate_buy_vol,total_delegate_sell_vol,"
             "active_buy_large_amt,active_sell_large_amt,"
             "active_buy_main_amt,active_sell_main_amt")


def call(url: str, codes: str, indicators: str, start: str, end: str,
         interval: str = "5") -> dict:
    access = get_access_token()
    payload = {
        "codes": codes, "indicators": indicators,
        "starttime": start, "endtime": end,
        "functionpara": {"Fill": "Original", "Interval": interval},
    }
    r = requests.post(url, json=payload,
                      headers={"Content-Type": "application/json",
                               "access_token": access}, timeout=120)
    # content-type 为 text/plain 但 body 是 JSON（实测 2026-09-25），直接 r.json()
    data = r.json()
    return {"http": r.status_code, "payload": payload, "resp": data}


def show(tag: str, res: dict, max_rows: int = 6) -> None:
    d = res["resp"]
    ec, em = d.get("errorcode"), d.get("errmsg") or d.get("errormsg")
    print(f"\n== [{tag}] http={res['http']} errorcode={ec} {em or ''}")
    tables = d.get("tables") or []
    for t in tables[:4]:
        code = t.get("thscode")
        times = t.get("time") or []
        tbl = t.get("table") or {}
        print(f"  {code}: n_bars={len(times)} first={times[:2]} last={times[-2:]}")
        for ind, vals in list(tbl.items())[:12]:
            vv = vals[:max_rows]
            print(f"    {ind:26s} {vv} …")


def main() -> int:
    # ---- P1: 双端点对照（quantapi vs ft），4 codes × 11 指标 × 2 日，Interval=5
    for tag, url in (("P1a quantapi", QUANTAPI_HF), ("P1b ft.10jqka", FT_HF)):
        res = call(url, "399001.SZ,399303.SZ,000688.SH,883404.TI",
                   PROBE_IND, "2026-09-24 09:15:00", "2026-09-24 15:15:00")
        show(tag, res)

    # ---- P2: 语义判别——883404.TI 单日 5min 全序列，看主动买额是否日内单调（累计）或独立（流量）
    res = call(QUANTAPI_HF, "883404.TI",
               "active_buy_main_amt,active_sell_main_amt,active_buy_large_amt,raise_num,fall_num,buyVolume,sellVolume",
               "2026-09-24 09:15:00", "2026-09-24 15:15:00")
    d = res["resp"]
    t = (d.get("tables") or [{}])[0]
    times = t.get("time") or []
    tbl = t.get("table") or {}
    print("\n== [P2] 883404.TI 2026-09-24 全日 5min 序列（语义判别）")
    for ind in ("active_buy_main_amt", "active_buy_large_amt", "raise_num", "buyVolume"):
        vals = tbl.get(ind) or []
        if vals:
            nums = [v for v in vals if v not in (None, "")]
            mono = all(float(nums[i]) <= float(nums[i + 1]) for i in range(len(nums) - 1))
            print(f"  {ind:26s} n={len(nums)} 首值={nums[0]} 尾值={nums[-1]} 日内单调={mono} "
                  f"序列(前8)={nums[:8]}")

    # ---- P3: 历史 depth——单指标 Interval=60 逐步回拨（883404.TI 与 399001.SZ 各测）
    for code in ("883404.TI", "399001.SZ"):
        for start in ("2026-06-01 09:15:00", "2026-01-01 09:15:00",
                      "2025-09-01 09:15:00", "2025-01-01 09:15:00"):
            res = call(QUANTAPI_HF, code, "active_buy_main_amt",
                       start, "2026-09-24 15:15:00", interval="60")
            d = res["resp"]
            ec = d.get("errorcode")
            tables = d.get("tables") or []
            n = sum(len((t.get("time") or [])) for t in tables)
            print(f"\n== [P3] {code} start={start[:10]} ec={ec} bars={n} "
                  f"{d.get('errmsg') or d.get('errormsg') or ''}")
            if ec not in ("0", None):
                break

    # ---- P4: quantapi 主机 history_data 兼容性冒烟（日线额度是否同池，不做多次）
    print("\n[P4] 跳过（日线链路已由 collect 验证，不重复耗配额）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
