"""Thin iFinD HTTP client：refresh_token → access_token 缓存 → history_data 指数日线。

调用格式对齐 3.qlib_ifind_beta 的实战验证版本：
- body 包裹 ``{"reqBody": {...}}``；token 走请求头 ``access_token``；
- history_data 单次 codes >10 会静默截断，按 10 个/批分块；
- 响应结构 ``tables: [{thscode, time: [...], table: {indicator: [vals]}}]``。

凭证纪律（与 3.qlib_ifind_beta 相同）：
- refresh_token 从环境变量 ``IFIND_REFRESH_TOKEN`` 或全局脚本源解析，绝不复制进本仓库；
- access_token 扁平缓存于 ``/home/zxh/qlib_data/.ifind_token``（多项目共享，避免重复刷新）；
- 仅 I/O，解析在 ``parse_history_data``。
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from pathlib import Path

import pandas as pd
import requests

from . import config

CODES_PER_REQUEST = 10  # history_data >10 codes 会静默截断（项目3实战结论）

# 配额类错误码：重试无意义，直接失败（对照 2.qlib_ifind_hot_concept 错误码表）
_QUOTA_ERRORCODES = {"-4301", "-4302", "-4303", "-4317", "-4318", "-4321"}


class IfindError(RuntimeError):
    """永久性 iFinD 失败（参数错、配额耗尽、非 JSON 响应）——不应退避重试。"""


def _errcode_ok(payload: dict) -> bool:
    return str(payload.get("errorcode")) in ("0", "None", "", "nan", "None")


# --- refresh_token loader（镜像项目3 canonical 模式） ---
def load_refresh_token() -> str:
    import os

    token = os.environ.get("IFIND_REFRESH_TOKEN", "").strip()
    if token:
        return token
    for path in config.REFRESH_TOKEN_PATHS:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("IFIND_REFRESH_TOKEN") and "=" in stripped:
                _, rhs = stripped.split("=", 1)
                token = rhs.strip().strip('"').strip("'")
                if token:
                    return token
    searched = ", ".join(str(p) for p in config.REFRESH_TOKEN_PATHS)
    raise RuntimeError(f"IFIND_REFRESH_TOKEN not found in environment or: {searched}")


# --- access_token 扁平缓存 ---
def _read_token_cache() -> tuple[str | None, str | None]:
    f = config.IFIND_TOKEN_FILE
    if not f.exists():
        return None, None
    tok = exp = None
    for line in f.read_text().splitlines():
        if line.startswith("access_token="):
            tok = line.split("=", 1)[1].strip()
        elif line.startswith("expired_time="):
            exp = line.split("=", 1)[1].strip()
    return tok, exp


def _token_expired(expired_time: str | None) -> bool:
    if not expired_time:
        return True
    m = re.match(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})", expired_time)
    if not m:
        return True
    try:
        exp = dt.datetime.fromisoformat(m.group(0))
    except ValueError:
        return True
    return dt.datetime.now() >= exp - dt.timedelta(minutes=10)  # 提前 10 分钟视为过期


def _refresh_access_token() -> str:
    refresh = load_refresh_token()
    resp = requests.post(
        config.IFIND_TOKEN_URL,
        headers={"Content-Type": "application/json"},
        json={"refresh_token": refresh},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not _errcode_ok(data):
        raise IfindError(f"token refresh failed: errorcode={data.get('errorcode')} {data.get('errmsg')}")
    access = data["data"]["access_token"]
    exp = data["data"]["expired_time"]
    config.IFIND_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.IFIND_TOKEN_FILE.write_text(f"access_token={access}\nexpired_time={exp}\n", encoding="utf-8")
    return access


def get_access_token() -> str:
    """取有效 access_token：缓存可用则复用，否则用 refresh_token 换新。"""
    tok, exp = _read_token_cache()
    if tok and not _token_expired(exp):
        return tok
    return _refresh_access_token()


# --- HTTP core ---
def _post(url: str, payload: dict, timeout: int = 120) -> dict:
    """POST + JSON 解码 + token 失效刷新重试一次；永久错误 fail-fast。"""
    access = get_access_token()
    r = requests.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json", "access_token": access},
        timeout=timeout,
    )
    if 400 <= r.status_code < 500 and r.status_code != 429:
        raise IfindError(f"iFinD HTTP {r.status_code} (permanent) @ {url}: {r.text[:200]!r}")
    if r.status_code >= 500 or r.status_code == 429:
        raise IfindError(f"iFinD HTTP {r.status_code} (transient) @ {url}: {r.text[:200]!r}")
    data = r.json()
    if not _errcode_ok(data):
        # token 失效签名：刷新一次重试一次
        access = _refresh_access_token()
        data = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json", "access_token": access},
            timeout=timeout,
        ).json()
    if not _errcode_ok(data):
        code = str(data.get("errorcode"))
        if code in _QUOTA_ERRORCODES:
            raise IfindError(f"配额耗尽 errorcode={code}: {data.get('errmsg')}")
        raise IfindError(f"iFinD errorcode={code} @ {url}: errormsg={data.get('errmsg')!r}")
    return data


# --- history_data：指数/概念日线（自动按 10 codes/批分块） ---
def fetch_history_data(
    codes: list[str],
    start_date: str,
    end_date: str,
    indicators: list[str] | None = None,
    cps: str = "0",
) -> list[pd.DataFrame]:
    """history_data（CPS 缺省 0 = raw）。返回每批的解析 DF 列表（长表）。

    日期格式 ``YYYY-MM-DD``；区间 >1 个月需调用方分段（-4308）。
    """
    if indicators is None:
        indicators = list(config.HD_FIELD_MAP.keys())
    frames: list[pd.DataFrame] = []
    for i in range(0, len(codes), CODES_PER_REQUEST):
        chunk = codes[i : i + CODES_PER_REQUEST]
        payload = {
            "reqBody": {
                "codes": ",".join(chunk),
                "startdate": start_date,
                "enddate": end_date,
                "indicators": ",".join(indicators),
                "functionpara": {"CPS": cps},
            }
        }
        resp = _post(config.IFIND_HISTORY_URL, payload)
        frames.append(parse_history_data(resp, indicators))
        if len(codes) > CODES_PER_REQUEST:
            time.sleep(0.5)
    return frames


def parse_history_data(resp: dict, indicators: list[str]) -> pd.DataFrame:
    """history_data / date_sequence 响应 ``tables:[{thscode,time,table}]`` → 长表 DF。

    输出列 ``[symbol, date, <indicators>]``（一行一 code×date）。
    """
    field_map = config.HD_FIELD_MAP
    tables = resp.get("tables") or resp.get("result", {}).get("tables", [])
    frames = []
    for t in tables:
        code = t.get("thscode") or t.get("code") or t.get("symbol")
        times = t.get("time") or t.get("times")
        tbl = t.get("table") or t.get("columns") or {}
        if code is None or times is None or not tbl:
            continue
        col = {"symbol": code, "date": list(times)}
        for ind in indicators:
            vals = tbl.get(ind)
            if vals is not None:
                col[field_map.get(ind, ind)] = list(vals)
        frames.append(pd.DataFrame(col))
    if not frames:
        return pd.DataFrame(columns=["symbol", "date"])
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df
