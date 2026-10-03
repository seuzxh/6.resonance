# CLAUDE.md

本文件只补充 ZCode 的项目约束；项目全貌见
[README.md](README.md)，现行方案见 [docs/spec/current-plan.md](docs/spec/current-plan.md)。

## 项目定位

指数—概念上涨共振策略研究与样本外验证。现行生产冻结配置为 **V4.4 开盘执行的三锚动选**
（深证成指/国证2000/科创50，执行规格见 docs/spec/v44-open-exec-plan.md）；OOS（样本外）四轨纸面验证
参数保持冻结，历史记录为每日同步暂停，本次未核验外部调度（docs/ops/oos-validation-design.md，评价期禁改参）。数据源同花顺 iFinD
REST + 本地 qlib 备用链路。领域词汇表见
[CONTEXT.md](CONTEXT.md)（锚/领先指数、降级/兜底、分钟重排/分钟执行层
等易混概念的唯一权威区分）。

## 硬约束

1. 只使用 conda 环境 `resonance`，禁止调用系统 Python。**例外（2026-09-29
   批准，随路线 A 选定生效）**：qlib 验证/扩展轨脚本（research/qlib_route_a/
   与 tests/qlib_route_a/）用 conda 环境 `qlib`（pyqlib 0.9.7；跨环境只经
   outputs/ 下 parquet 契约文件交换，依据 docs/spec/v44-open-exec-plan.md
   与 docs/research/qlib-validation-plan.md §九）。
2. 所有网络调用必须在测试中 mock，测试必须离线可运行。
3. 禁止未来数据（T 日信号最早 T+1 计收益）、硬编码凭证。
4. refresh_token 读全局源或环境变量 `IFIND_REFRESH_TOKEN`，绝不复制进仓库；
   access_token 缓存于 /home/zxh/qlib_data/.ifind_token（多项目共享）。
5. 策略结论必须标注：指数收益研究口径（统一成本压力假设）、概念目录
   幸存者偏差、样本内/上界措辞（experiment-playbook L3）。
6. 保留无关的未提交改动；清理运行资产前先取得用户确认。
7. 新实验开跑前必须过 docs/research/experiment-playbook.md §五检查清单。
8. **700050.TI（微盘股）/ 932000.CSI（中证2000）已退役**：HF 端点永久无
   5min 数据（09-19 首测、09-25 同请求对照复核 0 bar），2026-09-25 用户
   指令禁止用于任何新实验/池/锚候选，入口自检用 `config.assert_no_retired`。
   唯一例外是预注册冻结 OOS 轨（A9 九池含 700050、B13 十三池含两者，
   禁改参），评价期结束后随池清理移除。

## 版本生命周期（2026-09-27 版本整理定；目录：docs/ops/version-catalog.md）

1. 新版本 = 新参数组 + 新 spec 文档 + 新 runner，禁止为版本差异新建引擎文件。
2. 结构性演进前先 tag 旧版（`vX.Y.Z-名`，message = 版本结论一句话 + 文档位置）。
3. runner 完成使命、结论冻结进 docs 后即删，靠 tag 复现（git 历史是博物馆，
   工作区是车间）。

## 常用验证与入口

```bash
conda run -n resonance python -m pytest -q            # 离线测试（数量以运行结果为准）
conda run -n resonance python ops/probe.py           # iFinD 网络冒烟
conda run -n resonance python ops/signal_daily.py    # OOS 每日 runner（暂停中，用户通知后开启）
conda run -n resonance python ops/backtest_v41.py    # 生产栈回测复现
conda run -n qlib python -m pytest tests/qlib_route_a/test_qlib_provider.py tests/qlib_route_a/test_qlib_pipeline.py -v  # qlib 侧测试（qlib env）
conda run -n qlib python research/qlib_route_a/qlib_pipeline.py        # 5min 因子+训练+IC
conda run -n resonance python research/qlib_route_a/qlib_f8_compare.py # X3 五相位对照
conda run -n resonance python research/qlib_route_a/qlib_bridge_export.py    # 验证轨信号桥+G2 门
conda run -n qlib python research/qlib_route_a/qlib_equivalence.py           # 验证轨 E-Gate 逐笔门
```

## 关键参数位置

`resonance/config.py`：V43_ANCHOR_POOL（三锚）、V41_BROAD_POOL（A9 对照轨）、
历史池与采集参数。冻结参数与现行执行说明：docs/spec/current-plan.md。
数据资产明细与口径：docs/data/data-inventory.md（2026-09-25 清点）。
