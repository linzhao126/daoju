# _superseded_20261029 —— 已归档的脚本副本，禁止直接运行

## 为什么归档

这里原本是 2026-10-28 第二批执行时留在 `14_整改执行_v2b/` 下的脚本副本（顶层 5 个 + `reproducible_scripts/` 一套 8 个）。问题：

1. **与正式脚本漂移**：和 `09_repro_scripts/` 同名文件对比，`bt_core.py` / `hi_asof.py` / `s23_dataset_v2.py` / `s26_package_v2.py` **内容不同**（快照是 10-28 版，正式版含 10-29 的修订），而 `s22` / `s24` 又完全相同。同一个仓库里同名脚本有两种内容，读者无法判断哪份才是回测真正跑的那份。
2. **命名与粒度不符**：`s25_chipping_row.py` 被审核 P1-01 判为"row/行级名不副实"，正式版已改名为 `s25_chipping_piece_phase.py`（件级 40 点相位残差），副本还留旧名，会让人以为存在两套崩刃检测实现。

## 唯一权威位置

| 用途 | 路径 |
|---|---|
| **运行 / 复现 / 引用** | `09_repro_scripts/` |
| 交付清单与哈希 | `01_交付清单与版本指纹/MANIFEST_v2.csv` |
| 归档副本（本目录） | 只作 10-28 那一轮的过程追溯，**不得 `python` 执行**，不得在报告中引用 |

`run_all.sh` 与 `s26_package_v2.py` 只引用 `09_repro_scripts/`，归档后不影响任何复现路径。

## 归档内容

- `s22_grid_trace.py` / `s23_dataset_v2.py` / `s24_backtest_versionA.py` / `s25_chipping_row.py` / `s26_package_v2.py`：10-28 顶层散落副本
- `reproducible_scripts/`：10-28 打包用的脚本集（8 个文件）

## 与 P1-01 的关系

P1-01 要求"文档与代码措辞统一"。归档掉旧名副本之后，全仓库只剩一份崩刃检测实现，文件名 `s25_chipping_piece_phase.py` 与其实际粒度（1 件 = 1 个标量分）一致。
