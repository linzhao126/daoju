# -*- coding: utf-8 -*-
"""test_b — 审核 §3 Test-B：时间截断因果性

判据（审核原文）
  "固定某刀前 N 件所有原始行，替换后 N 件为不同曲线或直接删除；
   以第 N 件预测时的相位网格、特征、HI、M0 与 RUL 都应完全相同。"

做法：对 3 个代表周期 × 4 个截断点 × 2 种扰动
  del    ：删掉第 N 件之后所有件
  random ：把第 N 件之后所有件的负载替换为随机数
断言 z0 / grid_lo / grid_hi / HI 序列 逐位相同（atol=0）。
"""
import os, sys
import numpy as np
import pandas as pd

HERE0 = os.path.dirname(os.path.abspath(__file__))
BASE0 = os.path.abspath(os.path.join(HERE0, '..'))
sys.path.insert(0, os.path.abspath(os.path.join(BASE0, '..', '09_repro_scripts')))
import hi_asof as H

HERE = os.path.dirname(os.path.dirname(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))
RAW = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv')
OUT = os.path.join(BASE0, 'tests')
os.makedirs(OUT, exist_ok=True)

CYCLES = [25, 26, 38]
NS = [20, 40, 60, 80]
MODES = ['C1', 'C2']
RNG = np.random.default_rng(20261027)

idx = H.build_piece_index(RAW, cycles=CYCLES)
print('[idx] 周期 %s' % {c: len(v['pieces']) for c, v in idx.items()})

rows = []
for c in CYCLES:
    pieces, allrows = idx[c]['pieces'], idx[c]['rows']
    for N in NS:
        if len(pieces) <= N:
            continue
        for mode in MODES:
            # 基准：完整数据，但只用前 N 件
            base = H.hi_asof(pieces, allrows, N, mode)
            if not base.get('ok'):
                rows.append(dict(Cycle=c, N=N, Mode=mode, Perturb='-', Status='SKIP', Reason=base.get('reason')))
                continue
            # 扰动 1：删除尾部件
            p1 = pieces[:N]
            r1 = hi_asof_trunc = H.hi_asof(p1, allrows, N, mode)
            # 扰动 2：尾部负载随机化
            all2 = {a: q.copy() for a, q in allrows.items()}
            for a in pieces[N:]:
                q = all2[a]
                q[H.METRIC] = RNG.normal(500, 300, len(q))
                all2[a] = q
            r2 = H.hi_asof(pieces, all2, N, mode)
            for tag, r in (('del', r1), ('random', r2)):
                if not r.get('ok'):
                    rows.append(dict(Cycle=c, N=N, Mode=mode, Perturb=tag, Status='FAIL',
                                     Reason='扰动后不可算: %s' % r.get('reason')))
                    continue
                same = (abs(r['grid_lo'] - base['grid_lo']) < 1e-12 and
                        abs(r['grid_hi'] - base['grid_hi']) < 1e-12 and
                        abs(r['z0'] - base['z0']) < 1e-12 and
                        np.array_equal(np.asarray(r['hi'], float), np.asarray(base['hi'], float)))
                rows.append(dict(Cycle=c, N=N, Mode=mode, Perturb=tag,
                                 Status='PASS' if same else 'FAIL',
                                 Pieces=len(base['hi']),
                                 dGridLo=float(abs(r['grid_lo'] - base['grid_lo'])),
                                 dGridHi=float(abs(r['grid_hi'] - base['grid_hi'])),
                                 dHImax=float(np.nanmax(np.abs(np.asarray(r['hi'], float) -
                                                               np.asarray(base['hi'], float))))
                                 if len(r['hi']) == len(base['hi']) else np.inf))

R = pd.DataFrame(rows)
R.to_csv(os.path.join(OUT, 'test_b_causality.csv'), index=False, encoding='utf-8-sig')
n = int(R.Status.isin(['PASS', 'FAIL']).sum())
p = int((R.Status == 'PASS').sum()); f = int((R.Status == 'FAIL').sum())
print('\n=== Test-B 时间截断因果性 ===')
print('用例 %d（PASS %d / FAIL %d / SKIP %d）' % (len(R), p, f, int((R.Status == 'SKIP').sum())))
if n:
    print('最大 |Δgrid_lo| = %.3e   |Δgrid_hi| = %.3e   |ΔHI|max = %.3e'
          % (R.dGridLo.fillna(0).max(), R.dGridHi.fillna(0).max(), R.dHImax.fillna(0).max()))
print('\n分周期×模式：')
print(R[R.Status.isin(['PASS', 'FAIL'])].groupby(['Cycle', 'Mode', 'Status']).size().to_string())
print('\n结论：%s' % ('PASS —— 删/改第 N 件之后的任何数据，前 N 件的 HI 逐位不变'
                    if f == 0 else 'FAIL —— %d 个用例的 HI 被未来数据影响' % f))
if f:
    print(R[R.Status == 'FAIL'].head(10).to_string(index=False))
