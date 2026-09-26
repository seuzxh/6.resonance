"""采集数据验证：schema / 交易日网格 / 字段自洽 / 跨源抽查 / 新概念分布。

用法：
    conda run -n resonance python ops/validate_data.py

对照源：
1. 本地 qlib 库 ~/.qlib/qlib_data/cn_data（沪深300 等宽基，独立数据链路）
2. data/cache/probe_benchmark.csv（同 API 独立请求，检采集管线）
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402

QLIB_DIR = Path.home() / ".qlib/qlib_data/cn_data"


def load_bars() -> pd.DataFrame:
    f = config.CACHE_DIR / "daily_bars.parquet"
    if not f.exists():
        f = config.CACHE_DIR / "daily_bars.partial.parquet"
    return pd.read_parquet(f)


def check_schema(bars: pd.DataFrame) -> list[str]:
    """close 空/非正检查：区分前导 NaN（新概念激活日前，合法）与有效期内 NaN（缺陷）。"""
    problems = []
    need = {"symbol", "date", "close", "pct_chg", "pre_close", "open", "high", "low", "volume", "amount"}
    missing = need - set(bars.columns)
    if missing:
        problems.append(f"缺字段: {missing}")
    dup = bars.duplicated(subset=["symbol", "date"]).sum()
    if dup:
        problems.append(f"重复 (symbol,date) {dup} 行")
    bad = bars["close"].isna() | (bars["close"] <= 0)
    if bad.any():
        # 每个代码首个有效 close 之后的 NaN/非正才是缺陷
        bars_valid = bars.dropna(subset=["close"])
        bars_valid = bars_valid[bars_valid["close"] > 0]
        first_valid = bars_valid.groupby("symbol")["date"].min()
        bad_rows = bars[bad].copy()
        bad_rows["fv"] = bad_rows["symbol"].map(first_valid)
        inner_bad = bad_rows[bad_rows["date"] > bad_rows["fv"]]
        n_leading = len(bad_rows) - len(inner_bad)
        n_inner = len(inner_bad)
        activated = int((~first_valid.index.duplicated()).sum())
        if n_inner:
            worst = inner_bad.groupby("symbol")["date"].count().sort_values(ascending=False).head(3)
            problems.append(f"有效期内 close NaN/非正 {n_inner} 行（{dict(worst)}）")
        print(f"[INFO] 前导 NaN（激活日前，合法）{n_leading} 行，涉及概念 "
              f"{bad_rows['symbol'].nunique()} 个；有有效行情的概念 {activated} 个")
    return problems


def check_grid(bars: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """以全A(883957)日历为基准网格，查概念/指数的中间缺口与首日分布。"""
    problems = []
    alla = sorted(bars.loc[bars["symbol"] == "883957.TI", "date"].unique())
    cal = pd.Series(alla)
    cov = bars.groupby("symbol")["date"].agg(["min", "max", "count"])
    # 中间缺口：首末之间缺的网格日
    cal_set = set(alla)
    gaps = {}
    for sym, row in cov.iterrows():
        expected = {d for d in cal_set if row["min"] <= d <= row["max"]}
        actual = set(bars.loc[bars["symbol"] == sym, "date"])
        gap = len(expected - actual)
        if gap:
            gaps[sym] = gap
    # 微小缺口（≤2 日/代码）在概念指数上偶发（如 886111 首月 1 日洞），
    # 宽表转 NaN 后自然被 20 日窗口排除，降级为告警
    big = {k: v for k, v in gaps.items() if v > 2}
    if big:
        worst = sorted(big.items(), key=lambda kv: -kv[1])[:5]
        problems.append(f"{len(big)} codes 缺口 >2 日（最多: {worst}）")
    if gaps:
        small = sorted(gaps.items(), key=lambda kv: -kv[1])[:5]
        print(f"[INFO] 微小缺口（≤2 日，告警不判失败）: {small}")
    late_end = cov[cov["max"] < alla[-1]]
    if len(late_end):
        problems.append(f"{len(late_end)} codes 提前止步（最早已止: {late_end['max'].min()}）")
    return cov, problems


def check_self_consistency(bars: pd.DataFrame) -> list[str]:
    problems = []
    # pct_chg 单位确认 + 与 close/pre_close 自洽（指数无分红，pre_close 应严格衔接）
    sub = bars.dropna(subset=["close", "pre_close", "pct_chg"]).copy()
    sub["implied_pct"] = (sub["close"] / sub["pre_close"] - 1.0) * 100.0
    err = (sub["implied_pct"] - sub["pct_chg"]).abs()
    bad = (err > 0.5).sum()  # 单位若错会差百倍，0.5 容差足够识别单位错误
    if bad:
        problems.append(f"pct_chg 与 close/pre_close 不自洽 {bad} 行（中位误差 {err.median():.4f}）")
    # OHLC 关系
    ohlc_bad = ((bars["high"] < bars["low"]) | (bars["close"] > bars["high"]) | (bars["close"] < bars["low"])).sum()
    if ohlc_bad:
        problems.append(f"OHLC 关系违例 {ohlc_bad} 行")
    return problems


def _qlib_read_bin(code_dir: Path, field: str) -> np.ndarray:
    return np.fromfile(code_dir / f"{field}.day.bin", dtype="<f")


def check_cross_source(bars: pd.DataFrame) -> tuple[list[str], pd.DataFrame]:
    """与本地 qlib 库（独立链路）对照宽基 close。

    索引 bin 不在 instruments/all.txt（项目2 dump 后剔除 benchmark），span 未知，
    故用滑动对齐：在 bin 的所有可行偏移中找与我方序列相对误差中位数最小者；
    要求 ①最优偏移的误差 <5bps（值级验证）②各指数最优偏移互一致（对齐正确性
    的旁证：同一批 dump，日历窗口相同）。
    """
    problems, rows = [], []
    qlib_cal = (QLIB_DIR / "calendars/day.txt").read_text().split()
    mapping = {  # iFinD → qlib 代码
        "000300.SH": "sh000300", "000905.SH": "sh000905", "000852.SH": "sh000852",
        "399006.SZ": "sz399006",
    }
    offsets = {}
    for ifind_code, qcode in mapping.items():
        qdir = QLIB_DIR / "features" / qcode
        if not qdir.exists():
            continue
        qclose = _qlib_read_bin(qdir, "close")
        mine = bars.loc[bars["symbol"] == ifind_code].sort_values("date")
        my_dates = list(mine["date"])  # qlib 日历同样为 YYYY-MM-DD
        my_close = mine["close"].to_numpy()
        k = qlib_cal.index(my_dates[0])  # 我方首日在 qlib 日历中的位置
        # bin 内偏移 b0：bin[b0 + i] 对应 my_dates[i]，要求 b0 + n ≤ len(bin)
        best = None
        for b0 in range(0, len(qclose) - len(my_dates) + 1):
            cal_gap = k - b0  # >0 表示 bin 窗口比我方窗口早开始 cal_gap 个日历日
            if cal_gap < 0:
                break
            seg = qclose[b0 : b0 + len(my_dates)]
            with np.errstate(divide="ignore", invalid="ignore"):
                rel = np.abs(seg / my_close - 1.0)
            med = float(np.nanmedian(rel))
            if best is None or med < best[1]:
                best = (b0, med)
        b0, med = best
        seg = qclose[b0 : b0 + len(my_dates)]
        rel = np.abs(seg / my_close - 1.0)
        # 尾部锚定：bin 末值对齐到的日历日（对齐正确则 ≤ 2026-09-18 且合理）
        tail_cal = qlib_cal.index(my_dates[-1])
        offsets[qcode] = tail_cal - (b0 + len(my_dates) - 1)  # bin 尾后剩余日历日数
        rows.append({
            "index": ifind_code,
            "n_overlap": int(np.sum(np.isfinite(rel))),
            "median_diff_bps": round(med * 1e4, 2),
            "max_diff_bps": round(float(np.nanmax(rel)) * 1e4, 2),
            "n_gt_1pct": int(np.nansum(rel > 0.01)),
            "bin_tail_lag_days": offsets[qcode],
        })
        if med > 5e-4:
            problems.append(f"{ifind_code} vs qlib {qcode}: 中位偏差 {med*1e4:.1f}bps（>5bps）")
        if np.nansum(rel > 0.01) > len(rel) * 0.01:
            problems.append(f"{ifind_code}: >1% 偏差点占比过高")
    if len(set(offsets.values())) > 1:
        problems.append(f"各指数对齐偏移不一致: {offsets}（对齐或 dump 批次存疑）")
    return problems, pd.DataFrame(rows)


def check_probe_cache(bars: pd.DataFrame) -> list[str]:
    problems = []
    probe = pd.read_csv(config.CACHE_DIR / "probe_benchmark.csv")
    merged = probe.merge(bars[["symbol", "date", "close", "pct_chg"]], on=["symbol", "date"], suffixes=("_probe", ""))
    if len(merged) != len(probe):
        problems.append(f"probe {len(probe)} 行仅对上 {len(merged)} 行")
    cerr = (merged["close_probe"] / merged["close"] - 1).abs().max()
    if cerr > 1e-9:
        problems.append(f"probe close 对照最大偏差 {cerr:.2e}")
    return problems


def concept_listing_dist(bars: pd.DataFrame, catalog: pd.DataFrame) -> pd.DataFrame:
    cov = bars.groupby("symbol")["date"].agg(first="min", last="max", n="count")
    concepts = cov.loc[[c for c in catalog["code"] if c in cov.index]]
    n_full = (concepts["first"] <= "2024-10-31").sum()
    n_2025 = ((concepts["first"] > "2024-12-31") & (concepts["first"] <= "2025-12-31")).sum()
    n_2026 = (concepts["first"] > "2025-12-31").sum()
    early = (concepts["first"] <= "2025-01-02").sum()
    return pd.DataFrame({
        "已采集概念": [len(concepts)],
        "数据始于2024-10(全历史)": [n_full],
        "数据始于2025前(≤2025-01-02)": [early],
        "2025年内上市": [n_2025],
        "2026年上市": [n_2026],
    })


def main() -> int:
    bars = load_bars()
    catalog = pd.read_csv(config.DATA_DIR / "concept_catalog.csv")
    print(f"载入 {len(bars)} 行 × {bars['symbol'].nunique()} codes "
          f"({bars['date'].min()} ~ {bars['date'].max()})；目录 {len(catalog)} 概念\n")

    all_problems: dict[str, list[str]] = {}

    all_problems["schema"] = check_schema(bars)
    cov, grid_problems = check_grid(bars)
    all_problems["grid"] = grid_problems
    all_problems["self_consistency"] = check_self_consistency(bars)
    cross_problems, cross_tbl = check_cross_source(bars)
    all_problems["cross_source_qlib"] = cross_problems
    all_problems["probe_cache"] = check_probe_cache(bars)

    print("=== 跨源对照（iFinD vs 本地 qlib 库，独立链路）===")
    if len(cross_tbl):
        print(cross_tbl.to_string(index=False))
    print("\n=== 概念上市分布（幸存者偏差标注用）===")
    print(concept_listing_dist(bars, catalog).to_string(index=False))

    print("\n=== 问题清单 ===")
    ok = True
    for section, problems in all_problems.items():
        if problems:
            ok = False
            for p in problems:
                print(f"[FAIL] {section}: {p}")
        else:
            print(f"[PASS] {section}")
    print("\nVALIDATION " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
