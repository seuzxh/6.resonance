# research

探索验证脚本（预注册实验的实施面）。与 docs/research/ 同名同构：
**docs/research/<实验>.md 预注册设计 ↔ research/<实验>_*.py 实施脚本**。

## 纪律（防止探索脚本堆砌成 tmp 垃圾场）

1. **进入门槛**：脚本必须对应 docs/research/ 下的一份预注册设计（过
   experiment-playbook §五检查清单），命名 `<实验名>_<阶段>.py`。
2. **收口即清理**：实验收口后脚本删除（git 可溯），结论并入 docs；
   本目录只保留进行中的实验。
3. **禁止堆砌**：不设 tmp 目录，一次性调试代码不留档——未预注册的
   探索在会话内跑完即弃。`.gitignore` 已含 `tmp/` 物理防线。
4. 环境：探索脚本标明 conda env（如 qlib 验证用 `qlib` 环境）。

## 在册实验

- `resonance_alternatives_core.py` 与 `resonance_alternatives_run.py`：
  三类替代机制验证，预注册见
  [resonance-alternatives.md](../docs/research/resonance-alternatives.md)。

qlib 验证与扩展轨（路线 A）的代码文件集中在子目录
[qlib_route_a/](qlib_route_a/)（含 README 与目录命名说明——禁止命名为
`qlib/`，会遮蔽 pyqlib 包）：

- `qlib_route_a/qlib_smoke.py`：qlib 验证 P0 冒烟（parquet 直接驱动
  pyqlib 回测，无 bin、无 qlib.init；conda env `qlib`）。方案见
  [qlib-validation-plan](../docs/research/qlib-validation-plan.md)，
  实施按 P1→P5 推进，全部完成后本实验脚本随收口清理。
- `qlib_route_a/qlib_ml_smoke.py`：扩展轨 A0 链冒烟（因子 DataFrame →
  from_df → DatasetH → LGBModel，2026-09-27 通过；conda env `qlib`）。
  路线 A 选定后降为备选证据。
- `qlib_route_a/qlib_provider_smoke.py`：**选定路线 A** 的数据层注入
  冒烟（ParquetProvider 三接口 + qlib.init + 表达式引擎，day/5min 双频
  逐项核对，2026-09-28 通过 exit=0；conda env `qlib`）。
- `qlib_explore_am_ovnt.py`：探索性诊断（未预注册）——上午盘/隔夜/
  隔日因子族 IC（2026-09-29 用户指令；结论：上午与隔夜信息域基本无效，
  PMAM_ROT 呈慢因子形态未过线；随收口清理）。
- `alpha158_verify_prep.py` + `alpha158_verify.py`：A158-V1 预注册
  验证（docs/research/alpha158-verify-plan.md）——B 三因子 LGBM 判
  HARMFUL（二连败）、C BETA20 单因子判 NEUTRAL（与 F8 信息等价）；
  随收口清理。
- `qlib_explore_alpha158.py`：探索性挖掘（未预注册）——Alpha158
  全库 157 因子 × 5min 的 IC 扫描（2026-09-29 用户指令；正侧 BETA20
  test +0.159 与 TAIL_MOM24 互证、负侧 CNTP/WVMA 微观反转过 Bonferroni；
  采纳级须另行预注册；随收口清理）。
- 验证轨（同日完成）：`qlib_harness.py`（P1 回测适配层）、
  `qlib_bridge_export.py`（P2 信号桥+重放引擎，G2 45/45）、
  `qlib_equivalence.py`（P3 ResonanceStrategy，E-Gate 45/45 逐笔一致）。
- 扩展轨实施完成（2026-09-29 夜，T1–T7 全过）：`qlib_provider.py`、
  `qlib_factor_export.py`、`qlib_pipeline.py`、`qlib_f8_compare.py` 全部
  落盘并带合成测试；**X3 判决 HARMFUL（负结论已登记 playbook §三）**，
  总结见 [outputs/qlib_ml/report.md](../outputs/qlib_ml/report.md)。实验收口
  待用户过目报告后执行（收口即清理本目录）。
