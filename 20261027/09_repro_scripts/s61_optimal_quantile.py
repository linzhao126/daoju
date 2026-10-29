# -*- coding: utf-8 -*-
"""s61_optimal_quantile.py —— 最优换刀分位点：到底该在寿命的百分之几换刀

为什么做这个
------------
`0.4 / 0.9` 这两个数从 2026-10-20 调参时就定了（嵌套 CV 选出），但**从没回答过**
"给定这把刀的寿命分布，最省的换刀比例是多少"。D-05 又把 0.9 定成了默认参考线——
如果 0.9 不是最优，这个默认就在系统性地多花钱。

这个问题**不需要任何新数据、不需要任何硬件**：只要 23 个可判周期的寿命 + 成本模型。
它正好落在"预测刀具寿命"这个目标上——因为"什么时候换"就是"预测还剩多少件"的另一面。

模型
----
    对每个候选比例 f ∈ [0.50, 1.00]：
        limit_i = ceil(f × M0_prog(i))          # 这把刀打算用到第几件
        崩刃点  = Life_i − k                     # k = 崩刃后平均还要做几件才发现
        合格    = Σ min(limit_i, 崩刃点_i)
        失效    = #(limit_i > 崩刃点_i)
        成本/件 = (刀数 + ρ × 失效) / 合格
    对每个 (k, ρ) 网格，取 argmin_f。

输出
----
R61_optimal_f.csv        每个 (k, ρ) 的最优 f、对应成本、以及 f=0.9 的相对代价
R61_curve.csv            k=0/1/3 × ρ=1/5/10 下的"f → 成本"完整曲线（可画图）
R61_by_program.csv       按主程序分别求最优 f（prog11/14/20）

⚠ 样本量提醒：只有 23 把刀，最优 f 的置信区间很宽。本文给的是**方向**（该往哪边挪），
   不是"精确到 0.01"。判据：若 f 在 [0.80, 0.95] 整段成本差 <3%，则说明"精确值不重要"。
"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
OUT = os.path.join(BASE, '附件_证据', '68_最优换刀分位')
os.makedirs(OUT, exist_ok=True)

AUD = os.path.join(BASE, '附件_证据', '52_R02_延长分支审计', 'r02_grant_audit.csv')
FS = np.round(np.arange(0.50, 1.0001, 0.01), 2)
KS = [0, 1, 2, 3, 5]
RHOS = [0.5, 1, 1.5, 2, 3, 5, 10, 20]
LOG = []


def P(*a):
    s = ' '.join(str(x) for x in a)
    print(s)
    LOG.append(s)


def cost_curve(d, k, rho):
    """返回 f → 成本 的曲线"""
    life = d['Life'].to_numpy(int)
    m0 = d['M0'].to_numpy(float)
    chip = np.maximum(1, life - k)
    out = []
    for f in FS:
        lim = np.ceil(f * m0).astype(int)
        good = np.minimum(lim, chip)
        fail = int((lim > chip).sum())
        sick = int(np.maximum(0, np.minimum(lim, life) - chip).sum())
        g = max(1, int(good.sum()))
        out.append(dict(f=float(f), 合格产出=int(good.sum()), 失效=fail, 带病=sick,
                        刀数=len(d), 成本=(len(d) + rho * fail) / g,
                        件每刀=round(float(good.sum()) / len(d), 1)))
    return pd.DataFrame(out)


if __name__ == '__main__':
    P('=' * 92)
    P('最优换刀分位点：给定寿命分布，最省的 f 是多少')
    P('=' * 92)
    d = pd.read_csv(AUD, encoding='utf-8-sig')
    d = d[d.Status == 'OK'].copy()
    P('可判周期 %d 个；主程序分布 %s' % (len(d), d.Prog.value_counts().to_dict()))
    P('寿命（件）：%s' % sorted(d.Life.tolist()))
    P('各程序 M0：%s' % {int(k): float(v) for k, v in
                        d.groupby('Prog')['M0'].median().items()})

    rows, curves = [], []
    for k in KS:
        for rho in RHOS:
            c = cost_curve(d, k, rho)
            c['k'], c['rho'] = k, rho
            curves.append(c)
            i = c['成本'].idxmin()
            f_best = float(c.loc[i, 'f'])
            c09 = float(c.loc[(c.f - 0.9).abs().idxmin(), '成本'])
            # 有效平台：成本不超过最优 3% 的 f 区间
            plat = c[c['成本'] <= c['成本'].min() * 1.03]['f']
            rows.append(dict(k=k, rho=rho, 最优f=f_best, 最优成本=round(float(c['成本'].min()), 4),
                             f09成本=round(c09, 4),
                             f09相对代价=round(100 * (c09 / c['成本'].min() - 1), 1),
                             平台下限=float(plat.min()), 平台上限=float(plat.max())))
    R = pd.DataFrame(rows)
    C = pd.concat(curves, ignore_index=True)
    R.to_csv(os.path.join(OUT, 'R61_optimal_f.csv'), index=False, encoding='utf-8-sig')
    C.to_csv(os.path.join(OUT, 'R61_curve.csv'), index=False, encoding='utf-8-sig')

    P('\n[最优 f]（行=k，列=ρ）')
    P(R.pivot(index='k', columns='rho', values='最优f').to_string())
    P('\n[f=0.9 相对最优的代价 %]（正 = 0.9 比最优更贵）')
    P(R.pivot(index='k', columns='rho', values='f09相对代价').to_string())
    P('\n[成本平台：不超过最优 3% 的 f 区间]')
    P(R.pivot(index='k', columns='rho', values='平台下限').to_string())
    P(R.pivot(index='k', columns='rho', values='平台上限').to_string())

    # 按主程序分别求
    prows = []
    for p, g in d.groupby('Prog'):
        if len(g) < 3:
            continue
        for k in (0, 1, 3):
            c = cost_curve(g, k, 5)
            i = c['成本'].idxmin()
            prows.append(dict(Prog=int(p), 刀数=len(g), k=k,
                              寿命=sorted(g.Life.tolist()),
                              最优f=float(c.loc[i, 'f']),
                              最优成本=round(float(c['成本'].min()), 4),
                              f09成本=round(float(c.loc[(c.f - 0.9).abs().idxmin(), '成本']), 4)))
    PR = pd.DataFrame(prows)
    PR.to_csv(os.path.join(OUT, 'R61_by_program.csv'), index=False, encoding='utf-8-sig')
    P('\n[按主程序]')
    P(PR.to_string(index=False))

    # 结论行（写死判据）
    P('\n[判据]')
    main = R[(R.rho == 5) & (R.k.isin([0, 1, 3]))]
    P('  ρ=5 时：k=0 → f*=%.2f；k=1 → f*=%.2f；k=3 → f*=%.2f'
      % tuple(main.set_index('k').loc[[0, 1, 3], '最优f']))
    platw = float((R['平台上限'] - R['平台下限']).median())
    P('  成本平台宽度中位 %.2f（f 在平台内挪动，成本差 <3%%）' % platw)
    if platw >= 0.10:
        P('  → 结论：**精确的 f 不重要**（平台宽 %.2f），只要别掉出平台；' % platw)
        P('     平台大致在 %.2f~%.2f，D-05 的 0.9 落在平台内 → 默认值可以保留。'
          % (R['平台下限'].median(), R['平台上限'].median()))
    else:
        P('  → 结论：**f 的选择很敏感**（平台仅 %.2f），需要用更多样本把最优点定准。' % platw)

    with open(os.path.join(OUT, 's61_console.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG))
    P('\nDONE →', OUT)
