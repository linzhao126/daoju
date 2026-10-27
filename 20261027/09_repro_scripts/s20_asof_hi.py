# -*- coding: utf-8 -*-
"""s20_asof_hi.py — FIX-02 执行：为 17 个周期生成 as_of_N 的 HI（C1/C2 两模式）

输出（13_整改执行_v2/hi_asof/）
  hi_asof_long.csv     每 (life, mode, N, piece) 一行
  hi_grid_drift.csv    每 (life, mode, N) 一行的 z0 / grid / 覆盖率
  hi_full_vs_asof.csv  同一件 HI_full 与 HI_asof_N 的差异
  hi_validate.csv      C1@full 与既有 xalign_HI.csv 的一致性（实现是否等价）
"""
import os, sys, json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import hi_asof as H

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
RAW = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv')
HI_CSV = os.path.join(ROOT, 'out_T1111', 'xalign_HI.csv')
OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '13_整改执行_v2', 'hi_asof'))
NS = [20, 40, 60, 80]
MODES = ['C1', 'C2']

os.makedirs(OUT, exist_ok=True)
idx = H.build_piece_index(RAW)
print('[idx] %d 个周期，件数合计 %d' % (len(idx), sum(len(v['pieces']) for v in idx.values())))

CYCLES = sorted([c for c, v in idx.items() if len(v['pieces']) >= 20])
print('[sel] 可算周期 %d：%s' % (len(CYCLES), CYCLES))

long_rows, grid_rows, val_rows = [], [], []
full_hi = {}
full_rows = []

for c in CYCLES:
    pieces, rows = idx[c]['pieces'], idx[c]['rows']
    z0, segs = H.compute_segments(pieces, rows)
    if not segs:
        print('  [skip] c%d 无有效段' % c); continue
    # full = 用全部可用件（C1）
    rf = H.hi_from_segments(segs, len(segs), 'C1')
    if rf.get('ok'):
        full_hi[c] = dict(allnums=rf['allnums'], hi=rf['hi'], grid_lo=rf['grid_lo'], grid_hi=rf['grid_hi'])
    for mode in MODES:
        for N in NS + [len(segs)]:
            r = H.hi_from_segments(segs, N, mode)
            if not r.get('ok'):
                continue
            tag = 'full' if N >= len(segs) else str(N)
            grid_rows.append(dict(LifeCycle=c, Mode=mode, N=tag, Pieces=len(r['allnums']),
                                  Z0=round(float(z0), 3), GridLo=r['grid_lo'], GridHi=r['grid_hi'],
                                  GridSpan=round(r['grid_hi'] - r['grid_lo'], 3),
                                  GridUsed=r['n_grid_used'], NSeg=len(segs),
                                  Dropped=len(segs) - len(r['allnums'])))
            if tag != 'full':
                for i, (a, h) in enumerate(zip(r['allnums'], r['hi'])):
                    long_rows.append(dict(LifeCycle=c, Mode=mode, N=int(tag), PieceIdx=i + 1,
                                          AllNum=a, HI=round(float(h), 6)))
            else:
                for i, (a, h) in enumerate(zip(r['allnums'], r['hi'])):
                    full_rows.append(dict(LifeCycle=c, Mode=mode, PieceIdx=i + 1,
                                          AllNum=a, HI=round(float(h), 6)))
    # 校验：C1@full 与既有 CSV
    if c in full_hi:
        pass

L = pd.DataFrame(long_rows)
G = pd.DataFrame(grid_rows)
L.to_csv(os.path.join(OUT, 'hi_asof_long.csv'), index=False, encoding='utf-8-sig')
G.to_csv(os.path.join(OUT, 'hi_grid_drift.csv'), index=False, encoding='utf-8-sig')
F = pd.DataFrame(full_rows)
F.to_csv(os.path.join(OUT, 'hi_full_by_mode.csv'), index=False, encoding='utf-8-sig')
print('[out] hi_asof_long.csv %d 行 / hi_grid_drift.csv %d 行 / hi_full_by_mode.csv %d 行'
      % (L.shape[0], G.shape[0], F.shape[0]))

# ---- 校验：C1@full vs 既有 xalign_HI（若实现等价，逐件应≈1）
old = pd.read_csv(HI_CSV, encoding='utf-8-sig')
v = []
for c, d in full_hi.items():
    ref = old[old.life_cycle == c].sort_values('piece_idx')
    if not len(ref):
        continue
    n = min(len(ref), len(d['hi']))
    a = ref['HI_L_spindle_load'].to_numpy(float)[:n]
    b = np.asarray(d['hi'], float)[:n]
    ok = np.isfinite(a) & np.isfinite(b)
    cc = float(np.corrcoef(a[ok], b[ok])[0, 1]) if ok.sum() > 3 else np.nan
    med = float(np.nanmedian(np.abs(b[ok] - a[ok])))
    v.append(dict(LifeCycle=c, N_old=len(ref), N_new=len(d['hi']), N_cmp=int(ok.sum()),
                  Corr=round(cc, 4), MedAbsDiff=round(med, 5),
                  GridLo=round(d['grid_lo'], 3), GridHi=round(d['grid_hi'], 3),
                  Note='实现是否等价' if cc > 0.999 else '存在实现差异'))
V = pd.DataFrame(v)
V.to_csv(os.path.join(OUT, 'hi_validate.csv'), index=False, encoding='utf-8-sig')
print('\n=== C1@full 与既有 xalign_HI 的一致性 ===')
print(V.to_string(index=False))

# ---- full vs as_of 差异
fv = []
for c, d in full_hi.items():
    base = pd.Series(d['hi'], index=d['allnums'])
    for mode in MODES:
        for N in NS:
            s = L[(L.LifeCycle == c) & (L.Mode == mode) & (L.N == N)]
            if not len(s):
                continue
            a = s.set_index('AllNum')['HI']
            j = pd.concat([base.rename('HI_full'), a.rename('HI_asof')], axis=1, join='inner').dropna()
            if len(j) < 5:
                continue
            fv.append(dict(LifeCycle=c, Mode=mode, N=N, Pieces=len(j),
                           Corr=round(float(j.HI_full.corr(j.HI_asof)), 4),
                           MedAbsDiff=round(float((j.HI_asof - j.HI_full).abs().median()), 5),
                           MeanDiff=round(float((j.HI_asof - j.HI_full).mean()), 5)))
FV = pd.DataFrame(fv)
FV.to_csv(os.path.join(OUT, 'hi_full_vs_asof.csv'), index=False, encoding='utf-8-sig')
print('\n=== HI_full 与 HI_asof 的差异（按模式/截断点汇总）===')
print(FV.groupby(['Mode', 'N'])[['Corr', 'MedAbsDiff', 'MeanDiff']].mean().round(4).to_string())

print('\n=== 网格漂移（每个周期 4 个 N 的 GridLo/GridHi）===')
piv = G[G.Mode == 'C1'].pivot_table(index='LifeCycle', columns='N', values='GridLo')
print(piv.round(2).to_string())
