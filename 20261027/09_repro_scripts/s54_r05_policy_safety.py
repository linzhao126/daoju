# -*- coding: utf-8 -*-
"""s54_r05_policy_safety.py —— R05：两档策略的安全与收益重做

审核要求（R05，P0）
------------------
「列规则的离线回放、实测上线观察、真实失效证据缺口；无真实崩刃时刻禁止叫零崩刃/零失效；
  提供不同崩刃发生位置、误报、报废成本与 ρ 的敏感性。」

本脚本只做一件事：把「同一条规则在不同崩刃位置、不同失效代价下到底省不省」摊成一张表，
不再用「0/16 失效」这种单一数字代替安全结论。

口径
----
* 每个生命周期 = 一把物理刃口，`Life` = 台账换刀点件数（= 观测件数，23 个可判周期）
* 规则输出 `Limit`：允许用到第几件
* 崩刃位置 θ：假设真实崩刃发生在 θ·Life（θ=1.0 表示"直到台账换刀点都没崩"）
* 合格产出  = Σ min(Limit, θ·Life)
* 带病加工  = Σ max(0, min(Limit,Life) − θ·Life)      ← 崩了还在用
* 失效次数  = #(Limit > θ·Life)
* 成本      = (刀数 + ρ × 失效次数) / 合格产出        ← 刀数 = 周期数（每个周期正好换一把）
* 提前换废弃 = Σ max(0, θ·Life − Limit)

五条规则
--------
  STATUS_QUO  用到台账终点才换（现状）
  COUNT_B     纯计数，一律 0.4·M0
  COUNT_H     纯计数，一律 0.9·M0
  TWO_STAGE   两档（线上现行）：HI 升 ≥1% → 用到 0.9·M0，否则 0.4·M0
  TWO_STAGE_HYST  同两档，但要求 b 之后**连续 3 件** hi_now ≥ 1+TAU（52-00 §7 方案 B 的离线评估）
"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
OUT = os.path.join(BASE, '附件_证据', '55_R05_策略安全与收益')
os.makedirs(OUT, exist_ok=True)

HI_F = os.path.join(BASE, '24_程序归属修正', 'HI_merged_28.csv')
AUD_F = os.path.join(BASE, '附件_证据', '52_R02_延长分支审计', 'r02_grant_audit.csv')
SEG_F = os.path.join(BASE, '03_原始数据与标签', 'life_program_segments.csv')

W, TAU, HOLD = 10, 0.01, 3
THETAS = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
RHOS = [1, 2, 5, 10, 20, 50]
LOG = []


def P(*a):
    s = ' '.join(str(x) for x in a)
    print(s)
    LOG.append(s)


def load():
    h = pd.read_csv(HI_F, encoding='utf-8-sig')
    curves, prog = {}, {}
    for (p, c), g in h.groupby(['prog', 'life_cycle']):
        curves[int(c)] = g.sort_values('piece_idx')['HI_L_spindle_load'].to_numpy(float)
        prog[int(c)] = int(p)
    a = pd.read_csv(AUD_F, encoding='utf-8-sig')
    return curves, prog, a


def hi_now_at(curve, i, w=W):
    seg = curve[max(0, i - w):i]
    seg = seg[np.isfinite(seg)]
    return float(np.median(seg)) if len(seg) >= 3 else np.nan


def eval_grants(a, curves):
    """两档的放行判定：锁存版 / 滞回版"""
    out = {}
    for _, r in a.iterrows():
        c = int(r['Cycle'])
        b, hh = int(r['B']), int(r['H'])
        cv = curves.get(c)
        latch = bool(r['Granted'])
        hyst = False
        if cv is not None and b < len(cv):
            seq = [hi_now_at(cv, i) for i in range(b + 1, min(hh, len(cv)) + 1)]
            run = 0
            for v in seq:
                if np.isfinite(v) and (v - 1.0) >= TAU:
                    run += 1
                    if run >= HOLD:
                        hyst = True
                        break
                else:
                    run = 0
        out[c] = dict(latch=latch, hyst=hyst)
    return out


def limits(a, grants):
    rows = []
    for _, r in a.iterrows():
        c = int(r['Cycle'])
        L, b, hh = int(r['Life']), int(r['B']), int(r['H'])
        g = grants[c]
        rows.append(dict(
            Cycle=c, Prog=int(r['Prog']), Life=L, M0=float(r['M0']), B=b, H=hh,
            HiAtB=float(r['HiAtB']),
            锁存放行=g['latch'], 滞回放行=g['hyst'],
            STATUS_QUO=L,
            COUNT_B=min(b, L),
            COUNT_H=min(hh, L),
            TWO_STAGE=(hh if g['latch'] else b),
            TWO_STAGE_HYST=(hh if g['hyst'] else b),
        ))
    d = pd.DataFrame(rows)
    # 允许"超过台账终点"= 超期使用，不做 min(L) 截断
    for k in ('TWO_STAGE', 'TWO_STAGE_HYST'):
        d[k + '_raw'] = d[k]
    return d


def replay(d, pol):
    """按 θ 回放：返回逐 θ 的汇总"""
    rows = []
    for th in THETAS:
        cut = np.round(th * d['Life']).astype(int)
        lim = d[pol].to_numpy(int)
        good = np.minimum(lim, cut)
        sick = np.maximum(0, np.minimum(lim, d['Life'].to_numpy(int)) - cut)
        fail = (lim > cut).astype(int)
        early = np.maximum(0, cut - lim)
        if pol == 'STATUS_QUO':
            # 「用到失效」：换刀时刻本身就是真实失效（0 号位 2026-10-27 确认"换刀=真实失效"）
            # 所以它每个周期都记 1 次失效，合格产出只算到崩刃那一刻。
            fail = np.ones(len(d), dtype=int)
            good = cut.copy()
            sick = np.maximum(0, d['Life'].to_numpy(int) - cut)
        rows.append(dict(Policy=pol, Theta=th,
                         合格产出=int(good.sum()), 带病加工=int(sick.sum()),
                         失效次数=int(fail.sum()), 提前换废弃件=int(early.sum()),
                         刀数=len(d)))
    return pd.DataFrame(rows)


def cost_table(rep):
    rows = []
    for _, r in rep.iterrows():
        for rho in RHOS:
            denom = max(1, r['合格产出'])
            rows.append(dict(Policy=r['Policy'], Theta=r['Theta'], Rho=rho,
                             成本=(r['刀数'] + rho * r['失效次数']) / denom,
                             每件成本_含带病分母=(r['刀数'] + rho * r['失效次数'])
                             / max(1, r['合格产出'] + r['带病加工'])))
    return pd.DataFrame(rows)


def equiv_check(d):
    """当量折算敏感性：4 个多程序周期的 B/H/Limit 在两种口径下差多少"""
    seg = pd.read_csv(SEG_F, encoding='utf-8-sig')
    seg['c'] = seg.PhysicalLifeId.str.extract(r'c(\d+)')[0].astype(int)
    multi = [c for c, g in seg.groupby('c') if len(g) > 1]
    m0 = {int(r['Prog']): float(r['M0']) for _, r in d.iterrows()}
    rows = []
    for c in sorted(multi):
        g = seg[seg.c == c]
        rec = d[d.Cycle == c]
        if not len(rec):
            continue
        rec = rec.iloc[0]
        eq, detail = 0.0, []
        for _, s in g.iterrows():
            p = int(s['Program'])
            n = int(s['PieceCount'])
            m = m0.get(p, rec['M0'])
            eq += n / m
            detail.append('%d@%d' % (n, p))
        cur = g.sort_values('SegIdx').iloc[-1]
        only_cur = int(cur['PieceCount']) / rec['M0']
        rows.append(dict(Cycle=c, 段明细='+'.join(detail), 物理件数=int(g['PieceCount'].sum()),
                         台账刀寿=int(rec['Life']), 当前程序M0=rec['M0'],
                         只数当前程序消耗=round(only_cur, 3),
                         当量消耗=round(eq, 3),
                         只数当前程序低估百分点=round(100 * (eq - only_cur), 1),
                         B_物理=rec['B'], B_当量=round(0.4 * eq * 100, 1),
                         H_物理=rec['H'], H_当量=round(0.9 * eq * 100, 1)))
    return pd.DataFrame(rows)


if __name__ == '__main__':
    P('=' * 90)
    P('R05　两档策略的安全与收益重做')
    P('=' * 90)
    curves, prog, a = load()
    a = a[a.Status == 'OK'].copy()
    P('可判周期 %d 个（%d 个因同程序历史不足被排除）' % (len(a), 28 - len(a)))
    gr = eval_grants(a, curves)
    d = limits(a, gr)
    d.to_csv(os.path.join(OUT, 'R05_limits_per_cycle.csv'), index=False, encoding='utf-8-sig')
    P('锁存放行 %d 把 / 滞回放行 %d 把'
      % (int(d['锁存放行'].sum()), int(d['滞回放行'].sum())))
    P('锁存与滞回结论不同 %d 把：%s'
      % (int((d['锁存放行'] != d['滞回放行']).sum()),
         d[d['锁存放行'] != d['滞回放行']].Cycle.tolist()))

    reps = []
    for pol in ('STATUS_QUO', 'COUNT_B', 'COUNT_H', 'TWO_STAGE', 'TWO_STAGE_HYST'):
        reps.append(replay(d, pol))
    rep = pd.concat(reps, ignore_index=True)
    rep.to_csv(os.path.join(OUT, 'R05_replay_by_theta.csv'), index=False, encoding='utf-8-sig')
    P('\n[θ=1.0 永不崩刃] 与 [θ=0.8 崩在八成]')
    P(rep[rep.Theta.isin([0.8, 1.0])].pivot(index='Policy', columns='Theta',
                                             values=['合格产出', '带病加工', '失效次数', '提前换废弃件'])
      .to_string())

    ct = cost_table(rep)
    ct.to_csv(os.path.join(OUT, 'R05_cost_rho.csv'), index=False, encoding='utf-8-sig')
    P('\n成本（ρ=10）= (刀数 + 10×失效) / 合格产出')
    P(ct[ct.Rho == 10].pivot(index='Policy', columns='Theta', values='成本').round(4).to_string())

    eq = equiv_check(d)
    eq.to_csv(os.path.join(OUT, 'R05_equiv_sensitivity.csv'), index=False, encoding='utf-8-sig')
    P('\n[当量折算敏感性]')
    P(eq.to_string(index=False))

    # 放行/拦截的真实性
    rows = []
    for _, r in d.iterrows():
        rows.append(dict(Cycle=r['Cycle'], Life=r['Life'], B=r['B'], H=r['H'],
                         锁存放行=r['锁存放行'], 滞回放行=r['滞回放行'],
                         活到B=r['Life'] > r['B'], 活到H=r['Life'] > r['H']))
    q = pd.DataFrame(rows)
    q.to_csv(os.path.join(OUT, 'R05_grant_quality.csv'), index=False, encoding='utf-8-sig')
    P('\n[放行质量]')
    for k in ('锁存放行', '滞回放行'):
        g = q[q[k]]
        ng = q[~q[k]]
        P('  %s：放行 %d（其中活过H %d = %.0f%%）；拦截 %d（其中本可活过H %d = %.0f%%）'
          % (k, len(g), int(g['活到H'].sum()), 100 * g['活到H'].mean() if len(g) else 0,
             len(ng), int(ng['活到H'].sum()), 100 * ng['活到H'].mean() if len(ng) else 0))

    with open(os.path.join(OUT, 's54_console.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG))
    P('DONE →', OUT)
