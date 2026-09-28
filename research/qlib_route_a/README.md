# research/qlib_route_a

qlib 验证与扩展轨（路线 A：ParquetProvider 全 qlib 链）的代码文件集中
目录。规格见
[docs/research/qlib-validation-plan.md](../../docs/research/qlib-validation-plan.md)，
实施计划见
[docs/superpowers/plans/2026-09-29-qlib-routeA-5min-factors.md](../../docs/superpowers/plans/2026-09-29-qlib-routeA-5min-factors.md)。

## 目录命名说明

**禁止把本目录命名为 `qlib/` 或置于仓库根**：脚本运行时仓库根在
sys.path 上，顶层 `qlib/` 目录会作为命名空间包遮蔽 pyqlib，
`import qlib` 将解析到本仓库目录而非已安装的库。

## 内容

| 文件 | 角色 | 环境 |
|---|---|---|
| `qlib_smoke.py` | 冒烟①：parquet 直接驱动回测框架（不 qlib.init） | qlib |
| `qlib_ml_smoke.py` | 冒烟②：因子 DataFrame 直驱训练链（A0 备选证据） | qlib |
| `qlib_provider_smoke.py` | 冒烟③：三接口注入 + 表达式引擎双频对拍（路线 A 核心，九条契约的活证据） | qlib |
| `qlib_provider.py` | （T1/T2 待建）ParquetData 容器 + 三接口 provider + init_qlib_parquet() | qlib |
| `qlib_factor_export.py` | （T3 待建）F1–F7 日线因子导出 | resonance |
| `qlib_pipeline.py` | （T4/T5 待建）5min 因子表达式 → 特征 → 训练 → 预测与 IC | qlib |
| `qlib_f8_compare.py` | （T6 待建）F8 替换的 5 相位预注册对照 | resonance |

## 纪律

- 遵守 [research/README.md](../README.md) 的探索层纪律：实验收口后本
  目录整体删除（git 历史可溯），结论并入 docs。
- 环境：qlib 侧 `conda run -n qlib`，resonance 侧 `conda run -n
  resonance`；跨环境只经 outputs/ 下的 parquet 契约文件交换。
