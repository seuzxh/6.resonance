# CLAUDE.md

本文件只补充 ZCode 的项目约束；项目全貌见
[README.md](README.md)，GPT 会话迁移上下文见 [docs/gpt-session-summary.md](docs/gpt-session-summary.md)。

## 项目定位

指数—概念共振分析与指数轮动回测。数据源为同花顺 iFinD REST；策略规则
（20日信号、5日调仓、单持仓、Top5缓冲、T+1、几何复利）已由用户在
GPT 会话中逐条确认，改动口径前必须先与用户确认。

## 硬约束

1. 只使用 conda 环境 `resonance`，禁止调用系统 Python。
2. 所有网络调用必须在测试中 mock，测试必须离线可运行。
3. 禁止未来数据（T 日信号最早 T+1 计收益）、硬编码凭证。
4. refresh_token 读全局源或环境变量 `IFIND_REFRESH_TOKEN`，绝不复制进仓库。
5. 策略结论必须标注：指数收益研究口径（无费率/滑点）、概念目录幸存者偏差。
6. 保留无关的未提交改动；清理运行资产前先取得用户确认。

## 常用验证

```bash
conda run -n resonance python -m pytest -q
conda run -n resonance python work/probe.py   # 网络冒烟，轻量（4 指数×10 日）
```

## 关键参数位置

`resonance/config.py`：指数池（BENCHMARK_INDEXES 四指数 / BROAD_INDEX_POOL 13 宽基）、
信号窗口与调仓参数、iFinD 端点与凭证路径。
