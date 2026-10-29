# -*- coding: utf-8 -*-
"""s36_backtest_28.py —— 28 周期回测 + 程序标签修正的分离效应

三组对照（同一份 HI 源 = xalign 口径，唯一覆盖 28 周期的）
  A  legacy17_oldprog  17 周期 / 旧 prog        ← 13-00 的 baseline，用于确认本脚本复现成功
  B  merged28_oldprog  28 周期 / 旧 prog        ← 只加样本，不修标签
  C  merged28_newprog  28 周期 / 修正 prog      ← 正解

为什么要分三组
  A→B 只回答"样本翻倍有没有用"；B→C 只回答"标签修正有没有用"。
  如果只报 A→C，两个因素会混在一起，无法归因。

修正的来源：s35_prog_relabel.py（prog = z0 带内行数最多的程序）
  c27 14→20 / c33 20→19 / c34 19→14 / c49 17→11 / c57 17→16 / c58 16→13 / c59 13→14

⚠ bootstrap 按 PhysicalLifeId 聚类（审核 P1-03）：
   同一把刀在 4 个截断点各出现一次，不是 4 个独立样本。按刀重采样后再汇总。

输出：24_程序归属修正/
  pred_{A,B,C}.csv / metrics_{A,B,C}.csv / compare_28.csv / bootstrap_cluster.csv
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import bt_core as BT

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))
BASE = os.path.abspath(os.path.join(HERE, '..'))
OUT = os.path.join(BASE, '24_程序归属修正')
OLD_HI = os.path.join(ROOT, 'out_T1111', 'xalign_HI.csv')
MERGED = os.path.join(OUT, 'HI_merged_28.csv')
RELABEL = os.path.join(OUT, 'prog_relabel.csv')

K, W = 20, 10
CUTS = [20, 40, 60, 80]
RNG = np.random.default_rng(20261028)
NBOOT = 4000
LOG = []


def P(*a):
    s = ' '.join(str(x) for x in a)
    print(s)
    LOG.append(s)


# ---------------- 构造三组数据
old = pd.read_csv(OLD_HI, encoding='utf-8-sig')
new = pd.read_csv(MERGED, encoding='utf-8-sig')

# A：旧 17 周期，prog 用 xalign_HI.csv 原值（= 首行判定）
A_hi = {}
for (p, c), g in old.groupby(['prog', 'life_cycle']):
    A_hi[int(c)] = dict(prog=int(p), hi=g.sort_values('piece_idx')['HI_L_spindle_load'].to_numpy(float))

# B：28 周期，prog 用 HI_merged_28 里的 prog_old（= 首行判定原值）
B_hi = {}
for (p, c), g in new.groupby(['prog_old', 'life_cycle']):
    B_hi[int(c)] = dict(prog=str(p), hi=g.sort_values('piece_idx')['HI_L_spindle_load'].to_numpy(float))

# C：28 周期，prog 用修正值
C_hi = {}
for (p, c), g in new.groupby(['prog', 'life_cycle']):
    C_hi[int(c)] = dict(prog=str(p), hi=g.sort_values('piece_idx')['HI_L_spindle_load'].to_numpy(float))


def keep_of(d):
    life = {c: len(v['hi']) for c, v in d.items()}
    prog = {c: v['prog'] for c, v in d.items()}
    cnt = pd.Series(list(prog.values())).value_counts()
    k = sorted([c for c in d if cnt[prog[c]] >= 3 and life[c] >= max(K, W) + 5])
    return k, life, prog


def rise_of(hi, N):
    if N > len(hi):
        return np.nan
    seg = hi[max(0, N - W):N]
    seg = seg[np.isfinite(seg)]
    return float(np.median(seg) - 1.0) if len(seg) >= 3 else np.nan


def run(tag, d, exclude=()):
    d = {c: v for c, v in d.items() if c not in exclude}
    KEEP, LIFE, PROG = keep_of(d)
    P('[%s] 周期 %d → 纳入 %d；件数 %d；程序分布 %s'
      % (tag, len(d), len(KEEP), sum(LIFE[c] for c in KEEP),
         dict(pd.Series([PROG[c] for c in KEEP]).value_counts())))
    rows = []
    for N in CUTS:
        RISE = {c: rise_of(d[c]['hi'], N) for c in KEEP}
        for k in [c for c in KEEP if LIFE[c] > N]:
            r = BT.nested_predict(LIFE, PROG, k, N, RISE)
            if r is None:
                continue
            rows.append(dict(Tag=tag, CutoffN=N,
                             PhysicalLifeId='4152-L-T1111-c%d' % k,
                             Program=PROG[k],
                             TrainIds=','.join('c%d' % o for o in r['S']),
                             TrainN=len(r['S']),
                             Life=LIFE[k], ActualRUL=LIFE[k] - N,
                             Rise=round(RISE[k], 6) if np.isfinite(RISE[k]) else np.nan,
                             FitB=round(r['FitB'], 4) if np.isfinite(r['FitB']) else np.nan,
                             M0A=round(r['M0A'], 2), M0B=round(r['M0B'], 2),
                             M1Old=round(r['M1Old'], 2) if np.isfinite(r['M1Old']) else np.nan,
                             M1New=round(r['M1New'], 2) if np.isfinite(r['M1New']) else np.nan,
                             PI_lo=round(r['PI_lo'], 2) if np.isfinite(r['PI_lo']) else np.nan,
                             PI_hi=round(r['PI_hi'], 2) if np.isfinite(r['PI_hi']) else np.nan))
    R = pd.DataFrame(rows)
    for m in ('M0A', 'M0B', 'M1Old', 'M1New'):
        R['Abs' + m] = (R[m] - R.ActualRUL).abs()
    R['InPI'] = ((R.PI_lo <= R.ActualRUL) & (R.ActualRUL <= R.PI_hi)).astype('Int64')
    R.to_csv(os.path.join(OUT, 'pred_%s.csv' % tag), index=False, encoding='utf-8-sig')
    return R


def summ(R):
    out = []
    for N, d in R.groupby('CutoffN'):
        for m in ('M0A', 'M0B', 'M1Old', 'M1New'):
            r = d.dropna(subset=[m])
            if not len(r):
                continue
            e = r['Abs' + m]
            out.append(dict(Tag=R.Tag.iloc[0], CutoffN=N, Model=m, n=len(r),
                            MAE=round(e.mean(), 2), MedAE=round(e.median(), 2),
                            Bias=round(float((r[m] - r.ActualRUL).mean()), 2),
                            Over=int((r[m] > r.ActualRUL).sum()),
                            Cover=round(float(r.InPI.mean()), 3) if m == 'M0B' else None))
    return pd.DataFrame(out)


P('=== 三组对照 ===')
PA = run('A_legacy17_oldprog', A_hi)
PB = run('B_merged28_oldprog', B_hi)
PC = run('C_merged28_newprog', C_hi)
P('')

# D 组：C 组基础上剔除「件数/台账刀寿 偏差 >50%」的周期
#   c38(197/102=1.93)、c51(170/100=1.70) —— 件数"超出"，无采集解释
#   c25(0.43 左截断)、c41(0.47 提前停)、c57(0.43 缺前段) —— 件数"缺失"，有采集解释
D1_EXCL = (38, 51)
D2_EXCL = (25, 38, 41, 51, 57)
PD1 = run('D1_drop_c38_c51', C_hi, D1_EXCL)
PD2 = run('D2_drop_all5', C_hi, D2_EXCL)

MA, MB, MC = summ(PA), summ(PB), summ(PC)
MD1, MD2 = summ(PD1), summ(PD2)
ALL = pd.concat([MA, MB, MC, MD1, MD2], ignore_index=True)
ALL.to_csv(os.path.join(OUT, 'metrics_ABC.csv'), index=False, encoding='utf-8-sig')

P('=== MAE 对照（同截断点同模型）===')
P('%-4s %-6s %10s %10s %10s %10s %10s'
  % ('N', '模型', 'A:17旧', 'B:28旧', 'C:28新', 'D1:C去c38c51', 'D2:C去5个'))
for N in CUTS:
    for m in ('M0A', 'M0B', 'M1Old', 'M1New'):
        a = MA[(MA.CutoffN == N) & (MA.Model == m)]
        b = MB[(MB.CutoffN == N) & (MB.Model == m)]
        c = MC[(MC.CutoffN == N) & (MC.Model == m)]
        va = float(a.MAE.iloc[0]) if len(a) else np.nan
        vb = float(b.MAE.iloc[0]) if len(b) else np.nan
        vc = float(c.MAE.iloc[0]) if len(c) else np.nan
        d1 = MD1[(MD1.CutoffN == N) & (MD1.Model == m)]
        d2 = MD2[(MD2.CutoffN == N) & (MD2.Model == m)]
        P('%-4d %-6s %10s %10s %10s %16s %16s'
          % (N, m,
             ('%.2f(%d)' % (va, a.n.iloc[0])) if len(a) else '—',
             ('%.2f(%d)' % (vb, b.n.iloc[0])) if len(b) else '—',
             ('%.2f(%d)' % (vc, c.n.iloc[0])) if len(c) else '—',
             ('%.2f(%d)' % (float(d1.MAE.iloc[0]), d1.n.iloc[0])) if len(d1) else '—',
             ('%.2f(%d)' % (float(d2.MAE.iloc[0]), d2.n.iloc[0])) if len(d2) else '—'))
    P('')

# ---------------- 配对 bootstrap（按 PhysicalLifeId 聚类）
P('=== 配对 bootstrap（C 组，相对 M0-B；按刀聚类重采样）===')


def cluster_boot(R, model, nboot=NBOOT):
    d = R[['PhysicalLifeId', 'CutoffN', 'AbsM0B', 'Abs' + model, model, 'ActualRUL']].dropna(
        subset=['Abs' + model, 'AbsM0B'])
    if not len(d):
        return None
    ids = d.PhysicalLifeId.unique()
    dif = (d['Abs' + model] - d.AbsM0B).groupby(d.PhysicalLifeId).mean()
    obs = float(dif.mean())
    vals = []
    for _ in range(nboot):
        s = RNG.choice(ids, len(ids), replace=True)
        vals.append(float(dif.reindex(s).mean()))
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return dict(model=model, n_tools=len(ids), n_rows=len(d), delta=round(obs, 3),
                lo=round(lo, 3), hi=round(hi, 3),
                sig=('YES' if (lo > 0 or hi < 0) else 'NO'))


boot = []
for tag, R in (('A', PA), ('B', PB), ('C', PC), ('D1', PD1), ('D2', PD2)):
    for m in ('M0A', 'M1Old', 'M1New'):
        r = cluster_boot(R, m)
        if r:
            r['Tag'] = tag
            boot.append(r)
            P('  [%s] %-6s n刀=%d n行=%d  ΔMAE(vs M0B)=%+.3f  CI[%+.3f,%+.3f]  显著=%s'
              % (tag, m, r['n_tools'], r['n_rows'], r['delta'], r['lo'], r['hi'], r['sig']))
B = pd.DataFrame(boot)[['Tag', 'model', 'n_tools', 'n_rows', 'delta', 'lo', 'hi', 'sig']]
B.to_csv(os.path.join(OUT, 'bootstrap_cluster.csv'), index=False, encoding='utf-8-sig')
P('')

# ---------------- 同样本对比（C 组，四模型都有定义的刀）
P('=== C 组同样本对比（只保留四模型都有定义的刀）===')
for N, d in PC.groupby('CutoffN'):
    d2 = d.dropna(subset=['M0B', 'M1Old', 'M1New'])
    if not len(d2):
        continue
    P('  N=%-3d n=%-3d  M0A %.2f / M0B %.2f / M1Old %.2f / M1New %.2f | 程序 %s'
      % (N, len(d2), d2.AbsM0A.mean(), d2.AbsM0B.mean(),
         d2.AbsM1Old.mean(), d2.AbsM1New.mean(),
         ','.join(sorted(d2.Program.astype(str).unique()))))

# ---------------- 区间覆盖
P('')
P('=== M0-B 区间覆盖率（名义 80%）===')
for tag, R in (('A', PA), ('B', PB), ('C', PC), ('D1', PD1), ('D2', PD2)):
    for N, d in R.groupby('CutoffN'):
        P('  [%s] N=%-3d n=%-3d 覆盖 %.3f' % (tag, N, len(d), d.InPI.mean()))

with open(os.path.join(OUT, 's36_console.txt'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(LOG) + '\n')
print()
print('[out] pred_A/B/C.csv, metrics_ABC.csv, bootstrap_cluster.csv')
