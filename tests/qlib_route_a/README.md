# tests/qlib_route_a

qlib 路线 A 扩展轨的测试专目录，与 `research/qlib_route_a/` 对称
（用户 2026-09-29 指令，便于收口时整体清理）。pyproject 的
`testpaths = ["tests"]` 递归收集本目录，无需额外配置。

## 运行方式

- qlib 侧测试（本目录 test_qlib_provider / test_qlib_pipeline）：

```bash
conda run -n qlib python -m pytest tests/qlib_route_a/test_qlib_provider.py tests/qlib_route_a/test_qlib_pipeline.py -v
```

- resonance 侧测试（本目录 test_qlib_factor_export / test_qlib_f8_compare）
  随全量回归运行：

```bash
conda run -n resonance python -m pytest -q
```

- qlib 侧测试文件顶部有 `pytest.importorskip("qlib")`：在 resonance 环境
  全量跑时自动 SKIP（不会失败）；反之 qlib 环境不跑 tests/ 根的其余
  resonance 测试（其 import resonance 会失败），只按文件名指定运行。

## 注意

- 测试文件求仓库根用 `parents[2]`（本目录比 tests/ 深一层）：
  `sys.path.insert(0, str(Path(__file__).resolve().parents[2]))`。
- 全部测试用合成数据，离线可运行（CLAUDE.md 硬约束 2）。
