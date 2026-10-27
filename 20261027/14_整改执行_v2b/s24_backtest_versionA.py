# -*- coding: utf-8 -*-
"""s24_backtest_versionA.py — FIX-03/FIX-07：在 Version A（严格样本）上重跑嵌套回测

Version A = 12 把：MinorProgramShare==0 且 DQ∈{A,B} 且 边界闭合
   prog14: c26 c35 c36 c37（4）
   prog20: c28 c29 c30 c31 c32（5）
   prog11: c39 c40 c41（3）

同一套 bt_core.nested_predict；同一套 HI 源（legacy / asofC2）。
输出到 14_整改执行_v2b/metrics/
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import bt_core as BT

HERE = os.path.dirname(__file__)
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))
HI_CSV = os.path.join(ROOT, 'out_T1111', 'xalign_HI.csv')
BASE = os.path.abspath(os.path.join(HERE, '..', '13_整改执行_v2'))
ASOF = os.path.join(BASE, 'hi_asof')
OUT = os.path.abspath(os.path.join(HERE, '..', '14_整改执行_v2b', 'metrics'))
VER_A = os.path.abspath(os.path.join(HERE, '..', '14_整改执行_v2b', 'dataset', 'sample_versions.csv'))
os.makedirs(OUT, exist_ok=True)

K, W = 20, 10
CUTS = [20, 40, 60, 80]
RNG = np.random.default_rng(20261027)
NBOOT = 2000

legacy = {}
for (p, c), g in pd.read_csv(HI_CSV, encoding='utf-8-sig').groupby(['prog', 'life_cycle']):
    legacy[int(c)] = dict(prog=int(p), hi=g.sort_values('piece_idx')['HI_L_spindle_load'].to_numpy(float))

asof = {}
d = pd.read_csv(os.path.join(ASOF, 'hi_asof_long.csv'), encoding='utf-8-sig')
for (mode, N, c), g in d.groupby(['Mode', 'N', 'LifeCycle']):
    asof[(mode, int(N), int(c))] = g.sort_values('PieceIdx')['HI'].to_numpy(float)

va = pd.read_csv(VER_A, encoding='utf-8-sig')
A_IDS = [int(x[-2:]) if x[-3] != 'c' else int(x[-1]) for x in
         va[va.Version == 'A_strict'].Cycles.iloc[0].split(',')]
A_IDS = [int(s.split('-c')[1]) for s in va[va.Version == 'A_strict'].Cycles.iloc[0].split(',')]
B_IDS = [int(s.split('-c')[1]) for s in va[va.Version == 'B_layered'].Cycles.iloc[0].split(',')]

prog_of = {c: v['prog'] for c, v in legacy.items()}
life_all = {c: len(v['hi']) for c, v in legacy.items()}
print('[sample] Version A = %d 把 %s' % (len(A_IDS), A_IDS))
print('[sample] Version B = %d 把' % len(B_IDS))


def rise_of(hi, N):
    if N > len(hi):
        return np.nan
    seg = hi[max(0, N - W):N]
    seg = seg[np.isfinite(seg)]
    return float(np.median(seg) - 1.0) if len(seg) >= 3 else np.nan


def series_for(src, c, N):
    L = life_all[c]
    out = np.full(L, np.nan)
    if src == 'legacy':
        h = legacy[c]['hi']; m = min(len(h), L); out[:m] = h[:m]
        return out
    mode = 'C1' if src == 'asofC1' else 'C2'
    h = asof.get((mode, N, c))
    if h is not None:
        n = min(len(h), L, N); out[:n] = h[:n]
    return out


def run(ids, src):
    LIFE = {c: life_all[c] for c in ids}
    PROG = {c: prog_of[c] for c in ids}
    rows = []
    for N in CUTS:
        RISE = {c: rise_of(series_for(src, c, N), N) for c in ids}
        for k in [c for c in ids if LIFE[c] > N]:
            r = BT.nested_predict(LIFE, PROG, k, N, RISE)
            if r is None:
                continue
            rows.append(dict(Version='A' if ids is A_IDS else 'B', Source=src, CutoffN=N,
                             PhysicalLifeId='4152-L-T1111-c%d' % k, Program=PROG[k],
                             ActualRUL=LIFE[k] - N, Rise=round(RISE[k], 6) if np.isfinite(RISE[k]) else np.nan,
                             FitB=round(r['FitB'], 4) if np.isfinite(r['FitB']) else np.nan,
                             M0A=round(r['M0A'], 2), M0B=round(r['M0B'], 2),
                             M1Old=round(r['M1Old'], 2) if np.isfinite(r['M1Old']) else np.nan,
                             M1New=round(r['M1New'], 2) if np.isfinite(r['M1New']) else np.nan))
    P = pd.DataFrame(rows)
    for m in ('M0A', 'M0B', 'M1Old', 'M1New'):
        P['Abs' + m] = (P[m] - P.ActualRUL).abs()
    return P


def summ(P):
    out = []
    for N, d in P.groupby('CutoffN'):
        for m in ('M0A', 'M0B', 'M1Old', 'M1New'):
            r = d.dropna(subset=[m])
            if not len(r):
                continue
            e = r['Abs' + m]
            out.append(dict(Version=P.Version.iloc[0], Source=P.Source.iloc[0], CutoffN=N, Model=m,
                            n=len(r), MAE=round(e.mean(), 2), MedAE=round(e.median(), 2),
                            Bias=round(float((r[m] - r.ActualRUL).mean()), 2),
                            Over=int((r[m] > r.ActualRUL).sum())))
    return pd.DataFrame(out)


allP, allM = [], []
for src in ('legacy', 'asofC2'):
    for ids, tag in ((A_IDS, 'A'), (B_IDS, 'B')):
        P = run(ids, src)
        P['Version'] = tag
        allP.append(P); allM.append(summ(P))

P = pd.concat(allP)
P.to_csv(os.path.join(OUT, 'predictions_versionA.csv'), index=False, encoding='utf-8-sig')
M = pd.concat(allM)
M.to_csv(os.path.join(OUT, 'metrics_versionA.csv'), index=False, encoding='utf-8-sig')

print('\n=== Version A vs B（asofC2）===')
print(M[(M.Source == 'asofC2')].to_string(index=False))

# 配对差：M1 vs M0B（A 版）
pr = []
for (ver, src, N), d in P.groupby(['Version', 'Source', 'CutoffN']):
    for m in ('M0A', 'M1Old', 'M1New'):
        b = d.dropna(subset=['M0B', m])
        if len(b) < 3:
            continue
        diff = (b['Abs' + m] - b.AbsM0B).to_numpy()
        bs = np.array([RNG.choice(diff, size=len(diff), replace=True).mean() for _ in range(NBOOT)])
        pr.append(dict(Version=ver, Source=src, CutoffN=N, Metric='Delta(%s-M0B)' % m, n=len(diff),
                       Point=round(diff.mean(), 2),
                       CI_lo=round(float(np.percentile(bs, 2.5)), 2),
                       CI_hi=round(float(np.percentile(bs, 97.5)), 2)))
PR = pd.DataFrame(pr)
PR.to_csv(os.path.join(OUT, 'metrics_paired_versionA.csv'), index=False, encoding='utf-8-sig')
print('\n=== Version A 配对差（asofC2，<0 才好）===')
print(PR[(PR.Version == 'A') & (PR.Source == 'asofC2')].to_string(index=False))
