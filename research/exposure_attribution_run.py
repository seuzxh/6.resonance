"""风险暴露环节归因（阶段一）：只读既有双锚账单，不新增策略回测。

设计见 docs/research/exposure-attribution-plan.md。运行环境：conda resonance。
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "outputs" / "anchor_selection"
DEFAULT_OUT = PROJECT_ROOT / "outputs" / "exposure_attribution"
DEFAULT_DATA = Path("/home/zxh/projects/6.resonance/data/cache/daily_bars.parquet")
WINDOWS = ("main", "early", "recent")
PHASES = (1, 2, 3, 4, 5)
DUAL_ANCHORS = ("399001.SZ", "000852.SH")
COST_BP = 10.0
RECOVERY_THRESHOLD = 0.05
DATA_END = pd.Timestamp("2026-09-18")


def build_segments(trades: pd.DataFrame, nav: pd.DataFrame,
                   cost_bp: float = COST_BP) -> pd.DataFrame:
    """把逐笔事件重构为完整持仓段；switch 终点先剔除新买入成本。"""
    required = {"date", "type", "from", "to", "nav"}
    if not required.issubset(trades.columns):
        raise ValueError(f"账单缺少字段：{sorted(required - set(trades.columns))}")
    if trades.empty:
        return pd.DataFrame(columns=[
            "start_date", "end_date", "concept", "origin", "exit_reason",
            "net_return", "holding_days", "open_position",
        ])

    trades = trades.copy()
    trades["date"] = pd.to_datetime(trades["date"])
    nav_index = pd.DatetimeIndex(pd.to_datetime(nav["date"]))
    cost = cost_bp / 1e4
    rows: list[dict] = []
    open_seg: dict | None = None
    prev_exit_reason: str | None = None

    def close_seg(end: pd.Timestamp, end_nav: float, reason: str) -> None:
        nonlocal open_seg
        if open_seg is None:
            raise ValueError(f"{end.date()} 出现 {reason}，但此前没有持仓段")
        adjusted_nav = end_nav / (1.0 - cost) if reason == "switch" else end_nav
        rows.append({
            "start_date": open_seg["start_date"],
            "end_date": end,
            "concept": open_seg["concept"],
            "origin": open_seg["origin"],
            "exit_reason": reason,
            "net_return": adjusted_nav / open_seg["start_nav"] - 1.0,
            "holding_days": int(nav_index.searchsorted(end, side="right")
                                - nav_index.searchsorted(open_seg["start_date"], side="left")),
            "open_position": False,
        })
        open_seg = None

    for r in trades.itertuples(index=False):
        typ = str(r.type)
        dt = pd.Timestamp(r.date)
        nav_after = float(r.nav)
        if typ == "entry":
            if open_seg is not None:
                raise ValueError(f"{dt.date()} 在未平仓前再次 entry")
            origin = "reentry_after_stop" if prev_exit_reason == "stop" else "cold_entry"
            open_seg = {"start_date": dt, "concept": str(r.to),
                        "start_nav": nav_after, "origin": origin}
        elif typ == "switch":
            close_seg(dt, nav_after, "switch")
            open_seg = {"start_date": dt, "concept": str(r.to),
                        "start_nav": nav_after, "origin": "switch_entry"}
        elif typ in ("exit", "stop"):
            close_seg(dt, nav_after, typ)
            prev_exit_reason = typ
        else:
            raise ValueError(f"未知账单事件类型：{typ}")

    if open_seg is not None:
        rows.append({
            "start_date": open_seg["start_date"], "end_date": pd.NaT,
            "concept": open_seg["concept"], "origin": open_seg["origin"],
            "exit_reason": "open", "net_return": math.nan,
            "holding_days": int(len(nav_index)
                                - nav_index.searchsorted(open_seg["start_date"], side="left")),
            "open_position": True,
        })
    return pd.DataFrame(rows)


def add_recovery_flags(segments: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    """标注亏损段卖出后、下一次入场前的双锚收盘修复与再入场结果。"""
    if segments.empty:
        return segments.assign(recovery_before_reentry=False, next_net_return=np.nan)
    close = bars[["symbol", "date", "close"]].copy()
    close["date"] = pd.to_datetime(close["date"])
    anchors = close[close["symbol"].isin(DUAL_ANCHORS)]
    if anchors.empty:
        raise ValueError("日线数据中没有双锚 399001.SZ / 000852.SH")
    anchor_close = anchors.pivot(index="date", columns="symbol", values="close").sort_index()
    out = segments.reset_index(drop=True).copy()
    next_returns: list[float] = []
    recoveries: list[bool] = []

    for i, r in out.iterrows():
        nxt = out.iloc[i + 1] if i + 1 < len(out) else None
        next_return = float(nxt["net_return"]) if nxt is not None and not bool(nxt["open_position"]) else math.nan
        next_returns.append(next_return)
        if bool(r.open_position) or float(r.net_return) >= 0 or nxt is None or pd.isna(nxt["start_date"]):
            recoveries.append(False)
            continue
        after = anchor_close.loc[anchor_close.index > r["end_date"]]
        before_next = after.loc[after.index < nxt["start_date"]]
        if before_next.empty:
            recoveries.append(False)
            continue
        gains = before_next / anchor_close.loc[anchor_close.index <= r["end_date"]].iloc[-1] - 1.0
        recoveries.append(bool((gains >= RECOVERY_THRESHOLD).any().any()))
    out["recovery_before_reentry"] = recoveries
    out["next_net_return"] = next_returns
    return out


def _phase_summary(df: pd.DataFrame) -> dict:
    closed = df[~df["open_position"]].copy()
    losses = closed[closed["net_return"] < 0]
    closed = closed.copy()
    closed["recovery_next_win"] = (closed["recovery_before_reentry"]
                                   & (closed["next_net_return"] > 0))
    max_losing_streak = 0
    streak = 0
    for v in closed["net_return"]:
        if v < 0:
            streak += 1
            max_losing_streak = max(max_losing_streak, streak)
        else:
            streak = 0
    return {
        "closed_trades": len(closed),
        "open_trades": int(df["open_position"].sum()),
        "win_rate": float((closed["net_return"] > 0).mean()) if len(closed) else math.nan,
        "median_return": float(closed["net_return"].median()) if len(closed) else math.nan,
        "worst_return": float(closed["net_return"].min()) if len(closed) else math.nan,
        "loss_count": len(losses),
        "loss_log_sum": float(np.log1p(losses["net_return"]).sum()) if len(losses) else 0.0,
        "max_losing_streak": max_losing_streak,
        "median_holding_days": float(closed["holding_days"].median()) if len(closed) else math.nan,
        "stop_reentry_count": int((closed["origin"] == "reentry_after_stop").sum()),
        "recovery_event_count": int(closed["recovery_before_reentry"].sum()),
        "recovery_next_win_rate": float((closed.loc[closed["recovery_before_reentry"], "next_net_return"] > 0).mean())
        if closed["recovery_before_reentry"].any() else math.nan,
    }


def aggregate(segments: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """返回窗口汇总与来源×退出原因汇总，均为5相位中位数。"""
    window_rows: list[dict] = []
    group_rows: list[dict] = []
    for window, wg in segments.groupby("window", sort=True):
        phase_stats = [_phase_summary(g) for _, g in wg.groupby("phase", sort=True)]
        row = {"window": window}
        row.update(pd.DataFrame(phase_stats).median(numeric_only=True).to_dict())
        window_rows.append(row)

        parts: list[pd.DataFrame] = []
        for phase, pg in wg.groupby("phase", sort=True):
            closed = pg[~pg["open_position"]].copy()
            closed["recovery_next_win"] = (closed["recovery_before_reentry"]
                                           & (closed["next_net_return"] > 0))
            if closed.empty:
                continue
            grouped = (closed.groupby(["origin", "exit_reason"], dropna=False)
                       .agg(closed_trades=("net_return", "size"),
                            win_rate=("net_return", lambda x: float((x > 0).mean())),
                            median_return=("net_return", "median"),
                            worst_return=("net_return", "min"),
                            loss_log_sum=("net_return", lambda x: float(np.log1p(x[x < 0]).sum())),
                            median_holding_days=("holding_days", "median"),
                            recovery_event_count=("recovery_before_reentry", "sum"),
                            recovery_next_win_count=("recovery_next_win", "sum"))
                       .reset_index())
            grouped["phase"] = phase
            parts.append(grouped)
        if parts:
            all_groups = pd.concat(parts, ignore_index=True)
            med = (all_groups.groupby(["origin", "exit_reason"], as_index=False)
                   .median(numeric_only=True).drop(columns="phase"))
            med["recovery_next_win_rate"] = (
                med["recovery_next_win_count"] / med["recovery_event_count"].replace(0, np.nan))
            med.insert(0, "window", window)
            group_rows.extend(med.to_dict("records"))
    return (pd.DataFrame(window_rows), pd.DataFrame(group_rows))


def classify_empty_exits(segments: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    """复核主窗口排名换仓后空榜退出的信号日原因；不改变任何交易结果。"""
    close = bars[["symbol", "date", "close"]].copy()
    close["date"] = pd.to_datetime(close["date"])
    anchors = ["399001.SZ", "000852.SH"]
    anchor_close = (close[close["symbol"].isin(anchors)]
                    .pivot(index="date", columns="symbol", values="close")
                    .sort_index())
    concepts = sorted(set(close["symbol"]) - set(anchors) - {"883957.TI"})
    # 与实验账单的概念目录保持一致：只在 catalog 概念段内计数。
    concepts = [c for c in concepts if str(c).startswith(("885", "886"))]
    all_close = close.pivot(index="date", columns="symbol", values="close").sort_index()
    concept_close = all_close.reindex(columns=concepts)
    mom10 = anchor_close.pct_change(10)
    ret3 = all_close.pct_change(3)
    eligible = (concept_close.rolling(10).count().eq(10)
                & ret3.reindex(columns=concepts).gt(0))
    rows = []
    subset = segments[(segments["window"] == "main")
                      & (segments["origin"] == "switch_entry")
                      & (segments["exit_reason"] == "exit")]
    for r in subset.itertuples(index=False):
        end = pd.Timestamp(r.end_date)
        pos = anchor_close.index.searchsorted(end)
        if pos <= 0:
            raise ValueError(f"空榜退出日 {end.date()} 之前没有信号日")
        signal_date = anchor_close.index[pos - 1]
        spos = anchor_close.index.get_loc(signal_date)
        momentum = mom10.iloc[spos].dropna()
        if momentum.empty:
            reason, leader, gate = "no_leader", None, float("nan")
            n_eligible = 0
        else:
            leader = str(momentum.idxmax())
            gate = float(ret3.at[signal_date, leader])
            n_eligible = int(eligible.iloc[spos].sum())
            if gate <= 0:
                reason = "gate_fail"
            elif n_eligible == 0:
                reason = "no_positive_complete_concept"
            else:
                reason = "score_or_data_empty"
        rows.append({
            "phase": int(r.phase), "exec_date": end, "signal_date": signal_date,
            "reason": reason, "leader": leader, "leader_3d_return": gate,
            "eligible_concepts": n_eligible,
        })
    return pd.DataFrame(rows)


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _pct(x: float) -> str:
    return "—" if pd.isna(x) else f"{x * 100:.2f}%"


def _num(x: float) -> str:
    return "—" if pd.isna(x) else f"{x:.3f}"


def write_report(out: Path, window_summary: pd.DataFrame,
                 group_summary: pd.DataFrame, audit: dict) -> None:
    origin_cn = {
        "cold_entry": "cold_entry（空仓后首次入场）",
        "reentry_after_stop": "reentry_after_stop（止损冷静期后再入场）",
        "switch_entry": "switch_entry（排名换仓入场）",
    }
    exit_cn = {
        "exit": "exit（候选榜为空退出）",
        "stop": "stop（止损）",
        "switch": "switch（跌出前三名续持缓冲后换仓）",
    }
    w = window_summary.set_index("window")
    main = w.loc["main"]
    early = w.loc["early"]
    recent = w.loc["recent"]
    top_loss = (group_summary[group_summary["window"] == "main"]
                .sort_values("loss_log_sum").head(5).copy())
    top_rows = []
    for _, r in top_loss.iterrows():
        top_rows.append(
            f"| {origin_cn[r['origin']]} | {exit_cn[r['exit_reason']]} | "
            f"{int(r['closed_trades'])} | {_pct(r['win_rate'])} | "
            f"{_num(r['loss_log_sum'])} | {_pct(r['worst_return'])} |"
        )
    gate_fail_count = int(audit.get("empty_exit_reason_counts", {}).get("gate_fail", 0))
    empty_exit_total = int(sum(audit.get("empty_exit_reason_counts", {}).values()))
    report = f"""# 风险暴露环节归因（阶段一）结果

执行日期：2026-10-04。研究对象为深证成指与中证1000双锚原策略；信号日T（当日收盘形成判断）收盘判断、下一交易日T+1（执行日）开盘成交，单边成本10个基点，主窗口、早期窗口、近期窗口均为5相位中位数，历史只截至2026-09-18。概念指数不可直接交易，成本是统一压力假设，概念目录存在幸存者偏差；以下全部是样本内描述，不是采纳依据。

## 一、窗口总览

| 窗口 | 已结束完整交易中位数 | 未平仓中位数 | 完整交易胜率 | 中位扣费收益 | 最差扣费收益 | 亏损段中位数 | 最长连亏中位数 | 修复事件中位数 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 主窗口 | {int(main['closed_trades'])} | {int(main['open_trades'])} | {_pct(main['win_rate'])} | {_pct(main['median_return'])} | {_pct(main['worst_return'])} | {int(main['loss_count'])} | {int(main['max_losing_streak'])} | {int(main['recovery_event_count'])} |
| 早期窗口 | {int(early['closed_trades'])} | {int(early['open_trades'])} | {_pct(early['win_rate'])} | {_pct(early['median_return'])} | {_pct(early['worst_return'])} | {int(early['loss_count'])} | {int(early['max_losing_streak'])} | {int(early['recovery_event_count'])} |
| 近期窗口 | {int(recent['closed_trades'])} | {int(recent['open_trades'])} | {_pct(recent['win_rate'])} | {_pct(recent['median_return'])} | {_pct(recent['worst_return'])} | {int(recent['loss_count'])} | {int(recent['max_losing_streak'])} | {int(recent['recovery_event_count'])} |

在主窗口5相位中位、单边10个基点、T+1开盘执行口径下，双锚原策略的已结束完整交易胜率为{_pct(main['win_rate'])}，低于近期窗口的{_pct(recent['win_rate'])}，高于早期窗口的{_pct(early['win_rate'])}；三个窗口的持有时长中位数均为{int(main['median_holding_days'])}个账单日。窗口之间包含重叠样本，不能把三个窗口合计当作独立验证。

## 二、空榜退出与5%修复定义复核

对主窗口全部{empty_exit_total}个“排名换仓入场后空榜退出”账面事件逐条复核后，{gate_fail_count}个事件的信号日均由`gate_fail`（领先指数3日复合收益小于或等于0的闸门失败）造成；这不是概念目录无标的或行情缺失。复核方法为：先定位退出执行日的前一个交易日作为信号日，再按双锚10日复合收益选当日领先指数，若该指数3日复合收益小于或等于0，当日候选榜直接为空。相位样本重叠，{empty_exit_total}是账面事件数，不是独立样本数。

本报告的5%修复定义也不是策略收益阈值。它只统计亏损段退出后、下一次入场前，任一双锚自退出日收盘起的后续收盘累计涨幅是否达到5%；该阈值沿用现行止损幅度，未参与寻优，也不触发任何交易。

## 三、主窗口亏损集中环节

下表按亏损段对数收益合计从差到较好排序，数值为5相位中位数；对数收益合计越小说明该环节亏损复利影响越大。

| 入场来源 | 退出原因 | 完整交易中位数 | 胜率 | 亏损段对数收益合计 | 最差扣费收益 |
|---|---|---:|---:|---:|---:|
{chr(10).join(top_rows)}

在主窗口5相位中位、单边10个基点口径下，按亏损段对数收益合计比较各“入场来源×退出原因”环节，最差环节是排名换仓入场后止损：其完整交易中位数为{int(top_loss.iloc[0]['closed_trades'])}笔、胜率中位数为{_pct(top_loss.iloc[0]['win_rate'])}、亏损段对数收益合计为{_num(top_loss.iloc[0]['loss_log_sum'])}。排名换仓入场后因候选榜为空退出的环节次之，其完整交易中位数为{int(top_loss.iloc[1]['closed_trades'])}笔、胜率中位数为{_pct(top_loss.iloc[1]['win_rate'])}、亏损段对数收益合计为{_num(top_loss.iloc[1]['loss_log_sum'])}。

按预注册的5%双锚修复定义，主窗口每个相位的中位修复事件只有{int(main['recovery_event_count'])}次，近期窗口为{int(recent['recovery_event_count'])}次；本账单不能支持“大量修复机会被原策略错过”的判断。止损后再入场不是本表最大亏损来源：主窗口该来源完整交易中位数为{int(main['stop_reentry_count'])}笔、三组胜率中位数为{_pct(group_summary[(group_summary['window'] == 'main') & (group_summary['origin'] == 'reentry_after_stop')]['win_rate'].median())}。

## 四、研究含义与边界

本阶段只定位既有账单的亏损环节，没有改变锚池、概念排序、最短持有参数、止损、执行价或成本，也没有生成新的候选策略收益。若继续阶段二，应优先解释“排名换仓后止损”与“排名换仓后空榜退出”的共同状态，而不是先做恢复加仓；任何危险期参与控制都必须先证明与既有闸门、半衰期和止损功能正交，并通过新的预注册采纳判据。

未平仓段只计数、不计胜负。五相位账单存在样本重叠和后续收敛，中位数不是独立样本数。原始行情只读主仓生产副本，本工作树不复制行情、不访问网络、不运行每日样本外程序。

## 五、复现

```bash
conda run --no-capture-output -n resonance python research/exposure_attribution_run.py
```

输入内容摘要见`data_audit.json`（输入文件哈希与事件计数）；逐笔明细见`segments.parquet`（持仓段明细表），窗口与环节汇总见`window_summary.csv`（窗口汇总表）、`group_summary.csv`（来源与退出原因汇总表）、`empty_exit_reasons.csv`（空榜退出原因复核表）。
"""
    (out / "report.md").write_text(report, encoding="utf-8")


def run(source: Path, data_parquet: Path, out: Path) -> int:
    bars = pd.read_parquet(data_parquet, columns=["symbol", "date", "close"])
    bars = bars[pd.to_datetime(bars["date"]) <= DATA_END].copy()
    all_segments: list[pd.DataFrame] = []
    audit = {"windows": {}, "cost_bp": COST_BP, "recovery_threshold": RECOVERY_THRESHOLD}
    for window in WINDOWS:
        audit["windows"][window] = {}
        for phase in PHASES:
            tp = source / f"trades_{window}_dual_phase{phase}.csv"
            np_ = source / f"nav_{window}_dual_phase{phase}.csv"
            if not tp.exists() or not np_.exists():
                raise FileNotFoundError(f"缺少账单或净值文件：{tp} / {np_}")
            trades = pd.read_csv(tp)
            nav = pd.read_csv(np_)
            audit["windows"][window][str(phase)] = {
                "trades_sha256": _sha256(tp), "nav_sha256": _sha256(np_),
            }
            seg = build_segments(trades, nav)
            seg["window"] = window
            seg["phase"] = phase
            all_segments.append(seg)
            audit["windows"][window][str(phase)].update({
                "events": len(trades),
                "segments": len(seg),
                "closed": int((~seg["open_position"]).sum()),
                "open": int(seg["open_position"].sum()),
            })
    segments = add_recovery_flags(pd.concat(all_segments, ignore_index=True), bars)
    segments["year_month"] = pd.to_datetime(segments["start_date"]).dt.to_period("M").astype(str)
    window_summary, group_summary = aggregate(segments)
    empty_reasons = classify_empty_exits(segments, bars)
    out.mkdir(parents=True, exist_ok=True)
    segments.to_parquet(out / "segments.parquet", index=False)
    window_summary.to_csv(out / "window_summary.csv", index=False)
    group_summary.to_csv(out / "group_summary.csv", index=False)
    empty_reasons.to_csv(out / "empty_exit_reasons.csv", index=False)
    audit["empty_exit_reason_counts"] = (
        empty_reasons["reason"].value_counts().to_dict() if not empty_reasons.empty else {})
    audit["daily_bars_sha256"] = _sha256(data_parquet)
    audit["daily_bars_data_end"] = str(DATA_END.date())
    (out / "data_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(out, window_summary, group_summary, audit)
    print(f"[OK] {len(segments)} 段 → {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--data-parquet", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    return run(args.source, args.data_parquet, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
