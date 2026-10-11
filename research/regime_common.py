"""市场状态识别研究共享工具（regime-detection R1–R5）。

预注册：docs/research/regime-detection-plan.md（2026-10-12 预注册版）。
全部函数离线：只读 data/cache 与主仓 outputs 既有数据表，不访问网络。
窗口冻结 2021-12-01 ~ 2026-09-18；特征主线 = logret + 5 日滚动波动率（用户
2026-10-12 指令，20 日不再使用）。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM
from scipy.special import logsumexp
from scipy.stats import multivariate_normal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from resonance import config  # noqa: E402

RESEARCH_START = "2021-12-01"
RESEARCH_END = "2026-09-18"                      # 信息截止（roadmap 冻结）
MAIN_CODE = "883957.TI"                          # 全A（主对象，用户 2026-10-11 裁决）
ANCHOR_CODES = ["399001.SZ", "399303.SZ", "000688.SH"]  # 三锚稳健性复制
GATE_DUAL = ["399001.SZ", "000852.SH"]           # 研究主基线双锚（仅 R4 闸门复算）
ALL_CODES = [MAIN_CODE, *ANCHOR_CODES, "000852.SH"]

OUT_DIR = config.OUTPUTS_DIR / "regime"
# 主仓 outputs 只读引用（worktree 的 outputs 不含历史数据表；outputs 数据表不入 git）
MAIN_OUTPUTS = Path("/home/zxh/projects/6.resonance/outputs")

# 与 resonance/v3.py V3Params 默认一致（复用口径，不重写）
LEADER_WINDOW = 10
POS_WINDOW = 3
DD_WINDOW = 10
DD_TIERS = (0.02, 0.04)
HALF_LIVES = (5, 3, 2)

HMM_SEEDS = range(10)
HMM_STATES_CANDIDATES = (2, 3, 4)

# R2 冻结：PELT+rbf，pen 扫描档位，中心档 10
RUPTURES_PENS = (1, 3, 10, 30, 100)
CENTRAL_PEN = 10
MIN_SIZE = 20

# Q3 事件清单（预注册冻结，见方案 §四 R2）
EVENTS: dict[str, tuple[str, str]] = {
    "E1": ("2022-04-27", "疫情封控市场低点"),
    "E2": ("2022-11-11", "防疫二十条与地产政策转向"),
    "E3": ("2024-01-02", "小盘/微盘流动性危机起点"),
    "E4": ("2024-09-24", "一揽子金融政策转向"),
    "E5": ("2025-04-07", "对等关税冲击"),
}
EVENT_WINDOW_DAYS = 10      # 对齐窗口（交易日）
BKPT_STABLE_DAYS = 15       # 判据 1：相邻 pen 档对应断点最大漂移
CONFIRM_WINDOW_DAYS = 5     # 判据 3：HMM 切换日与断点确认窗口
MIN_TRAIN_DAYS = 120        # R5：训练窗不足此数不产生状态


def load_daily_closes() -> dict[str, pd.Series]:
    """缓存日线 → {代码: 收盘价序列}，窗口 2021-12-01~2026-09-18。"""
    bars = pd.read_parquet(config.CACHE_DIR / "daily_bars.parquet")
    out: dict[str, pd.Series] = {}
    for code in ALL_CODES:
        sub = bars[bars["symbol"] == code].set_index("date").sort_index()
        sub.index = pd.to_datetime(sub.index)
        out[code] = sub["close"].loc[RESEARCH_START:RESEARCH_END]
    return out


def build_features(close: pd.Series) -> pd.DataFrame:
    """logret + vol5（近 5 个交易日 logret 样本标准差，含当日；无未来函数）。"""
    logret = np.log(close).diff()
    vol5 = logret.rolling(5).std(ddof=1)
    return pd.DataFrame({"close": close, "logret": logret, "vol5": vol5})


def zscore(X: np.ndarray) -> np.ndarray:
    mu = X.mean(axis=0)
    sd = X.std(axis=0, ddof=0)
    sd = np.where(sd == 0.0, 1.0, sd)
    return (X - mu) / sd


def hmm_n_params(k: int, d: int) -> int:
    """GaussianHMM(full) 自由参数数：初始 K−1 + 转移 K(K−1) + 均值 Kd + 协方差 Kd(d+1)/2。"""
    return (k - 1) + k * (k - 1) + k * d + k * d * (d + 1) // 2


@dataclass(frozen=True)
class HmmFit:
    k: int
    seed: int
    loglik: float
    bic: float
    model: GaussianHMM
    labels: np.ndarray          # 维特比解码（仅全样本结构对照，ISOLATED）
    relabel: np.ndarray         # 原状态 → 按训练窗 vol5 均值升序的新标签


def fit_hmm_best_seed(
    X_scaled: np.ndarray, vol_unscaled: np.ndarray, k: int,
    seeds=HMM_SEEDS,
) -> HmmFit:
    """同一 K 下多种子拟合，取对数似然最高者；状态按 vol5 均值升序重贴标签。"""
    best: GaussianHMM | None = None
    best_ll, best_seed = -np.inf, -1
    for s in seeds:
        m = GaussianHMM(
            n_components=k, covariance_type="full",
            n_iter=200, tol=1e-3, random_state=s,
        )
        try:
            m.fit(X_scaled)
            ll = float(m.score(X_scaled))
        except Exception:
            continue
        if ll > best_ll:
            best, best_ll, best_seed = m, ll, s
    if best is None:
        raise RuntimeError(f"HMM 拟合全部种子失败：k={k}")
    labels = best.predict(X_scaled)  # 维特比
    state_vol = np.array([vol_unscaled[labels == j].mean() for j in range(k)])
    order = np.argsort(state_vol)            # vol 升序
    rank = np.empty(k, dtype=int)
    rank[order] = np.arange(k)
    n = len(X_scaled)
    bic = -2.0 * best_ll + hmm_n_params(k, X_scaled.shape[1]) * np.log(n)
    return HmmFit(k, best_seed, best_ll, bic, best, labels, rank)


def forward_filter_probs(model: GaussianHMM, X_scaled: np.ndarray) -> np.ndarray:
    """逐日前向滤波概率（只用 ≤t 数据；不用维特比/前向-后向平滑）。"""
    k = model.n_components
    logb = np.column_stack([
        multivariate_normal.logpdf(X_scaled, model.means_[j], model.covars_[j])
        for j in range(k)
    ])
    logstart = np.log(np.clip(model.startprob_, 1e-300, None))
    logT = np.log(np.clip(model.transmat_, 1e-300, None))
    logalpha = np.empty((len(X_scaled), k))
    la = logstart + logb[0]
    logalpha[0] = la - logsumexp(la)
    for t in range(1, len(X_scaled)):
        la = logsumexp(logalpha[t - 1][:, None] + logT, axis=0) + logb[t]
        logalpha[t] = la - logsumexp(la)
    return np.exp(logalpha)


def compound_win(close: pd.Series, window: int) -> pd.Series:
    """近 window 日复合收益（窗口含 NaN → NaN），对齐 resonance/v3.compound_window。"""
    ok = close.notna().rolling(window + 1).sum() == window + 1
    return (close / close.shift(window) - 1.0).where(ok)


def path_dd_win(close: pd.Series, window: int) -> pd.Series:
    """近 window 日路径最大回撤幅度（不足窗按 0=无回撤，对齐 v3._dd_mag_series）。"""
    out = pd.Series(0.0, index=close.index)
    for i in range(window, len(close)):
        sub = close.iloc[i - window: i + 1]
        if len(sub) == window + 1 and not sub.isna().any():
            out.iloc[i] = -float((sub / sub.cummax() - 1.0).min())
    return out


def half_life_tier(dd_mag: float) -> int:
    """v3._tier：回撤 ≤2% → 5；≤4% → 3；>4% → 2（最高风险档）。"""
    if dd_mag <= DD_TIERS[0]:
        return HALF_LIVES[0]
    if dd_mag <= DD_TIERS[1]:
        return HALF_LIVES[1]
    return HALF_LIVES[2]


def dual_anchor_states(closes: pd.DataFrame) -> pd.DataFrame:
    """研究主基线双锚逐日状态：leader / 闸门 / 半衰期档（全部按 v3 口径复算）。

    gate=False 即判据集合 G（闸门拦截日，含无领先日）；hl=2 即集合 H（高风险定档日）。
    """
    comp10 = pd.DataFrame({c: compound_win(closes[c], LEADER_WINDOW) for c in closes.columns})
    comp3 = {c: compound_win(closes[c], POS_WINDOW) for c in closes.columns}
    dd10 = {c: path_dd_win(closes[c], DD_WINDOW) for c in closes.columns}
    has_leader = comp10.notna().any(axis=1)
    # idxmax 对整行 NaN 会抛异常（预热窗），只在有领先日的行上取
    leader = pd.Series(pd.NA, index=closes.index, dtype="object")
    if has_leader.any():
        leader.loc[has_leader] = comp10[has_leader].idxmax(axis=1)
    gate = pd.Series(False, index=closes.index)
    hl = pd.Series(np.nan, index=closes.index)
    for date, ld in leader.items():
        if not (isinstance(ld, str)) or pd.isna(ld):
            hl.loc[date] = half_life_tier(0.0)          # 无领先按最稳档（v3 口径）
            continue
        gate.loc[date] = float(comp3[ld].loc[date]) > 0
        hl.loc[date] = half_life_tier(float(dd10[ld].loc[date]))
    return pd.DataFrame({"leader": leader, "gate": gate, "hl": hl})


def event_date_on_calendar(dates: pd.DatetimeIndex, event: str) -> int:
    """事件参考日期映射到研究日历上不早于该日期的最近交易日（返回下标）。"""
    ts = pd.Timestamp(event)
    pos = dates.searchsorted(ts)
    if pos >= len(dates):
        pos = len(dates) - 1
    return int(pos)
