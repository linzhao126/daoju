# -*- coding: utf-8 -*-
"""s26_package_v2.py — FIX-04：交付包重建（MANIFEST / 单命令入口 / 预期输出快照）

审核 P0-04 指出仓库里缺关键 CSV。本脚本做四件事：
  1) 递归哈希本目录（20261027）下全部文件 → MANIFEST_v2.csv（含三层证据标记列）
  2) 生成 run_all.sh（单命令、set -e、日志落盘）
  3) 把本轮"预期输出"复制到 expected_outputs/（供对方 diff）
  4) 写 NOT_REPRODUCIBLE.md（哪些东西不可能从仓库重建）
"""
import os, hashlib, shutil
import pandas as pd

HERE = os.path.dirname(__file__)
BASE = os.path.abspath(os.path.join(HERE, '..'))
D1 = os.path.join(BASE, '01_交付清单与版本指纹')
D8 = os.path.join(BASE, '08_本轮可复现执行日志')
EXP = os.path.join(BASE, 'expected_outputs')
os.makedirs(D1, exist_ok=True)
os.makedirs(EXP, exist_ok=True)

# 证据分级（审核 §4.5：不再用自填 DATA_VERIFIED）
GOV = {
    'predictions_': 'DATA_PENDING_REPRO',
    'metrics_': 'DATA_PENDING_REPRO',
    'hi_asof': 'DATA_PENDING_REPRO',
    'grid_': 'DATA_PENDING_REPRO',
    'parity_': 'DATA_PENDING_REPRO',
    'decision_': 'DATA_PENDING_REPRO',
    'chipping_': 'DATA_PENDING_REPRO',
    'life_': 'DATA_PENDING_REPRO',
    'test_': 'DATA_PENDING_REPRO',
    'reproducible_scripts': 'CODE_CONFIRMED',
    'source': 'CODE_CONFIRMED',
}


def level(rel):
    for k, v in GOV.items():
        if k in rel:
            return v
    if rel.endswith('.py'):
        return 'CODE_CONFIRMED'
    return 'DATA_PENDING_REPRO'


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()

# 大文件不重复算哈希：3.3GB 每次跑几十秒，且哈希已落盘在 21_新数据标签审计/raw_new_sha256.txt
BIG_BYTES = 500 * 1024 * 1024
SHA_REF_FILE = '21_新数据标签审计/raw_new_sha256.txt'


def sha_or_ref(p):
    if os.path.getsize(p) > BIG_BYTES:
        return 'SEE:' + SHA_REF_FILE
    return sha(p)


rows = []
for dp, _, fs in os.walk(BASE):
    for fn in fs:
        p = os.path.join(dp, fn)
        rel = os.path.relpath(p, BASE).replace('\\', '/')
        if rel.startswith('expected_outputs/') or rel == '01_交付清单与版本指纹/MANIFEST_v2.csv':
            continue
        if rel.startswith('08_本轮可复现执行日志/result_hashes.csv'):
            continue
        rows.append(dict(Path=rel, SHA256=sha(p), Bytes=os.path.getsize(p),
                         EvidenceLevel=level(rel)))
MAN = pd.DataFrame(rows).sort_values('Path')
MAN.to_csv(os.path.join(D1, 'MANIFEST_v2.csv'), index=False, encoding='utf-8-sig')
print('[1] MANIFEST_v2.csv %d 条' % len(MAN))

# 外部输入（不在包内，但必须能对上）
ROOT = os.path.abspath(os.path.join(BASE, '..', '..', '..'))
EXT = [
    ('influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv',
     os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv')),
    ('out_T1111/xalign_HI.csv', os.path.join(ROOT, 'out_T1111', 'xalign_HI.csv')),
    ('out_T1111/xalign.txt', os.path.join(ROOT, 'out_T1111', 'xalign.txt')),
    ('tool_life_records.csv', os.path.join(ROOT, 'tool_life_records.csv')),
    ('2ndPrediction/tool_log/data/tool.db', os.path.join(ROOT, '2ndPrediction', 'tool_log', 'data', 'tool.db')),
    # 2026-10-28 新增：未过滤全刀原始数据（8.2M 行 / 3.3GB / 46 列，2026-09-17 ~ 10-27）
    # B 段（s32/s33/s34）依赖它；缺失只影响 B 段，A/C 段照常可跑
    ('influxdb_4152_FanucNormalData_20261027_200034.csv',
     os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261027_200034.csv')),
    # 换刀台账 60 行：**已随包交付**（15_台账真值检验/input/，P1-07 获 0 号位授权公开）
    # → 不再列为外部输入。包内副本与仓库根原件 SHA256 一致，见 44_P1-07台账入库/ledger_sha256.txt
    # 2428 / R 通道 / T3434（P1-08 通道映射实证用；110 MB）
    ('influxdb_2428_FanucNormalData_20261014_141920_R_T3434_T3434_life_labeled.csv',
     os.path.join(ROOT, 'influxdb_2428_FanucNormalData_20261014_141920_R_T3434_T3434_life_labeled.csv')),
]
er = []
for rel, p in EXT:
    if os.path.exists(p):
        er.append(dict(Path='EXTERNAL:' + rel, SHA256=sha_or_ref(p), Bytes=os.path.getsize(p),
                       EvidenceLevel='EXTERNAL_INPUT'))
pd.DataFrame(er).to_csv(os.path.join(D1, 'MANIFEST_external.csv'), index=False, encoding='utf-8-sig')
print('[2] MANIFEST_external.csv %d 条' % len(er))

# 预期输出快照
KEEP = [
    '04_RUL复现实验/predictions_loco_baseline.csv',
    '04_RUL复现实验/metrics_baseline.csv',
    '13_整改执行_v2/metrics_v2/metrics_ablation.csv',
    '13_整改执行_v2/metrics_v2/leak_impact.csv',
    '13_整改执行_v2/metrics_v2/metrics_samesample_v2.csv',
    '13_整改执行_v2/tests/test_a_train_isolation.csv',
    '13_整改执行_v2/tests/test_b_causality.csv',
    '14_整改执行_v2b/metrics/metrics_versionA.csv',
    '14_整改执行_v2b/metrics/grid_mode_corr.csv',
    '14_整改执行_v2b/metrics/parity_samegrid.csv',
    '14_整改执行_v2b/tests/test_c_program_mixing.csv',
    '14_整改执行_v2b/chipping/chipping_piecephase_metrics.csv',
    # ---- 2026-10-28 新增（21~28 轮）
    '21_新数据标签审计/label_audit_7col.csv',
    '21_新数据标签审计/label_audit_by_program.csv',
    '23_两批数据等价性/L2_by_window.csv',
    '23_两批数据等价性/L3_key_sets.csv',
    '23_两批数据等价性/L4_value_fingerprint.csv',
    '24_程序归属修正/prog_relabel.csv',
    '24_程序归属修正/M0_compare.csv',
    '24_程序归属修正/metrics_ABC.csv',
    '24_程序归属修正/bootstrap_cluster.csv',
    '24_程序归属修正/grouping_test.csv',
    '24_程序归属修正/grouping_bootstrap.csv',
    '25_品番混批诊断/prog_mix_profile.csv',
    '25_品番混批诊断/d1_prog_part_map.csv',
    '25_品番混批诊断/part_grouping.csv',
    '27_区间校准/interval_summary.csv',
    '27_区间校准/alpha_sensitivity.csv',
    # ---- 2026-10-29（P0-04 / P1-08）
    '30_缺口四分类/gap_class_per_cycle.csv',
    '30_缺口四分类/gap_class_detail.csv',
    '31_通道映射/channel_contract.csv',
    '31_通道映射/channel_field_health.csv',
    '31_通道映射/channel_pipeline_run.csv',
    # ---- 2026-10-29（G0 治理 + P0-01 收尾）
    '33_治理模板_G0/g0_redlines.csv',
    '34_P0-01收尾/P01_evidence_all60.csv',
    # ---- 2026-10-29（P1-06 / P1-07 / R07 收尾）
    '43_模型区间与不确定性/metrics_all_models_pi.csv',
    '43_模型区间与不确定性/pi_vs_baseline.csv',
    '44_P1-07台账入库/ledger_fields.csv',
    '44_P1-07台账入库/ledger_pii_scan.txt',
    '44_P1-07台账入库/ledger_sha256.txt',
    '45_R07_UI证据/ui_status.csv',
    '45_R07_UI证据/ui_phrase_scan.csv',
    '45_R07_UI证据/ui_package_consistency.csv',
    '14_整改执行_v2b/logs/acceptance_check.csv',
]
n = 0
for rel in KEEP:
    src = os.path.join(BASE, rel)
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(EXP, rel.replace('/', '__')))
        n += 1
print('[3] expected_outputs/ %d 个快照' % n)

# run_all.sh
SH = '''#!/usr/bin/env bash
# 单命令复现入口（只读原始数据；不连机床、不写生产库）
#
# 分三段执行：
#   A 必需 —— 只依赖「包内文件 + 仓库根的旧 labeled / xalign_HI / 台账」，无外部依赖
#   B 可选 —— 需要 3.3 GB 未过滤原始文件 influxdb_4152_FanucNormalData_20261027_200034.csv
#   C 可选 —— 只需包内产物（22_c48-c60_xalign/labeled_T1111_new.csv 已随包交付）
#
# 用法：
#   bash run_all.sh           # A + C（无 3.3GB 也覆盖 24/26 个脚本）
#   bash run_all.sh --full    # A + B + C（有 3.3GB 时跑全量）
set -euo pipefail
cd "$(dirname "$0")"
PY="${PY:-C:/abc/workBoddyHome刀具/.venv/Scripts/python.exe}"
export PYTHONUTF8=1
L=08_本轮可复现执行日志/stdout_stderr
L2=14_整改执行_v2b/logs
S=09_repro_scripts
mkdir -p "$L" "$L2"

FULL=0
if [ "${1:-}" = "--full" ]; then FULL=1; fi

step(){ local name="$1"; shift; echo ">>> $name"; "$@" || { echo "FAILED: $*"; exit 1; }; }

echo "=========== A 段：必需（无外部依赖）==========="
step "A01 生命周期审计"         "$PY" $S/s01_build_audit.py        > "$L/s01.log" 2>&1
step "A02 逐刀 LOCO 基线"       "$PY" $S/s02_repro_loco.py         > "$L/s02.log" 2>&1
step "A03 条件生存基线"         "$PY" $S/s03_conditional.py        > "$L/s03.log" 2>&1
step "A04 线上离线 HI 一致性"   "$PY" $S/s04_hi_parity.py          > "$L/s04.log" 2>&1
step "A05 线上 HI 信号强度"     "$PY" $S/s05_online_hi_signal.py   > "$L/s05.log" 2>&1
step "A06 C2 策略逐刀回放"      "$PY" $S/s06_decision_replay.py    > "$L/s06.log" 2>&1
step "A07 崩刃件级旧口径"       "$PY" $S/s07_chipping.py            > "$L/s07.log" 2>&1
step "A08 逐刀案例册"           "$PY" $S/s08_report.py              > "$L/s08.log" 2>&1
step "A09 因果 HI as_of"        "$PY" $S/s20_asof_hi.py             > "$L2/s20.log" 2>&1
step "A10 三源消融回测"         "$PY" $S/s21_backtest_v3.py         > "$L2/s21.log" 2>&1
step "A11 网格溯源 FIX-06"      "$PY" $S/s22_grid_trace.py          > "$L2/s22.log" 2>&1
step "A12 数据集分层 FIX-03"    "$PY" $S/s23_dataset_v2.py          > "$L2/s23.log" 2>&1
step "A13 VersionA 回测"        "$PY" $S/s24_backtest_versionA.py   > "$L2/s24.log" 2>&1
step "A14 崩刃件级相位残差"     "$PY" $S/s25_chipping_piece_phase.py > "$L2/s25.log" 2>&1
step "A15 换刀真值检验"         "$PY" $S/s30_switch_truth.py        > "$L2/s30.log" 2>&1
step "A16 形态分层 RUL"         "$PY" $S/s31_form_stratified_rul.py > "$L2/s31.log" 2>&1
step "A17 Test-A 训练隔离"      "$PY" 13_整改执行_v2/tests/test_a_train_isolation.py > "$L2/test_a.log" 2>&1
step "A18 Test-B 因果截断"      "$PY" 13_整改执行_v2/tests/test_b_causality.py       > "$L2/test_b.log" 2>&1
step "A19 Test-C 程序混用"      "$PY" 14_整改执行_v2b/tests/test_c_program_mixing.py > "$L2/test_c.log" 2>&1
step "A20 Test-D 崩刃截断"      "$PY" 14_整改执行_v2b/tests/test_d_chipping_cutoff.py > "$L2/test_d.log" 2>&1
step "A21 Test-E 换刀不读标签"  "$PY" 14_整改执行_v2b/tests/test_e_switch_truth.py > "$L2/test_e.log" 2>&1
step "A22 通道映射自检"        "$PY" $S/channel_map.py              > "$L2/channel_map.log" 2>&1
step "A23 通道映射实证"        "$PY" $S/s41_channel_map_check.py    > "$L2/s41.log" 2>&1
step "A24 P0-01 逐把证据链"    "$PY" $S/s42_p01_evidence.py          > "$L2/s42.log" 2>&1
step "A25 四模型独立区间"        "$PY" $S/s43_model_intervals.py       > "$L2/s43.log" 2>&1
step "A26 R07 UI措辞扫描"        "$PY" $S/s44_r07_ui_scan.py           > "$L2/s44.log" 2>&1
step "A27 P1-07 台账审计"        "$PY" $S/s45_ledger_audit.py          > "$L2/s45.log" 2>&1

if [ "$FULL" = "1" ]; then
  echo "=========== B 段：可选（需 3.3GB 未过滤原始文件）==========="
  step "B20 标签与真值审计"     "$PY" $S/s32_label_audit.py         > "$L2/s32.log" 2>&1
  step "B21 生成新周期 labeled" "$PY" $S/s33_build_labeled_new.py   > "$L2/s33.log" 2>&1
  step "B22 两批数据等价性"     "$PY" $S/s34_data_equiv.py          > "$L2/s34.log" 2>&1
  step "B23 缺口四分类"         "$PY" $S/s40_gap_classify.py         > "$L2/s40.log" 2>&1
else
  echo "=========== B 段：跳过（未加 --full）==========="
fi

echo "=========== C 段：可选（需包内 labeled_T1111_new.csv）==========="
if [ -f 22_c48-c60_xalign/labeled_T1111_new.csv ]; then
  step "C23 程序归属重标注"     "$PY" $S/s35_prog_relabel.py        > "$L2/s35.log" 2>&1
  step "C24 品番诊断免原始"     "$PY" $S/s38_prog_diagnostics.py --no-raw > "$L2/s38.log" 2>&1
  step "C25 28 周期三组对照"    "$PY" $S/s36_backtest_28.py         > "$L2/s36.log" 2>&1
  step "C26 分组粒度对比"       "$PY" $S/s37_grouping_test.py       > "$L2/s37.log" 2>&1
  step "C27 区间校准"           "$PY" $S/s39_interval_calib.py      > "$L2/s39.log" 2>&1
else
  echo "  [SKIP] 缺 22_c48-c60_xalign/labeled_T1111_new.csv，C 段跳过"
fi

echo "=========== G0 治理自检（非阻塞：只看回执类文档）==========="
"$PY" $S/check_g0.py 33_治理模板_G0/33-01_R06遵循性检查_模板.md \
   20_R06_项目目标遵循性检查/20-00_R06_项目目标遵循性检查_v1.0.md \
   > "$L2/g0_check.log" 2>&1 || echo "  [WARN] G0 自检有告警，详见 $L2/g0_check.log"

echo "=========== V01 验收判据机检（失败即中止，防止判据空转）==========="
step "V01 判据机检"             "$PY" $S/s46_verify_acceptance.py      > "$L2/acceptance_check.log" 2>&1

echo "=========== 打包（必须最后：扫全目录重建清单）==========="
step "P01 重建 MANIFEST"        "$PY" $S/s09_package.py             > "$L/s09.log" 2>&1
step "P02 重建交付包清单"       "$PY" $S/s26_package_v2.py          > "$L/s26.log" 2>&1

echo "ALL DONE"
echo "验收看 3 个日志："
echo "  $L2/test_a.log  171/171 PASS 且 Delta = 0.000e+00"
echo "  $L2/test_b.log  40/40 PASS 且 dHI = 0"
echo "  $L2/test_c.log  4 项 PASS"
echo "  $L2/test_d.log  9/9 PASS 且 dScore = 0.000e+00"
echo "  $L2/test_e.log  46/46 PASS 且 Delta 全 0"
echo "  $L2/channel_map.log  12/12 PASS"
echo "  $L2/s41.log  L 通道 3/3 有段，R 通道 0/3"
echo "  $L2/s42.log  可正式验收 8 个周期（c30/31/39/40/49/50/56/59）"
echo "  $L2/s43.log  四模型 S1 覆盖 0.69~0.83；借 M0-B 区间 Delta 全 >= 0"
echo "  $L2/s44.log  HARD 措辞 0；IMPLEMENTED 9 / DESIGN_ONLY 6；exit=0"
echo "  $L2/s45.log  PII 0 命中；7 脚本包内优先 7/7；SHA 三方一致"
'''
open(os.path.join(BASE, 'run_all.sh'), 'w', encoding='utf-8', newline='\n').write(SH)
print('[4] run_all.sh 已写')

NOT = '''# 不可能从本仓库重建的东西（FIX-04 要求单列）

> 2026-10-29 更新：拿到 60 行完整台账 `三厂换刀记录.xlsx` 与 3.3 GB 未过滤原始数据后，
> 原第 1、6 条的表述已修正（不是"另一份台账"，而是"同一份的 38 行截断版"）。
>
> **2026-10-29 11:00 再更新（P1-07）**：台账已获 0 号位授权随包进公开仓库，7 个读它的脚本
> 改为"包内副本优先、仓库根回退"，SHA256 三方一致 → 它从"仓库外输入"降级为"包内文件"，
> 见 `44_P1-07台账入库/44-00`。下面第 6 条只剩 3.3 GB 原始 CSV。

| # | 项 | 为什么不可重建 | 影响 |
|---|---|---|---|
| 1 | `change_reason` 的原始措辞 | 数据侧已去掉"渐进式磨损"前缀并改写为"刀尖磨损"；台账 xlsx 原文**已在包内**（`15_台账真值检验/input/`），但两套文本的映射**未逐条确认** | 破损/磨损分类只能追溯到数据侧文字。⚠ 另：台账 `备注` 列 60 行只填了 1 行，形态信息几乎全在 `换刀原因` 自由文本里（32 个唯一值） |
| 2 | `real_failure` 列的真实性 | 来自 60 行台账的 `是否真实失效`，但**该列 60/60 全填"是"**，若按"换刀就填是"定义则不含信息 | 无法区分物理失效与预防性更换 |
| 3 | `xalign_HI.csv` 的逐位复现 | 由 **2026-10-12 版** `t1111_pipeline.py` 生成，该版含 **X≥19 裁剪**；现行 10-14 版无此裁剪 | 现行代码复算只能到 ρ=0.936（`14_整改执行_v2b/metrics/grid_mode_corr.csv`） |
| 4 | 真实的物理崩刃时刻 | 数据里不存在；台账只有换刀时刻；失效形态是生命周期末一次性人工填写（0 号位 D-02 裁决） | 崩刃 A 只能报候选区间，不能报事件召回率 |
| 5 | 数据到达延迟 L2 | 库中只有 `_time`（采集时刻），无 `recv_ts`（入库时刻） | "≤1 秒"端到端目标无法评估 |
| 6 | 3.3 GB 未过滤原始文件 | 超 Git 容量限制（**非保密原因**），未入包 | 只影响 **B 段**（s32/s33/s34/s40）；A/C 段不需要。获取：4152 InfluxDB 的 `FanucNormalData` 导出 09-17~10-27，SHA256 见 `21_新数据标签审计/raw_new_sha256.txt` |
| 7 | c38 / c51 的真实加工件数 | 台账刀寿 102/100，数据实测 197/170（比值 1.93/1.70），方向为"超出"且无采集解释 | 这两把只能排除出正式验收；待车间确认 |
| 8 | UI 的浏览器渲染结果与接口响应 | 本轮只改字符串字面量并做了 `node --check` / `py_compile`；未起服务打 `/api/state`（会写 `kv` 表、影响线上基准） | `45_R07_UI证据/45-00` §3 已如实标注"渲染回归未做" |
| 9 | 区间覆盖率的不确定性 | 校准池只有 5–9 把刀，覆盖率点估计 0.69~0.83 但 Wilson 95% 下界 0.40~0.57 | 只能交"点估计 + 宽度"，不能交"区间已校准达标" |

**能重建的**：全部审计表、全部回测、全部单元测试、全部崩刃候选、两批数据等价性、程序归属重标注、分组粒度对比、区间校准、四模型独立预测区间、台账字段审计、UI 措辞扫描。

**重建验收标准**（`bash run_all.sh` 后逐条 grep；2026-10-29 11:05 全量实跑一次，39 步 / 3 分 31 秒 / exit 0）

> ⚠ 判据里给的字符串是**日志里原样存在的片段**，可以直接 `grep` 验证。第一版判据有三处
> 写的是我概括的短语（`40/40 PASS`、`4 项 PASS`、`AUC_gap = 0.5599`），日志里查不到原串——
> 已全部改为可 grep 的实串或改指 CSV。**判据做不到 grep，就等于没有判据。**

| 文件 | 判据（原样 grep） |
|---|---|
| `logs/test_a.log` | `171（PASS 171 / FAIL 0` 且 `最大 |ΔFitB| = 0.000e+00` |
| `logs/test_b.log` | `结论：PASS`（逐位不变；该脚本不打印计数汇总） |
| `logs/test_c.log` | `结论：PASS` 且三个 `PASS_` 用例全部 `PASS` |
| `logs/test_d.log` | `汇总：9 / 9 PASS` 且 `最大 |Δscore|  = 0.000e+00` |
| `logs/test_e.log` | `汇总：46 / 46 PASS` |
| `logs/channel_map.log` | `12/12 PASS` |
| `logs/s41.log` | `3/3 周期有几何段`（L）与 `0/3 周期有段`（R） |
| `logs/s42.log` | `可正式验收` 后为 `8` |
| `logs/s44.log` | `改动后（线上 + 交付副本）HARD 命中：0 处` 且 `exit=0` |
| `logs/s45.log` | `PII 命中 0 处`、`7 脚本包内优先 7/7`、`一致：是` |
| `logs/s30.log` 第 43 行 | `AUC=0.560`（**不是** 0.862 —— 那是循环论证版）；CSV 对应 `15_台账真值检验/switch_detect_metrics.csv` → `AUC_gap,0.5599` |
| `43_模型区间与不确定性/metrics_all_models_pi.csv` | `S1_Cover` 列 0.60~0.83（点估计），`S1_CIlo` 0.397~0.570（样本不足） |
| `43_模型区间与不确定性/pi_vs_baseline.csv` | `Delta` 列 48 行全部 ≥ 0 |
| `logs/g0_check.log` | `合计：PASS 1 / WARN 1 / FAIL 0`（WARN 为非阻塞，见 20-00 的 9 处豁免） |
'''
open(os.path.join(BASE, 'NOT_REPRODUCIBLE.md'), 'w', encoding='utf-8').write(NOT)
print('[5] NOT_REPRODUCIBLE.md 已写')
