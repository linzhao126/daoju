# -*- coding: utf-8 -*-
"""s37_grouping_test.py —— M0 的分组粒度该多细？（32 项对比）

背景
----
用户确认「允许一把刀跨品番」，且实测：
  - 4 个"明显混批"周期的两段负荷比只有 1.01~1.06（全库各程序负荷极差 1.15），
    而单件噪声 σ≈2% → **跨品番对 HI 的影响与噪声同量级**
  - 按品番分组的寿命差异不显著（MWU p=0.143 / Welch p=0.816，均值差 −2.6 件 vs MDE 30.7）

于是问题变成：**M0 该按什么分组？**
  G1  global   不分组（全体其他刀的中位）
  G2  part     按品番（C5G25-02 / -03）
  G3  prog     按程序（11/13/14/16/17/19/20）← 现状

每种再分「含 / 不含 c38+c51」两版（这两把件数分别是台账的 1.93 / 1.70 倍，无采集解释）。

判据：MAE。若 G1 不劣于 G3，说明按程序分组是过拟合（切细了但没换来精度）。
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import bt_core as BT

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
OUT = os.path.join(BASE, '24_程序归属修正')
MERGED = os.path.join(OUT, 'HI_merged_28.csv')
MIXP = os.path.join(BASE, '25_品番混批诊断', 'prog_mix_profile.csv')

K, W = 20, 10
CUTS = [20, 40, 60, 80]
RNG = np.random.default_rng(20261028)
LOG = []

PART = {'11': '02', '13': '02', '14': '02', '16': '03', '17': '03', '19': '03', '20': '03'}


def P(*a):
    s = ' '.join(str(x) for x in a)
    print(s)
    LOG.append(s)


hi = pd.read_csv(MERGED, encoding='utf-8-sig')
mix = pd.read_csv(MIXP, encoding='utf-8-sig').set_index('Cycle')

curves = {}
for (p, c), g in hi.groupby(['prog', 'life_cycle']):
    curves[int(c)] = dict(prog=str(p),
                          hi=g.sort_values('piece_idx')['HI_L_spindle_load'].to_numpy(float))
P('28 周期，%d 件' % sum(len(v['hi']) for v in curves.values()))


def prog_of(grouping, c):
    p = curves[c]['prog']
    if grouping == 'global':
        return 'ALL'
    if grouping == 'part':
        return PART.get(p, '?')
    return p


def rise_of(h, N):
    if N > len(h):
        return np.nan
    seg = h[max(0, N - W):N]
    seg = seg[np.isfinite(seg)]
    return float(np.median(seg) - 1.0) if len(seg) >= 3 else np.nan


def run(grouping, drop=()):
    pool = [c for c in curves if c not in drop]
    life = {c: len(curves[c]['hi']) for c in pool}
    prog = {c: prog_of(grouping, c) for c in pool}
    rows = []
    for N in CUTS:
        RISE = {c: rise_of(curves[c]['hi'], N) for c in pool}
        for k in [c for c in pool if life[c] > N]:
            r = BT.nested_predict(life, prog, k, N, RISE)
            if r is None:
                continue
            rows.append(dict(Grouping=grouping, Drop=','.join(map(str, drop)) or 'none',
                             CutoffN=N, Cycle=k, Life=life[k], ActualRUL=life[k] - N,
                             M0A=r['M0A'], M0B=r['M0B'], M1Old=r['M1Old'], M1New=r['M1New'],
                             TrainN=len(r['S'])))
    R = pd.DataFrame(rows)
    for m in ('M0A', 'M0B', 'M1Old', 'M1New'):
        R['Abs' + m] = (R[m] - R.ActualRUL).abs()
    return R


P('')
P('=' * 96)
P('%-8s %-12s %4s %6s %8s %8s %8s %8s %6s'
  % ('分组', '剔除', 'N', '刀数', 'M0A', 'M0B', 'M1-旧', 'M1-新', '训练池'))
P('-' * 96)
allR = []
for grouping in ('global', 'part', 'prog'):
    for drop in ((), (38, 51)):
        R = run(grouping, drop)
        R['Tag'] = '%s_%s' % (grouping, 'drop' if drop else 'all')
        allR.append(R)
        for N, d in R.groupby('CutoffN'):
            P('%-8s %-12s %4d %6d %8.2f %8.2f %8.2f %8.2f %6.1f'
              % (grouping, ('c38,c51' if drop else '—'), N, len(d),
                 d.AbsM0A.mean(), d.AbsM0B.mean(),
                 d.AbsM1Old.mean(), d.AbsM1New.mean(), d.TrainN.mean()))
        P('')

A = pd.concat(allR, ignore_index=True)
A.to_csv(os.path.join(OUT, 'grouping_test.csv'), index=False, encoding='utf-8-sig')

# ---------- 关键对比：同样本下的 M0-B
P('=' * 96)
P('=== M0-B 最佳分组判定（同样本，只比三组都有的刀）===')
for drop in ((), (38, 51)):
    tag = 'drop_c38c51' if drop else 'all28'
    sub = A[(A.Drop == (','.join(map(str, drop)) if drop else 'none'))]
    piv = sub.pivot_table(index=['Cycle', 'CutoffN'], columns='Grouping', values='AbsM0B')
    piv = piv.dropna()
    P('  [%s] 共有 %d 条预测可比' % (tag, len(piv)))
    for g in ('global', 'part', 'prog'):
        P('     %-8s MAE %.3f' % (g, piv[g].mean()))
    # 配对差（相对 prog）
    for g in ('global', 'part'):
        d = piv[g] - piv['prog']
        ids = piv.reset_index().Cycle
        vals = d.groupby(ids.values).mean()
        bs = [float(vals.sample(len(vals), replace=True, random_state=i).mean()) for i in range(2000)]
        lo, hi = np.percentile(bs, [2.5, 97.5])
        P('     %-8s − prog : %+.3f  CI[%+.3f,%+.3f]  %s'
          % (g, d.mean(), lo, hi, '显著' if (lo > 0 or hi < 0) else '不显著'))
    P('')

# ---------- bootstrap：M1 在最佳分组下是否还有增量
P('=' * 96)
P('=== M1 是否还有增量（相对 M0-B，按刀聚类）===')
boot = []
for tag, R in A.groupby('Tag'):
    for m in ('M1Old', 'M1New'):
        d = R.dropna(subset=['Abs' + m, 'AbsM0B'])
        if not len(d):
            continue
        dif = (d['Abs' + m] - d.AbsM0B).groupby(d.Cycle).mean()
        if len(dif) < 3:
            continue
        bs = [float(dif.sample(len(dif), replace=True, random_state=i).mean()) for i in range(2000)]
        lo, hi = np.percentile(bs, [2.5, 97.5])
        boot.append(dict(Tag=tag, Model=m, n_tools=len(dif), n_rows=len(d),
                         delta=round(float(dif.mean()), 3), lo=round(lo, 3), hi=round(hi, 3),
                         sig=('YES' if (lo > 0 or hi < 0) else 'NO')))
B = pd.DataFrame(boot).sort_values(['Tag', 'Model'])
B.to_csv(os.path.join(OUT, 'grouping_bootstrap.csv'), index=False, encoding='utf-8-sig')
P(B.to_string(index=False))

with open(os.path.join(OUT, 's37_console.txt'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(LOG) + '\n')
print()
print('[out] grouping_test.csv / grouping_bootstrap.csv')
