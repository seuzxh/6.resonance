"""发布层：outputs/oos/*.csv → site/public/data/*.json → git commit + push。

挂在 ops/signal_daily.py 尾部自动执行，也可手动：
    python ops/publish_site.py             # 校验 + 写 JSON + commit + push
    python ops/publish_site.py --dry-run   # 校验 + 写 JSON，不动 git（自检用）

契约 v1（site/.vitepress/theme/components 消费）：
    recent.json   近 5 日日报流（锚相位 / 信号 / 两轨净值）
    nav.json      D3 整体 + 三锚分净值序列 + D3 绩效
    signals.json  D3/C1 全量信号事件（含持仓浮动）
    archive.json  docs/*.md 档案索引（链接到 GitHub 原文）

纪律：校验不过 → 一个文件都不写、退出 1（站点停在上一版）；
      PUBLISH_LAG=0 → 当日信号实时公开（2026-09-25 起，用户决策）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402

OUT = config.OUTPUTS_DIR / "oos"
SITE_DATA = Path(__file__).resolve().parents[1] / "site" / "public" / "data"
PUBLISH_LAG = 0                     # 实时公开（2026-09-25 用户反馈信号不可见，弃 T-1）
NAV_START = "2026-01-01"            # 展示净值起点（样本内+样本外连续，图上标注 OOS 起点）
SHOW_TRACKS = ("D3", "C1", "G2", "K5")   # 站点展示轨（D3 生产 + 三锚；C1=深证锚，展示名统一用锚名）
NAV_TRACKS = ("D3", "C1", "G2", "K5")  # 进净值图的轨（G2/K5 为三锚分净值对照）
TRACK_NAMES = {
    "D3": "D3 三锚动选（生产）",
    "C1": "深证成指锚",
    "G2": "国证 2000 锚",
    "K5": "科创 50 锚",
}
ANCHORS = config.V43_ANCHOR_POOL    # code -> name（三锚）
SPARK_DAYS = 30
ACT = {"entry": "买", "exit": "卖", "stop": "卖", "switch": "换"}
WD = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _names() -> dict[str, str]:
    cat = pd.read_csv(config.DATA_DIR / "concept_catalog.csv")
    m = dict(zip(cat["code"], cat["name"]))
    m.update(ANCHORS)
    return m


def _closes() -> pd.DataFrame:
    bars = pd.read_parquet(config.CACHE_DIR / "daily_bars.parquet")
    c = bars.pivot(index="date", columns="symbol", values="close").sort_index()
    c.index = pd.to_datetime(c.index)
    return c


def _nav(track: str) -> pd.Series:
    df = pd.read_csv(OUT / f"nav_{track}.csv")
    s = pd.Series(df["nav"].to_numpy(), index=pd.to_datetime(df["date"]))
    return s.dropna()


def _anchors_at(closes: pd.DataFrame, day: pd.Timestamp) -> list[dict]:
    rows = []
    for code, name in ANCHORS.items():
        s = closes[code].dropna()
        s = s.loc[:day]
        if len(s) < 11:
            continue
        spark = s.tail(SPARK_DAYS).round(3).tolist()
        chg = float(s.iloc[-1] / s.iloc[-2] - 1)
        ret10 = float(s.iloc[-1] / s.iloc[-11] - 1)
        rows.append({"code": code, "name": name, "state": "多" if ret10 > 0 else "空",
                     "chg": round(chg, 4), "spark": spark})
    return rows


def _trades(track: str) -> pd.DataFrame:
    f = OUT / f"trades_{track}.csv"
    if not f.exists():
        return pd.DataFrame(columns=["date", "type", "from", "to", "price"])
    t = pd.read_csv(f, dtype={"from": str, "to": str}).fillna("")
    t["date"] = pd.to_datetime(t["date"])
    return t


def validate(today: pd.Timestamp) -> tuple[str, pd.Series]:
    f = OUT / "nav_D3.csv"                     # 硬闸只看生产轨；其余轨缺官方文件走连续展示口径
    if not f.exists():
        raise SystemExit("[publish] 缺 nav_D3.csv（runner 未跑完？）拒绝发布")
    navs = {tr: _nav(tr) for tr in NAV_TRACKS if (OUT / f"nav_{tr}.csv").exists()}
    d3 = navs["D3"]
    if "C1" in navs and d3.index[-1] != navs["C1"].index[-1]:
        print(f"[publish] 警告：D3/C1 末日不一致（{d3.index[-1].date()} vs {navs['C1'].index[-1].date()}）")
    ret = d3.pct_change().abs()
    if (ret > 0.2).any():
        raise SystemExit(f"[publish] D3 单日 |{ret.max():.1%}| >20%，疑似脏数据，拒绝发布")
    as_of = d3.index.max()
    lag_days = (today.normalize() - as_of).days
    if lag_days > 4:
        print(f"[publish] 警告：数据滞后 {lag_days} 天（配额/采集中断？）")
    return str(as_of.date()), d3


def _stitched(tr: str, as_of: pd.Timestamp) -> pd.Series:
    """拼接展示口径净值：样本内 replay + 官方 OOS×样本内末值缩放；无官方文件则连续 replay。

    与 build_nav 曲线同口径（近五日卡片/天数窗口不再受官方 OOS 落盘起点限制）。"""
    f = OUT / f"nav_{tr}.csv"
    official = _nav(tr) if f.exists() else None
    end_in = str((official.index[0] - pd.Timedelta(days=1)).date()) if official is not None else str(as_of.date())
    nav_in = replay_display(tr, end_in)["nav_curve"]
    if official is not None:
        return pd.concat([nav_in, official * float(nav_in.iloc[-1])])
    return nav_in


def _trades_stitched(tr: str, cutoff: pd.Timestamp) -> pd.DataFrame:
    """拼接展示口径事件：replay（至 OOS 前一日）+ 官方 OOS；无官方文件则连续 replay。"""
    f_tr = OUT / f"trades_{tr}.csv"
    if f_tr.exists():
        prev = str((_nav(tr).index[0] - pd.Timedelta(days=1)).date())
        return pd.concat([replay_display(tr, prev)["trades"], _trades(tr)], ignore_index=True)
    return replay_display(tr, cutoff)["trades"]


def build_recent(names: dict, closes: pd.DataFrame, cutoff: pd.Timestamp) -> dict:
    d3 = _stitched("D3", cutoff)
    days_idx = [d for d in d3.index if d <= cutoff][-5:]
    days = []
    for day in days_idx:
        sigs = []
        for tr in SHOW_TRACKS:
            for _, r in _trades_stitched(tr, cutoff).iterrows():
                if r["date"] != day:
                    continue
                to = r["to"] if isinstance(r["to"], str) else ""
                fr = r["from"] if isinstance(r["from"], str) else ""
                code = to or fr
                sigs.append({"code": code.replace(".TI", ""), "name": names.get(code, code),
                             "meta": f"{ACT.get(r['type'], r['type'])} @{r['price']:.2f} · {TRACK_NAMES[tr]}"})
        tracks = []
        for tr in NAV_TRACKS:
            s = _stitched(tr, cutoff)
            if day not in s.index:
                continue
            i = s.index.get_loc(day)
            tracks.append({"track": TRACK_NAMES[tr], "nav": round(float(s.iloc[i]), 4),
                           "ret": None if i == 0 else round(float(s.iloc[i] / s.iloc[i - 1] - 1), 5)})
        days.append({"date": str(day.date()), "wd": WD[day.weekday()],
                     "anchors": _anchors_at(closes, day), "signals": sigs,
                     "tracks": tracks, "note": ""})
    last = days_idx[-1]
    aligned = sum(1 for a in days[-1]["anchors"] if a["state"] == "多")
    return {"version": 1, "as_of": str(last.date()),
            "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
            "health": "ok" if (pd.Timestamp.now().normalize() - last).days <= 4 else "stale",
            "align": {"aligned": aligned, "total": len(days[-1]["anchors"])},
            "days": days[::-1]}   # 最新在前


_REPLAY_CACHE: dict = {}


def replay_display(track: str, end_date: str) -> dict:
    """展示用净值重放：NAV_START 起全窗（读缓存，不触网、不动 OOS 官方落盘）。"""
    if (track, end_date) in _REPLAY_CACHE:
        return _REPLAY_CACHE[(track, end_date)]
    from resonance.v3 import MinuteBarProvider, V3Backtester
    from ops import signal_daily as sd
    close_all, concepts = sd.load_wide()
    m5 = pd.read_parquet(config.CACHE_DIR / "minute5_bars.parquet")
    m5["datetime"] = pd.to_datetime(m5["datetime"])
    prov = MinuteBarProvider(m5.pivot(index="datetime", columns="symbol", values="close").sort_index())
    bt = V3Backtester(close_all, concepts, broad_codes=sd.TRACKS[track],
                      params=sd.FROZEN, minute_bars_provider=prov)
    out = bt.run(NAV_START, end_date)
    _REPLAY_CACHE[(track, end_date)] = out
    return out


def _holdings_from_trades(tr: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp):
    """OOS 官方段持仓推导：空仓起步，事件推进，按日历日展开（跳过周末）。"""
    # 展开为逐日：cur 期间每天持有
    hold_days, cur = [], None
    events = list(tr.itertuples(index=False))
    def cur_at(day):
        hold = None
        for r in events:
            if pd.Timestamp(r.date) > day:
                break
            to = r.to if isinstance(r.to, str) else ""
            if r.type == "entry" or (r.type == "switch" and to):
                hold = to
            elif r.type in ("exit", "stop"):
                hold = None
        return hold
    for d in pd.date_range(start, end):
        if d.weekday() >= 5:
            continue
        h = cur_at(d)
        if h:
            hold_days.append([str(d.date()), h.replace(".TI", ""), h])
    return hold_days


def build_nav(names: dict, as_of: str) -> dict:
    """净值序列＝样本内展示重放（NAV_START→OOS 前一日）＋ 官方 OOS 净值缩放拼接。

    口径纪律：样本外段与 outputs/oos 官方文件逐点一致（空仓起步语义），
    仅按样本内末值缩放纵轴衔接；events/holdings 同样拼接，供曲线 tooltip。
    """
    from resonance.backtest import perf_stats
    series, stats = [], {}
    for tr in NAV_TRACKS:
        f = OUT / f"nav_{tr}.csv"
        official = _nav(tr) if f.exists() else None   # G2/K5 等新轨官方文件未生成前走连续展示口径
        end_in = str((official.index[0] - pd.Timedelta(days=1)).date()) if official is not None else as_of
        try:
            out = replay_display(tr, end_in)
            nav_in = out["nav_curve"]
            tr_in = out["trades"]
            hold_in = out["holdings"]
        except Exception as ex:  # noqa: BLE001
            print(f"[publish] 警告：{tr} 展示重放失败（{str(ex)[:60]}）")
            if official is None:
                continue                            # 无官方可退，跳过该轨
            nav_in, tr_in, hold_in = None, None, None
        if nav_in is not None:
            if official is not None:
                scale = float(nav_in.iloc[-1])
                nav = pd.concat([nav_in, official * scale])   # 样本外=官方口径（空仓起步）
            else:
                nav = nav_in                                   # 样本外=连续展示口径
        else:
            nav = official
        pts = [[str(d.date()), round(float(v), 4)] for d, v in nav.items()]
        events, seen = [], set()
        if tr_in is not None and len(tr_in):
            for _, r in tr_in.iterrows():
                to = r["to"] if isinstance(r["to"], str) else ""
                fr = r["from"] if isinstance(r["from"], str) else ""
                code = to or fr
                k = (str(pd.Timestamp(r["date"]).date()), code)
                if k not in seen:
                    seen.add(k)
                    events.append([k[0], ACT.get(r["type"], r["type"]),
                                   code.replace(".TI", ""), names.get(code, code)])
        if official is not None:
            for _, r in _trades(tr).iterrows():  # OOS 官方事件
                to = r["to"] if isinstance(r["to"], str) else ""
                fr = r["from"] if isinstance(r["from"], str) else ""
                code = to or fr
                events.append([str(pd.Timestamp(r["date"]).date()), ACT.get(r["type"], r["type"]),
                               code.replace(".TI", ""), names.get(code, code)])
        holdings = []
        if hold_in is not None and len(hold_in):
            for d, row in hold_in.iterrows():
                h = row.get("holding")
                if isinstance(h, str) and h:
                    holdings.append([str(pd.Timestamp(d).date()), h.replace(".TI", ""), names.get(h, h)])
        if official is not None:
            for d, code, raw in _holdings_from_trades(_trades(tr), official.index[0],
                                                      pd.Timestamp(as_of)):
                holdings.append([d, code, names.get(raw, raw)])
        series.append({"track": tr, "name": TRACK_NAMES[tr], "points": pts,
                       "events": events, "holdings": holdings})
        if tr == "D3":
            st = perf_stats(nav)
            stats["D3"] = {"nav": round(float(nav.iloc[-1]), 4),
                           "total_ret": round(float(st["total_return"]), 4),
                           "max_dd": round(float(st["max_drawdown"]), 4)}
    return {"version": 1, "start": NAV_START, "oos_start": "2026-09-22",
            "as_of": series[0]["points"][-1][0] if series else "",
            "stats": stats, "series": series}


def build_signals(names: dict, closes: pd.DataFrame, cutoff: pd.Timestamp) -> dict:
    rows = []
    for tr in SHOW_TRACKS:
        f_tr = OUT / f"trades_{tr}.csv"
        if f_tr.exists():
            # 样本内展示重放（至 OOS 前一日）＋ 官方 OOS 事件，与净值拼接口径一致
            prev = str((_nav(tr).index[0] - pd.Timedelta(days=1)).date())
            tdf = pd.concat([replay_display(tr, prev)["trades"], _trades(tr)], ignore_index=True)
        else:
            tdf = replay_display(tr, cutoff)["trades"]   # G2/K5 无官方落盘 → 连续展示口径
        open_pos = None
        for _, r in tdf.iterrows():
            if r["date"] > cutoff:
                continue
            typ = r["type"]
            if typ == "entry":
                open_pos = {"date": r["date"], "code": r["to"], "price": float(r["price"])}
            elif typ in ("exit", "stop") and open_pos:
                hold = int((r["date"] - open_pos["date"]).days)
                ret = float(r["price"]) / open_pos["price"] - 1
                rows.append({"date": str(r["date"].date()), "track": TRACK_NAMES[tr],
                             "code": open_pos["code"].replace(".TI", ""),
                             "name": names.get(open_pos["code"], open_pos["code"]),
                             "action": "卖", "entry": round(open_pos["price"], 2),
                             "exit": round(float(r["price"]), 2), "ret": round(ret, 4), "hold": hold})
                open_pos = None
            elif typ == "switch" and open_pos:
                rows.append({"date": str(r["date"].date()), "track": TRACK_NAMES[tr],
                             "code": open_pos["code"].replace(".TI", ""),
                             "name": names.get(open_pos["code"], open_pos["code"]),
                             "action": "换", "entry": round(open_pos["price"], 2),
                             "exit": None, "ret": None,
                             "hold": int((r["date"] - open_pos["date"]).days)})
                open_pos = {"date": r["date"], "code": r["to"], "price": float(r["price"])}
        if open_pos and open_pos["date"] <= cutoff:      # 未平仓 → 持仓行（浮动）
            px = closes[open_pos["code"]].dropna()
            px = px.loc[:cutoff]
            last_px = float(px.iloc[-1])
            rows.append({"date": str(open_pos["date"].date()), "track": TRACK_NAMES[tr],
                         "code": open_pos["code"].replace(".TI", ""),
                         "name": names.get(open_pos["code"], open_pos["code"]),
                         "action": "持", "entry": round(open_pos["price"], 2),
                         "exit": round(last_px, 2),
                         "ret": round(last_px / open_pos["price"] - 1, 4),
                         "hold": int((cutoff - open_pos["date"]).days)})
    rows.sort(key=lambda r: r["date"], reverse=True)   # 最新在前（compact 取头 N 条=真最新）
    return {"version": 1, "as_of": str(cutoff.date()), "rows": rows}


def build_archive() -> dict:
    root = Path(__file__).resolve().parents[1]
    items = []
    # docs/ 已按生命周期归类为 spec/ ops/ research/ data/ 子目录（2026-09-26）；
    # README.md 是导航页，不入档案流
    for f in sorted((root / "docs").rglob("*.md")):
        rel = f.relative_to(root).as_posix()
        if f.name == "README.md":
            continue
        date = subprocess.run(["git", "log", "-1", "--format=%as", "--", str(f)],
                              capture_output=True, text=True, cwd=root).stdout.strip() or "—"
        title = ""
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        if not title:
            continue
        items.append({"date": date, "title": title,
                      "url": f"https://github.com/seuzxh/6.resonance/blob/master/{rel}"})
    items.sort(key=lambda x: x["date"], reverse=True)
    return {"version": 1, "items": items[:12]}


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="写 JSON 但不 commit/push")
    args = ap.parse_args(argv)

    today = pd.Timestamp.now()
    as_of, _ = validate(today)
    cutoff = pd.Timestamp(as_of) - pd.Timedelta(days=PUBLISH_LAG)
    cutoff = pd.Timestamp(_nav("D3").index[_nav("D3").index <= cutoff][-1])  # 对齐交易日

    names = _names()
    closes = _closes()
    payload = {
        "recent.json": build_recent(names, closes, cutoff),
        "nav.json": build_nav(names, as_of),
        "signals.json": build_signals(names, closes, cutoff),
        "archive.json": build_archive(),
    }
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    for fname, obj in payload.items():
        (SITE_DATA / fname).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    days_n = len(payload["recent.json"]["days"])
    print(f"[publish] 已写出 {SITE_DATA}/（截至 {payload['recent.json']['as_of']}，"
          f"近 {days_n} 日，{len(payload['signals.json']['rows'])} 条信号）")

    if args.dry_run:
        print("[publish] dry-run：不执行 git")
        return 0
    root = Path(__file__).resolve().parents[1]
    subprocess.run(["git", "add", "site/public/data"], cwd=root, check=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=root).returncode
    if diff == 0:
        print("[publish] 数据无变化，跳过提交")
        return 0
    msg = f"site-data: as of {payload['recent.json']['as_of']}"
    subprocess.run(["git", "commit", "-m", msg], cwd=root, check=True)
    r = subprocess.run(["git", "push", "origin", "HEAD:master"], cwd=root)
    if r.returncode != 0:
        print("[publish] push 失败（本地已 commit，网络恢复后手动 git push）")
        return 1
    print(f"[publish] 已提交并推送：{msg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
