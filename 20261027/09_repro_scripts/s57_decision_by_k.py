# -*- coding: utf-8 -*-
"""s57_decision_by_k.py —— 把"崩刃位置 θ"换成车间能回答的"k 件"

为什么要换
----------
审核 R05 要求"不同崩刃发生位置"的敏感性，`55-00` 用的是 θ（崩刃发生在 θ·寿命）。
但 θ 不是 0 号位能拍的数：没人知道"崩刃发生在 87.3% 寿命"。**车间能回答的是另一种问法**：

    「崩一次刀，平均还要再做几件，才会被发现/停下来？」  ← 这就是 k

两者关系：chip_at = Life − k，即 θ = 1 − k/Life。k=0 表示"一崩就停"（或崩了不影响尺寸）。

本文输出
--------
R05_decision_by_k.csv      每个 k × 每条规则：同产出(2179 件)下要多少把刀、多少次失效、多少带病件
R05_breakeven_rho.csv      每个 k 下，规则之间的盈亏平衡 ρ

成本口径（与 55-00 一致）
------------------------
    成本/件 = (刀数 + ρ×失效次数) / 合格件数
为便于决策，"刀数"已**折算到同一产出 2179 件**（= 现状在 k=0 时的合格产出）。
"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
OUT = os.path.join(BASE, '附件_证据', '55_R05_策略安全与收益')
LIM = os.path.join(OUT, 'R05_limits_per_cycle.csv')
TARGET = 2179
KS = [0, 1, 2, 3, 5, 8, 10, 15]
POLS = ['STATUS_QUO', 'COUNT_B', 'COUNT_H', 'TWO_STAGE', 'TWO_STAGE_HYST']
LOG = []


def P(*a):
    s = ' '.join(str(x) for x in a)
    print(s)
    LOG.append(s)


def replay(d, pol, k):
    L = d['Life'].to_numpy(int)
    chip = np.maximum(1, L - k)
    lim = d[pol].to_numpy(int)
    good = np.minimum(lim, chip)
    sick = np.maximum(0, np.minimum(lim, L) - chip)
    fail = (lim > chip).astype(int)
    if pol == 'STATUS_QUO':
        fail = np.ones(len(d), dtype=int)
        good = chip.copy()
        sick = np.maximum(0, L - chip)
    return dict(good=int(good.sum()), sick=int(sick.sum()), fail=int(fail.sum()), tools=len(d))


if __name__ == '__main__':
    d = pd.read_csv(LIM, encoding='utf-8-sig')
    P('=' * 100)
    P('按 k（崩刃后平均几件才被发现）重算 —— 同产出 %d 件折算' % TARGET)
    P('=' * 100)
    rows = []
    for k in KS:
        # 先算每条规则的单位产出，再统一折算到 TARGET
        per = {p: replay(d, p, k) for p in POLS}
        for p, r in per.items():
            good_per_tool = r['good'] / r['tools']
            scale = TARGET / good_per_tool                       # 需要多少把刀
            rows.append(dict(
                k=k, Policy=p,
                合格产出_原始=r['good'], 件每刀=round(good_per_tool, 1),
                折算刀数=round(scale, 1),
                失效次数=round(r['fail'] / r['tools'] * scale, 1),
                带病件数=round(r['sick'] / r['tools'] * scale, 1),
            ))
    R = pd.DataFrame(rows)
    R.to_csv(os.path.join(OUT, 'R05_decision_by_k.csv'), index=False, encoding='utf-8-sig')

    for k in KS:
        P('\n---- k = %d 件（产 %d 件合格品）----' % (k, TARGET))
        sub = R[R.k == k][['Policy', '件每刀', '折算刀数', '失效次数', '带病件数']]
        P(sub.to_string(index=False))

    # 盈亏平衡：两条规则等价时的 ρ
    P('\n' + '=' * 100)
    P('盈亏平衡 ρ（两规则等价）：解 (T1 + ρF1) = (T2 + ρF2) → ρ = (T2−T1)/(F1−F2)')
    P('=' * 100)
    be = []
    pairs = [('COUNT_H', 'STATUS_QUO'), ('TWO_STAGE', 'STATUS_QUO'),
             ('COUNT_B', 'TWO_STAGE'), ('COUNT_B', 'COUNT_H'), ('COUNT_H', 'TWO_STAGE')]
    for k in KS:
        s = R[R.k == k].set_index('Policy')
        for a, b in pairs:
            T1, F1 = s.loc[a, '折算刀数'], s.loc[a, '失效次数']
            T2, F2 = s.loc[b, '折算刀数'], s.loc[b, '失效次数']
            num, den = (T2 - T1), (F1 - F2)
            rho = (num / den) if abs(den) > 1e-9 else np.nan
            be.append(dict(k=k, 对比='%s vs %s' % (a, b), 刀数差=round(num, 1),
                           失效差=round(F1 - F2, 1),
                           平衡rho=(round(rho, 2) if np.isfinite(rho) else '无解(一方全面更优)')))
    B = pd.DataFrame(be)
    B.to_csv(os.path.join(OUT, 'R05_breakeven_rho.csv'), index=False, encoding='utf-8-sig')
    P(B.pivot(index='k', columns='对比', values='平衡rho').to_string())

    # ---- 决策网格：(k, ρ) → 最省钱的规则
    RHOS = [0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 20.0]
    g = []
    for k in KS:
        for rho in RHOS:
            s2 = R[R.k == k].copy()
            s2['cost'] = (s2['折算刀数'] + rho * s2['失效次数']) / TARGET
            s2 = s2.sort_values('cost')
            g.append(dict(k=k, Rho=rho, 最优=s2.iloc[0].Policy,
                          成本=round(float(s2.iloc[0]['cost']), 4),
                          次优=s2.iloc[1].Policy))
    G = pd.DataFrame(g)
    G.to_csv(os.path.join(OUT, 'R05_decision_grid.csv'), index=False, encoding='utf-8-sig')
    P('\n' + '=' * 100)
    P('决策网格 (k, ρ) → 最省钱规则')
    P('=' * 100)
    P(G.pivot(index='k', columns='Rho', values='最优').to_string())

    # ---- 每个 k 下规则的切换点
    P('\n切换点（扫描 ρ 0.05~30）：')
    for k in KS:
        s3 = R[R.k == k].set_index('Policy')
        seq, prev = [], None
        for rho in np.arange(0.05, 30.01, 0.05):
            c = s3['折算刀数'] + rho * s3['失效次数']
            b = c.idxmin()
            if b != prev:
                seq.append((round(float(rho), 2), b))
                prev = b
        P('  k=%2d → ' % k + '  '.join('ρ≥%.2f:%s' % (r, b) for r, b in seq))

    with open(os.path.join(OUT, 's57_console.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG))
    P('\nDONE →', OUT)
