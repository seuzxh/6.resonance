"""部署冒烟测试：token 链路 + 四指数行情拉取（对应 GPT 会话 work/probe.py）。

用法：
    conda run -n resonance python ops/probe.py

只做 1 次轻量请求（近 10 个交易日、4 个指数），验证凭证与网关可用。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from resonance import config  # noqa: E402
from resonance.ifind import fetch_history_data, get_access_token  # noqa: E402


def main() -> int:
    # 1) token 链路
    token = get_access_token()
    print(f"[OK] access_token 获取成功: {token[:12]}...signs_{token.split('signs_')[-1]}")

    # 2) 四指数近 10 个交易日行情
    end = pd.Timestamp.today().strftime("%Y-%m-%d")
    start = (pd.Timestamp.today() - pd.Timedelta(days=20)).strftime("%Y-%m-%d")
    frames = fetch_history_data(list(config.BENCHMARK_INDEXES), start, end, indicators=["close", "pct_chg"])
    df = pd.concat(frames, ignore_index=True)
    if df.empty:
        print("[FAIL] history_data 返回空")
        return 1
    for code, name in config.BENCHMARK_INDEXES.items():
        sub = df[df["symbol"] == code]
        if sub.empty:
            print(f"[FAIL] {name}({code}) 无行情")
            return 1
        last = sub.iloc[-1]
        print(f"[OK] {name}({code}): {len(sub)} 条, 最新 close={last.get('close')}")

    cache = config.CACHE_DIR / "probe_benchmark.csv"
    df.to_csv(cache, index=False)
    print(f"[OK] 缓存写入 {cache}")
    print("SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
