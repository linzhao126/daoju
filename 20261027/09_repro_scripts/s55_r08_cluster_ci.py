# -*- coding: utf-8 -*-
"""s55_r08_cluster_ci.py —— R08：按物理刃口聚类的置信区间 + 分组/严格集敏感性

审核要求（R08，P1）
------------------
「说明 jackknife+ 实际算法形态及覆盖保证的适用条件；按物理刃口聚类、保留品番分组敏感性，
  披露严格集样本不足。」

三件事
------
§A  聚类 bootstrap：同一把刀在 4 个截断点各出现一次，是**同一单元**，不是 4 个独立样本。
    这里同时给「按刀重采样」与「按行重采样」两种 CI，用差值说明朴素做法会把区间压窄多少。
§B  分组敏感性：prog / part / global 三口径的 MAE 差，用**配对**（同一刀同一截断）× **按刀聚类** 的 CI。
§C  严格 8 把：同样口径再算一遍，看区间宽度是否已经宽到"什么结论都放不下"。
§D  区间覆盖率的聚类 CI：43-00 的 S1/Cover 是点估计，这里给按刀聚类的 bootstrap CI。

输出到 附件_证据/58_R08_区间与分组核证/
"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
OUT = os.path.join(BASE, '附件_证据', '58_R08_区间与分组核证')
os.makedirs(OUT, exist_ok=True)

GROUP = os.path.join(BASE, '26_M0分组粒度终局', 'grouping_test.csv')
PI = os.path.join(BASE, '43_模型区间与不确定性', 'pred_all_models_pi.csv')

RNG = np.random.default_rng(20261029)
NBOOT = 4000
STRICT8 = [30, 31, 39, 40, 49, 50, 56, 59]
MODELS = ['M0A', 'M0B', 'M1Old', 'M1New']
LOG = []


def P(*a):
    s = ' '.join(str(x) for x in a)
    print(s)
    LOG.append(s)


def ci(v, lo=2.5, hi=97.5):
    return float(np.percentile(v, lo)), float(np.percentile(v, hi))


def boot_mean(df, col, cluster='Cycle', n=NBOOT):
    """按 cluster 重采样后取均值分布"""
    groups = {k: g[col].to_numpy(float) for k, g in df.groupby(cluster)}
    keys = list(groups)
    out = np.empty(n)
    for i in range(n):
        pick = RNG.choice(keys, size=len(keys), replace=True)
        vals = np.concatenate([groups[k] for k in pick])
        out[i] = vals.mean()
    return out


def boot_rows(df, col, n=NBOOT):
    v = df[col].to_numpy(float)
    idx = RNG.integers(0, len(v), size=(n, len(v)))
    return v[idx].mean(axis=1)


def part_a(d):
    rows = []
    for (grp, drop, n), g in d.groupby(['Grouping', 'Drop', 'CutoffN']):
        for m in MODELS:
            col = 'Abs' + m
            gg = g.dropna(subset=[col])
            if len(gg) < 3:
                continue
            b_cl = boot_mean(gg, col)
            b_rw = boot_rows(gg, col)
            lo_c, hi_c = ci(b_cl)
            lo_r, hi_r = ci(b_rw)
            rows.append(dict(Grouping=grp, Drop=drop, CutoffN=n, Model=m,
                             n_rows=len(gg), n_tools=gg.Cycle.nunique(),
                             MAE=round(float(gg[col].mean()), 3),
                             CI_聚类_lo=round(lo_c, 3), CI_聚类_hi=round(hi_c, 3),
                             聚类宽度=round(hi_c - lo_c, 3),
                             CI_按行_lo=round(lo_r, 3), CI_按行_hi=round(hi_r, 3),
                             按行宽度=round(hi_r - lo_r, 3),
                             宽度被低估倍数=round((hi_c - lo_c) / max(1e-9, hi_r - lo_r), 2)))
    return pd.DataFrame(rows)


def part_a2(d):
    """跨截断合并后：这里聚类才真的起作用（每刀出现 4 次）"""
    rows = []
    for (grp, drop), g in d.groupby(['Grouping', 'Drop']):
        for m in MODELS:
            col = 'Abs' + m
            gg = g.dropna(subset=[col])
            if len(gg) < 5:
                continue
            b_cl = boot_mean(gg, col)
            b_rw = boot_rows(gg, col)
            lo_c, hi_c = ci(b_cl)
            lo_r, hi_r = ci(b_rw)
            rows.append(dict(Grouping=grp, Drop=drop, Model=m,
                             n_rows=len(gg), n_tools=gg.Cycle.nunique(),
                             MAE=round(float(gg[col].mean()), 3),
                             CI_聚类_lo=round(lo_c, 3), CI_聚类_hi=round(hi_c, 3),
                             聚类宽度=round(hi_c - lo_c, 3),
                             CI_按行_lo=round(lo_r, 3), CI_按行_hi=round(hi_r, 3),
                             按行宽度=round(hi_r - lo_r, 3),
                             宽度被低估倍数=round((hi_c - lo_c) / max(1e-9, hi_r - lo_r), 2)))
    return pd.DataFrame(rows)


def part_b(d):
    """配对差：同一刀同一截断，prog 与 part 各有一个误差"""
    rows = []
    for drop in d.Drop.unique():
        sub = d[d.Drop == drop]
        piv = {}
        for m in MODELS:
            col = 'Abs' + m
            a = sub[sub.Grouping == 'prog'].set_index(['CutoffN', 'Cycle'])[col]
            b = sub[sub.Grouping == 'part'].set_index(['CutoffN', 'Cycle'])[col]
            g = sub[sub.Grouping == 'global'].set_index(['CutoffN', 'Cycle'])[col]
            idx = a.index.intersection(b.index)
            if len(idx) < 3:
                continue
            diff = (a.loc[idx] - b.loc[idx]).reset_index()
            diff.columns = ['CutoffN', 'Cycle', 'diff']
            bg = (a.loc[idx] - g.loc[idx]).reset_index()
            bg.columns = ['CutoffN', 'Cycle', 'diff']
            for tag, df in (('prog-part', diff), ('prog-global', bg)):
                bs = boot_mean(df, 'diff')
                lo, hi = ci(bs)
                rows.append(dict(Drop=drop, Model=m, 对比=tag, n_tools=df.Cycle.nunique(),
                                 n_rows=len(df), 平均差=round(float(df['diff'].mean()), 3),
                                 CI_lo=round(lo, 3), CI_hi=round(hi, 3),
                                 跨0=bool(lo < 0 < hi)))
    return pd.DataFrame(rows)


def part_c(d):
    sub = d[d.Cycle.isin(STRICT8)]
    rows = []
    for (grp, drop, n), g in sub.groupby(['Grouping', 'Drop', 'CutoffN']):
        for m in MODELS:
            col = 'Abs' + m
            gg = g.dropna(subset=[col])
            if len(gg) < 3:
                continue
            bs = boot_mean(gg, col)
            lo, hi = ci(bs)
            rows.append(dict(Grouping=grp, Drop=drop, CutoffN=n, Model=m,
                             n_rows=len(gg), n_tools=gg.Cycle.nunique(),
                             MAE=round(float(gg[col].mean()), 3),
                             CI_lo=round(lo, 3), CI_hi=round(hi, 3), 宽度=round(hi - lo, 3)))
    return pd.DataFrame(rows)


def part_d():
    p = pd.read_csv(PI, encoding='utf-8-sig')
    p['Cycle'] = p.PhysicalLifeId.str.extract(r'c(\d+)')[0].astype(int)
    rows = []
    for (src, n, m, sc), g in p.groupby(['Source', 'CutoffN', 'Model', 'CalScope']):
        g = g.dropna(subset=['S1_lo', 'S1_hi'])
        if len(g) < 5:
            continue
        g = g.assign(hit=((g.S1_lo <= g.ActualRUL) & (g.ActualRUL <= g.S1_hi)).astype(float))
        bs = boot_mean(g, 'hit')
        lo, hi = ci(bs)
        rows.append(dict(Source=src, CutoffN=n, Model=m, CalScope=sc,
                         n_rows=len(g), n_tools=g.Cycle.nunique(),
                         覆盖率=round(float(g.hit.mean()), 3),
                         CI_lo=round(lo, 3), CI_hi=round(hi, 3)))
    return pd.DataFrame(rows)


if __name__ == '__main__':
    P('=' * 96)
    P('R08　按物理刃口聚类的置信区间 + 分组 / 严格集敏感性')
    P('=' * 96)
    d = pd.read_csv(GROUP, encoding='utf-8-sig')
    P('grouping_test.csv：%d 行；分组 %s；样本方案 %s'
      % (len(d), sorted(d.Grouping.unique()), sorted(d.Drop.unique())))

    A = part_a(d)
    A.to_csv(os.path.join(OUT, 'R08_cluster_ci_mae.csv'), index=False, encoding='utf-8-sig')
    P('\n§A 聚类 CI vs 按行 CI（摘 M0B / Drop=38,51）')
    P(A[(A.Model == 'M0B') & (A.Drop == '38,51')]
      [['Grouping', 'CutoffN', 'n_tools', 'n_rows', 'MAE', 'CI_聚类_lo', 'CI_聚类_hi',
        'CI_按行_lo', 'CI_按行_hi', '宽度被低估倍数']].to_string(index=False))
    P('  按行 CI 宽度被低估的倍数：中位 %.2f，最大 %.2f'
      % (A['宽度被低估倍数'].median(), A['宽度被低估倍数'].max()))

    A2 = part_a2(d)
    A2.to_csv(os.path.join(OUT, 'R08_cluster_ci_pooled.csv'), index=False, encoding='utf-8-sig')
    P('\n§A2 跨 4 个截断合并后（每刀出现 4 次，聚类才起作用）')
    P(A2[A2.Model == 'M0B'][['Grouping', 'Drop', 'n_tools', 'n_rows', 'MAE',
                             'CI_聚类_lo', 'CI_聚类_hi', 'CI_按行_lo', 'CI_按行_hi',
                             '宽度被低估倍数']].to_string(index=False))

    B = part_b(d)
    B.to_csv(os.path.join(OUT, 'R08_grouping_paired_diff.csv'), index=False, encoding='utf-8-sig')
    P('\n§B 配对差（正 = prog 误差更大 / part 更好）')
    P(B[B.对比 == 'prog-part'][['Drop', 'Model', 'n_tools', 'n_rows', '平均差',
                                'CI_lo', 'CI_hi', '跨0']].to_string(index=False))

    C = part_c(d)
    C.to_csv(os.path.join(OUT, 'R08_strict8_ci.csv'), index=False, encoding='utf-8-sig')
    P('\n§C 严格 8 把（Drop=38,51，M0B）')
    P(C[(C.Model == 'M0B') & (C.Drop == '38,51')].to_string(index=False))

    D = part_d()
    D.to_csv(os.path.join(OUT, 'R08_pi_coverage_cluster.csv'), index=False, encoding='utf-8-sig')
    P('\n§D S1 区间覆盖率的聚类 CI（摘 CalScope=prog）')
    P(D[D.CalScope == 'prog'][['Source', 'CutoffN', 'Model', 'n_tools', '覆盖率',
                               'CI_lo', 'CI_hi']].to_string(index=False))

    with open(os.path.join(OUT, 's55_console.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG))
    P('DONE →', OUT)
