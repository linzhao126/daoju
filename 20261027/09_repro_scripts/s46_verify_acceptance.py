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
    ('L-g0', 'g0_check.log', r'合计：PASS 1 / WARN 1 / FAIL 0', 'G0 自检无 FAIL'),
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
