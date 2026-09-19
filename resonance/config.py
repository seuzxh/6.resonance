"""共振项目配置：端点、指数池、路径、窗口参数。

迁移自 ChatGPT Codex 会话「验证 iFinD 方案」（2026-09-17/18，Windows 环境），
本地化改造：凭证沿用 /home/zxh/qlib_data 全局源（见 resonance/ifind.py）。
"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

# --- iFinD 网关（与 2.qlib_ifind_hot_concept / 3.qlib_ifind_beta 一致） ---
IFIND_TOKEN_URL = "https://quantapi.51ifind.com/api/v1/get_access_token"
IFIND_HISTORY_URL = "https://quantapi.51ifind.com/api/v1/history_data"
IFIND_DATAPOOL_URL = "https://quantapi.51ifind.com/api/v1/data_pool"
IFIND_BASIC_URL = "https://quantapi.51ifind.com/api/v1/basic_data_service"
IFIND_HF_URL = "https://ft.10jqka.com.cn/api/v1/high_frequency"

# access_token 扁平缓存（与项目3共用，避免重复刷新）
IFIND_TOKEN_FILE = Path("/home/zxh/qlib_data/.ifind_token")

# refresh_token 全局源（按顺序探测；凭证不复制进本仓库）
REFRESH_TOKEN_PATHS = (
    Path("/home/zxh/qlib_data/scripts/verify_data.py"),
    Path("/home/zxh/qlib_data/scripts/qlib_dumper/instrument_source.py"),
    Path("/home/zxh/qlib_data/scripts/daily_update.py"),
)

# --- 指数池 ---
# 原始四指数（GPT 会话第一版共振口径）
BENCHMARK_INDEXES = {
    "883957.TI": "同花顺全A",
    "000680.SH": "科创综指",
    "399006.SZ": "创业板指",
    "700050.TI": "微盘股",
}

# 动态宽基轮动的 13 指数池（GPT 会话 2026-09-18 扩展版；
# 部分代码以会话记录为准，首次采集时用 probe 核对）
BROAD_INDEX_POOL = {
    "883957.TI": "同花顺全A",
    "700050.TI": "微盘股",
    "000680.SH": "科创综指",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
    "000016.SH": "上证50",
    "899050.BJ": "北证50",
    "932000.CSI": "中证2000",
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
    "399303.SZ": "国证2000",
    "000015.SH": "红利指数",
}

# 概念指数：同花顺概念板块，代码段 885xxx.TI / 886xxx.TI（目录快照 390/394 个）。
# 枚举候选范围（实测 885 段接近满段、886 段稀疏、887 段不存在；无效代码在
# basic_data_service 响应中被直接省略，见 ifind.fetch_index_names）
CONCEPT_CODE_RANGE = tuple(range(885001, 887000))

# 数据采集起始（回测 2025-01-01 前留 ~3 个月 lookback，覆盖 20/60 日信号窗口预热）
COLLECT_START = "2024-10-01"

# --- 共振与回测参数（GPT 会话已确认的口径） ---
SIGNAL_WINDOW = 20          # 信号观察窗口：20 日相关度
REBALANCE_DAYS = 5          # 调仓周期：每 5 个交易日检查（对照版 3 日）
HOLDING_COUNT = 1           # 单持仓
TOPK_BUFFER = 5             # Top5 缓冲：持仓跌出 Top5 才更换
SHORT_WINDOW = 5            # 短窗口相关（仅对照用，不作为主榜口径）
LONG_WINDOW = 60            # 长窗口相关（稳健性复核）

# 回测区间（GPT 会话最终版：2025-01-01 起，全A对照）
BACKTEST_START = "2025-01-01"
# 数据截止由采集决定；会话最后截止 2026-09-18

# history_data 日线字段（CPS=0 不复权，指数无需复权）
HD_FIELD_MAP = {
    "pre_close": "pre_close",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "pct_chg": "pct_chg",
    "volume": "volume",
    "amt": "amount",
    "turn": "turnover_ratio",
}
