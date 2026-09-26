"""V4.1 概念 5min 增量补采（按新 9 池 Top5 需求矩阵，下午盘省配额口径）。

需求来源：V4.1 日线层（9 池）在 2025-09-22→2026-09-18 每信号日的 Top5 概念，
现存 m5 缺失的 (code, day) 对。只采当日 12:00 以后的 bar（下午盘
13:05~15:00 = 24 bar，MinuteBarProvider 恰好只需最后 24 根）→ 配额减半。
微盘股 700050.TI 经两账号实测无 HF 覆盖，不采（其领先日回退日线排序）。

用法：
    conda run -n resonance python work/collect_v41_topup.py [--dry-run] [--span N]
断点语义：按 (code, day) 已覆盖判定，重跑只补缺口；无覆盖代码记入
data/cache/v41_topup_nocover.json 不再重试。
--span N：跨日窗口口径（用户 8 小时=96bar 指令）——按 window_span(code, T, N)
判需求，缺/bar 数不足 48 的日子按全天补采（此前下午盘口径的日子重采上午）；
N=144 一次采齐可覆盖 72/96/144 全网格。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.ifind import fetch_minute_bars  # noqa: E402
from resonance.v3 import MinuteBarProvider, V3Params, V3Signals  # noqa: E402
from work.backtest_v3 import load_wide  # noqa: E402

M5_START, M5_END = "2025-09-22", "2026-09-18"
NO_HF_COVER_EXCLUDE = {"700050.TI"}
AUDIT = config.CACHE_DIR / "v41_topup_audit.csv"
NOCOVER = config.CACHE_DIR / "v41_topup_nocover.json"


def demand_matrix(pool: list[str] | None = None) -> tuple[dict[str, list[str]], dict]:
    """V4.1 日线层 Top5 需求 → 缺失 (code, day) 按 code 分组的日期列表。

    pool 缺省 = 当前 config.V41_BROAD_POOL；可传入其他池（如原 13 池）做
    诊断口径的需求补采。
    """
    close_all, concepts = load_wide()
    if pool is None:
        pool = list(config.V41_BROAD_POOL)
    pool = [c for c in pool if c in close_all.columns]
    m5 = pd.read_parquet(config.CACHE_DIR / "minute5_bars.parquet")
    m5["datetime"] = pd.to_datetime(m5["datetime"])
    prov = MinuteBarProvider(m5.pivot(index="datetime", columns="symbol", values="close").sort_index())
    sig = V3Signals(close_all, concepts, pool, "883957.TI", V3Params())
    cal = close_all.index
    s0 = cal.searchsorted(pd.Timestamp(M5_START))
    s1 = cal.searchsorted(pd.Timestamp(M5_END), side="right")

    need: dict[str, set[str]] = {}
    stats = {"signal_days": 0, "pairs_total": 0, "pairs_missing": 0, "leaders": {}}
    for i in range(s0, s1):
        if not (sig.has_leader[i] and sig.gate[i]):
            continue
        r = sig.ranking(i)
        if r.empty:
            continue
        stats["signal_days"] += 1
        d = cal[i]
        leader = pool[sig.leader_idx[i]]
        stats["leaders"][leader] = stats["leaders"].get(leader, 0) + 1
        for c in list(r["concept"].head(5)) + [leader]:
            if c in NO_HF_COVER_EXCLUDE and c == leader:
                continue  # 微盘股领先日永久回退，不采
            stats["pairs_total"] += 1
            if prov.window(c, d, 24).empty:
                stats["pairs_missing"] += 1
                need.setdefault(c, set()).add(str(d.date()))
    return {c: sorted(days) for c, days in need.items()}, stats


def demand_matrix_span(pool: list[str] | None, n_bars: int) -> tuple[dict[str, list[str]], dict]:
    """跨日窗口需求：window_span(code, T, n_bars) 不满足的信号日 → 该 code
    需要的全天（48bar）日子集合（信号日 T 与回看 ceil(n/48)+1 个交易日，
    含节假日安全余量；当日 bar<48 即列入补采）。"""
    close_all, concepts = load_wide()
    if pool is None:
        pool = list(config.V41_BROAD_POOL)
    pool = [c for c in pool if c in close_all.columns]
    m5 = pd.read_parquet(config.CACHE_DIR / "minute5_bars.parquet")
    m5["datetime"] = pd.to_datetime(m5["datetime"])
    prov = MinuteBarProvider(m5.pivot(index="datetime", columns="symbol", values="close").sort_index())
    sig = V3Signals(close_all, concepts, pool, "883957.TI", V3Params())
    cal = close_all.index
    s0 = cal.searchsorted(pd.Timestamp(M5_START))
    s1 = cal.searchsorted(pd.Timestamp(M5_END), side="right")
    lookback = max(1, -(-n_bars // 48)) + 1  # ceil + 安全余量

    need_days: dict[str, set[str]] = {}
    stats = {"signal_days": 0, "pairs_missing": 0}
    for i in range(s0, s1):
        if not (sig.has_leader[i] and sig.gate[i]):
            continue
        r = sig.ranking(i)
        if r.empty:
            continue
        stats["signal_days"] += 1
        d = cal[i]
        leader = pool[sig.leader_idx[i]]
        for c in list(r["concept"].head(5)) + [leader]:
            if c == leader and c in NO_HF_COVER_EXCLUDE:
                continue
            if not prov.window_span(c, d, n_bars).empty:
                continue
            pos = cal.searchsorted(d)
            for j in range(max(0, pos - lookback), pos + 1):
                day = str(cal[j].date())
                sub = prov._day(day)
                nbar = 0
                if sub is not None and c in sub.columns:
                    nbar = int(sub[c].notna().sum())
                if nbar < 48:
                    need_days.setdefault(c, set()).add(day)
    return {c: sorted(v) for c, v in need_days.items()}, stats


def day_runs(days: list[str]) -> list[tuple[str, str]]:
    """排序日期 → 连续段（gap ≤3 日历日视为同段，跨周末）。"""
    ts = [pd.Timestamp(d) for d in days]
    runs, s, p = [], ts[0], ts[0]
    for t in ts[1:]:
        if (t - p).days > 3:
            runs.append((s.strftime("%Y-%m-%d"), p.strftime("%Y-%m-%d")))
            s = t
        p = t
    runs.append((s.strftime("%Y-%m-%d"), p.strftime("%Y-%m-%d")))
    return runs


POOL13 = ["883957.TI", "700050.TI", "000680.SH", "399006.SZ", "000688.SH", "000016.SH",
          "899050.BJ", "932000.CSI", "000300.SH", "000905.SH", "000852.SH", "399303.SZ",
          "000015.SH", "000001.SH", "399001.SZ"]


def main() -> int:
    dry = "--dry-run" in sys.argv
    pool = POOL13 if "--pool13" in sys.argv else None
    span = None
    if "--span" in sys.argv:
        span = int(sys.argv[sys.argv.index("--span") + 1])
    if span:
        need, stats = demand_matrix_span(pool, span)
        print(f"[需求·span={span}] 信号日 {stats['signal_days']} | 待补全天 codes {len(need)} "
              f"（合计 {sum(len(v) for v in need.values())} code-day）")
    else:
        need, stats = demand_matrix(pool)
        print(f"[需求] 信号日 {stats['signal_days']} | 概念+领先对 {stats['pairs_total']} | "
              f"缺失 {stats['pairs_missing']} | 待采 codes {len(need)}")
    if "leaders" in stats:
        lead_desc = ", ".join(f"{config.V41_BROAD_POOL.get(k, k)}:{v}"
                              for k, v in sorted(stats["leaders"].items(), key=lambda x: -x[1]))
        print(f"[领先分布] {lead_desc}")
    if dry:
        for c, days in list(need.items())[:10]:
            print(f"  {c}: {len(days)} 日 → {day_runs(days)}")
        return 0

    nocover: set[str] = set()
    if NOCOVER.exists():
        nocover = set(json.loads(NOCOVER.read_text()))

    f = config.CACHE_DIR / "minute5_bars.parquet"
    base = pd.read_parquet(f)
    base["datetime"] = pd.to_datetime(base["datetime"])
    audit, new_frames = [], []
    todo = {c: d for c, d in need.items() if c not in nocover}
    print(f"[采集] {len(todo)} codes（跳过已知无覆盖 {len(nocover)}）")
    for k, (c, days) in enumerate(sorted(todo.items())):
        for s, e in day_runs(days):
            try:
                df = fetch_minute_bars([c], s, e, interval="5",
                                        day_start="12:00:00" if not span else "09:30:00")
            except Exception as ex:  # noqa: BLE001
                print(f"  {c} {s}~{e} 失败：{ex}")
                time.sleep(2.0)
                continue
            got_days = df["datetime"].dt.date.nunique() if len(df) else 0
            want = len([d for d in days if s <= d <= e])
            audit.append({"code": c, "run": f"{s}~{e}", "want_days": want,
                          "got_days": int(got_days), "bars": len(df)})
            if len(df):
                new_frames.append(df)
            if got_days == 0 and want > 0:
                nocover.add(c)
            time.sleep(0.4)
        if (k + 1) % 20 == 0:
            print(f"  … {k + 1}/{len(todo)} codes")

    if new_frames:
        new = pd.concat(new_frames, ignore_index=True)
        merged = pd.concat([base, new], ignore_index=True)
        merged = merged.drop_duplicates(subset=["symbol", "datetime"],
                                        keep="last").sort_values(
            ["symbol", "datetime"]).reset_index(drop=True)
        merged.to_parquet(f, index=False)
        print(f"[写回] +{len(new)} bar（去重后总 {len(merged)} 行）")
    pd.DataFrame(audit).to_csv(AUDIT, index=False)
    NOCOVER.write_text(json.dumps(sorted(nocover)))
    print(f"[审计] {AUDIT} | 无覆盖代码 {len(nocover)} → {NOCOVER}")

    # 复盘覆盖率
    _, stats2 = (demand_matrix_span(pool, span) if span else demand_matrix(pool))
    print(f"[复盘] 缺失 {stats['pairs_missing']} → {stats2['pairs_missing']} "
          f"（覆盖 {1 - stats2['pairs_missing'] / stats2['pairs_total']:.1%}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
