"""阶段二A危险状态诊断：只读阶段一持仓段与日线，不生成新策略回测。

设计见 docs/research/exposure-danger-diagnostic-plan.md。运行环境：conda resonance。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEGMENTS = PROJECT_ROOT / "outputs" / "exposure_attribution" / "segments.parquet"
DEFAULT_DATA = Path("/home/zxh/projects/6.resonance/data/cache/daily_bars.parquet")
DEFAULT_OUT = PROJECT_ROOT / "outputs" / "exposure_danger_diagnostic"
ANCHORS = ("399001.SZ", "000852.SH")
ALLA = "883957.TI"
FEATURES = (
    "leader3", "leader_dd10", "leader_dvol10", "vol_ratio_5_20",
    "anchor_mom_gap10", "anchor_corr20", "concept3", "concept10",
    "concept_minus_leader3",
)
DATA_END = pd.Timestamp("2026-09-18")
PRIMARY_PHASE = 3


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def signal_date_before(calendar: pd.DatetimeIndex, execution_date: pd.Timestamp) -> pd.Timestamp:
    pos = calendar.searchsorted(pd.Timestamp(execution_date))
    if pos <= 0:
        raise ValueError(f"执行日 {execution_date.date()} 之前没有信号日")
    signal = calendar[pos - 1]
    if signal >= pd.Timestamp(execution_date):
        raise ValueError("信号日必须早于执行日")
    return signal


def path_drawdown(close: pd.Series) -> float:
    values = close.to_numpy(dtype=float)
    if len(values) == 0 or np.isnan(values).any():
        return math.nan
    running_max = np.maximum.accumulate(values)
    return float(np.min(values / running_max - 1.0))


def build_events(segments: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    """为第3相位排名换仓段构造信号日特征；不读取执行日之后的数据。"""
    required = {"window", "phase", "origin", "exit_reason", "open_position",
                "start_date", "end_date", "concept", "net_return", "holding_days"}
    if not required.issubset(segments.columns):
        raise ValueError(f"阶段一持仓段缺少字段：{sorted(required - set(segments.columns))}")
    selected = segments[
        (segments["window"] == "main")
        & (segments["phase"].astype(int) == PRIMARY_PHASE)
        & (segments["origin"] == "switch_entry")
        & (~segments["open_position"].astype(bool))
    ].copy()
    if selected.empty:
        raise ValueError("主窗口第3相位没有已结束的排名换仓事件")

    bars = bars[["symbol", "date", "close"]].copy()
    bars["date"] = pd.to_datetime(bars["date"])
    bars = bars[bars["date"] <= DATA_END]
    close = bars.pivot(index="date", columns="symbol", values="close").sort_index()
    rets = close.pct_change()
    if not set(ANCHORS).issubset(close.columns):
        raise ValueError("日线数据缺少双锚")
    calendar = close.index
    rows: list[dict] = []
    for r in selected.itertuples(index=False):
        execution_date = pd.Timestamp(r.start_date)
        signal = signal_date_before(calendar, execution_date)
        anchor_mom = (close.loc[signal, list(ANCHORS)]
                      / close.loc[:signal, list(ANCHORS)].iloc[-11] - 1.0)
        leader = str(anchor_mom.idxmax())
        leader_close = close.loc[:signal, leader].iloc[-10:]
        leader_rets = rets.loc[:signal, leader].iloc[-10:]
        anchor_return10 = close.loc[:signal, list(ANCHORS)].iloc[-11]
        anchor_return10 = close.loc[signal, list(ANCHORS)] / anchor_return10 - 1.0
        vol5 = rets.loc[:signal, leader].iloc[-5:].std()
        vol20 = rets.loc[:signal, leader].iloc[-20:].std()
        concept = str(r.concept)
        concept3 = close.at[signal, concept] / close.loc[:signal, concept].iloc[-4] - 1.0
        concept10 = close.at[signal, concept] / close.loc[:signal, concept].iloc[-11] - 1.0
        leader3 = close.at[signal, leader] / close.loc[:signal, leader].iloc[-4] - 1.0
        rows.append({
            "exec_date": execution_date,
            "signal_date": signal,
            "concept": concept,
            "leader": leader,
            "exit_reason": str(r.exit_reason),
            "adverse_exit": str(r.exit_reason) in ("stop", "exit"),
            "net_return": float(r.net_return),
            "holding_days": int(r.holding_days),
            "stage": "early" if execution_date <= pd.Timestamp("2024-12-31") else "recent",
            "leader3": leader3,
            "leader_dd10": path_drawdown(leader_close),
            "leader_dvol10": float(np.sqrt(np.mean(np.minimum(leader_rets.to_numpy(dtype=float), 0.0) ** 2))),
            "vol_ratio_5_20": float(vol5 / vol20) if math.isfinite(vol20) and vol20 > 0 else math.nan,
            "anchor_mom_gap10": float(abs(anchor_return10.iloc[0] - anchor_return10.iloc[1])),
            "anchor_corr20": float(rets.loc[:signal, list(ANCHORS)].iloc[-20:].corr().iloc[0, 1]),
            "concept3": float(concept3),
            "concept10": float(concept10),
            "concept_minus_leader3": float(concept3 - leader3),
        })
    events = pd.DataFrame(rows).sort_values(["exec_date", "concept"]).reset_index(drop=True)
    events["year_month"] = pd.to_datetime(events["exec_date"]).dt.to_period("M").astype(str)
    return events


def add_bins(events: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    thresholds = {}
    out = events.copy()
    for feature in FEATURES:
        q1, q2, q3 = events[feature].quantile([0.25, 0.50, 0.75])
        thresholds[feature] = {"q25": float(q1), "q50": float(q2), "q75": float(q3)}
        labels = np.select(
            [out[feature] <= q1, out[feature] <= q2, out[feature] <= q3],
            ["Q1", "Q2", "Q3"], default="Q4")
        labels = np.where(out[feature].isna().to_numpy(), "missing", labels)
        out[f"{feature}_bin"] = labels
    return out, pd.DataFrame(thresholds).T.reset_index().rename(columns={"index": "feature"})


def summarize_bins(binned: pd.DataFrame) -> pd.DataFrame:
    # Explicit long-form grouping avoids dynamic column-name ambiguity.
    long_rows = []
    for feature in FEATURES:
        col = f"{feature}_bin"
        for (stage, bin_name), g in binned.groupby(["stage", col], sort=True):
            losses = g.loc[g["net_return"] < 0, "net_return"]
            long_rows.append({
                "stage": stage, "feature": feature, "bin": bin_name,
                "n": len(g), "adverse_count": int(g["adverse_exit"].sum()),
                "adverse_rate": float(g["adverse_exit"].mean()),
                "stop_rate": float(g["exit_reason"].eq("stop").mean()),
                "exit_rate": float(g["exit_reason"].eq("exit").mean()),
                "median_return": float(g["net_return"].median()),
                "worst_return": float(g["net_return"].min()),
                "loss_log_sum": float(np.log1p(losses).sum()) if len(losses) else 0.0,
                "median_holding_days": float(g["holding_days"].median()),
            })
    return pd.DataFrame(long_rows)


def test_candidates(binned: pd.DataFrame, summary: pd.DataFrame) -> pd.DataFrame:
    rows = []
    available_features = [f for f in FEATURES if f in set(summary["feature"])]
    for feature in available_features:
        col = f"{feature}_bin"
        parts = {}
        for stage in ("early", "recent"):
            sub = summary[(summary["feature"] == feature) & (summary["stage"] == stage)]
            q1 = sub[sub["bin"] == "Q1"].iloc[0]
            q4 = sub[sub["bin"] == "Q4"].iloc[0]
            parts[stage] = {
                "adverse_diff": float(q4["adverse_rate"] - q1["adverse_rate"]),
                "return_diff": float(q4["median_return"] - q1["median_return"]),
                "q1_n": int(q1["n"]), "q4_n": int(q4["n"]),
            }
        e, r = parts["early"], parts["recent"]
        same_adverse_direction = (e["adverse_diff"] > 0 and r["adverse_diff"] > 0) or \
                                 (e["adverse_diff"] < 0 and r["adverse_diff"] < 0)
        enough_adverse = abs(e["adverse_diff"]) >= 0.05 and abs(r["adverse_diff"]) >= 0.05
        enough_return = (e["return_diff"] < 0 and r["return_diff"] < 0) or \
                        (e["return_diff"] > 0 and r["return_diff"] > 0)
        enough_n = min(e["q1_n"], e["q4_n"], r["q1_n"], r["q4_n"]) >= 8
        missing_rate = float(binned[feature].isna().mean())
        if same_adverse_direction and enough_adverse and e["adverse_diff"] > 0 and enough_return and e["return_diff"] < 0:
            risk_end = "Q4"
        elif same_adverse_direction and enough_adverse and e["adverse_diff"] < 0 and enough_return and e["return_diff"] > 0:
            risk_end = "Q1"
        else:
            risk_end = "none"
        monthly_ok = True
        monthly_max_share = math.nan
        if risk_end != "none":
            shares = []
            risk = binned[binned[col] == risk_end]
            for stage in ("early", "recent"):
                adverse = risk[(risk["stage"] == stage) & risk["adverse_exit"]]
                if adverse.empty:
                    shares.append(math.nan)
                else:
                    shares.append(float(adverse["year_month"].value_counts().max() / len(adverse)))
            monthly_max_share = max([v for v in shares if math.isfinite(v)], default=math.nan)
            finite = [v for v in shares if math.isfinite(v)]
            monthly_ok = bool(finite and all(v <= 0.5 for v in finite))
        passed = bool(risk_end != "none" and enough_n and missing_rate <= 0.05 and monthly_ok)
        rows.append({
            "feature": feature,
            "early_adverse_diff": e["adverse_diff"], "recent_adverse_diff": r["adverse_diff"],
            "early_return_diff": e["return_diff"], "recent_return_diff": r["return_diff"],
            "min_endpoint_n": min(e["q1_n"], e["q4_n"], r["q1_n"], r["q4_n"]),
            "risk_end": risk_end, "monthly_max_share": monthly_max_share,
            "missing_rate": missing_rate, "candidate": passed,
        })
    return pd.DataFrame(rows)


def phase_overlap(segments: pd.DataFrame) -> pd.DataFrame:
    selected = segments[(segments["window"] == "main")
                        & (segments["origin"] == "switch_entry")
                        & (~segments["open_position"].astype(bool))].copy()
    primary = selected[selected["phase"].astype(int) == PRIMARY_PHASE]
    primary_keys = set(zip(primary["start_date"], primary["concept"]))
    rows = []
    for phase, g in selected.groupby("phase", sort=True):
        keys = set(zip(g["start_date"], g["concept"]))
        overlap = keys & primary_keys
        rows.append({
            "phase": int(phase), "events": len(g),
            "overlap_with_phase3": len(overlap),
            "overlap_share": float(len(overlap) / len(g)) if not g.empty else math.nan,
        })
    return pd.DataFrame(rows)


def write_report(out: Path, events: pd.DataFrame, summary: pd.DataFrame,
                 candidates: pd.DataFrame, thresholds: pd.DataFrame,
                 overlap: pd.DataFrame) -> None:
    early = events[events["stage"] == "early"]
    recent = events[events["stage"] == "recent"]
    passed = candidates[candidates["candidate"]]
    candidate_text = "没有特征通过全部预注册条件" if passed.empty else \
        "、".join(f"`{r.feature}`（风险端{r.risk_end}）" for r in passed.itertuples())
    top_rows = []
    for _, r in candidates.sort_values(["candidate", "recent_adverse_diff"], ascending=[False, False]).iterrows():
        top_rows.append(
            f"| `{r['feature']}` | {r['risk_end']} | {r['early_adverse_diff'] * 100:.2f}个百分点 | "
            f"{r['recent_adverse_diff'] * 100:.2f}个百分点 | {r['min_endpoint_n']} | "
            f"{"—" if pd.isna(r['monthly_max_share']) else f"{r['monthly_max_share'] * 100:.1f}%"} | {'通过' if r['candidate'] else '未通过'} |")
    report = f"""# 阶段二A危险状态诊断结果

执行日期：2026-10-04。研究对象为双锚原策略主窗口第3相位已结束的排名换仓事件；阶段一账单口径为T+1（信号日后下一交易日）开盘、单边10个基点，历史只截至2026-09-18。本报告是样本内描述性诊断，不生成新策略回测，也不构成采纳依据。

## 一、样本

| 分段 | 事件数 | 不利退出数 | 不利退出率 | 止损数 | 空榜退出数 | 中位扣费收益 |
|---|---:|---:|---:|---:|---:|---:|
| 早期 | {len(early)} | {int(early['adverse_exit'].sum())} | {early['adverse_exit'].mean() * 100:.2f}% | {int(early['exit_reason'].eq('stop').sum())} | {int(early['exit_reason'].eq('exit').sum())} | {early['net_return'].median() * 100:.2f}% |
| 近期 | {len(recent)} | {int(recent['adverse_exit'].sum())} | {recent['adverse_exit'].mean() * 100:.2f}% | {int(recent['exit_reason'].eq('stop').sum())} | {int(recent['exit_reason'].eq('exit').sum())} | {recent['net_return'].median() * 100:.2f}% |

## 二、候选判据

下表的高档（Q4）减低档（Q1）不利退出率差异以个百分点表示；通过条件见预注册设计。

| 特征 | 风险端 | 早期差异 | 近期差异 | 最小端点样本 | 风险端不利事件单月最大占比 | 判定 |
|---|---|---:|---:|---:|---:|---|
{chr(10).join(top_rows)}

判读：{candidate_text}。候选观察只允许进入新的独立设计；若没有候选，则按预注册停止危险期参与控制方向，不改分位、窗口或标签继续搜索。

其余四个相位的事件数为{int(overlap[overlap['phase'] != PRIMARY_PHASE]['events'].min())}至{int(overlap[overlap['phase'] != PRIMARY_PHASE]['events'].max())}个，与第3相位的事件重合率为{overlap[overlap['phase'] != PRIMARY_PHASE]['overlap_share'].min() * 100:.2f}%至{overlap[overlap['phase'] != PRIMARY_PHASE]['overlap_share'].max() * 100:.2f}%，因此不能把五相位重复事件当作独立样本。特征最高缺失率为{max(events[f].isna().mean() for f in FEATURES) * 100:.2f}%，缺失事件单独分档，不计入高档或低档。

## 三、边界

所有特征均使用执行日前一个交易日的收盘及以前数据；分位阈值来自主样本全体，仅用于描述，不是交易阈值。概念指数不可直接交易，成本是统一压力假设，概念目录存在幸存者偏差。第3相位之外的事件只做重合审计，不作为独立样本计入检验。
"""
    (out / "report.md").write_text(report, encoding="utf-8")


def run(segments_path: Path, data_path: Path, out: Path) -> int:
    segments = pd.read_parquet(segments_path)
    bars = pd.read_parquet(data_path, columns=["symbol", "date", "close"])
    events = build_events(segments, bars)
    binned, thresholds = add_bins(events)
    summary = summarize_bins(binned)
    candidates = test_candidates(binned, summary)
    overlap = phase_overlap(segments)
    out.mkdir(parents=True, exist_ok=True)
    events.to_csv(out / "events.csv", index=False)
    thresholds.to_csv(out / "feature_thresholds.csv", index=False)
    summary.to_csv(out / "bin_summary.csv", index=False)
    candidates.to_csv(out / "candidate_tests.csv", index=False)
    overlap.to_csv(out / "phase_overlap_audit.csv", index=False)
    audit = {
        "primary_phase": PRIMARY_PHASE,
        "data_end": str(DATA_END.date()),
        "events": len(events),
        "early_events": int(events["stage"].eq("early").sum()),
        "recent_events": int(events["stage"].eq("recent").sum()),
        "feature_missing_rates": {f: float(events[f].isna().mean()) for f in FEATURES},
        "segments_sha256": sha256(segments_path),
        "daily_bars_sha256": sha256(data_path),
    }
    (out / "data_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(out, events, summary, candidates, thresholds, overlap)
    print(f"[OK] {len(events)} 个排名换仓事件 → {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--segments", type=Path, default=DEFAULT_SEGMENTS)
    parser.add_argument("--data-parquet", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    return run(args.segments, args.data_parquet, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
