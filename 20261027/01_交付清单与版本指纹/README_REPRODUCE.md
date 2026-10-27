# 复现说明（README_REPRODUCE）

> 目标：**另一台机器、另一个 AI，按本文件从上到下执行，能得到相同的样本清单、逐刀预测与汇总指标。**
> 全部脚本**只读**原始数据；不连机床、不写生产库、不改任何产品代码。

---

## 0. 环境

```bash
# Windows + Git Bash，Python 3.14（.venv）
C:/abc/workBoddyHome刀具/.venv/Scripts/python.exe --version
# 依赖见 01_交付清单与版本指纹/environment_requirements.txt（61 个包）
# 关键三方包：numpy / pandas / matplotlib / fastapi / uvicorn（后两者仅线上服务需要）
```

**编码**：所有命令前加 `PYTHONUTF8=1`，否则中文列名在 Windows 控制台会乱码。

**工作目录**：所有相对路径以本目录（`20261026/恢复/20261027/`）为根；脚本内部的 `ROOT` 由 `__file__` 反推 4 级，指向 `workBoddyHome刀具/`。

---

## 1. 外部输入（不随包交付，需自备并核对哈希）

| 文件 | 相对 `workBoddyHome刀具/` | 规模 | MANIFEST 中的条目 |
|---|---|---|---|
| 打标原始数据 | `influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv` | 384,595 行 × 53 列 / 178 MB | `EXTERNAL:../influxdb_...` |
| 对齐后 HI | `out_T1111/xalign_HI.csv` | 1,614 行 × 12 列 | `EXTERNAL:../out_T1111/xalign_HI.csv` |
| 换刀台账 | `tool_life_records.csv` | 38 行 | `EXTERNAL:../tool_life_records.csv` |
| 在线库 | `2ndPrediction/tool_log/data/tool.db` | 2.4 MB | `EXTERNAL:../2ndPrediction/...` |

**先校验**：

```bash
cd "C:/abc/workBoddyHome刀具"
sha256sum influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv
# 与 01_交付清单与版本指纹/MANIFEST.csv 中 EXTERNAL: 行的 SHA256 比对
```

> 若哈希不符，**不要继续**——下面的所有数字都会变。

---

## 2. 完整执行顺序（8 步，约 4~6 分钟）

```bash
cd "C:/abc/workBoddyHome刀具/20261026/恢复/20261027"
PY="C:/abc/workBoddyHome刀具/.venv/Scripts/python.exe"

# S1 生命周期与标签审计（读 178MB + 台账 + HI）
PYTHONUTF8=1 $PY 09_repro_scripts/s01_build_audit.py            > 08_本轮可复现执行日志/stdout_stderr/s01_build_audit.log 2>&1
# → 03_原始数据与标签/{life_audit_4152.csv, life_program_segments.csv,
#                      source_log_mapping.csv, ledger_piece_check.csv}

# S2 复现原负结果（LOCO，固定截断）
PYTHONUTF8=1 $PY 09_repro_scripts/s02_repro_loco.py             > 08_本轮可复现执行日志/stdout_stderr/s02_repro_loco.log 2>&1
# → 04_RUL复现实验/{predictions_loco_baseline.csv, metrics_baseline.csv,
#                  metrics_paired_baseline.csv, metrics_strata.csv}

# S3 条件生存基线 + M1-new（必须先跑 S2：它读 metrics_paired_baseline.csv）
PYTHONUTF8=1 $PY 09_repro_scripts/s03_conditional.py            > 08_本轮可复现执行日志/stdout_stderr/s03_conditional.log 2>&1
# → 04_RUL复现实验/{predictions_loco_conditional.csv, metrics_conditional.csv,
#                  metrics_samesample.csv, metrics_paired.csv}

# S4 线上/离线 HI 一致性（3 个代表周期）
PYTHONUTF8=1 $PY 09_repro_scripts/s04_hi_parity.py              > 08_本轮可复现执行日志/stdout_stderr/s04_hi_parity.log 2>&1
# → 05_HI与换刀规则审计/{HI_online_offline_parity.csv, HI_parity_perpiece.csv}

# S5 用线上管道重算全周期 HI（384k 行 × align_piece，约 60~90 秒）
PYTHONUTF8=1 $PY 09_repro_scripts/s05_online_hi_signal.py       > 08_本轮可复现执行日志/stdout_stderr/s05_online_hi_signal.log 2>&1
# → 05_.../online_pieces.csv, online_hi_signal.csv
# → 04_.../predictions_loco_onlinehi.csv, metrics_online_hi.csv

# S6 C2 策略逐刀回放（依赖 S5 的 online_pieces.csv）
PYTHONUTF8=1 $PY 09_repro_scripts/s06_decision_replay.py        > 08_本轮可复现执行日志/stdout_stderr/s06_decision_replay.log 2>&1
# → 05_.../decision_replay_per_life.csv, decision_rho_sensitivity.csv,
#         decision_break_sensitivity.csv

# S7 崩刃 A（依赖 s01 的审计表）
PYTHONUTF8=1 $PY 09_repro_scripts/s07_chipping.py               > 08_本轮可复现执行日志/stdout_stderr/s07_chipping.log 2>&1
# → 06_崩刃A专项/{chipping_data_inventory.csv, chipping_candidates.csv,
#                chipping_reset_check.csv, chipping_cases/*.png}

# S8 生成逐刀案例册
PYTHONUTF8=1 $PY 09_repro_scripts/s08_report.py                 > 08_本轮可复现执行日志/stdout_stderr/s08_report.log 2>&1
# → 04_.../rul_casebook.md, metrics_digest.md

# S9 打包（依赖关系图 / 哈希 / 提取 / 快照）；若只需复算可跳过
PYTHONUTF8=1 $PY 09_repro_scripts/s09_package.py                > 08_本轮可复现执行日志/stdout_stderr/s09_package.log 2>&1
```

**依赖关系（不能乱序）**：
```
s01 ──┬─→ s02 ──→ s03
      └─→ s07
s05 ──→ s06
s02..s07 ──→ s08
全部 ──→ s09（MANIFEST 要收全部产物）
```

---

## 3. 每个关键数字的期望值（用来判断"你是否真的复现了"）

| 检查点 | 期望值 | 出现在 |
|---|---|---|
| 周期数 | 20（16, 25~42, 45） | s01 日志 |
| 观测件 / 对齐件 | 1,686 / 1,614 | s01 日志 |
| 质量分级 | A2 / B12 / C3 / D3 | s01 日志 |
| 标签 | CHIPPING 15 / WEAR 5 | s01 日志 |
| 台账匹配 | 14 条，其中 13 条件数相等 | s01 日志 `ledger_piece_check.csv` |
| LOCO 样本 | 16 / 16 / 13 / 12（N=20/40/60/80） | s02 日志 |
| MAE(M0-A) | 22.19 / 22.19 / 17.23 / 17.96 | s02 |
| MAE(M1-旧) | 27.93 / 25.96 / 19.74 / 17.36 | s02 |
| MAE(M0-B) | 22.19 / 22.19 / 14.38 / 14.58 | s03 |
| 配对差 M1-旧−M0-A @N=20 | +5.74，CI [2.30, 9.94] | s02 |
| parity 相关 | 0.339 / 0.165 / 0.447 | s04 |
| 网格跨度差 | ≈19 mm ×3 | s04 |
| 策略成本 | 0.1144 / 0.0268 / 0.0171 / 0.0193 / 0.0182 | s06 |
| 崩刃检出 | 10/15（med），健康段误报 4/15 | s07 |

> **随机性说明**：`s02`/`s03` 的 bootstrap 用固定种子 `20261027`、2000 次重采样 → **可完全复现**，不应当有随机误差。若你的区间与上表不同，先检查 pandas/numpy 版本。

---

## 4. 常见失败与原因

| 现象 | 原因 | 处理 |
|---|---|---|
| `KeyError: 'change_reason'` | 用了未打标的原始 CSV | 必须用 `*_life_labeled.csv` |
| `Cannot convert input [Series]` | pandas 版本差异导致 `Timestamp(Series)` 报错 | `s07` 已修为 `pd.to_datetime(...).max()` |
| 中文乱码 | 未设 `PYTHONUTF8=1` | 加环境变量 |
| `ImportError: tabulate` | `DataFrame.to_markdown` 依赖 tabulate | `s08` 已改为自带 `to_md()`，不依赖 |
| s03 读不到 `metrics_paired_baseline.csv` | 先跑了 s03 没跑 s02 | 按 §2 顺序执行 |
| s06 里没有 `TWO_onlineHI` 行 | `s05` 未跑 | 先跑 s05 |

---

## 5. 复现边界（说清"没复现什么"）

- **原始 4.3 GB 数据未入包**：只给哈希与列裁剪提取版（3.9 MB gz，384,595 行 × 19 列，行不裁剪）。
- **Git**：本轮未初始化（按指示）。所有文件用 SHA256 锚定，见 `MANIFEST.csv`（94 条）。
- **`tool.db` 快照**：文件级复制 + 5 张表导出 + DDL；`history` 的 16 条全为 seed，已在 `SNAPSHOT_INFO.md` 标注。
- **不可复现的部分**：
  - `life_labeled.csv` 的 `change_reason` / `real_failure` —— 打标用的台账比仓库这份多 5 行、多一列（见 `label_conflicts.md`），**无法从仓库文件重建**；
  - 崩刃真实时刻 —— 数据里不存在；
  - ≤1 秒延迟 —— 无实时链路。

---

## 6. 一句话验收标准

> 若你的 `metrics_baseline.csv` 与本包的 MAE 四行**完全一致**（22.19 / 22.19 / 17.23 / 17.96），且 `ledger_piece_check.csv` 的 `Equal` 列为 13 个 YES + 1 个 NO，则**样本、对齐、分组、拟合口径全部对上**，可以认为复现成立。
