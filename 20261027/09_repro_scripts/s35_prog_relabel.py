# -*- coding: utf-8 -*-
"""s35_prog_relabel.py —— 修正 xalign 的程序归属（审核 P0-03 / Test-C）

问题
----
`t1111_pipeline.py:1262` 取 `d['L_main_program'].dropna().iloc[0]` —— **时间上第一行**的程序。
混批周期（同一次装刀内换过品番）里，首行程序 ≠ 实际主体程序。

实测（旧数据 + 新数据）判错的周期：
  c27  判 14 → 实 20   （prog14 仅 274 行，prog20 有 10331 行）
  c33  判 20 → 实 19   （prog19 6093 行 vs prog20 5905 行）
  c34  判 19 → 实 14   （prog19 的 Z=−55.30 不在 z0 带内，76 件全来自 prog14）
  c49  判 17 → 实 11
  c57  判 17 → 实 16
  c58  判 16 → 实 13
  c59  判 13 → 实 14

关键区分：**z0 是对的，prog 是错的**
--------------------------------
`z0` 取"主轴转速>0 的行的 Z 众数"，它自动锁定了行数最多的那个工步，
所以几何定位一直正确。错的只是那个记录在 HI 表里的程序名 —— 而程序名
被下游用来分 M0 池（同程序其他周期的中位寿命），所以**它影响 MAE**。

因此本脚本**不重跑 xalign、不改任何 HI 值**，只做重标注：
  prog_corrected = 「|Z − z0| ≤ z_tol 且主轴转速>0 的行里，行数最多的程序」
即"这批 HI 实际来自哪个程序"。

同时输出每周期的**混合比例**（z0 带内第二大程序的行数占比）：
  mixed_frac > 0.20 → 该周期是"两种品番混在一起算 HI"，M0 池应降级或排除。

输出：24_程序归属修正/
  prog_relabel.csv        逐周期：旧label / z0 / 带内分布 / 新label / 混合比例
  HI_merged_28.csv        合并 28 周期的 HI（prog 用修正值，HI 值不变）
  M0_compare.csv          修正前 vs 修正后的 M0 与周期归属
"""
import os
import sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))
BASE = os.path.abspath(os.path.join(HERE, '..'))
OUT = os.path.join(BASE, '24_程序归属修正')
os.makedirs(OUT, exist_ok=True)

OLD_LABELED = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv')
NEW_LABELED = os.path.join(BASE, '22_c48-c60_xalign', 'labeled_T1111_new.csv')
OLD_HI = os.path.join(ROOT, 'out_T1111', 'xalign_HI.csv')
NEW_HI = os.path.join(BASE, '22_c48-c60_xalign', 'xalign_HI_new_c49c60.csv')

Z_TOL = 1.0          # 与 XALIGN_Z_TOL 一致
MIXED_TH = 0.20      # z0 带内第二大程序占比超过此值 → 视为混合
LOG = []


def P(*a):
    s = ' '.join(str(x) for x in a)
    print(s)
    LOG.append(s)


def prog_of(s):
    return str(s).split('(')[0].strip()


def load_rows():
    """两份 labeled 的 (life_cycle, L_main_program, L_abso_z, L_spindle_speed)"""
    cols = ['life_cycle', 'L_main_program', 'L_abso_z', 'L_spindle_speed']
    o = pd.read_csv(OLD_LABELED, usecols=cols, encoding='utf-8-sig')
    o['src'] = 'old'
    n = pd.read_csv(NEW_LABELED, usecols=cols, encoding='utf-8-sig')
    n['src'] = 'new'
    d = pd.concat([o, n], ignore_index=True)
    d['prog'] = d.L_main_program.map(prog_of)
    d['L_abso_z'] = pd.to_numeric(d.L_abso_z, errors='coerce')
    d['L_spindle_speed'] = pd.to_numeric(d.L_spindle_speed, errors='coerce')
    # 与 pipeline 同口径：只用主轴转速>0 的行
    d = d[d.L_spindle_speed.fillna(0) > 0]
    d = d.dropna(subset=['L_abso_z'])
    return d


def main():
    d = load_rows()
    P('参与判定的行（主轴转速>0）：%d' % len(d))

    old_hi = pd.read_csv(OLD_HI, encoding='utf-8-sig')
    new_hi = pd.read_csv(NEW_HI, encoding='utf-8-sig')
    hi = pd.concat([old_hi, new_hi], ignore_index=True)
    P('HI 表：旧 %d 行 / 新 %d 行 → 合并 %d 行，%d 个周期'
      % (len(old_hi), len(new_hi), len(hi), hi.life_cycle.nunique()))
    P('')

    rows = []
    P('=== 逐周期重标注 ===')
    P('%-5s %-6s %8s %-38s %-6s %8s %s'
      % ('周期', '旧prog', 'z0', 'z0带内各程序行数', '新prog', '混合比', '判定'))
    for c in sorted(hi.life_cycle.unique()):
        g = d[d.life_cycle == c]
        if not len(g):
            continue
        # z0：与 pipeline 完全一致
        z0 = float(g.L_abso_z.round(1).mode().iloc[0])
        band = g[(g.L_abso_z - z0).abs() <= Z_TOL]
        if not len(band):
            continue
        vc = band.prog.value_counts()
        new_prog = vc.index[0]
        mixed = float(vc.iloc[1] / vc.sum()) if len(vc) > 1 else 0.0
        old_prog = str(hi.loc[hi.life_cycle == c, 'prog'].iloc[0])
        same = (str(old_prog) == str(new_prog))
        tag = 'OK' if same else 'FIX'
        if mixed > MIXED_TH:
            tag += '+MIXED'
        dist = ' '.join('%s:%d(%.0f%%)' % (k, v, 100.0 * v / vc.sum()) for k, v in vc.head(3).items())
        rows.append(dict(Cycle=int(c), OldProg=old_prog, Z0=round(z0, 2),
                         InBandRows=int(vc.sum()), Dist=dist,
                         NewProg=new_prog, MixedFrac=round(mixed, 4),
                         MaxShare=round(float(vc.iloc[0] / vc.sum()), 4),
                         Same=bool(same), Flag=tag, Source=g.src.iloc[0]))
        P('c%-4d %-6s %8.2f %-38s %-6s %7.1f%% %s'
          % (c, old_prog, z0, dist, new_prog, 100 * mixed, tag))

    R = pd.DataFrame(rows)
    R.to_csv(os.path.join(OUT, 'prog_relabel.csv'), index=False, encoding='utf-8-sig')
    P('')
    P('修正 %d / %d 个周期；其中混合(z0带内次大程序>%.0f%%) %d 个：%s'
      % (int((~R.Same).sum()), len(R), 100 * MIXED_TH, int((R.MixedFrac > MIXED_TH).sum()),
         ','.join('c%d' % c for c in R.loc[R.MixedFrac > MIXED_TH, 'Cycle'])))

    # ---------- 合并 HI，prog 用修正值
    m = hi.merge(R[['Cycle', 'NewProg']], left_on='life_cycle', right_on='Cycle', how='left')
    m['prog_old'] = m['prog']
    m['prog'] = m['NewProg'].fillna(m['prog'].astype(str))
    m = m.drop(columns=['Cycle', 'NewProg'])
    m.to_csv(os.path.join(OUT, 'HI_merged_28.csv'), index=False, encoding='utf-8-sig')
    P('')
    P('已写 HI_merged_28.csv：%d 行 / %d 周期' % (len(m), m.life_cycle.nunique()))

    # ---------- M0 对比
    P('')
    P('=== M0 归属变化（M0 = 同程序其他周期的中位件数）===')
    life = m.groupby('life_cycle').size().rename('n')
    P('%-5s %6s %8s %8s %8s' % ('周期', '件数', '旧prog', '旧M0', '新M0'))
    cmp_rows = []
    for c in sorted(life.index):
        p_old = m.loc[m.life_cycle == c, 'prog_old'].iloc[0]
        p_new = m.loc[m.life_cycle == c, 'prog'].iloc[0]
        sib_old = [life[o] for o in life.index
                   if str(m.loc[m.life_cycle == o, 'prog_old'].iloc[0]) == str(p_old) and o != c]
        sib_new = [life[o] for o in life.index
                   if str(m.loc[m.life_cycle == o, 'prog'].iloc[0]) == str(p_new) and o != c]
        m0_old = float(np.median(sib_old)) if len(sib_old) >= 2 else np.nan
        m0_new = float(np.median(sib_new)) if len(sib_new) >= 2 else np.nan
        chg = '' if (m0_old == m0_new or (np.isnan(m0_old) and np.isnan(m0_new))) else '←变'
        cmp_rows.append(dict(Cycle=int(c), Pieces=int(life[c]), OldProg=p_old, NewProg=p_new,
                             M0_old=m0_old, M0_new=m0_new, NSib=len(sib_new), Change=chg))
        P('c%-4d %6d %8s %8s %8s  %s' % (c, life[c], '%s' % p_old,
                                         ('%.1f' % m0_old) if m0_old == m0_old else '—',
                                         ('%.1f' % m0_new) if m0_new == m0_new else '—', chg))
    M = pd.DataFrame(cmp_rows)
    M.to_csv(os.path.join(OUT, 'M0_compare.csv'), index=False, encoding='utf-8-sig')

    P('')
    P('=== 修正后各程序的周期数 ===')
    for p, g in M.groupby('NewProg'):
        P('  prog%-4s %d 个周期，件数 %d，M0 %s'
          % (p, len(g), int(g.Pieces.sum()),
             ('%.1f' % g.M0_new.median()) if g.M0_new.notna().any() else '**不足2个兄弟周期，算不出**'))

    with open(os.path.join(OUT, 's35_console.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG) + '\n')


if __name__ == '__main__':
    main()
