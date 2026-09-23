"""样本外验证每日 runner（docs/oos-validation-design.md，2026-09-22 启动）。

用法（每日收盘后）：
    conda run -n resonance python work/signal_daily.py            # 双轨（9池+13池）
    conda run -n resonance python work/signal_daily.py --date 2026-09-23
    conda run -n resonance python work/signal_daily.py --track A9   # 单轨

流程（每轨）：
1. 增量日线：缓存全部 codes 补至 --date（iFinD history_data，10/批）；
2. 自愈分钟：OOS 窗内每日 Top5+领先的尾盘 24bar 需求检查，缺失即补采
   （下午盘省配额口径；微盘股无 HF 覆盖跳过）；
3. 无状态重放：V3Backtester 自 OOS 起点（2026-09-22，空仓起步）重放至
   --date，冻结参数（设计文档 §二）；
4. 落盘：outputs/oos/signals_{track}.csv（逐日动作与指令）、nav_{track}.csv、
   控制台输出当日指令（T 信号 → T+1 收盘执行）。

幂等：重跑同一天结果逐位一致（无状态重放）；分钟补齐有 nocover 名单防重试。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.ifind import fetch_history_data, fetch_minute_close  # noqa: E402
from resonance.v3 import MinuteBarProvider, V3Params, V3Backtester, V3Signals  # noqa: E402

OOS_START = "2026-09-22"
OUT_DIR = config.OUTPUTS_DIR / "oos"
POOL13 = ["883957.TI", "700050.TI", "000680.SH", "399006.SZ", "000688.SH", "000016.SH",
          "899050.BJ", "932000.CSI", "000300.SH", "000905.SH", "000852.SH", "399303.SZ",
          "000015.SH", "000001.SH", "399001.SZ"]
TRACKS = {"A9": list(config.V41_BROAD_POOL), "B13": POOL13}
NO_HF_COVER = {"700050.TI"}
# 冻结参数（oos-validation-design §二；改任何一项实验作废）
FROZEN = V3Params(topk=3, daily_top=5, hl_source="leader", cost_bp=10.0)


# ---------------------------------------------------------------- 数据 -- 

def update_daily(end_date: str) -> None:
    """缓存全部 codes 的日线补至 end_date（断点：各 code 最后缓存日+1）。"""
    f = config.CACHE_DIR / "daily_bars.parquet"
    bars = pd.read_parquet(f)
    have_last = bars.groupby("symbol")["date"].max()
    # 统一按最大缓存日的次日为公共补采窗口（逐 code 差异会浪费请求批次）
    start = str(pd.Timestamp(have_last.max()) + pd.Timedelta(days=1))
    if start > end_date:
        print(f"[daily] 已是最新（{have_last.max()} ≥ {end_date}）")
        return
    codes = sorted(bars["symbol"].unique())
    print(f"[daily] 补采 {len(codes)} codes × {start}~{end_date}")
    frames = []
    for i in range(0, len(codes), 10):
        chunk = codes[i : i + 10]
        for attempt in range(3):
            try:
                frames.extend(fetch_history_data(chunk, start, end_date))
                break
            except Exception as ex:  # noqa: BLE001
                print(f"  批 {chunk[0]}~ 重试 {attempt + 1}: {str(ex)[:100]}")
                time.sleep(3.0)
        else:
            raise RuntimeError(f"日线补采失败：{chunk}")
        time.sleep(0.4)
    if frames:
        new = pd.concat(frames, ignore_index=True)
        out = pd.concat([bars, new], ignore_index=True)
        out = out.drop_duplicates(subset=["symbol", "date"]).sort_values(
            ["symbol", "date"]).reset_index(drop=True)
        out.to_parquet(f, index=False)
        got_days = pd.to_datetime(new["date"]).dt.date.nunique()
        print(f"[daily] +{len(new)} 行（{got_days} 个交易日）写回")


def topup_minute_for_window(close_all, concepts, pool, end_date) -> int:
    """OOS 窗内（OOS_START..end_date）每日 Top5+领先的尾盘 24bar 自愈补齐。"""
    f = config.CACHE_DIR / "minute5_bars.parquet"
    m5 = pd.read_parquet(f)
    m5["datetime"] = pd.to_datetime(m5["datetime"])
    prov = MinuteBarProvider(m5.pivot(index="datetime", columns="symbol", values="close").sort_index())
    nocover_f = config.CACHE_DIR / "oos_nocover.json"
    nocover: set[str] = set(json.loads(nocover_f.read_text())) if nocover_f.exists() else set()

    sig = V3Signals(close_all, concepts, pool, "883957.TI", FROZEN)
    cal = close_all.index
    s0 = cal.searchsorted(pd.Timestamp(OOS_START))
    s1 = cal.searchsorted(pd.Timestamp(end_date), side="right")
    need: dict[str, set[str]] = {}
    for i in range(s0, s1):
        if not (sig.has_leader[i] and sig.gate[i]):
            continue
        r = sig.ranking(i)
        if r.empty:
            continue
        d = cal[i]
        leader = pool[sig.leader_idx[i]]
        for c in list(r["concept"].head(FROZEN.daily_top)) + [leader]:
            if c in NO_HF_COVER and c == leader:
                continue
            if prov.window_span(c, d, FROZEN.minute_bars).empty and c not in nocover:
                need.setdefault(c, set()).add(str(d.date()))
    if not need:
        return 0
    print(f"[minute] 自愈补采 {len(need)} codes（尾盘 24bar 口径）")
    frames = []
    for c, days in sorted(need.items()):
        for day in sorted(days):
            try:
                df = fetch_minute_close([c], day, day, interval="5", day_start="12:00:00")
            except Exception as ex:  # noqa: BLE001
                print(f"  {c} {day} 失败：{str(ex)[:100]}")
                continue
            if len(df) == 0:
                nocover.add(c)
            else:
                frames.append(df)
            time.sleep(0.3)
    if frames:
        new = pd.concat(frames, ignore_index=True)
        out = pd.concat([m5, new], ignore_index=True)
        out = out.drop_duplicates(subset=["symbol", "datetime"]).sort_values(
            ["symbol", "datetime"]).reset_index(drop=True)
        out.to_parquet(f, index=False)
        print(f"[minute] +{len(new)} bar 写回")
    nocover_f.write_text(json.dumps(sorted(nocover)))
    return len(frames)


# ---------------------------------------------------------------- 重放 -- 

def load_wide():
    bars = pd.read_parquet(config.CACHE_DIR / "daily_bars.parquet")
    catalog = pd.read_csv(config.DATA_DIR / "concept_catalog.csv")
    concepts = [c for c in catalog["code"] if c in set(bars["symbol"])]
    close_all = bars.pivot(index="date", columns="symbol", values="close").sort_index()
    close_all.index = pd.to_datetime(close_all.index)
    return close_all, concepts


def replay_track(track: str, pool: list[str], end_date: str) -> dict:
    close_all, concepts = load_wide()
    m5 = pd.read_parquet(config.CACHE_DIR / "minute5_bars.parquet")
    m5["datetime"] = pd.to_datetime(m5["datetime"])
    prov = MinuteBarProvider(m5.pivot(index="datetime", columns="symbol", values="close").sort_index())
    topup_minute_for_window(close_all, concepts, pool, end_date)  # 自愈后重载
    m5 = pd.read_parquet(config.CACHE_DIR / "minute5_bars.parquet")
    m5["datetime"] = pd.to_datetime(m5["datetime"])
    prov = MinuteBarProvider(m5.pivot(index="datetime", columns="symbol", values="close").sort_index())

    bt = V3Backtester(close_all, concepts, broad_codes=pool, params=FROZEN,
                      minute_bars_provider=prov)
    out = bt.run(OOS_START, end_date)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    trades = out["trades"]
    trades.to_csv(OUT_DIR / f"trades_{track}.csv", index=False)
    out["nav_curve"].to_csv(OUT_DIR / f"nav_{track}.csv")
    return out


def main() -> int:
    import argparse

    from resonance.backtest import perf_stats

    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=str(pd.Timestamp.now().date()))
    ap.add_argument("--track", choices=list(TRACKS), default=None)
    ap.add_argument("--oos-start", default=None,
                    help="OOS 起点覆盖（仅限机制测试/起点顺延；正式实验用默认 2026-09-22）")
    args = ap.parse_args()

    global OOS_START
    if args.oos_start:
        OOS_START = args.oos_start

    update_daily(args.date)
    for track, pool in TRACKS.items():
        if args.track and track != args.track:
            continue
        out = replay_track(track, pool, args.date)
        trades = out["trades"]
        nav = out["nav_curve"]
        st = perf_stats(nav)
        print(f"\n===== 轨 {track}（{len(nav)} 交易日，OOS {nav.index[0].date()}~{nav.index[-1].date()}）=====")
        print(f"纸面净值（10bp）: {st['total_return']:+.2%} | 回撤 {st['max_drawdown']:.2%} | 夏普 {st['sharpe']:.2f}")
        print(f"换仓 {out['stats']['position_changes']} | 止损 {out['stats']['stop_count']} | "
              f"分钟层日 {out['stats'].get('minute_layer_days', 0)} "
              f"(回退L {out['stats'].get('minute_fallback_leader', 0)} / 剔除 {out['stats'].get('minute_excluded', 0)})")
        if len(trades):
            last = trades.iloc[-1]
            holding = out["holdings"]["holding"].iloc[-1]
            print(f"最新动作: {last['date'].date()} {last['type']} {last['from']}→{last['to']} @ {last['price']:.2f}")
            print(f"当前持仓: {holding or '空仓'}")
        else:
            print("尚无交易动作（空仓等待信号）")
        print(f"[OK] 落盘 {OUT_DIR}/trades_{track}.csv / nav_{track}.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
