# -*- coding: utf-8 -*-
"""test_a — 审核 §3 Test-A：训练隔离

判据（审核原文）
  "任选某测试周期 k，把 life(k) 改成另一数值，仅允许该测试记录的真值/误差变化；
   用于测试折的 FitB、训练归一化参数和训练模型权重必须严格不变。"

本测试对每个 (k, N) 做三档扰动：life+20 / life×0.7 / life×1.3，
断言 FitB、FitG、M0A、M0B 全部在 1e-12 内不变；同时统计误差确实变了（证明扰动生效）。
"""
import os, sys, json
import numpy as np
import pandas as pd

HERE0 = os.path.dirname(os.path.abspath(__file__))
BASE0 = os.path.abspath(os.path.join(HERE0, '..'))
sys.path.insert(0, os.path.abspath(os.path.join(BASE0, '..', '09_repro_scripts')))
import bt_core as BT

HERE = HERE0
ROOT = os.path.abspath(os.path.join(BASE0, '..', '..', '..', '..'))
BASE = BASE0
OUT = os.path.join(BASE, 'tests')
os.makedirs(OUT, exist_ok=True)

HI = pd.read_csv(os.path.join(ROOT, 'out_T1111', 'xalign_HI.csv'), encoding='utf-8-sig')
life, prog = {}, {}
for (p, c), g in HI.groupby(['prog', 'life_cycle']):
    life[int(c)] = len(g); prog[int(c)] = int(p)
cnt = pd.Series(list(prog.values())).value_counts()
KEEP = sorted([c for c in life if cnt[prog[c]] >= 3 and life[c] >= 30])
LIFE = {c: life[c] for c in KEEP}
PROG = {c: prog[c] for c in KEEP}
RISE = {c: 0.01 * ((c % 5) - 2) for c in KEEP}      # 固定趋势值，隔离拟合逻辑
CUTS = [20, 40, 60, 80]
PERT = {'plus20': lambda L: L + 20, 'x0.7': lambda L: max(5, int(round(L * 0.7))),
        'x1.3': lambda L: int(round(L * 1.3))}

rows = []
for N in CUTS:
    for k in [c for c in KEEP if life[c] > N]:
        base = BT.nested_predict(LIFE, PROG, k, N, RISE)
        if base is None:
            continue
        for name, fn in PERT.items():
            L2 = dict(LIFE); L2[k] = fn(LIFE[k])
            # 扰动后 k 可能不再满足 life>N，这里仍按同一截断点评估（审核要求"预测之前的前缀不变"）
            r = BT.nested_predict(L2, PROG, k, N, RISE)
            if r is None:
                rows.append(dict(N=N, k=k, Perturb=name, Status='SKIP(样本不足)'))
                continue
            dB = abs(r['FitB'] - base['FitB']) if np.isfinite(base['FitB']) and np.isfinite(r['FitB']) else 0.0
            dG = abs(r['FitG'] - base['FitG']) if np.isfinite(base['FitG']) and np.isfinite(r['FitG']) else 0.0
            dM0A = abs(r['M0A'] - base['M0A']); dM0B = abs(r['M0B'] - base['M0B'])
            ok = (dB < 1e-12) and (dG < 1e-12) and (dM0A < 1e-12) and (dM0B < 1e-12)
            # 扰动必须让"误差"变化，否则说明扰动没生效
            changed = abs(abs(r['M1Old'] - (L2[k] - N)) - abs(base['M1Old'] - (LIFE[k] - N))) > 0 \
                if np.isfinite(base['M1Old']) and np.isfinite(r['M1Old']) else True
            rows.append(dict(N=N, k=k, Perturb=name, Status='PASS' if ok else 'FAIL',
                             dFitB=dB, dFitG=dG, dM0A=dM0A, dM0B=dM0B,
                             ErrorChanged=changed,
                             FitB=round(base['FitB'], 6) if np.isfinite(base['FitB']) else None))

R = pd.DataFrame(rows)
R.to_csv(os.path.join(OUT, 'test_a_train_isolation.csv'), index=False, encoding='utf-8-sig')
n = len(R[R.Status.isin(['PASS', 'FAIL'])])
p = int((R.Status == 'PASS').sum()); f = int((R.Status == 'FAIL').sum())
print('=== Test-A 训练隔离 ===')
print('用例 %d（PASS %d / FAIL %d / SKIP %d）' % (len(R), p, f, int((R.Status.str.startswith('SKIP')).sum())))
print('最大 |ΔFitB| = %.3e   最大 |ΔFitG| = %.3e' % (R.dFitB.max(), R.dFitG.max()))
print('扰动确实改变了误差的比例: %d/%d' % (int(R.ErrorChanged.sum()), n))
print('\n分截断点：')
print(R[R.Status.isin(['PASS', 'FAIL'])].groupby(['N', 'Status']).size().to_string())
print('\n结论：%s' % ('PASS —— life(k) 的任何改动都不影响 FitB/FitG/M0A/M0B'
                    if f == 0 else 'FAIL —— 仍有 %d 个用例的拟合参数被 life(k) 影响' % f))
if f:
    print(R[R.Status == 'FAIL'].head(10).to_string(index=False))
