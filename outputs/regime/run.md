# regime-detection 运行摘要（2026-10-12，负结论收口）

预注册：docs/research/regime-detection-plan.md（2026-10-12）；收口结论见
docs/research/regime-detection-closeout.md。判决：**方向停止**，判据 3/4/5/6 失败。

主结果（全A 883957.TI，2021-12-01~2026-09-18，样本内结构研究上界）：

- R2 ruptures（PELT+rbf，min_size=20，中心档 pen=10）：3 断点 4 段，最短 220 日；
  断点对 pen=3 稳健 3/3；事件对齐 2/5（E2/E3 对齐，E1/E4/E5 偏 128/79/47 交易日）；
  BinSeg 对照 3/3 断点 5 日内一致。
- R3 HMM（full 协方差，10 种子，BIC）：五标的 BIC 均选候选上限 K=4；全A 四态
  =低波缓涨 672 日/高波急跌 93 日(日均−2.49%)/中高波上涨 328 日/极高波 68 日；
  维特比切换 220 次/1,161 日。
- R4 增量对照：HMM→ruptures 确认率 4.1%（门槛 70%）；D（最高波动档 68 日）对
  主窗口第 3 相位 71 个已实现亏损段的对数亏损覆盖 13.3%（门槛 30%），D 内账本
  持仓日占比 62.5%；D∩半衰期高风险档 86.8%、D∩3 日闸门拦截日 42.7%。
- R5 滚动前推（月度重估+前向滤波，K=4）：天级一致率 55.5%，双月补救 54.4%
  （门槛 60%）；高低波态 T+20 Welch t 两窗 p<0.001 但方向相反——高波态此后
  20 日 +7.4%/+8.5%，高于低波态 −0.9%/+1.3%（反弹态，非危险态）。

数据表（本地保留，不入库）：features.parquet、ruptures_*、hmm_*、
overlap_summary.csv、r4_verdict.json、wf_*、forward_returns.csv、welch_tests.csv。
复现命令链（脚本已按铁律 4 清理，见 git 历史本提交的父提交）：
regime_features.py → regime_ruptures.py → regime_hmm.py → regime_overlap.py
→ regime_walkforward.py [--refit-months 2]。
