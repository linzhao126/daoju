# -*- coding: utf-8 -*-
"""s21_backtest_v3.py — FIX-01（嵌套留一）+ FIX-02（因果 HI）消融回测

三套 HI 源，把两类改动拆开
  legacy : out_T1111/xalign_HI.csv           历史口径（2026-10-12 版流水线生成）
  asofC1 : hi_asof Mode=C1（自观测网格）       当前代码 + 因果截断
  asofC2 : hi_asof Mode=C2（冻结 19-40 网格）  与历史网格同口径 + 因果截断

四个模型全部走 bt_core.nested_predict（单一实现，Test-A 直接验证它）
  M0-A / M0-B / M1-old / M1-new
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import bt_core as BT

HERE = os.path.dirname(__file__)
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import wsroot as _wsroot          # G2：不再数 '..'，按工作区特征向上定位
ROOT = _wsroot.find(_os.path.dirname(_os.path.abspath(__file__)))
HI_CSV = os.path.join(ROOT, 'out_T1111', 'xalign_HI.csv')
BASE = os.path.abspath(os.path.join(HERE, '..', '13_整改执行_v2'))
ASOF = os.path.join(BASE, 'hi_asof')
OUTP = os.path.join(BASE, 'predictions_v2')
OUTM = os.path.join(BASE, 'metrics_v2')

K, W = 20, 10
CUTS = [20, 40, 60, 80]
RNG = np.random.default_rng(20261027)
NBOOT = 2000

legacy = {}
for (p, c), g in pd.read_csv(HI_CSV, encoding='utf-8-sig').groupby(['prog', 'life_cycle']):
    g = g.sort_values('piece_idx')
    legacy[int(c)] = dict(prog=int(p), hi=g['HI_L_spindle_load'].to_numpy(float))

asof = {}
d = pd.read_csv(os.path.join(ASOF, 'hi_asof_long.csv'), encoding='utf-8-sig')
for (mode, N, c), g in d.groupby(['Mode', 'N', 'LifeCycle']):
    asof[(mode, int(N), int(c))] = g.sort_values('PieceIdx')['HI'].to_numpy(float)

prog_of = {c: v['prog'] for c, v in legacy.items()}
life = {c: len(v['hi']) for c, v in legacy.items()}
cnt = pd.Series(list(prog_of.values())).value_counts()
KEEP = sorted([c for c in legacy if cnt[prog_of[c]] >= 3 and life[c] >= max(K, W) + 5])
LIFE = {c: life[c] for c in KEEP}
PROG = {c: prog_of[c] for c in KEEP}
print('[sample] legacy 周期 %d → 纳入 %d；件数合计 %d' % (len(legacy), len(KEEP), sum(LIFE.values())))


def series_for(src, c, N):
    L = life[c]
    out = np.full(L, np.nan)
    if src == 'legacy':
        h = legacy[c]['hi']
        m = min(len(h), L)
        out[:m] = h[:m]
        return out
    mode = 'C1' if src == 'asofC1' else 'C2'
    h = asof.get((mode, N, c))
    if h is not None:
        n = min(len(h), L, N)
        out[:n] = h[:n]
    return out


def rise_of(hi, N):
    """第 N-W..N-1 件 HI 中位 - 1。没活到 N 的刀返回 NaN（因果口径的必然）"""
    if N > len(hi):
        return np.nan
    seg = hi[max(0, N - W):N]
    seg = seg[np.isfinite(seg)]
    return float(np.median(seg) - 1.0) if len(seg) >= 3 else np.nan


def run_source(src):
    rows = []
    for N in CUTS:
        RISE = {c: rise_of(series_for(src, c, N), N) for c in KEEP}
        for k in [c for c in KEEP if life[c] > N]:
            r = BT.nested_predict(LIFE, PROG, k, N, RISE)
            if r is None:
                continue
            rows.append(dict(Source=src, CutoffN=N, PhysicalLifeId='4152-L-T1111-c%d' % k,
                             Program=PROG[k],
                             TrainLifeIds=','.join('c%d' % o for o in r['S']),
                             TrainN=len(r['S']), n_fit=r['n_fit'],
                             Life=LIFE[k], ActualRUL=LIFE[k] - N,
                             Rise=round(RISE[k], 6) if np.isfinite(RISE[k]) else np.nan,
                             FitB=round(r['FitB'], 4) if np.isfinite(r['FitB']) else np.nan,
                             FitG=round(r['FitG'], 4) if np.isfinite(r['FitG']) else np.nan,
                             M0A=round(r['M0A'], 2), M0B=round(r['M0B'], 2),
                             M1Old=round(r['M1Old'], 2) if np.isfinite(r['M1Old']) else np.nan,
                             M1New=round(r['M1New'], 2) if np.isfinite(r['M1New']) else np.nan,
                             PI_lo=round(r['PI_lo'], 2) if np.isfinite(r['PI_lo']) else np.nan,
                             PI_hi=round(r['PI_hi'], 2) if np.isfinite(r['PI_hi']) else np.nan))
    P = pd.DataFrame(rows)
    for m in ('M0A', 'M0B', 'M1Old', 'M1New'):
        P['Abs' + m] = (P[m] - P.ActualRUL).abs()
    P['InPI_M0B'] = ((P.PI_lo <= P.ActualRUL) & (P.ActualRUL <= P.PI_hi)).astype('Int64')
    return P


def summ(P):
    out = []
    for N, d in P.groupby('CutoffN'):
        for m in ('M0A', 'M0B', 'M1Old', 'M1New'):
            r = d.dropna(subset=[m])
            if not len(r):
                continue
            e = r['Abs' + m]
            out.append(dict(Source=P.Source.iloc[0], CutoffN=N, Model=m, n=len(r),
                            MAE=round(e.mean(), 2), MedAE=round(e.median(), 2),
                            RMSE=round(float(np.sqrt((e ** 2).mean())), 2),
                            Bias=round(float((r[m] - r.ActualRUL).mean()), 2),
                            Over=int((r[m] > r.ActualRUL).sum()),
                            OverAmt=round(float((r[m] - r.ActualRUL).clip(lower=0).mean()), 2)))
    return pd.DataFrame(out)


os.makedirs(OUTP, exist_ok=True)
os.makedirs(OUTM, exist_ok=True)
allP, allM = [], []
for src in ('legacy', 'asofC1', 'asofC2'):
    P = run_source(src)
    P.to_csv(os.path.join(OUTP, 'pred_%s.csv' % src), index=False, encoding='utf-8-sig')
    M = summ(P)
    allP.append(P)
    allM.append(M)
    print('\n[%s] %d 行' % (src, len(P)))
    print(M.to_string(index=False))

AB = pd.concat(allM, ignore_index=True)
AB.to_csv(os.path.join(OUTM, 'metrics_ablation.csv'), index=False, encoding='utf-8-sig')

# ---------------- 同样本对比（只保留四个模型都有定义的刀）
ss = []
for (src, N), d in pd.concat(allP).groupby(['Source', 'CutoffN']):
    d2 = d.dropna(subset=['M0B', 'M1Old', 'M1New'])
    if not len(d2):
        continue
    for m in ('M0A', 'M0B', 'M1Old', 'M1New'):
        e = d2['Abs' + m]
        ss.append(dict(Source=src, CutoffN=N, Model=m, n_same=len(d2),
                       Programs=','.join(sorted(d2.Program.astype(str).unique())),
                       MAE=round(e.mean(), 2), MedAE=round(e.median(), 2),
                       Bias=round(float((d2[m] - d2.ActualRUL).mean()), 2),
                       Over=int((d2[m] > d2.ActualRUL).sum()),
                       PI_Cover=round(float(d2.InPI_M0B.mean()), 3) if m == 'M0B' else None,
                       PI_Note='' if m == 'M0B' else 'PI_NOT_DEFINED(未校准，不套用M0-B区间)'))
SS = pd.DataFrame(ss)
SS.to_csv(os.path.join(OUTM, 'metrics_samesample_v2.csv'), index=False, encoding='utf-8-sig')
print('\n=== 同样本对比（n_same 见列）===')
print(SS[SS.Source == 'asofC2'].to_string(index=False))

# ---------------- 配对 bootstrap（相对 M0-B，同一批刀）
pr = []
ALLP = pd.concat(allP)
for (src, N), d in ALLP.groupby(['Source', 'CutoffN']):
    for m in ('M0A', 'M1Old', 'M1New'):
        base = d.dropna(subset=['M0B', m])
        if len(base) < 3:
            continue
        diff = (base['Abs' + m] - base.AbsM0B).to_numpy()
        bs = np.array([RNG.choice(diff, size=len(diff), replace=True).mean() for _ in range(NBOOT)])
        pr.append(dict(Source=src, CutoffN=N, Metric='DeltaMAE(%s-M0B)' % m, n=len(diff),
                       Point=round(diff.mean(), 2),
                       CI_lo=round(float(np.percentile(bs, 2.5)), 2),
                       CI_hi=round(float(np.percentile(bs, 97.5)), 2)))
PR = pd.DataFrame(pr)
PR.to_csv(os.path.join(OUTM, 'metrics_paired_v2.csv'), index=False, encoding='utf-8-sig')
print('\n=== 相对 M0-B 的配对差（<0 才好） ===')
print(PR.to_string(index=False))

# ---------------- 泄漏修复影响（legacy 口径，扁平 vs 嵌套）
flat = pd.read_csv(os.path.abspath(os.path.join(HERE, '..', '04_RUL复现实验',
                                                'predictions_loco_baseline.csv')), encoding='utf-8-sig')
new = ALLP[ALLP.Source == 'legacy']
j = flat.merge(new, on=['PhysicalLifeId', 'CutoffN'], suffixes=('_flat', '_nested'))
j['dM1'] = j.AbsErr_M1 - j.AbsM1Old
j['dM0'] = j.AbsErr_M0 - j.AbsM0A
LK = j.groupby('CutoffN').agg(n=('dM1', 'size'),
                              MAE_M1_flat=('AbsErr_M1', 'mean'),
                              MAE_M1_nested=('AbsM1Old', 'mean'),
                              DeltaM1=('dM1', 'mean'),
                              MAE_M0_flat=('AbsErr_M0', 'mean'),
                              MAE_M0_nested=('AbsM0A', 'mean'),
                              DeltaM0=('dM0', 'mean')).round(2).reset_index()
LK.to_csv(os.path.join(OUTM, 'leak_impact.csv'), index=False, encoding='utf-8-sig')
print('\n=== 泄漏修复影响（legacy 口径，逐刀配对） ===')
print(LK.to_string(index=False))
print('\n[out] metrics_ablation.csv / metrics_paired_v2.csv / leak_impact.csv')
