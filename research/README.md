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

- `qlib_smoke.py`：qlib 验证 P0 冒烟（parquet 直驱 pyqlib 回测，无 bin、
  无 qlib.init；conda env `qlib`）。方案见
  [qlib-validation-plan](../docs/research/qlib-validation-plan.md)，
  实施按 P1→P5 推进，全部完成后本实验脚本随收口清理。
