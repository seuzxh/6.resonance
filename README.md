# resonance — 指数—概念共振与指数轮动回测

概念板块指数与宽基指数的"共振"（相关性）分析，及其驱动的指数轮动回测。
迁移自 ChatGPT Codex 会话「验证 iFinD 方案」（2026-09-17/18，Windows），
完整上下文见 [docs/gpt-session-summary.md](docs/gpt-session-summary.md)。

## 项目定位

- **数据层**：同花顺 iFinD REST（quantapi 网关），指数/概念日线 + 概念目录。
- **指标层**：20 日 Pearson 相关（主口径）、控制全A偏相关（信息量口径）、5/60 日对照。
- **回测层**：指数轮动收益回测器（Top5 缓冲、T+1、几何复利）；VectorBT 适配。
- **研究性质**：指数收益累加研究，费率/滑点为 0，不代表可实盘成交。

## 当前状态

- [x] 2026-09-19 建仓迁移：iFinD 客户端、共振指标、回测引擎核心、离线测试就绪。
- [x] 2026-09-19 数据采集（全量）：概念目录快照 529 个；13 宽基 + 529 概念日线落盘
      （478 交易日；首轮配额中断，换备用账号 token 后断点续传补全）。
- [x] 2026-09-19 基线复现（核心口径对齐）：dyn5 总收益 +152.36% vs 锚点 +148.59%，
      超额 +79.60% vs +80.13%，夏普 1.840 vs 1.792，回撤 −35.37% 精确一致。
      判定会话口径：exec_lag=1（信号次日收盘入场）、全A 基准日 = 2024-12-31 收盘。
      **⚠️ 新发现：调仓网格相位敏感性极高（相邻相位总收益 +29%~+152%），锚点恰为
      最优相位**——总收益数字不可作稳健预期。见
      [outputs/index_backtest_framework/reproduction_report.md](outputs/index_backtest_framework/reproduction_report.md)。
- [x] 2026-09-19 分钟共振探索 v1+v2（预注册验证，结论 **均不采纳**）：Top10 日线池内
      分钟共振再排序——60min 版 NEUTRAL（中位差 +2.3pp 方向不一致）、**5min 版
      HARMFUL（中位差 −9.6pp）**，剂量效应明确：粒度越细越差；IC 诊断表明池内
      相关度排序（任意频率）对未来 5 日收益无预测力。5min 数据经 (code,day) 需求
      矩阵裁剪采集（611k dataVol，15:00 bar 与日线 0bps 一致）。见
      [outputs/archive/minute_resonance/report.md（master 快照）](outputs/archive/minute_resonance/report.md（master 快照）)。
- [x] 2026-09-19 分钟级执行层优化（信号层不变，**B 项采纳**）：A 盘中交易时点
      NEUTRAL 不采纳（两腿对消：早卖旧 −0.25% vs 早买新 +0.29%，净≈0）；
      **B 5min 盘中追踪止损 SUCCESS——推荐 x=4%**：5 相位中位总收益 +28.93%
      vs 基线 +13.02%，最大回撤 −17.30% vs −36.70%，夏普 0.55→1.33，
      收益/回撤/夏普 5/5 相位全部改善；分钟 vs 收盘粒度增量 = 收益 +13.4pp +
      回撤 +5.7pp（64% 止损在上午 9–10 点触发=隔夜跳空早离场）。10/10 网格点
      过预注册判据。见 [outputs/archive/minute_exec/report.md](outputs/archive/minute_exec/report.md)
      与 [docs/archive/minute-exec-design.md](docs/archive/minute-exec-design.md)。
- [x] 2026-09-19 信号窗口变更验证（用户指令 w∈{3,5}）：**缩短信号窗损大于益**——
      w=3 证伪（验证窗中位 −12.6%，全窗超额 −20pp 跑输全A）；w=5 收益让渡近半
      （全窗 +53% vs +152%）换更浅回撤；盘中止损增量与信号窗强相关（网格通过率
      w=20 10/10 > w=5 2/10 > w=3 1/10）。**最优组合仍为 w=20 + B4% 盘中止损**
      （帕累托支配所有短窗配置）；次优 w=5+B4%（+20.7%/−19.5%）。短窗 B 结果
      需 1,209 对补采去覆盖混杂后才是最终口径。见
      [outputs/archive/minute_exec/report_w35.md](outputs/archive/minute_exec/report_w35.md)。
- [x] 2026-09-20 上涨共振验证（用户指令，up=上涨日条件相关，down 对照）：
      **不采纳**——裸栈 +4.2pp 小改善真实但被止损层吸收（生产栈 B4% 上
      −2.3~−4.8pp，功能重叠）；全窗 up +109% vs full +152%。**维持全样本
      20 日 Pearson + B4% 盘中止损（+28.9%/−17.3%）**；up 裸栈
      （+17.2%/−31.4%）留作无止损场景备选。判定脚本对照 bug 已修正并记录。
      见 [outputs/archive/minute_exec/report_upres.md](outputs/archive/minute_exec/report_upres.md)。
- [x] 2026-09-20 用户组合口径验证（短窗上涨共振 4/5 日 + 3 日动量闸门 + 逐日滚动，
      消融阶梯归因）：**完整组合不替换生产配置**（B4% 栈让渡收益 11~20pp，止损与
      快信号第三次功能挤占）；**但 R3_w5 裸栈（+20.1%/−19.9%/0.82，5/5 相位，
      无分钟依赖）作为纯日线场景备选入库**。组件归因：短窗up共振 +3.6pp、闸门
      −6.7pp、滚动 +10.2pp（换手 6 倍）；逐日滚动消解网格相位敏感性（协议检验力
      相应下降）。见 [outputs/archive/minute_exec/report_uc.md](outputs/archive/minute_exec/report_uc.md)。
- [x] 2026-09-20 **V3 最佳方案实现 + 十轮预注册优化**（用户指令"根据最佳方案
      进行项目开发"）：规格 [docs/v3-best-plan.md](docs/v3-best-plan.md)，
      引擎 `resonance/v3.py`（上涨共振 EW + 动态半衰期 + Top3 缓冲 + 3 日最短
      持有 + 5% 止损 + 冷静期 + 双边成本；47 项测试）。锚点对照：0bp
      +61.22%/−20.47% vs 文档 +299.11%/−19.16%（残差=目录 529 vs 390 + 会话
      样本内最优；建仓错日一致性精确复现；**相位敏感性远低于 dyn5**：
      5 相位 +44~+61%）。十轮判据预注册于
      [docs/v3-optimization-design.md](docs/v3-optimization-design.md)，
      R1–R8 维持规格值（R7 P5 "+27pp/5/5" 被孤峰形态约束正确拦下，终栈复核
      跌回 −46pp 证伪），**R9 采纳防御层**（全A 10 日回撤>4% 禁开新仓+检查日
      退出，复用半衰期 regime 边界，零新自由参数）。**冻结终栈（10bp 5 相位
      中位）：+74.38% / −13.24% / 夏普 1.30，30bp 仍 +34.92%，择优分数
      +0.523 vs 规格栈 −0.073**。警示：全部样本内、防御层路径收敛=1 条路径
      检验力、阈值面 4→5% 锯齿在案。见
      [outputs/v3/final_report.md](outputs/v3/final_report.md) 与
      [outputs/v3/rounds/](outputs/v3/rounds/)。
- [ ] 探索方向①：扩展权重/风格指数池；②动态调仓区间（日频滚动已验，见 UC R3）；
      ③止损（执行层 4% 版已验证，可再试基准自适应 x；V3 固定止损下分钟粒度
      已证伪）；④空仓/国债避险（**V3 防御层已落地 strong4%**；2% 档过度防御
      已证伪）；⑤V3+防御栈上的共振窗 W12–15 单调改善（冻结轮事后发现，
      需独立预注册验证）；⑥冻结参数滚动样本外验证（V3 §12 第 5 条）。
- [x] 2026-09-22 **V4.1 最佳方案落地**（用户指令"根据最佳方案进行修改"）：
      规格 [docs/v4-best-plan.md](docs/v4-best-plan.md)——新 13 指数池
      （+上证指数/深证成指）、日线 Top5 预选 + 收盘前 24 根 5min 纯分钟重排、
      Top2 缓冲、无防御层。实现于 `resonance/v3.py`（`daily_top`/`minute_bars`
      + `MinuteBarProvider`，52 项测试；`daily_top=0` 与 V3 逐位一致）。
      数据约束：iFinD 月度配额 -4318 → 两新指数日线走本地 qlib 链路补采
      （`work/collect_v41_qlib.py`，4 指数交叉验证 ≤0.9bps）；5min 覆盖仅
      49.8%（dyn5 时代需求矩阵），分钟层按预注册降级策略执行并计数。
      **覆盖受限回测（2025-09-22→2026-09-18，10bp 5 相位中位）：V4.1 分钟版
      +53.39%/−23.07%/1.36 vs 纯日线 +43.80%/−24.60%/1.21——分钟层增量
      +9.8pp 与文档全覆盖增量 +23.3pp 同向**；配额恢复（约 10-01）后补采
      缺失 5min 做全覆盖锚点复现。V3+防御层栈保留为研究备选。见
      [outputs/v4/report.md](outputs/v4/report.md)。
- [x] 2026-09-22 **指数池调整与定案**（用户指令两轮：删上证50/深证成指/
      中证2000/科创综指+增大盘股883417 → 再删北证50，13→9 池；新 access
      token 恢复配额后 883417 日线/5min 补齐、Top5 概念 5min 覆盖 49.8%→
      **100%**，微盘股为唯一无 HF 覆盖领先）。**全覆盖定案（10bp 5 相位
      中位）：9 池 +8.24%/−28.89% vs 13 池 +44.25%/−20.42%——池调整 −36pp/
      −8.5pp 显著负效**；13 池全覆盖与 V4.1 锚点同向同量级（引擎正确性
      确认）；分钟层增量 +9.6~+17.1pp 与池无关稳健。维持/回滚由用户定夺
      （config.V41_BROAD_POOL 一处）。见
      [docs/v41-pool-adjustment.md](docs/v41-pool-adjustment.md)。
- [x] 2026-09-22 **V4.2 优化轮**（用户三指令，预注册判据验证）：**A topk3
      采纳**（+7.3pp，5/5）；**B 动态半衰期基准全A→当日领先指数采纳**
      （+15.4pp + 回撤改善 6.4pp 双指标，5/5）；**C 分钟窗跨 2-3 日
      （72/96/144bar）证伪不采纳**（全部 −16pp 以上：跨日窗退化为慢信号
      与日线层功能重叠，第四次快慢挤占；数据已按 144bar 补齐排除混杂）。
      **V4.2 生产栈 = V4.1 规格 + A + B（9池 10bp 中位 +8.24%→+25.07%，
      夏普 0.41→0.81）**；交互警示：A+B 在 13 池小幅反效（+44.25%→
      +39.44%），13 池整体仍领先 9 池。见
      [outputs/v4/report_v42.md](outputs/v4/report_v42.md)。
- [x] 2026-09-22 **现阶段方案定稿 V4.2**（docs/v42-best-plan.md）：9 池 +
- [x] 2026-09-22 **经验提炼 + 样本外实验准备**：
      [docs/experiment-playbook.md](docs/experiment-playbook.md)（七条定律：五次
      快慢挤占/孤峰必伪/样本内上界/评分免疫大杂烩/池-组件交互/领先指数对症/
      降级预注册；负结论登记表防重复试验；新实验七条检查清单）；
      [docs/oos-validation-design.md](docs/oos-validation-design.md)（双池并行
      9vs13 样本外裁决、参数冻结、≥60 信号日判据、禁止事项与 backlog）；
      `work/signal_daily.py` 每日 runner（增量日线+分钟自愈+OOS 起点无状态
      重放+双轨落盘，机制已离线验收）。**阻塞：iFinD 月度配额再耗尽，
      OOS 起点顺延至配额恢复（约 10-01）或新 token。**
- [x] 2026-09-22 **固定锚共振对比实验**（用户指令，outputs/exp_anchor/）：
      16 风格指数逐一作固定共振锚（日线栈，V4.2 其余参数冻结）：**深证成指
      +118.7%/−13.6%/1.84 居首，成长/中小盘梯队连贯非孤峰（国证2000 +104%/
      科创50 +78%/中证1000 +72%，两年皆正）；10/16 固定锚打败动态锚双池**
      （动态9池 +36.8% 排 11 且回撤 −38.4% 最差——换锚 whipsaw+闸门误关）。
      上证50/红利垫底（用户删除直觉被佐证）。保留：16 选 1 赢家诅咒（结论
      锚定梯队而非单点）、分钟层交互未验（配额恢复后第一复验项，L5）。
      追加拆解（exp_leader_split）：**9 池成员共振超额全负**（中证500 最好
      −3.7pp、科创50 最差 −127pp）——锚自身动量与概念层超额成反比（深证成指
      自身+35%→概念层+83.5pp；微盘自身+77%→−66pp），动态选锚按构造永远挑
      极端动量者=永远踩中负超额锚，机制与固定锚实验互相闭环。
      澄清版追加（exp_anchor_full9，9 指数逐一全流程×完整周期）：**9 池内
      最佳=科创50 锚 +114.1%/−19.5%（两年最均衡）；池外国证2000 +118.1%
      略胜**；7/9 固定锚全流程仍胜动态锚（+76.8% 列第3、回撤最差档）；
      分钟层增益因锚而异 +35.8~−13.9pp（L5 第三证，锚点结论须全流程判定）；
      上证指数锚回撤 −11.7% 最浅（防御型）。深证成指全流程待配额补其 5min。
- [x] 2026-09-23 **V4.3 定稿**（用户决策：锚定指数池=深证成指/国证2000/
      科创50 三锚动选；tag `v4.3.0-trio-anchor`，docs/v43-best-plan.md）。
      L5 复验实测：**完整周期 +165.4%/−16.2%/夏普 1.98、分钟子窗 +87.6%/
      −13.0%/2.07，优于任一单锚**（冠军深证成指 +135.5%）；领先分布 73/66/51
      均衡轮动——三成员全为正概念超额好锚，动选收益+好锚下限兼得（§六机制
      的正面应用）。OOS 增 D3 主轨（A9/B13/C1 转对照）。
      领先指数半衰期 + Top3 缓冲 + 24bar 分钟重排；三档成本与参数冻结清单、
- [ ] 2026-09-23 **2022-2024 历史扩展验证（时间外推，警示级）**：三锚动选
      −56.2%/回撤 −58.1% **未通过**（三年皆负、止损14次；regime 依赖确证）；
      深证成指单锚 −1.7%≈现金为唯一跨 regime 存活者；幸存者偏差下数字仍偏
      乐观。生产口径（三锚 vs 深证成指单锚 vs regime 过滤）待用户决策。
      见 [outputs/exp_hist_2022/report.md](outputs/exp_hist_2022/report.md)。
- [x] 2026-09-23 **定档**：用户决策**暂定三锚为生产口径**（知情保留
      2022-24 警示；深证成指单锚跨 regime 备选由 OOS C1 并行裁决；防御层
      熊市补测证明救不动三锚也不帮单锚，regime 过滤路线关闭）。dyn5 时代
      实验资产归档至 work|outputs|docs /archive/（10 脚本+报告系列+2 设计
      文档）；docs/v43-best-plan.md §七定档补记。
      六项负结论存档、每日执行流程、待决事项（池 9vs13 / 样本外验证 /
      每日 runner / 个股映射）一文件齐备。

## 环境

只使用 conda 环境 `resonance`（Python 3.12，vectorbt 1.1.0）：

```bash
conda run -n resonance python -m pytest -q          # 离线测试（47 个）
conda run -n resonance python work/probe.py         # iFinD 冒烟（需网络+凭证）
conda run -n resonance python work/collect.py       # 日线采集（目录+日线，断点续传）
conda run -n resonance python work/collect_minute.py    # 60min 分钟采集（全窗）
conda run -n resonance python work/collect_minute5.py   # 5min 分钟采集（需求矩阵裁剪）
conda run -n resonance python work/validate_data.py # 数据验证（schema/网格/跨源对照）
conda run -n resonance python work/backtest_dynamic.py  # 基线复现（变体矩阵）
conda run -n resonance python work/backtest_v3.py       # V3 规格栈锚点对照 + 相位表
conda run -n resonance python work/opt_v3.py --round N  # V3 十轮优化（N=1..10；终栈兜底 FROZEN_FINAL）
conda run -n resonance python work/backtest_v41.py       # V4.1 分钟重排栈（覆盖受限口径）
conda run -n resonance python work/collect_v41_qlib.py   # V4.1 两新指数日线补采（qlib 链路）
conda run -n resonance python work/backtest_minute.py   # 分钟共振验证（5 相位×三变体）
conda run -n resonance python work/backtest_exec.py     # 执行层验证（时点网格+止损网格×双粒度）
conda run -n resonance python work/backtest_exec_w35.py  # 信号窗 w∈{20,5,3} 重验证
conda run -n resonance python work/backtest_upres.py    # 上涨共振 vs 全样本口径验证
conda run -n resonance python work/backtest_uc.py      # 用户组合口径（短窗up+闸门+滚动）消融
```

凭证不进仓库：refresh_token 从 `/home/zxh/qlib_data/scripts/` 全局源或环境变量
`IFIND_REFRESH_TOKEN` 读取；access_token 缓存于 `/home/zxh/qlib_data/.ifind_token`（多项目共享）。

## 目录

```
resonance/     Python 包：config / ifind 客户端 / metrics 共振指标 / backtest 轮动引擎 / v3 上涨共振引擎
work/          运维脚本（probe 冒烟、采集、回测、优化运行器）
tests/         离线单元测试（全部 mock）
data/          本地缓存（gitignored）
outputs/       交付物（oos / v4 / v3 / exp_anchor / exp_hist_2022 / index_backtest_framework；archive/ 为 dyn5 时代归档）
docs/          GPT 会话纪要、方案文档（V3 规格/优化协议/执行层设计）
```

## 硬约束

1. 禁止未来函数：T 日信号最早 T+1 计收益。
2. 指数不可直接交易；任何"实盘化"结论必须注明缺费率/滑点/跟踪误差。
3. 概念目录是当前快照，历史回测存在幸存者偏差，结论须带此标注。
4. 凭证不入库（secrets 纪律，见 `resonance/ifind.py` 文档字符串）。
