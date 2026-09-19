"""共振指标：概念指数 ↔ 宽基指数的相关性。

口径迁移自 GPT 会话（2026-09-17/18）：
- 主口径：20 日窗口 Pearson 相关；
- 信息量口径：控制同花顺全A后的偏相关（排除市场普涨普跌）；
- 5 日窗口仅作对照（样本过短，易出现 ~1 的偶然相关）；
- 60 日窗口用于稳健性复核。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def daily_returns(close: pd.DataFrame) -> pd.DataFrame:
    """收盘价宽表（index=日期, columns=代码）→ 日收益率。"""
    return close.pct_change()


def rolling_correlation(
    returns: pd.DataFrame,
    concept: str,
    index_code: str,
    window: int,
) -> pd.Series:
    """概念与指数的滚动 Pearson 相关。"""
    return returns[concept].rolling(window).corr(returns[index_code])


def partial_correlation(
    returns: pd.DataFrame,
    concept: str,
    index_code: str,
    control: str,
    window: int,
) -> pd.Series:
    """滚动偏相关：控制 ``control``（如同花顺全A）后 concept↔index 的相关。

    residualize 两序列对控制变量的滚动回归残差，再算残差相关。
    """
    x, y, z = returns[concept], returns[index_code], returns[control]

    def _resid(a: pd.Series) -> pd.Series:
        cov_az = a.rolling(window).cov(z)
        var_z = z.rolling(window).var()
        beta = cov_az / var_z
        mean_a = a.rolling(window).mean()
        mean_z = z.rolling(window).mean()
        return a - (mean_a + beta * (z - mean_z))

    rx, ry = _resid(x), _resid(y)
    return rx.rolling(window).corr(ry)


def resonance_rankings(
    returns: pd.DataFrame,
    index_code: str,
    concepts: list[str],
    window: int,
    control: str | None = None,
    asof: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """截至 asof 的概念-指数共振榜单。

    返回 DataFrame[concept, corr] 按 corr 降序；样本不足 window 的概念排除。
    """
    df = returns if asof is None else returns.loc[:asof]
    rows = []
    for c in concepts:
        if c not in df.columns:
            continue
        series = (
            partial_correlation(df, c, index_code, control, window)
            if control
            else rolling_correlation(df, c, index_code, window)
        )
        val = series.iloc[-1]
        if pd.notna(val):
            rows.append({"concept": c, "corr": float(val)})
    out = pd.DataFrame(rows, columns=["concept", "corr"])
    return out.sort_values("corr", ascending=False).reset_index(drop=True)


def updown_resonance_rankings(
    returns: pd.DataFrame,
    index_code: str,
    concepts: list[str],
    window: int,
    side: str = "up",
    min_days: int = 8,
    asof: pd.Timestamp | None = None,
    stats: dict | None = None,
) -> pd.DataFrame:
    """上涨/下跌共振榜单：仅用窗口内领先指数上涨日（side='up'，收益>0）
    或下跌日（side='down'，收益<0）计算概念-指数 Pearson 相关。

    做多轮动先验：跟随领导指数上攻的同涨性才是可用共振，同跌只是风险暴露。
    口径约束：概念须窗口内收益全非 NaN（对齐 rolling min_periods=window 的
    排除规则）；条件样本 < min_days 时该信号日整体回退全窗口相关（stats 计数）。
    返回 DataFrame[concept, corr] 降序，与 resonance_rankings 同构。
    """
    assert side in ("up", "down")
    df = returns if asof is None else returns.loc[:asof]
    rets = df.iloc[-window:]
    y_all = rets[index_code] if index_code in rets.columns else None
    cols = [c for c in concepts if c in rets.columns]
    if y_all is None or len(rets) < window or y_all.isna().any():
        if stats is not None:
            stats["no_leader"] = stats.get("no_leader", 0) + 1
        return pd.DataFrame(columns=["concept", "corr"])
    eligible = rets[cols].notna().all(axis=0)
    cols = [c for c in cols if eligible[c]]
    if not cols:
        return pd.DataFrame(columns=["concept", "corr"])

    mask = (y_all > 0) if side == "up" else (y_all < 0)
    if int(mask.sum()) < min_days:
        if stats is not None:
            stats["fallback"] = stats.get("fallback", 0) + 1
        mask = pd.Series(True, index=rets.index)
    elif stats is not None:
        stats.setdefault("n_cond", []).append(int(mask.sum()))

    X = rets.loc[mask, cols].to_numpy(dtype=float)
    y = y_all[mask].to_numpy(dtype=float)
    Xc = X - X.mean(axis=0)
    yc = y - y.mean()
    num = Xc.T @ yc
    den = np.sqrt((Xc ** 2).sum(axis=0) * (yc ** 2).sum())
    with np.errstate(divide="ignore", invalid="ignore"):
        corr = np.where(den > 0, num / den, np.nan)
    out = pd.DataFrame({"concept": cols, "corr": corr}).dropna()
    return out.sort_values("corr", ascending=False).reset_index(drop=True)
