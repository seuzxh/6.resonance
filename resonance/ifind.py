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
            if stripped.startswith("export "):
                stripped = stripped[len("export "):].strip()
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
    """POST + JSON 解码 + 一次刷新重试；配额类错误 fail-fast 不浪费刷新。

    errorcode≠0 的处理次序（2026-09-26 回补事故教训：任何非零码曾被一律
    当 token 失效去刷新，refresh 不可用时把真实错误码掩盖成 token 加载错）：
    ① 配额码直接抛（刷新救不了）；② 其余先刷新 token 重试一次（刷新不可用
    则沿用旧 token 原样重试——瞬时错误码常自愈）；③ 仍失败则抛出**原始**
    errorcode/errmsg。
    """
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
        code0 = str(data.get("errorcode"))
        if code0 in _QUOTA_ERRORCODES:
            raise IfindError(f"配额耗尽 errorcode={code0}: {data.get('errmsg')}")
        try:
            access = _refresh_access_token()
        except Exception:  # noqa: BLE001 — 刷新不可用：带原码重试，不掩盖真实错误
            access = get_access_token()
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


# --- basic_data_service：指数代码 → 简称（概念目录快照） ---
# 目录段边界实测（2026-09-19）：885010~885400 均无效、885500+ 有效（通用航空）、
# 886001 有效（高压快充）、886400+ 无效、887 段不存在。低 885xxx 无效。
NAME_PROBE_SENTINEL = "885999.TI"  # 已知有效（汽车热管理），用作全无效批次的哨兵


# --- high_frequency：分钟K线全指标（2026-09-19 实测口径；09-26 多指标扩展） ---
# ① 端点 ft.10jqka.com.cn（与 history_quotation 共享月度配额池）；② 覆盖：宽基
# 11/13（缺 700050/932000，均已退役 RETIRED_NO_HF_CODES）、概念 389/529——无分钟
# 数据的代码在响应中返回 0 bar；③ Fill="Forward" 对无数据代码返回字面字符串
# "Forward"（伪数据，禁用）；④ 历史仅滚动 ~1 年（更早 errorcode=-4309）→ 全
# 指标回补要趁窗口；⑤ Interval=5 → 48 bar/日、Interval=60 → 4 bar/日（bar
# 结束时刻）；⑥ 响应 MaxPoints≈50000——dataVol=指标数×bar 数，多指标下请求
# 由 _minute_request_plan 按点数预算自动切分。
MINUTE_CODES_PER_REQUEST = 20
# 高频全指标（2026-09-26 用户指令：指标直接取接口，不做本地推导）。返回列名
# snake_case 化（avgPrice→avg_price、changeRatio→change_ratio），值为接口原样、
# 量纲未缩放。dataVol=指标数×bar 数，全指标成本≈close 单指标口径的 9 倍。
MINUTE_INDICATORS = ("open", "high", "low", "close", "avgPrice",
                     "volume", "amount", "change", "changeRatio")
_INDICATOR_COLS = {"avgPrice": "avg_price", "changeRatio": "change_ratio"}
MINUTE_INDICATOR_COLS = tuple(
    _INDICATOR_COLS.get(k, k) for k in MINUTE_INDICATORS
)  # ("open",...,"avg_price",...,"change_ratio")
_BARS_PER_DAY = {"1": 240, "5": 48, "60": 4}
_MAX_POINTS_PER_REQUEST = 45000  # 响应 MaxPoints≈50k，留 10% 余量


def _minute_request_plan(codes: list[str], start_date: str, end_date: str,
                         n_ind: int, interval: str):
    """按点数预算切分（codes×预估bar×指标 ≤ 45k）→ yield (chunk, s, e)。"""
    ppd = _BARS_PER_DAY.get(interval, 48) * n_ind
    s0 = dt.date.fromisoformat(start_date)
    e0 = dt.date.fromisoformat(end_date)
    for i in range(0, len(codes), MINUTE_CODES_PER_REQUEST):
        chunk = codes[i : i + MINUTE_CODES_PER_REQUEST]
        max_days = max(1, _MAX_POINTS_PER_REQUEST // (len(chunk) * ppd))
        cur = s0
        while cur <= e0:
            nxt = min(cur + dt.timedelta(days=max_days - 1), e0)
            yield chunk, cur.isoformat(), nxt.isoformat()
            cur = nxt + dt.timedelta(days=1)


def fetch_minute_bars(
    codes: list[str],
    start_date: str,
    end_date: str,
    indicators=MINUTE_INDICATORS,
    interval: str = "5",
    day_start: str = "09:30:00",
) -> pd.DataFrame:
    """high_frequency 分钟K线全指标 → 长表 DF[symbol, datetime, <指标列...>]。

    请求自动按 MaxPoints 预算切分（长区间/多指标下拆多次请求），对调用方
    透明。空值（None/""）转 NaN，全空 bar 跳行；无分钟数据的代码静默缺行
    （调用方按 coverage 降级）。datetime 为 bar 结束时刻；
    day_start="12:00:00" 为下午盘省配额口径（13:05~15:00 共 24 根 5min bar）。
    """
    ind = list(indicators)
    cols = ["symbol", "datetime"] + [_INDICATOR_COLS.get(k, k) for k in ind]
    rows: list[tuple] = []
    multi = len(codes) > MINUTE_CODES_PER_REQUEST
    for chunk, s, e in _minute_request_plan(codes, start_date, end_date,
                                            len(ind), interval):
        payload = {
            "codes": ",".join(chunk),
            "indicators": ",".join(ind),
            "starttime": f"{s} {day_start}",
            "endtime": f"{e} 15:01:00",
            "functionpara": {"Interval": interval, "CPS": "-no", "Fill": "Original"},
        }
        resp = _post(config.IFIND_HF_URL, payload, timeout=300)
        for t in resp.get("tables") or []:
            code = t.get("thscode")
            times = t.get("time") or []
            tbl = t.get("table") or {}
            arrs = [tbl.get(k) or [] for k in ind]
            for j, ts in enumerate(times):
                vals, any_v = [], False
                for a in arrs:
                    v = a[j] if j < len(a) else None
                    if v is None or str(v) == "":
                        vals.append(float("nan"))
                    else:
                        vals.append(float(v))
                        any_v = True
                if any_v:
                    rows.append((code, ts, *vals))
        if multi:
            time.sleep(0.4)
    if not rows:
        df = pd.DataFrame(columns=cols)
        df["datetime"] = pd.to_datetime(df["datetime"])  # 空 .dt 访问安全
        return df
    df = pd.DataFrame(rows, columns=cols)
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df.sort_values(["symbol", "datetime"]).reset_index(drop=True)


def fetch_minute_close(
    codes: list[str],
    start_date: str,
    end_date: str,
    interval: str = "60",
    day_start: str = "09:30:00",
) -> pd.DataFrame:
    """close 单指标省配额口径（旧调用方兼容）；新采集一律用 fetch_minute_bars。"""
    return fetch_minute_bars(codes, start_date, end_date, indicators=["close"],
                             interval=interval, day_start=day_start)


def fetch_index_names(codes: list[str], chunk_size: int = 200) -> dict[str, str]:
    """批量查询指数简称（``ths_index_short_name_index``）。返回 {code: name}。

    无效代码在响应 ``tables`` 中被直接省略；**全无效批次**返回 errorcode=-4210
    （实测 885010~885400 单批全无效触发）。为区分"全无效"与"真参数错"，
    每批混入哨兵代码 NAME_PROBE_SENTINEL（已知有效）：哨兵在场时 -4210 只能是
    参数错误 → 正常抛出；全无效批次则 ec=0 且仅含哨兵。
    """
    out: dict[str, str] = {}
    sentinel = NAME_PROBE_SENTINEL
    for i in range(0, len(codes), chunk_size):
        chunk = [c for c in codes[i : i + chunk_size] if c != sentinel]
        resp = _post(
            config.IFIND_BASIC_URL,
            {
                "codes": ",".join([sentinel] + chunk),
                "indipara": [{"indicator": "ths_index_short_name_index", "indiparams": []}],
            },
        )
        tables = resp.get("tables") or []
        for t in tables:
            code = t.get("thscode")
            tbl = t.get("table") or {}
            names = tbl.get("ths_index_short_name_index") or []
            if code and names and names[0]:
                out[code] = str(names[0])
        if len(codes) > chunk_size:
            time.sleep(0.3)
    out.pop(sentinel, None)  # 哨兵不属于目录
    return out
