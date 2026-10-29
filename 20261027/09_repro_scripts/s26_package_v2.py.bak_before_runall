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
]
er = []
for rel, p in EXT:
    if os.path.exists(p):
        er.append(dict(Path='EXTERNAL:' + rel, SHA256=sha(p), Bytes=os.path.getsize(p),
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
    '14_整改执行_v2b/chipping/chipping_row_metrics.csv',
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
# FIX-04 单命令复现入口（只读原始数据；不连机床、不写生产库）
set -euo pipefail
cd "$(dirname "$0")"
PY="${PY:-C:/abc/workBoddyHome刀具/.venv/Scripts/python.exe}"
export PYTHONUTF8=1
mkdir -p 08_本轮可复现执行日志/stdout_stderr 14_整改执行_v2b/logs
S=09_repro_scripts

run(){ echo ">>> $1"; shift; "$@" > /dev/null 2>&1 || { echo "FAILED: $*"; exit 1; }; }

"$PY" $S/s01_build_audit.py         > 08_本轮可复现执行日志/stdout_stderr/s01.log 2>&1
"$PY" $S/s02_repro_loco.py          > 08_本轮可复现执行日志/stdout_stderr/s02.log 2>&1
"$PY" $S/s03_conditional.py         > 08_本轮可复现执行日志/stdout_stderr/s03.log 2>&1
"$PY" $S/s05_online_hi_signal.py    > 08_本轮可复现执行日志/stdout_stderr/s05.log 2>&1
"$PY" $S/s06_decision_replay.py     > 08_本轮可复现执行日志/stdout_stderr/s06.log 2>&1
"$PY" $S/s07_chipping.py            > 08_本轮可复现执行日志/stdout_stderr/s07.log 2>&1
"$PY" $S/s08_report.py              > 08_本轮可复现执行日志/stdout_stderr/s08.log 2>&1
"$PY" $S/s20_asof_hi.py             > 14_整改执行_v2b/logs/s20.log 2>&1
"$PY" $S/s21_backtest_v3.py         > 14_整改执行_v2b/logs/s21.log 2>&1
"$PY" $S/s22_grid_trace.py          > 14_整改执行_v2b/logs/s22.log 2>&1
"$PY" $S/s23_dataset_v2.py          > 14_整改执行_v2b/logs/s23.log 2>&1
"$PY" $S/s24_backtest_versionA.py   > 14_整改执行_v2b/logs/s24.log 2>&1
"$PY" $S/s25_chipping_row.py        > 14_整改执行_v2b/logs/s25.log 2>&1
"$PY" 13_整改执行_v2/tests/test_a_train_isolation.py > 14_整改执行_v2b/logs/test_a.log 2>&1
"$PY" 13_整改执行_v2/tests/test_b_causality.py       > 14_整改执行_v2b/logs/test_b.log 2>&1
"$PY" 14_整改执行_v2b/tests/test_c_program_mixing.py > 14_整改执行_v2b/logs/test_c.log 2>&1
"$PY" $S/s26_package_v2.py          > 08_本轮可复现执行日志/stdout_stderr/s26.log 2>&1
echo "ALL DONE"
'''
open(os.path.join(BASE, 'run_all.sh'), 'w', encoding='utf-8', newline='\n').write(SH)
print('[4] run_all.sh 已写')

NOT = '''# 不可能从本仓库重建的东西（FIX-04 要求单列）

| # | 项 | 为什么不可重建 | 影响 |
|---|---|---|---|
| 1 | `*_life_labeled.csv` 里的 `change_reason` | 打标用的台账比仓库 `tool_life_records.csv` **多 5 行（c39~c45）、多一列 `是否真实失效`**，且原因文字被改写（"渐进式磨损"→"刀尖磨损"） | 所有"破损/磨损"分类只能追溯到数据侧文字 |
| 2 | `real_failure` 列 | 来自上述缺失台账的 `是否真实失效` 列，仓库版没有该列 | 无法区分"换刀事件"与"物理失效" |
| 3 | `xalign_HI.csv` | 由 **2026-10-12 版** `t1111_pipeline.py` 生成；仓库里是 10-14 版（内容 `diff=0` 于今日，但生成时点早 2 天）。历史版**含 X≥19 裁剪**，现行代码没有 | 用现行代码复算只能做到 ρ=0.936（`grid_mode_corr.csv`），不能逐位复现 |
| 4 | 真实崩刃时刻 | 数据里不存在；只有"人工发现/换刀时刻" | 崩刃 A 只能报"候选区间"，不能报召回率 |
| 5 | 数据到达延迟 | 库中只有 `_time`，无 `recv_ts` | "≤1 秒"无法评估 |
| 6 | 4.3 GB 原始文件 | 未入包；只给 `raw_extract_T1111_20cycles.csv.gz`（3.9 MB，行不裁剪、列裁剪 19 列） | 需要超过 19 列的字段时须回原始文件 |

**能重建的**：全部审计表、全部回测、全部单元测试、全部崩刃候选。
**重建验收集**：`test_a.log` 出现 `171/171 PASS` 且 `ΔFitB = 0.000e+00`；`test_b.log` 出现 `40/40 PASS` 且 `|ΔHI|max = 0.000e+00`；`test_c.log` 出现 4 项 PASS。
'''
open(os.path.join(BASE, 'NOT_REPRODUCIBLE.md'), 'w', encoding='utf-8').write(NOT)
print('[5] NOT_REPRODUCIBLE.md 已写')
