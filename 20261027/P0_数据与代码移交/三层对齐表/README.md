# 三层对齐表（R-4 · 对应任务书 §3-P0-D03）

> 生成脚本：`09_repro_scripts/s74_build_three_layer.py`（输入 `手工提供文件/tool.db.synthesized`，由 s70 从 3.3 GB CSV 灌成）
> 用途：让审核方**不依赖 1 号位结论**，自己按"刃口 → 段 → 件"三层重建每把刀的一生并对账。
> ⚠ 不入公开 Git（`*.db`/大 CSV 已被 .gitignore 屏蔽）；随 `手工提供文件/` 一并手动交审核方。

## 三张表

| 层 | 文件 | 行×列 | 一行代表 |
|---|---|---|---|
| **刃口层** | `physical_tool_lives.csv` | 60 × 25 | 一个物理刃口生命周期（上刀 → 下刀） |
| **段层** | `tool_program_segments.csv` | 36 × 14 | 一次"程序连续运行"（prog 变化即断段） |
| **件层** | `tool_piece_exposure.csv` | 2554 × 17 | 一件（`allnum`），带切削暴露量与缺口标注 |

## 任务书 §3-D03 三条硬要求的落实情况

| 要求 | 实现 | 本批实测 |
|---|---|---|
| "同一程序第二次进入必须能分出新的段；`SegmentCount` 不得假称 `ToolInvocationCount`" | 段层用 **gaps-and-islands**（按 `ts` 排序，`prog` 变化即断段），`SegIdxWithinLife` + `IsReEntry` 逐段标注 | 覆盖 28 周期 / 36 段，其中 **8 个周期跨程序（≥2 段）**；⚠ **A→B→A 式二次进入在本批真实数据中 0 例**——能力由 N02 单测 `test_i I2`（02→03→02→3 段）覆盖，**不声称已实证** |
| "缺口只允许真实观测或 `UNKNOWN`，不能伪造未加工/实际加工件数" | 件层 `ToolParticipationEvidence` 只取 `OBSERVED`/`UNKNOWN`；刃口层 `InDataWindow`/`Coverage` 单列 | 28 周期有数据；32 周期 `InDataWindow=NO`（窗口在 09-17 之前，不伪造件数） |
| "人工续设刀寿计数器不自动等于物理换刀" | 刃口层 `EndReason_ledgerText`（台账文字）与 `EndReason_dbServiceEvent`（服务运行时事件）**分列**；段层不含刀寿字段 | `EndReason_dbServiceEvent` 全为"服务未触发 tool_events"（`MONITOR_ONLY`+`judge_allowed` 下从未放行） |

## 对账与已知边界（先于审核方自查出来）

| 项 | 结果 |
|---|---|
| 件层件数 vs 段层件数汇总 | **全部一致**（0 处不一致） |
| **件层跨窗口共享 `allnum`** | **6 行**（3 个 allnum 各落入 2 个相邻周期窗口：58424 / 61231 / 61540）→ 已标 `BoundaryShared=YES`；**逐周期求和会双计**，去重后唯一 allnum = 2551 |
| **与仓库旧段表 `life_program_segments.csv` 的覆盖差异** | 旧表基于**旧批 labeled**（数据从 09-14）→ 含 c25~c28；本表基于 **3.3 GB CSV**（数据从 09-17 08:15）→ **c25~c28 不在本表范围，不是丢数据**。旧表 4 个跨程序周期（c27/c33/c34/c38）中，本表含 c33/c34/c38；c27（09-15 窗口）整体在数据起点前 |
| `ProgramSub` / `PartNo` | 全为 `NOT_COLLECTED`（合成库未含子程序列；品番按 D-04 明确不使用） |
| `ExposureSeconds_proxy` | 按 `rows(state=3) × 15 s 名义采集周期` 估算，**非逐行积分**；逐行 dt 版本留待 s70b |
| `刃口层 EvidenceLevel` | 20 行为 E2（有刃口审计），40 行 E3_UNKNOWN（无审计记录，未进 HI 管道） |

## 复算

```bash
cd daoju_repo/20261027
python -X utf8 09_repro_scripts/s70_synthesize_tool_db.py    # 先灌库（约 90 s）
python -X utf8 09_repro_scripts/s74_build_three_layer.py     # 再出三层表
```

依赖顺序：`三厂换刀记录.xlsx` →（r0）→ `tool_life_records_v3.csv` →（s70）→ `tool.db.synthesized` →（s74）→ 本目录三张表。
