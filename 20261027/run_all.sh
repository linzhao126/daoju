#!/usr/bin/env bash
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
