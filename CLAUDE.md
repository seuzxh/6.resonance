# CLAUDE.md

本文件只补充 ZCode 的项目约束；项目全貌见
[README.md](README.md)，现行方案见 [docs/v43-best-plan.md](docs/v43-best-plan.md)。

## 项目定位

指数—概念上涨共振策略研究与样本外验证。现行生产口径 **V4.3 三锚动选**
（深证成指/国证2000/科创50，参数冻结见 v43-best-plan）；OOS 四轨纸面验证
运行中（docs/oos-validation-design.md，评价期禁改参）。数据源同花顺 iFinD
REST + 本地 qlib 备用链路。

## 硬约束

1. 只使用 conda 环境 `resonance`，禁止调用系统 Python。
2. 所有网络调用必须在测试中 mock，测试必须离线可运行。
3. 禁止未来数据（T 日信号最早 T+1 计收益）、硬编码凭证。
4. refresh_token 读全局源或环境变量 `IFIND_REFRESH_TOKEN`，绝不复制进仓库；
   access_token 缓存于 /home/zxh/qlib_data/.ifind_token（多项目共享）。
5. 策略结论必须标注：指数收益研究口径（统一成本压力假设）、概念目录
   幸存者偏差、样本内/上界措辞（experiment-playbook L3）。
6. 保留无关的未提交改动；清理运行资产前先取得用户确认。
7. 新实验开跑前必须过 docs/experiment-playbook.md §五检查清单。

## 常用验证与入口

```bash
conda run -n resonance python -m pytest -q            # 离线测试（54 个）
conda run -n resonance python work/probe.py           # iFinD 网络冒烟
conda run -n resonance python work/signal_daily.py    # OOS 每日 runner（暂停中，用户通知后开启）
conda run -n resonance python work/backtest_v41.py    # 生产栈回测复现
```

## 关键参数位置

`resonance/config.py`：V43_ANCHOR_POOL（三锚）、V41_BROAD_POOL（A9 对照轨）、
历史池与采集参数。冻结参数清单：docs/v43-best-plan.md §三。
