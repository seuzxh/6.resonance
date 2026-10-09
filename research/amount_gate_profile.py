"""amount-gate P0：锚/概念指数成交额缺失画像（只读缓存，不触网）。

预注册：docs/research/amount-gate.md §二。裁决门限：主用三锚的
5 日滚动窗（t−5..t−1）可得率均 ≥ 80% 方可进入主实验。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402

ANCHORS = dict(config.V43_ANCHOR_POOL)          # D3 主用三锚
D2_EXTRA = {"000852.SH": "中证1000"}            # D2 锚（备用）
WIN = 5                                          # 量能分位窗口（冻结）
RESEARCH_END = "2026-09-18"                      # 研究数据冻结截止


def profile(symbols: dict[str, str], bars: pd.DataFrame, label: str) -> pd.DataFrame:
    rows = []
    for code, name in symbols.items():
        s = bars[bars["symbol"] == code].set_index("date").sort_index()
        s.index = pd.to_datetime(s.index)
        s = s.loc[:RESEARCH_END]
        amt = s["amount"]
        ok = amt.notna()
        # 5 日窗可得 >=4 日（含窗内缺失判定；当日缺一并视为不可判）
        roll_ok = (ok.rolling(WIN).sum().shift(0) >= WIN - 1) & ok
        usable_5d = float(roll_ok.mean())
        # 最长连续缺口
        gap = (~ok).astype(int)
        longest = max((gap.groupby((gap != gap.shift()).cumsum()).sum()), default=0)
        by_year = ok.groupby(ok.index.year).mean().round(3)
        rows.append({
            "锚": name, "代码": code, "交易日数": len(s),
            "amount可得率": round(float(ok.mean()), 4),
            "5日窗可得率": round(usable_5d, 4),
            "最长连续缺口": int(longest),
            "分年可得率": dict(by_year),
        })
    df = pd.DataFrame(rows)
    print(f"\n===== {label} =====")
    print(df.to_string(index=False))
    return df


def main() -> int:
    bars = pd.read_parquet(config.CACHE_DIR / "daily_bars.parquet")
    anchors = profile(ANCHORS | D2_EXTRA, bars, "锚指数量能画像（研究窗截断 2026-09-18）")

    cat = pd.read_csv(config.DATA_DIR / "concept_catalog.csv")
    concepts = dict(zip(cat["code"], cat["name"]))
    sub = bars[bars["symbol"].isin(concepts)]
    ok = sub.assign(ok=sub["amount"].notna()).groupby("symbol")["ok"].mean()
    print(f"\n===== 概念池（参考，{len(ok)} 个）=====")
    print(f"概念 amount 可得率：中位 {ok.median():.3f}，P25 {ok.quantile(0.25):.3f}，"
          f"P75 {ok.quantile(0.75):.3f}，可得率 ≥80% 的概念占比 {(ok >= 0.8).mean():.3f}")

    gate = anchors[anchors["代码"].isin(config.V43_ANCHOR_POOL)]["5日窗可得率"] >= 0.8
    verdict = "PASS：三锚 5 日窗可得率均 ≥ 80%，可进入主实验" if gate.all() \
        else "FAIL：存在 5 日窗可得率 < 80% 的锚，按预注册 §二处置"
    print(f"\n[P0 裁决] {verdict}")

    out = Path(__file__).resolve().parents[1] / "outputs" / "amount_gate"
    out.mkdir(parents=True, exist_ok=True)
    anchors.drop(columns=["分年可得率"]).to_csv(out / "profile.csv", index=False)
    (out / "profile.md").write_text(
        f"# amount-gate P0 缺失画像\n\n生成：2026-10-10。窗口 5 日，研究窗截断 {RESEARCH_END}。\n\n"
        + anchors.to_markdown(index=False)
        + f"\n\n概念池可得率中位 {ok.median():.3f}（参考）。\n\n**裁决：{verdict}**\n",
        encoding="utf-8")
    print(f"[OK] 画像已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
