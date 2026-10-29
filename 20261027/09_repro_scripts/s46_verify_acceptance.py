# -*- coding: utf-8 -*-
"""s46_verify_acceptance.py —— 把 NOT_REPRODUCIBLE 的验收判据做成机检

为什么要有这一步
----------------
2026-10-29 第一次全量跑 `run_all.sh` 时发现：我写的三条判据（`40/40 PASS`、
`4 项 PASS`、`AUC_gap = 0.5599`）**在日志里根本查不到原字符串** —— 那是我概括的
说法，不是脚本实际打印的东西。审核方按文档 grep 会查不到，等于没有判据。

所以把判据全部改成"日志里原样存在的片段"，并固化成这个脚本：
  - 每条判据 = 一个文件 + 一个正则
  - 任一条不命中 → exit 1，run_all 当场失败
  - 输出 acceptance_check.csv，逐条留痕

放在 run_all 的打包之前跑（此时所有日志都是本轮刚生成的）。
"""
import os
import re
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
LOG = os.path.join(BASE, '14_整改执行_v2b', 'logs')
OUT = LOG

# (编号, 文件, 正则, 应看到什么)
CHECKS = [
    ('A-test_a-count', 'test_a.log', r'171（PASS 171 / FAIL 0', '171 用例全过'),
    ('A-test_a-dfit', 'test_a.log', r'最大 \|ΔFitB\| = 0\.000e\+00', '拟合参数零漂移'),
    ('B-test_b', 'test_b.log', r'结论：PASS', 'HI 逐位不变'),
    ('C-test_c', 'test_c.log', r'结论：PASS', '单刃口/多片段/刀龄/暴露 4 项'),
    ('D-test_d', 'test_d.log', r'汇总：9 / 9 PASS', '崩刃截断 9/9'),
    ('D-test_d-dscore', 'test_d.log', r'最大 \|Δscore\|\s+= 0\.000e\+00', '检测分零漂移'),
    ('E-test_e', 'test_e.log', r'汇总：46 / 46 PASS', '换刀指标不依赖 life_cycle'),
    ('F-channel_map', 'channel_map.log', r'12/12 PASS', '通道映射自检'),
    ('G-s41-l', 's41.log', r'3/3 周期有几何段', 'L 通道有段'),
    ('G-s41-r', 's41.log', r'0/3 周期有段', 'R 通道不可移植'),
    ('H-s42', 's42.log', r'可正式验收\s+8\b', '严格可验收 8 个周期'),
    ('I-s30-auc', 's30.log', r'AUC=0\.560\b', '换刀检测 AUC≈随机（非 0.862）'),
    ('J-s44-hard0', 's44.log', r'改动后（线上 \+ 交付副本）HARD 命中：0 处', 'UI 禁用措辞清零'),
    ('J-s44-exit', 's44.log', r'exit=0', 'UI 扫描自身通过'),
    ('K-s45-pii', 's45.log', r'PII 命中 0 处', '台账无个人标识'),
    ('K-s45-path', 's45.log', r'7 脚本包内优先 7/7', '台账路径解耦'),
    ('K-s45-sha', 's45.log', r'一致：是', '台账 SHA256 三方一致'),
    ('L-g0', 'g0_check.log', r'合计：PASS \d+ / WARN \d+ / FAIL 0\b', 'G0 自检无 FAIL（计数不作硬比）'),
    # ---- 2026-10-29 D-04（按主程序计件）：Test-F
    ('M-test_f', 'test_f.log', r'汇总：15 / 15 PASS', '件数口径 15/15'),
    ('M-test_f-107', 'test_f.log', r'used=107', 'c33 的 107 件不被劈成 56'),
    # ---- 2026-10-29 R02
    ('N-s52-flip', 's52.log', r'发生过翻转的周期: 13 / 23', '锁存 vs 复判 13/23 翻转'),
    ('N-s52-trade', 's52.log', r'少超期 95 件，多提前换 392 件', '件数代价可复算'),
    # ---- 2026-10-29 晚新增（R03~R09）
    ('O-test_g', 'test_g.log', r'汇总：31 / 31 PASS', '起点确认 / 五标识 31/31'),
    ('O-s53-s8', 's53.log', r'S8_eval_L26\s+20\s+M0B\s+9\s+9\.44', '严格集可算 9 把（D-07 定 10 把）'),
    ('O-s53-gold', 's53.log', r'±3黄金', 'D-07 黄金样本等级已写入'),
    ('O-s53-c29', 's53.log', r'覆盖截断假阳性', 'c29 判为覆盖截断假阳性'),
    ('O-s53-power', 's53.log', r'注入2_84pct台阶能否认出', '变点检验功效已披露'),
    ('O-s54-grant', 's54.log', r'锁存放行 12 把 / 滞回放行 15 把', '放行数可复算'),
    ('O-s54-hyst', 's54.log', r'0\.0824', '滞回成本高于锁存（撤回方案B）'),
    ('O-s55-cluster', 's55.log', r'跨 4 个截断合并后', '聚类CI已按跨截断重算'),
    ('O-s55-narrow', 's55.log', r'1\.60', '按行CI窄 1.6 倍'),
    ('O-s56-sha', 's56.log', r'policy_mode=MONITOR_ONLY\s+safety_permit=False', '运行版本探针通过'),
    ('O-s57-k0', 's57.log', r'k= 0 → ρ≥0\.05:STATUS_QUO\s+ρ≥0\.25:COUNT_H', 'k=0 时一律90%恒优'),
    ('O-s57-grid', 's57.log', r'决策网格 \(k, ρ\) → 最省钱规则', '双轴决策表已生成'),
    ('O-test_h', 'test_h.log', r'汇总：24 / 24 PASS', 'recv_ts / 改名回归 24/24（N05）'),
    ('O-s58', 's58.log', r'还差 \*\*\d+\*\* 条历史', '样本就绪自检给出缺口'),
    ('O-lag', 's56.log', r'到达延迟 lag：n=\d+', '接口已返回到达延迟'),
    ('O-s59-15', 's59.log', r'32\s+15\s+0\.501\s+0\.460', '采样率实验 15 个破损全过'),
    ('O-s59-null', 's59.log', r'8\.32\s+7\.92', '单窗峰值无特异性已披露'),
    ('O-s60-x', 's60.log', r'L_X_servo_load\s+32\s+15\s+2', '六通道扫描 X 轴已计入'),
    ('O-s60-none', 's60.log', r'没有一个通道通过', '六通道全阴性已披露'),
    ('O-s61-f', 's61.log', r'k=0 → f\*=0\.54', '最优 f 随 rho 变化已披露'),
    ('O-s61-cost', 's61.log', r'60\.9', 'f=0.9 在 rho=5 的代价已披露'),
    # ---- 2026-10-29 N01~N08 新增（审核 0338a5d）
    ('P-n02-test_i', 'test_i.log', r'\[test_i\] PASS=11 FAIL=0', '段/调用数据契约 11/11（N02）'),
    ('P-n03-c3', 's62.log', r"C3 可验收计数： \{'否': 28\}", '逐刃口 C3 真值 28/28 不可得（N03）'),
    ('P-n04-nested', 's63.log', r'argmin 的乐观偏差', '嵌套留一后样本内乐观偏差已量化（N04）'),
    ('P-n06-shadow', 's64.log', r'PASS=14 FAIL=0', '隔离影子安全回归 14 项全过（N06）'),
    ('P-n07-manifest', 's65.log', r'N07_输入清单_SHA\.csv', '输入清单+SHA 已生成（N07）'),
    ('P-n08-register', 's66.log', r'裁决引证登记', '裁决引证登记表与目标声明自检（N08）'),
]

# 仓库级判据（不是日志，是源码本身）：D-04 的红线
# 这些一旦被改回去，本脚本直接失败，不依赖任何人的记忆
REPO_CHECKS = [
    ('R1-d04-mode', '02_源代码与配置/source/server.py',
     r'GROUP_MODE\s*=\s*os\.environ\.get\("GROUP_MODE",\s*"prog"\)',
     '默认口径 = 主程序（D-04）'),
    ('R2-used-no-prog', '02_源代码与配置/source/server.py',
     r'used = c\.execute\("SELECT COUNT\(\*\) FROM pieces WHERE allnum>\?"',
     'used 不计程序过滤（物理件数）'),
    ('R3-gkey-no-prog', '02_源代码与配置/source/server.py',
     r'gkey = "grant_b%d" % base_allnum', 'gkey 不含 prog'),
    ('R5-judge-allowed', '02_源代码与配置/source/server.py',
     r'judge_allowed\s*=\s*bool\(\s*judge_ok\s+and\s+start_confirmed\s*\)',
     'P0-MOD-04 起点确认门禁存在'),
    ('R6-safety-permit', '02_源代码与配置/source/server.py',
     r'SAFETY_PERMIT\s*=\s*False', 'P0-MOD-01 不出安全许可'),
    ('R8-ref-rule', '02_源代码与配置/source/server.py',
     r'REFERENCE_RULE = os\.environ\.get\("REFERENCE_RULE", "COUNT_H"\)',
     'D-05 默认参考线 = 一律 0.9xM0'),
    ('R10-recv-ts', '02_源代码与配置/source/server.py',
     r'ALTER TABLE raw_rows ADD COLUMN recv_ts TEXT', 'R06 P0-a recv_ts 迁移存在'),
    ('R11-lag-fn', '02_源代码与配置/source/server.py',
     r'def batch_recv_lag_stats\(', 'R06 P0-a 延迟统计存在（审核 P0-06 改名）'),
    ('R12-no-oldname', '02_源代码与配置/source/server.py',
     r'(?<!recv_)lag_stats\(', 'P0-06 旧函数名不得再现（命中=FAIL，取反判定）'),
    ('R13-blind-spot', '02_源代码与配置/source/server.py',
     r'n_no_recvts', 'P0-06 盲区计数（recv_ts IS NULL）保留'),
    ('R9-ref-field', '02_源代码与配置/source/server.py',
     r'"ref_limit_90": h', 'D-05 参考线字段已暴露'),
    ('R7-code-sha', '02_源代码与配置/source/server.py',
     r'"code_sha": CODE_SHA', 'R09 运行版本 hash 暴露到 API'),
    ('R4-no-part-default', '02_源代码与配置/source/index.html',
     r'默认按\*\*主程序\*\*', '前端口径注释与后端一致'),
]

rows = []
for cid, fn, pat, want in CHECKS:
    p = os.path.join(LOG, fn)
    if not os.path.exists(p):
        rows.append(dict(ID=cid, File=fn, Pattern=pat, Want=want,
                         Status='MISSING_FILE', Hit=''))
        continue
    txt = open(p, encoding='utf-8', errors='replace').read()
    m = re.search(pat, txt)
    rows.append(dict(ID=cid, File=fn, Pattern=pat, Want=want,
                     Status=('PASS' if m else 'FAIL'),
                     Hit=(m.group(0) if m else '')))
for cid, rel, pat, want in REPO_CHECKS:
    p = os.path.join(BASE, rel)
    if not os.path.exists(p):
        rows.append(dict(ID=cid, File=rel, Pattern=pat, Want=want,
                         Status='MISSING_FILE', Hit=''))
        continue
    txt = open(p, encoding='utf-8', errors='replace').read()
    if cid.startswith('R12-'):                       # 取反类判据：命中=FAIL
        m = re.search(pat, txt)
        rows.append(dict(ID=cid, File=rel, Pattern=pat, Want=want,
                         Status=('FAIL' if m else 'PASS'),
                         Hit=(m.group(0)[:60] if m else '旧名不存在（符合预期）')))
        continue
    m = re.search(pat, txt)
    rows.append(dict(ID=cid, File=rel, Pattern=pat, Want=want,
                     Status=('PASS' if m else 'FAIL'),
                     Hit=(m.group(0)[:60] if m else '')))

R = pd.DataFrame(rows)
R.to_csv(os.path.join(OUT, 'acceptance_check.csv'), index=False, encoding='utf-8-sig')

print('=' * 90)
print('验收判据机检（对照 NOT_REPRODUCIBLE.md「重建验收标准」表）')
print('=' * 90)
print(R[['ID', 'File', 'Status', 'Want']].to_string(index=False))
n_pass = int((R.Status == 'PASS').sum())
n_fail = len(R) - n_pass
print('\n合计：PASS %d / FAIL %d / 共 %d' % (n_pass, n_fail, len(R)))
if n_fail:
    print('\n未命中明细：')
    for _, r in R[R.Status != 'PASS'].iterrows():
        print('  [%s] %s  /%s/  → 期望看到：%s' % (r.Status, r.File, r.Pattern, r.Want))
    print('\n结论：FAIL —— 交付包的可复现判据有一条以上不成立，不得提交')
    sys.exit(1)
print('结论：PASS —— 全部判据均可在日志中原样查到')
