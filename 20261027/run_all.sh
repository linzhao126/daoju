#!/usr/bin/env bash
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
step "A22 Test-F 件数口径"     "$PY" 14_整改执行_v2b/tests/test_f_piece_count.py > "$L2/test_f.log" 2>&1
step "A23 通道映射自检"        "$PY" $S/channel_map.py              > "$L2/channel_map.log" 2>&1
step "A24 通道映射实证"        "$PY" $S/s41_channel_map_check.py    > "$L2/s41.log" 2>&1
step "A25 P0-01 逐把证据链"    "$PY" $S/s42_p01_evidence.py          > "$L2/s42.log" 2>&1
step "A26 四模型独立区间"        "$PY" $S/s43_model_intervals.py       > "$L2/s43.log" 2>&1
step "A27 R07 UI措辞扫描"        "$PY" $S/s44_r07_ui_scan.py           > "$L2/s44.log" 2>&1
step "A28 P1-07 台账审计"        "$PY" $S/s45_ledger_audit.py          > "$L2/s45.log" 2>&1
step "A29 R02 延长分支审计"     "$PY" $S/s52_r02_grant_audit.py       > "$L2/s52.log" 2>&1
step "A30 R04 严格8/宽松26分列"  "$PY" $S/s53_r04_grade_split.py        > "$L2/s53.log" 2>&1
step "A31 R05 策略安全与收益"   "$PY" $S/s54_r05_policy_safety.py      > "$L2/s54.log" 2>&1
step "A32 Test-G 起点确认"      "$PY" 14_整改执行_v2b/tests/test_g_start_confirm.py > "$L2/test_g.log" 2>&1
step "A33 R08 聚类CI与分组"     "$PY" $S/s55_r08_cluster_ci.py         > "$L2/s55.log" 2>&1
step "A34 R09 版本核证"         "$PY" $S/s56_r09_release_verify.py      > "$L2/s56.log" 2>&1
step "A35 (k,rho) 决策表"    "$PY" $S/s57_decision_by_k.py           > "$L2/s57.log" 2>&1
step "A36 Test-H 到达延迟"   "$PY" 14_整改执行_v2b/tests/test_h_recv_ts.py > "$L2/test_h.log" 2>&1
step "A37 样本就绪自检"      "$PY" $S/s58_ready_check.py            > "$L2/s58.log" 2>&1
step "A38 采样率需求实验"    "$PY" $S/s59_bandwidth.py              > "$L2/s59.log" 2>&1
step "A39 六通道扫描"       "$PY" $S/s60_channel_scan.py            > "$L2/s60.log" 2>&1
step "A40 最优换刀分位"     "$PY" $S/s61_optimal_quantile.py         > "$L2/s61.log" 2>&1

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
"$PY" $S/check_g0.py 33_治理模板_G0/33-01_R06遵循性检查_模板.md    20_R06_项目目标遵循性检查/20-00_R06_项目目标遵循性检查_v1.0.md    > "$L2/g0_check.log" 2>&1 || echo "  [WARN] G0 自检有告警，详见 $L2/g0_check.log"

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
echo "  $L2/test_f.log  15/15 PASS 且 used=107（不是 56）"
echo "  $L2/channel_map.log  12/12 PASS"
echo "  $L2/s41.log  L 通道 3/3 有段，R 通道 0/3"
echo "  $L2/s42.log  可正式验收 8 个周期（c30/31/39/40/49/50/56/59）"
echo "  $L2/s43.log  四模型 S1 覆盖 0.69~0.83；借 M0-B 区间 Delta 全 >= 0"
echo "  $L2/s44.log  HARD 措辞 0；IMPLEMENTED 9 / DESIGN_ONLY 6；exit=0"
echo "  $L2/s45.log  PII 0 命中；7 脚本包内优先 7/7；SHA 三方一致"
echo "  $L2/test_g.log  31/31 PASS（起点确认 / 五标识 / 02→03→02 / 续设不归零）"
echo "  $L2/s53.log  L26_eval_L26 M0B 13.57/13.57/9.94/8.00；S8_eval_L26 n=9（D-07 定 10 把）；c28/c29 左截断"
echo "  $L2/s54.log  锁存放行 12 / 滞回 15；TWO_STAGE@θ=0.8,ρ=10 = 0.0810，带病 87"
echo "  $L2/s55.log  跨截断 按行CI窄 1.6 倍；part-prog +0.966 CI[0.35,1.53]"
echo "  $L2/s56.log  code_sha 与磁盘一致；MONITOR_ONLY；reference_rule COUNT_H；lag 可测"
echo "  $L2/s57.log  k=0 → 一律90%恒最优；两档只在 ρ≈1.3~5 有效"
echo "  $L2/test_h.log  22/22 PASS（recv_ts：DDL/迁移/写入/打戳/统计）"
echo "  $L2/s58.log  当前程序还差 N 条历史（P85 需 5 条）"
echo "  $L2/s59.log  负载表 AUC 0.501、峰值倍数 8.3 vs 7.9、采样率衰减 -0.008 → 只否证负载表"
echo "  $L2/s60.log  六通道全阴性，最高 AUC 0.586 → 软件通道到此为止"
echo "  $L2/s61.log  ρ=5: f*=0.54；ρ=1: f*=0.85；最短/M0 与 f* 差 <=0.03"
