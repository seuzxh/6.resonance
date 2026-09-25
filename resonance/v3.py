"""V3 指数—概念上涨共振策略引擎（用户最佳方案 V3，2026-09-18，docs/v3-best-plan.md）。

信号层（T 日收盘后计算，无未来数据）：
1. 领先指数 = 13 宽基近 leader_window(10) 日复合收益率最大者；其近
   pos_window(3) 日复合收益 ≤ 0 → 当日无候选（§4.1 闸门：不建新概念仓位；
   持仓检查日因无候选触发 §6.6 退出至现金——本实现的口径注，见下）。
2. 候选概念 = 近 3 日复合收益 > 0 且共振窗收益完整（§4.2）。
3. 上涨共振分（§4.3，共振窗 res_window(10) 日，EW 权重 w_t = 0.5^(age/h)）：
   同步率 = Σ w·I(领涨且概念涨) / Σ w·I(领涨)   （仅领先指数上涨日计入）
   捕获率 = Σ w·max(概念,0) / Σ w·max(领先,0)   （全窗正部）
   分数 = 同步率 × sqrt(clip(捕获率, 0, 2))
4. 动态半衰期（§5）：全A 近 dd_window(10) 日路径最大回撤 ≤2% → 5 日；
   ≤4% → 3 日；>4% → 2 日。

执行层（§6/§7，T+1 收盘成交、新持仓 T+2 起计收益、单边成本 cost_bp）：
- 空仓：闸门与候选满足 → 买入当日第 1 名；
- 持仓：至少持有 min_hold(3) 个交易日，此后每日检查；仍在 Top topk(3) →
  续持；跌出 → 换当日第 1 名；无候选 → 退至现金；
- 固定止损 stop_loss(5%)：相对入场收盘价、每日检查、不受最短持有期限制、
  T+1 收盘执行；止损后 cooldown(1) 个交易日内不产生入场信号。

口径注（V3 文档未细化的两处 ±1 日歧义，以本注为准；R1 轮做邻域验证）：
- min_hold 自执行日 E 起算：i−E ≥ min_hold 才可检查（首次检查信号在
  E+min_hold 收盘、E+min_hold+1 执行，最短 3 个完整收益日 E+1..E+3）。
- 冷静期：止损执行日 S 当天及其后 cooldown 个交易日（i ≤ S+cooldown）
  不产生入场信号；cooldown=0 时仅阻塞 S 当天。
- 闸门失败/无候选在检查日均触发"退出至现金"（§6.6 的自然推论）。

stop_mode='minute'（R2 剂量对照用）：止损改为逐 5min bar 检查，触发即按
触发 bar 收盘价卖出（当日离场，不再 T+1）；缺分钟数据当日退化为收盘口径
并计数（沿 minute-exec 协议）。minute_prices 为 exec_minute.MinutePrices。

指数收益研究口径：概念指数不可直接交易；成本为统一压力假设。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from . import config

RANK_COLS = ["concept", "sync", "capture", "score"]


@dataclass(frozen=True)
class V3Params:
    leader_window: int = 10
    pos_window: int = 3
    res_window: int = 10
    dd_window: int = 10
    dd_tiers: tuple[float, float] = (0.02, 0.04)
    half_lives: tuple[int, int, int] = (5, 3, 2)
    topk: int = 3
    min_hold: int = 3
    stop_loss: float = 0.05
    cooldown: int = 1
    cost_bp: float = 0.0
    stop_mode: str = "close"  # 'close' | 'minute'
    exec_lag: int = 1         # 1=信号 T 收盘 → T+1 收盘成交（V3 文档口径）；0=信号当日收盘成交
    # R9 防御层（docs/v3-optimization-design.md §三.R9）：全A 近 dd_window 日
    # 最大回撤幅度 > defense_dd 时禁开新仓；strong=True 时持仓检查日亦退出至
    # 现金（与闸门失败同构），False 仅禁止空仓入场。0 = 关闭。
    defense_dd: float = 0.0
    defense_strong: bool = True
    # V4.1 分钟重排层（docs/v4-best-plan.md §7）：daily_top > 0 时启用——日线
    # 上涨共振先选 Top(daily_top)，再按 5min K线纯分钟上涨共振重排
    # （0% 日线 + 100% 分钟）；topk 缓冲作用于最终排名。minute_bars 为窗内
    # 总 bar 数：24=当日最后 2 小时（V4.1 规格）；96=8 小时跨 2 日（用户
    # 2026-09-22 优化指令，MinuteBarProvider.window_span 跨日取末 N 根）。
    daily_top: int = 0
    minute_bars: int = 24
    # 动态半衰期的回撤基准（用户 2026-09-22 优化指令）：'allA'=同花顺全A
    # （V3/V4.1 规格）；'leader'=当日领先指数自身（各宽基预计算回撤序列，
    # 按日取领先指数的档位）。
    hl_source: str = "allA"

    def with_(self, **kw) -> "V3Params":
        return replace(self, **kw)


# ---------------------------------------------------------------- 信号层 --

def compound_window(
    close: pd.DataFrame, cols: list[str], window: int, pos: int
) -> pd.Series:
    """cols 各自近 window 日复合收益率（iloc pos 收盘 vs pos-window 收盘）。

    窗口内含 NaN 的代码返回 NaN（激活晚/数据缺的概念自动出局）。
    """
    sub = close.iloc[pos - window : pos + 1][cols]
    ok = sub.notna().all(axis=0)
    return (sub.iloc[-1] / sub.iloc[0] - 1.0).where(ok)


def dynamic_half_life(
    close_allA: pd.Series, pos: int, window: int,
    dd_tiers: tuple[float, float], half_lives: tuple[int, int, int],
) -> int:
    """全A 近 window 日路径最大回撤 → 半衰期档位（样本不足按最稳档）。"""
    sub = close_allA.iloc[pos - window : pos + 1]
    if len(sub) < window + 1 or sub.isna().any():
        return half_lives[0]
    m = -float((sub / sub.cummax() - 1.0).min())
    if m <= dd_tiers[0]:
        return half_lives[0]
    if m <= dd_tiers[1]:
        return half_lives[1]
    return half_lives[2]


def up_resonance_scores(
    leader_rets: pd.Series, concept_rets: pd.DataFrame, half_life: float
) -> pd.DataFrame:
    """上涨共振评分（输入为已切片的 res_window 日收益率，§4.3 公式）。

    返回 DataFrame[concept, sync, capture, score] 按 score 降序（并列按代码
    升序，保证确定性）。领先指数窗口内无上涨日 → 空表（闸门下不会发生）。
    """
    empty = pd.DataFrame(columns=RANK_COLS)
    n = len(leader_rets)
    if n == 0 or concept_rets.shape[1] == 0:
        return empty
    age = np.arange(n - 1, -1, -1, dtype=float)
    w = 0.5 ** (age / float(half_life))
    y = leader_rets.to_numpy(dtype=float)
    X = concept_rets.to_numpy(dtype=float)
    if not np.isfinite(y).all() or not np.isfinite(X).all():
        return empty
    up = y > 0
    if not up.any():
        return empty
    sync = (w[up, None] * (X[up] > 0)).sum(axis=0) / w[up].sum()
    cap_den = float((w * np.clip(y, 0.0, None)).sum())
    if cap_den <= 0:
        return empty
    capture = (w[:, None] * np.clip(X, 0.0, None)).sum(axis=0) / cap_den
    score = sync * np.sqrt(np.clip(capture, 0.0, 2.0))
    out = pd.DataFrame(
        {"concept": list(concept_rets.columns), "sync": sync,
         "capture": capture, "score": score}
    )
    return out.sort_values(["score", "concept"], ascending=[False, True]).reset_index(drop=True)


def v3_ranking(
    rets: pd.DataFrame,
    close: pd.DataFrame,
    concepts: list[str],
    broad_codes: list[str],
    allA_code: str,
    pos: int,
    p: V3Params,
    info: dict | None = None,
) -> pd.DataFrame:
    """信号日 pos 的合格候选上涨共振榜（闸门失败/无候选 → 空表）。

    info 可选 dict：回填 leader / half_life / n_candidates 供诊断。
    """
    empty = pd.DataFrame(columns=RANK_COLS)
    need = max(p.leader_window, p.res_window, p.dd_window, p.pos_window) + 1
    if pos + 1 < need or pos - p.res_window + 1 < 0:
        return empty
    lcomp = compound_window(close, broad_codes, p.leader_window, pos).dropna()
    if lcomp.empty:
        if info is not None:
            info.update(leader=None, half_life=None, n_candidates=0, gate=False)
        return empty
    leader = str(lcomp.idxmax())
    gate = float(compound_window(close, [leader], p.pos_window, pos).iloc[0])
    h = dynamic_half_life(close[allA_code], pos, p.dd_window, p.dd_tiers, p.half_lives)
    if not (gate > 0):
        if info is not None:
            info.update(leader=leader, half_life=h, n_candidates=0, gate=False)
        return empty
    rw = rets.iloc[pos - p.res_window + 1 : pos + 1]
    y = rw[leader]
    cols = [c for c in concepts if c in rw.columns]
    if y.isna().any() or not cols:
        if info is not None:
            info.update(leader=leader, half_life=h, n_candidates=0, gate=True)
        return empty
    complete = rw[cols].notna().all(axis=0)
    c3 = compound_window(close, cols, p.pos_window, pos)
    cand = [c for c in cols if complete[c] and c3[c] > 0]
    if info is not None:
        info.update(leader=leader, half_life=h,
                    n_candidates=len(cand), gate=True)
    if not cand:
        return empty
    return up_resonance_scores(y, rw[cand], h)


class V3Signals:
    """信号预计算缓存：与 v3_ranking 逐日等价（等价性由 work/ 脚本核验）。

    预计算：宽基动量榜（leader）、领先指数 3 日闸门、候选资格
    （共振窗收益完整 ∧ 3 日复合>0）、全A 动态半衰期。评分的 EW 卷积
    仍逐日进行（半衰期随日变化）。
    """

    def __init__(self, close_all: pd.DataFrame, concepts: list[str],
                 broad_codes: list[str], allA_code: str, p: V3Params):
        self.p = p
        self.concepts = [c for c in concepts if c in close_all.columns]
        self.broad = [c for c in broad_codes if c in close_all.columns]
        rets = close_all.pct_change()
        self.rets_np = rets.to_numpy(dtype=float)
        col_idx = {c: j for j, c in enumerate(rets.columns)}
        self._concept_j = np.array([col_idx[c] for c in self.concepts], dtype=int)
        self._broad_j = np.array([col_idx[c] for c in self.broad], dtype=int)

        n = len(close_all)
        # leader：宽基近 leader_window 日复合（窗口收益完整者才参选；等价于
        # compound_window 的整窗非 NaN 要求）
        mom = close_all[self.broad].pct_change(p.leader_window).to_numpy(dtype=float)
        elig_b = (rets[self.broad].rolling(p.leader_window).count()
                  == p.leader_window).to_numpy()
        mom_m = np.where(elig_b, mom, -np.inf)
        self.leader_idx = np.argmax(mom_m, axis=1)
        self.has_leader = np.isfinite(mom_m).any(axis=1) & (mom_m.max(axis=1) > -np.inf)
        # 闸门：leader 近 pos_window 日复合 > 0
        c3_b = close_all[self.broad].pct_change(p.pos_window).to_numpy(dtype=float)
        rows = np.arange(n)
        li_safe = np.where(self.has_leader, self.leader_idx, 0)
        self.gate = self.has_leader & (c3_b[rows, li_safe] > 0)
        # 候选资格（共振窗收益完整 ∧ 3 日复合>0；等价于逐日 compound_window）
        ok_w = (rets[self.concepts].rolling(p.res_window).count()
                == p.res_window).to_numpy()
        pos3 = (close_all[self.concepts].pct_change(p.pos_window) > 0).to_numpy()
        self.elig = ok_w & pos3
        # 回撤幅度序列工具（hl 与 R9 防御层共用；样本不足按 0=无回撤）
        def _dd_mag_series(s: pd.Series, window: int) -> np.ndarray:
            out = np.zeros(len(s))
            for i in range(window, len(s)):
                sub = s.iloc[i - window : i + 1]
                if len(sub) == window + 1 and not sub.isna().any():
                    out[i] = -float((sub / sub.cummax() - 1.0).min())
            return out

        def _tier(m: float) -> float:
            if m <= p.dd_tiers[0]:
                return float(p.half_lives[0])
            if m <= p.dd_tiers[1]:
                return float(p.half_lives[1])
            return float(p.half_lives[2])

        # 全A 动态半衰期（逐日；与 dynamic_half_life 等价：不足窗=0 回撤→最稳档）
        s_allA = close_all[allA_code]
        self.dd_mag = _dd_mag_series(s_allA, p.dd_window)
        if p.hl_source == "leader":
            # 用户 2026-09-22 指令：按当日领先指数自身的近 dd_window 日回撤定档
            dd_by_code = {c: _dd_mag_series(close_all[c], p.dd_window)
                          for c in self.broad}
            hl = np.empty(n)
            for i in range(n):
                if self.has_leader[i]:
                    hl[i] = _tier(dd_by_code[self.broad[self.leader_idx[i]]][i])
                else:
                    hl[i] = _tier(0.0)
            self.hl = hl
        else:
            self.hl = np.array([_tier(m) for m in self.dd_mag], dtype=float)

    def ranking(self, pos: int) -> pd.DataFrame:
        p = self.p
        empty = pd.DataFrame(columns=RANK_COLS)
        if pos - p.res_window + 1 < 0 or not self.has_leader[pos] or not self.gate[pos]:
            return empty
        w = self.rets_np[pos - p.res_window + 1 : pos + 1]
        y = w[:, self._broad_j[self.leader_idx[pos]]]
        if not np.isfinite(y).all():
            return empty
        elig = self.elig[pos]
        if not elig.any():
            return empty
        X = w[:, self._concept_j[elig]]
        out = up_resonance_scores_np(y, X, self.hl[pos])
        if out.empty:
            return empty
        out.insert(0, "concept", [self.concepts[k] for k in np.where(elig)[0]])
        return out.sort_values(["score", "concept"], ascending=[False, True]).reset_index(drop=True)


def up_resonance_scores_np(y: np.ndarray, X: np.ndarray, half_life: float) -> pd.DataFrame:
    """up_resonance_scores 的 numpy 内核（列为候选顺序，不含代码名）。"""
    n = len(y)
    age = np.arange(n - 1, -1, -1, dtype=float)
    w = 0.5 ** (age / float(half_life))
    up = y > 0
    sync = (w[up, None] * (X[up] > 0)).sum(axis=0) / w[up].sum()
    cap_den = float((w * np.clip(y, 0.0, None)).sum())
    if cap_den <= 0:
        return pd.DataFrame(columns=["sync", "capture", "score"])
    capture = (w[:, None] * np.clip(X, 0.0, None)).sum(axis=0) / cap_den
    score = sync * np.sqrt(np.clip(capture, 0.0, 2.0))
    return pd.DataFrame({"sync": sync, "capture": capture, "score": score})


# ---------------------------------------------------------------- 引擎 --

class MinuteBarProvider:
    """5min bar 收盘供给（V4.1 分钟重排层）。

    minute_wide：宽表（index=完整 datetime，48 bar/日，bar 结束时刻）。
    window(code, day, n) → 当日最后 n 根（V4.1 24bar 规格）；
    window_span(code, end_day, n) → 截至 end_day 收盘的最近 n 根（跨日，
    用户 8 小时=96bar 指令；不足 n 返回空 Series，调用方按降级策略处理）。
    日切片惰性索引。
    """

    def __init__(self, minute_wide: pd.DataFrame):
        self.wide = minute_wide
        self._slices: dict[str, pd.DataFrame] | None = None
        self._day_keys: list[str] | None = None

    def _ensure_slices(self) -> None:
        if self._slices is None:
            idx = self.wide.index
            self._slices = {str(k.date()): g for k, g in self.wide.groupby(idx.normalize())}
            self._day_keys = sorted(self._slices)

    def _day(self, day) -> pd.DataFrame | None:
        self._ensure_slices()
        return self._slices.get(str(pd.Timestamp(day).date()))

    def window(self, code: str, day, n: int) -> pd.Series:
        sub = self._day(day)
        if sub is None or code not in sub.columns:
            return pd.Series(dtype=float)
        col = sub[code].dropna()
        return col.iloc[-n:] if len(col) >= n else pd.Series(dtype=float)

    def window_span(self, code: str, end_day, n: int) -> pd.Series:
        """截至 end_day 的最近 n 根 bar（跨日拼接，时间正序返回）。"""
        import bisect

        self._ensure_slices()
        end_k = str(pd.Timestamp(end_day).date())
        j = bisect.bisect_right(self._day_keys, end_k)
        parts: list[pd.Series] = []
        have = 0
        for k in reversed(self._day_keys[:j]):
            sub = self._slices[k]
            if code not in sub.columns:
                continue
            col = sub[code].dropna()
            if len(col):
                parts.append(col)
                have += len(col)
                if have >= n:
                    break
        if not parts:
            return pd.Series(dtype=float)
        s = pd.concat(parts[::-1]) if len(parts) > 1 else parts[0]
        return s.iloc[-n:] if len(s) >= n else pd.Series(dtype=float)


def minute_up_resonance(leader_bars: pd.Series, concept_bars: pd.DataFrame) -> pd.DataFrame:
    """V4.1 §7.2 分钟上涨共振（无 EW 权重）：

    同涨比例 = 共同上涨 bar 数 / 领先上涨 bar 数（仅领先指数上涨的 bar 计入）
    捕获率   = Σmax(概念bar收益,0) / Σmax(领先bar收益,0)
    分数     = 同涨比例 × sqrt(clip(捕获率, 0, 2))

    输入为同长度 bar 收盘宽表（列=概念，index=bar 时刻），bar 收益为窗内
    bar-to-bar 收益（24 bar → 23 收益）。返回 [concept, sync, capture, score]
    按分数降序（并列按代码升序）；领先无上涨 bar 或捕获率分母为 0 → 空表。
    """
    empty = pd.DataFrame(columns=RANK_COLS)
    rets = pd.concat([leader_bars.rename("__leader__"), concept_bars], axis=1).pct_change().dropna()
    if len(rets) < 1:
        return empty
    y = rets["__leader__"].to_numpy(dtype=float)
    X = rets.drop(columns=["__leader__"]).to_numpy(dtype=float)
    if not np.isfinite(y).all() or not np.isfinite(X).all():
        return empty
    up = y > 0
    if not up.any():
        return empty
    sync = (X[up] > 0).sum(axis=0) / up.sum()
    cap_den = float(np.clip(y, 0.0, None).sum())
    if cap_den <= 0:
        return empty
    capture = np.clip(X, 0.0, None).sum(axis=0) / cap_den
    score = sync * np.sqrt(np.clip(capture, 0.0, 2.0))
    out = pd.DataFrame({"concept": list(concept_bars.columns), "sync": sync,
                        "capture": capture, "score": score})
    return out.sort_values(["score", "concept"], ascending=[False, True]).reset_index(drop=True)


class V3Backtester:
    """V3 事件循环回测器（语义见模块 docstring；引擎自含全部规则）。

    exec_lag=1（默认，V3 文档口径）：信号 T 收盘 → T+1 收盘成交、T+2 起计收益。
    exec_lag=0（口径对照用）：信号当日收盘成交、次日起计收益。
    """

    def __init__(
        self,
        close_all: pd.DataFrame,
        concepts: list[str],
        broad_codes: list[str] | None = None,
        allA_code: str = "883957.TI",
        params: V3Params | None = None,
        minute_prices=None,
        minute_bars_provider=None,
        entry_gate: pd.Series | None = None,
        exit_grid: pd.DataFrame | None = None,
        post_rank=None,
    ):
        self.close = close_all
        self.rets = close_all.pct_change()
        self.broad = [c for c in (broad_codes or list(config.BROAD_INDEX_POOL))
                      if c in close_all.columns]
        missing = set(broad_codes or config.BROAD_INDEX_POOL) - set(self.broad)
        if missing:
            import warnings
            warnings.warn(
                f"指数池 {sorted(missing)} 无日线数据，按可用子集 {len(self.broad)} 池运行"
                f"（883417.TI 待配额恢复补采；其余缺失为异常，请检查数据）",
                stacklevel=2,
            )
        self.allA = allA_code
        self.p = params or V3Params()
        self.mp = minute_prices
        if self.p.stop_mode == "minute":
            assert self.mp is not None, "stop_mode='minute' 需要 minute_prices"
        self.mbp = minute_bars_provider
        if self.p.daily_top > 0:
            assert self.mbp is not None, "daily_top>0（V4.1 分钟重排）需要 minute_bars_provider"
        # 研究钩子（moneyflow-gate，docs/moneyflow-gate-plan.md）：entry_gate 为
        # 日期→bool 序列；False 的空仓日不产生入场信号（soft gate：不影响持仓
        # 检查/换仓/止损）。日期不在索引 → 直通并计 gate_missing_days。
        # exit_grid 为 日期×概念 bool 宽表（R2 hard gate）：持仓检查日 True →
        # 退至现金（reason=flow_exit）；NaN/缺失 → 不触发。
        # post_rank(final_ranking, date) → ranking（R3 选择面钩子）：对最终榜
        # 做稳定重排/过滤，topk 缓冲与入场决策作用于其输出；None=不改。
        self.exit_grid = exit_grid
        self.post_rank = post_rank
        self._gate_idx: set | None = None
        self._gate_blocked: set | None = None
        if entry_gate is not None:
            idx = pd.DatetimeIndex(entry_gate.index).normalize()
            self._gate_idx = set(idx)
            self._gate_blocked = {d for d, v in zip(idx, entry_gate.to_numpy())
                                  if not bool(v)}
        self.sig = V3Signals(close_all, concepts, self.broad, allA_code, self.p)

    def _final_ranking(self, i: int, date, st: dict) -> pd.DataFrame:
        """日线榜 → （V4.1 启用时）分钟重排，含预注册降级策略。

        降级策略（docs/v4/report.md §数据残差 预注册）：
        - 领先指数截至当日的 bar 窗（minute_bars 根，可跨日）不完整/缺失 →
          回退日线 Top(daily_top) 原序；
        - 个别概念 bar 窗不完整 → 该概念退出当日最终排名；
        - 可评分概念 <2 → 回退日线原序。三类事件计数上报。
        """
        p = self.p
        daily = self.sig.ranking(i)
        if daily.empty or p.daily_top <= 0:
            return daily
        st["minute_layer_days"] = st.get("minute_layer_days", 0) + 1
        top = daily.head(p.daily_top)
        leader = self.broad[self.sig.leader_idx[i]]
        lb = self.mbp.window_span(leader, date, p.minute_bars)
        if lb.empty:
            st["minute_fallback_leader"] = st.get("minute_fallback_leader", 0) + 1
            return top
        cols = [c for c in top["concept"]
                if not self.mbp.window_span(c, date, p.minute_bars).empty]
        st["minute_excluded"] = st.get("minute_excluded", 0) + (len(top) - len(cols))
        if len(cols) < 2:
            st["minute_fallback_sparse"] = st.get("minute_fallback_sparse", 0) + 1
            return top
        cbars = pd.DataFrame({c: self.mbp.window_span(c, date, p.minute_bars)
                              for c in cols})
        out = minute_up_resonance(lb, cbars)
        if out.empty or len(out) < 1:
            st["minute_fallback_sparse"] = st.get("minute_fallback_sparse", 0) + 1
            return top
        return out

    def run(self, start, end=None) -> dict:
        p = self.p
        cal = self.close.index
        s = cal.searchsorted(pd.Timestamp(start))
        e = len(cal) if end is None else cal.searchsorted(pd.Timestamp(end), side="right")
        if s <= 0 or s >= len(cal):
            raise ValueError(f"非法窗口起点：{start}")
        cost = p.cost_bp / 1e4

        nav = 1.0
        holding: str | None = None
        entry_px: float | None = None
        exec_i: int | None = None
        pending: dict | None = None
        entry_block_until = -10  # 入场信号阻塞（含）下标

        out: dict[pd.Timestamp, float] = {}
        hold_rows: list[dict] = []
        trades: list[dict] = []
        stops: list[dict] = []
        st = {
            "entries": 0, "switches": 0, "exits": 0, "stop_count": 0,
            "check_days": 0, "gate_fail_days": 0, "no_leader_days": 0,
            "flat_days": 0, "held_days": 0, "signal_days": 0,
            "degraded_days": 0, "intraday_exits": 0,
            "entry_blocked_days": 0, "gate_missing_days": 0,
            "leaders": {}, "half_lives": {},
        }
        close_cols = {c: j for j, c in enumerate(self.close.columns)}
        close_np = self.close.to_numpy(dtype=float)

        def close_at(i: int, code: str) -> float:
            v = close_np[i, close_cols[code]]
            return float(v) if pd.notna(v) else float("nan")

        def exec_pending(i, date) -> None:
            """在 i 日收盘执行 pending（含双边成本与状态迁移）。"""
            nonlocal nav, holding, entry_px, exec_i, pending, entry_block_until
            act = pending
            pending = None
            if act["type"] == "buy":
                px = close_at(i, act["code"])
                if math.isfinite(px):
                    nav *= 1.0 - cost
                    trades.append({"date": date, "type": "entry",
                                   "from": None, "to": act["code"],
                                   "price": px, "nav": nav})
                    st["entries"] += 1
                    holding, entry_px, exec_i = act["code"], px, i
            elif act["type"] == "switch":
                px_new = close_at(i, act["to"])
                if math.isfinite(px_new):
                    nav *= (1.0 - cost) ** 2
                    trades.append({"date": date, "type": "switch",
                                   "from": act["from"], "to": act["to"],
                                   "price": px_new, "nav": nav})
                    st["switches"] += 1
                    holding, entry_px, exec_i = act["to"], px_new, i
            elif act["type"] == "sell":
                px = close_at(i, act["from"])
                nav *= 1.0 - cost
                if act["reason"] == "stop":
                    st["stop_count"] += 1
                    stops.append({"signal_date": act["signal_date"],
                                  "exec_date": date, "code": act["from"],
                                  "price": px, "day_ret": float("nan")})
                    entry_block_until = i + p.cooldown
                trades.append({"date": date,
                               "type": "stop" if act["reason"] == "stop" else "exit",
                               "from": act["from"], "to": None,
                               "reason": act.get("reason"),
                               "price": px, "nav": nav})
                st["exits"] += 1
                holding, entry_px, exec_i = None, None, None

        def record_info(i, date) -> None:
            sig = self.sig
            if sig.has_leader[i]:
                st["leaders"][str(pd.Timestamp(date).date())] = self.broad[sig.leader_idx[i]]
                st["half_lives"][str(pd.Timestamp(date).date())] = float(sig.hl[i])
                if not sig.gate[i]:
                    st["gate_fail_days"] += 1
            else:
                st["no_leader_days"] += 1

        for i in range(s, e):
            date = cal[i]
            # --- 1) 当日计收益（T+2 起算；minute 模式含盘中止损路径）---
            stopped_today = False
            if holding is not None and exec_i is not None and i > exec_i:
                c_prev = close_at(i - 1, holding)
                if p.stop_mode == "minute":
                    marks = self.mp.day_marks(holding, date) if self.mp else []
                    if not marks:
                        st["degraded_days"] += 1
                    for ts, mk in marks:
                        if math.isfinite(mk) and entry_px is not None \
                                and mk <= entry_px * (1.0 - p.stop_loss):
                            factor = (mk / c_prev
                                      if math.isfinite(c_prev) and c_prev > 0
                                      else float("nan"))
                            if math.isfinite(factor):
                                nav *= factor * (1.0 - cost)
                            stops.append({"signal_date": date, "exec_date": date,
                                          "code": holding, "price": mk,
                                          "day_ret": factor - 1.0})
                            trades.append({"date": date, "type": "stop",
                                           "from": holding, "to": None,
                                           "price": mk, "nav": nav})
                            st["stop_count"] += 1
                            st["intraday_exits"] += 1
                            holding, entry_px, exec_i = None, None, None
                            entry_block_until = i + p.cooldown
                            stopped_today = True
                            break
                if not stopped_today:
                    c_i = close_at(i, holding)
                    if math.isfinite(c_i) and math.isfinite(c_prev) and c_prev > 0:
                        nav *= c_i / c_prev
                        st["held_days"] += 1

            # --- 2) 执行滞后信号（exec_lag=1：T+1 收盘成交）---
            if p.exec_lag == 1 and pending is not None and not stopped_today:
                exec_pending(i, date)

            # --- 3) 今日收盘信号 ---
            if not stopped_today:
                defense_on = (p.defense_dd > 0
                              and self.sig.dd_mag[i] > p.defense_dd)
                if holding is not None:
                    c_i = close_at(i, holding)
                    do_rank = exec_i is not None and i - exec_i >= p.min_hold
                    if p.stop_mode == "close" and entry_px is not None \
                            and math.isfinite(c_i) \
                            and c_i <= entry_px * (1.0 - p.stop_loss):
                        pending = {"type": "sell", "from": holding,
                                   "reason": "stop", "signal_date": date}
                    elif do_rank and defense_on and p.defense_strong:
                        st["check_days"] += 1
                        record_info(i, date)
                        pending = {"type": "sell", "from": holding,
                                   "reason": "defense", "signal_date": date}
                    elif do_rank:
                        st["check_days"] += 1
                        record_info(i, date)
                        flow_exit = False
                        if self.exit_grid is not None:
                            d = pd.Timestamp(date).normalize()
                            if d in self.exit_grid.index and holding in self.exit_grid.columns:
                                v = self.exit_grid.at[d, holding]
                                flow_exit = bool(v) if pd.notna(v) else False
                        if flow_exit:
                            st["flow_exits"] = st.get("flow_exits", 0) + 1
                            pending = {"type": "sell", "from": holding,
                                       "reason": "flow_exit", "signal_date": date}
                        else:
                            st["signal_days"] += 1
                            ranking = self._final_ranking(i, date, st)
                            if self.post_rank is not None and not ranking.empty:
                                ranking = self.post_rank(ranking, date)
                            if ranking.empty:
                                pending = {"type": "sell", "from": holding,
                                           "reason": "exit", "signal_date": date}
                            else:
                                top = list(ranking["concept"].head(p.topk))
                                if holding not in top:
                                    pending = {"type": "switch", "from": holding,
                                               "to": top[0], "signal_date": date}
                elif i > entry_block_until and not defense_on:
                    record_info(i, date)
                    gate_ok = True
                    if self._gate_blocked is not None:
                        d = pd.Timestamp(date).normalize()
                        if d in self._gate_idx:
                            if d in self._gate_blocked:
                                gate_ok = False
                        else:
                            st["gate_missing_days"] += 1
                    if not gate_ok:
                        st["entry_blocked_days"] += 1
                    else:
                        st["signal_days"] += 1
                        ranking = self._final_ranking(i, date, st)
                        if self.post_rank is not None and not ranking.empty:
                            ranking = self.post_rank(ranking, date)
                        if not ranking.empty:
                            pending = {"type": "buy", "code": ranking["concept"].iloc[0]}
                if p.exec_lag == 0 and pending is not None:
                    exec_pending(i, date)

            out[date] = nav
            hold_rows.append({"date": date, "holding": holding, "nav": nav})
            if holding is None:
                st["flat_days"] += 1

        st["position_changes"] = st["entries"] + st["switches"]
        curve = pd.Series(out, name="nav")
        curve.index.name = "date"
        return {
            "nav_curve": curve,
            "holdings": pd.DataFrame(hold_rows).set_index("date"),
            "trades": pd.DataFrame(trades),
            "stops": pd.DataFrame(stops),
            "stats": st,
            "params": p,
        }


# ---------------------------------------------------------------- 统计 --

def yearly_returns(nav: pd.Series) -> dict[str, float]:
    """逐年收益（首年自窗口起点起算，与 V3 文档 §9 口径一致）。"""
    out: dict[str, float] = {}
    for y in sorted(set(nav.index.year)):
        sub = nav[nav.index.year == y]
        prev = nav[nav.index < sub.index[0]]
        base = float(prev.iloc[-1]) if len(prev) else float(sub.iloc[0])
        out[str(y)] = float(sub.iloc[-1] / base - 1.0)
    return out
