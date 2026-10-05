"""凌晨生产审计智能体：只读账单与快照，产出复盘报告和优化建议。

纪律：
1. 本脚本不触网、不改参数、不重算账户、不下单；
2. 审计失败只发布失败状态，不掩盖上一日信号；
3. 优化建议只进入人工裁决队列，OOS（样本外）评价期内禁止改参。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.backtest import perf_stats  # noqa: E402

OUT = config.OUTPUTS_DIR / "oos"
SITE_DATA = Path(__file__).resolve().parents[1] / "site" / "public" / "data"
TRACKS = ("D3", "D2")


def _nav(track: str) -> pd.Series | None:
    f = OUT / f"nav_{track}.csv"
    if not f.exists():
        return None
    df = pd.read_csv(f)
    return pd.Series(df["nav"].to_numpy(), index=pd.to_datetime(df["date"])).dropna()


def _daily_as_of() -> pd.Timestamp:
    bars = pd.read_parquet(config.CACHE_DIR / "daily_bars.parquet", columns=["symbol", "date"])
    return pd.to_datetime(bars["date"]).max()


def _minute_as_of() -> pd.Timestamp | None:
    f = config.CACHE_DIR / "minute5_bars.parquet"
    if not f.exists():
        return None
    df = pd.read_parquet(f, columns=["symbol", "datetime"])
    return pd.to_datetime(df["datetime"]).max()


def _snapshot() -> dict:
    f = OUT / "production_snapshot.json"
    if not f.exists():
        return {}
    return json.loads(f.read_text(encoding="utf-8"))


def _site_nav() -> dict:
    f = SITE_DATA / "nav.json"
    if not f.exists():
        return {}
    return json.loads(f.read_text(encoding="utf-8"))


def _check(condition: bool, level: str, code: str, message: str,
           checks: list[dict], proposals: list[dict], action: str) -> None:
    if condition:
        return
    checks.append({"level": level, "code": code, "message": message})
    if code not in {p["code"] for p in proposals}:
        proposals.append({"code": code, "priority": "最高" if level == "critical" else "高",
                          "action": action, "owner": "人工复核",
                          "guard": "修复只限数据与运行链路；不得修改冻结参数。"})

def build_audit(run_date: str) -> tuple[dict, str]:
    daily_as_of = _daily_as_of()
    minute_as_of = _minute_as_of()
    snap = _snapshot()
    site = _site_nav()
    navs = {tr: _nav(tr) for tr in TRACKS}
    checks: list[dict] = []
    proposals: list[dict] = []

    _check(all(n is not None for n in navs.values()), "critical", "NAV_MISSING",
           "D3 或 D2 官方净值文件缺失。", checks, proposals,
           "先补跑 ops/signal_daily.py，再发布站点。")
    expected = str(daily_as_of.date())
    if all(n is not None for n in navs.values()):
        ends = {tr: str(n.index[-1].date()) for tr, n in navs.items()}  # type: ignore[union-attr]
        _check(ends["D3"] == ends["D2"] == expected, "critical", "NAV_ALIGN",
               f"生产净值末日与日线末日不一致：{ends} != {expected}。", checks, proposals,
               "重跑当日 runner，并检查日线采集是否部分失败。")
        for tr, n in navs.items():  # type: ignore[assignment]
            ret = n.pct_change().abs()
            _check(not bool((ret > 0.2).any()), "critical", f"NAV_JUMP_{tr}",
                   f"{tr} 存在单日绝对收益大于20%的点。", checks, proposals,
                   "冻结站点发布，核对行情与交易文件。")

    tracks = snap.get("tracks", {})
    _check(set(TRACKS).issubset(tracks), "critical", "SNAP_MISSING",
           "生产快照缺少 D3 或 D2。", checks, proposals,
           "重跑默认 runner，生成完整 production_snapshot.json。")
    if set(TRACKS).issubset(tracks):
        snap_dates = {tr: tracks[tr].get("signal_date") for tr in TRACKS}
        _check(snap_dates["D3"] == snap_dates["D2"] == expected, "critical", "SNAP_ALIGN",
               f"生产快照信号日不一致：{snap_dates} != {expected}。", checks, proposals,
               "重跑默认 runner，禁止手工编辑快照。")
        for tr in TRACKS:
            t = tracks[tr]
            holding = bool(t.get("holding"))
            weight = float(t.get("position_weight", 0.0))
            _check(weight in (0.0, 1.0) and holding == (weight == 1.0),
                   "critical", f"POSITION_{tr}",
                   f"{tr} 持仓与仓位比例不相容。", checks, proposals,
                   "用官方交易文件重放持仓，再重建快照。")

    if minute_as_of is not None:
        lag_days = int((daily_as_of.normalize() - minute_as_of.normalize()).days)
        _check(lag_days <= 4, "warning", "MINUTE_STALE",
               f"5分钟数据滞后 {lag_days} 个自然日。", checks, proposals,
               "按需求矩阵补采缺失尾盘5分钟K线，并核对降级计数。")

    stats = site.get("stats", {})
    _check(set(TRACKS).issubset(stats), "warning", "SITE_STATS_MISSING",
           "站点净值数据缺少 D3 或 D2。", checks, proposals,
           "执行 publish_site.py --dry-run 通过后再发布。")

    metrics: dict[str, dict] = {}
    for tr, n in navs.items():
        if n is None:
            continue
        st = perf_stats(n)
        metrics[tr] = {"oos_total": round(float(st["total_return"]), 6),
                       "oos_max_drawdown": round(float(st["max_drawdown"]), 6),
                       "oos_days": int(len(n))}
    display_start = site.get("start", "2026-01-01")
    as_of = site.get("as_of", expected)
    for tr in TRACKS:
        if tr in stats:
            metrics[tr].update({
                "display_nav": round(float(stats[tr]["nav"]), 6),
                "display_total": round(float(stats[tr]["total_ret"]), 6),
                "display_max_drawdown": round(float(stats[tr]["max_dd"]), 6),
            })

    if {"D3", "D2"}.issubset(stats):
        diff = float(stats["D3"]["total_ret"] - stats["D2"]["total_ret"])
        metrics["display_total_difference"] = round(diff, 6)
        if abs(diff) >= 0.10:
            proposals.append({
                "code": "REGIME_REVIEW", "priority": "高",
                "action": "复盘两轨的锚选择差异、概念暴露和止损事件；只形成下一轮预注册课题。",
                "owner": "人工复核",
                "guard": "样本外（OOS）评价期内不改参数，不切换生产主轨。",
            })

    fallback = sum(int(tracks.get(tr, {}).get("stats", {}).get(k, 0))
                   for tr in TRACKS
                   for k in ("minute_fallback_leader", "minute_excluded", "minute_fallback_sparse"))
    metrics["minute_fallback_events"] = fallback
    if fallback > 0:
        proposals.append({
            "code": "MINUTE_COVERAGE", "priority": "高",
            "action": "按预注册降级原因分列缺口，优先修复高频覆盖与采集时机。",
            "owner": "数据运维",
            "guard": "不得用盘中未收盘数据替换官方收盘数据。",
        })

    proposals.extend([
        {"code": "KEEP_FROZEN", "priority": "常规",
         "action": "维持 D3/D2 参数冻结，继续并行记录，不因短期差异改参。",
         "owner": "策略负责人", "guard": "样本外（OOS）评价期禁改参。"},
        {"code": "OOS_DECISION", "priority": "常规",
         "action": "达到至少60个信号日后，按预注册判据发起人工裁决会。",
         "owner": "策略负责人", "guard": "裁决前只记录，不自动切换。"},
    ])

    status = "critical" if any(c["level"] == "critical" for c in checks) else (
        "warning" if checks else "ok")
    audit = {
        "version": 1, "run_date": run_date, "status": status,
        "daily_as_of": expected,
        "minute_as_of": str(minute_as_of.date()) if minute_as_of is not None else None,
        "snapshot_as_of": snap.get("tracks", {}).get("D3", {}).get("signal_date"),
        "display_start": display_start, "display_as_of": as_of,
        "metrics": metrics, "checks": checks, "proposals": proposals,
        "boundary": "指数不可直接交易；单边10个基点是统一压力假设；2026-09-29前为历史展示段，之后为官方样本外（OOS）段。",
    }

    d3 = metrics.get("D3", {})
    d2 = metrics.get("D2", {})
    diff = metrics.get("display_total_difference")
    status_label = {"ok": "通过", "warning": "告警", "critical": "硬告警"}.get(status, status)
    lines = [
        "# 生产并行凌晨审计报告",
        "",
        f"审计智能体在 {run_date} 审计D3三锚动选与D2双锚动选。审计结论为「{status_label}」。",
        "",
        "## 一、账户表现",
        "",
        f"- 在 {display_start} 至 {as_of}、单边10个基点、净值曲线拼接展示口径下，"
        f"D3 三锚动选累计收益为 {d3.get('display_total', float('nan')):+.2%}，"
        f"相对 D2 双锚动选的差额为 {diff * 100:+.2f}个百分点。" if diff is not None else
        f"- 在 {display_start} 至 {as_of}、单边10个基点、净值曲线拼接展示口径下，"
        f"D3 三锚动选累计收益为 {d3.get('display_total', float('nan')):+.2%}；D2站点统计缺失。",
        f"- 在 {snap.get('oos_start', '2026-09-29')} 至 {expected}、单边10个基点、官方样本外（OOS）空仓起步口径下，"
        f"D3 累计收益为 {d3.get('oos_total', float('nan')):+.2%}，"
        f"D2 累计收益为 {d2.get('oos_total', float('nan')):+.2%}。",
        f"- 同一展示口径下，D3 最大回撤为 {d3.get('display_max_drawdown', float('nan')):.2%}，"
        f"D2 最大回撤为 {d2.get('display_max_drawdown', float('nan')):.2%}。",
        "- 概念指数不可直接交易；收益是纸面账户结果，不是实盘承诺。",
        "",
        "## 二、审计检查",
    ]
    if checks:
        lines.extend(f"- [{ {'warning': '告警', 'critical': '硬告警'}.get(c['level'], c['level']) }] {c['code']}：{c['message']}" for c in checks)
    else:
        lines.append("- 全部硬检查通过。")
    lines.extend(["", "## 三、优化与复盘队列"])
    lines.extend(f"- {p['code']}（{p['priority']}）：{p['action']}约束：{p['guard']}"
                 for p in proposals)
    lines.extend([
        "",
        "## 四、复盘纪律",
        "",
        "1. 每日先核对数据末日、净值末日、快照信号日和持仓一致性。",
        "2. 每周复盘锚选择差异、概念持仓重叠、止损与降级计数。",
        "3. 每月重放同数据账单，要求逐位一致。",
        "4. OOS 评价期内只修复数据与运行故障；参数变更必须等待评价期结束并重新预注册。",
        "",
    ])
    return audit, "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=str(pd.Timestamp.now().date()))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    if os.environ.get("REQUIRE_CLEAN_REPO") == "1":
        dirty = subprocess.run(["git", "diff", "--quiet"], cwd=root).returncode != 0
        staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=root).returncode != 0
        if dirty or staged:
            print("[audit] 生产工作区不干净；为避免误提交代码，审计服务拒绝运行。")
            return 2

    audit, report = build_audit(args.date)
    report_dir = config.OUTPUTS_DIR / "production"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "audit_latest.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    (report_dir / f"audit_{args.date}.md").write_text(report, encoding="utf-8")
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    (SITE_DATA / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False), encoding="utf-8")
    print(f"[audit] 状态 {audit['status']}；报告 {report_dir / f'audit_{args.date}.md'}")
    if args.dry_run:
        return 0

    subprocess.run(["git", "add", "site/public/data/audit.json"], cwd=root, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=root).returncode == 0:
        print("[audit] 审计数据无变化，跳过提交")
        return 0
    msg = f"audit: production review {args.date}"
    subprocess.run(["git", "commit", "-m", msg], cwd=root, check=True)
    r = subprocess.run(["git", "push", "origin", "HEAD:master"], cwd=root)
    if r.returncode != 0:
        print("[audit] push 失败；本地已提交，网络恢复后手动 git push")
        return 1
    print(f"[audit] 已提交并推送：{msg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
