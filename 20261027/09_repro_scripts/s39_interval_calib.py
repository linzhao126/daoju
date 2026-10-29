# -*- coding: utf-8 -*-
"""s39_interval_calib.py —— 预测区间校准（3 号任务）

问题
----
此前 M0-B 的区间是 `np.percentile(cond_k, [10, 90])`，即
「训练池里条件剩余（life−N, life>N）的 10~90 分位」。
名义 80%，实测覆盖只有 **0.50~0.63**（五组 × 4 截断全部不达标）。

根因
----
那个区间校准的是**寿命分布**，不是**预测误差**。它有两个问题：
1. 训练池只有 2~14 把刀，`P10` 实际落在第 1.3 个样本、`P90` 落在第 12.7 个 —— 
   小样本下几乎等于 min/max，估计极不稳定；
2. 它假设"测试刀来自同一分布"，但没考虑预测值本身（中位数）的偏差。

正确做法：**conformal prediction**
--------------------------------
用**留一残差** |actual_i − pred_i|（i≠k）定宽度，对每个 k 单独校准。
jackknife+ (Barber et al. 2021) 对可交换样本有有限样本覆盖保证：
    覆盖 ≥ 1 − 2α （对称版用 α/2 分位时更保守）

四个方案对比
------------
  S0  原版      pred ± (P10,P90 of cond_k)              ← 现状
  S1  jackknife+ 对称   [pred − q_{1−α/2}(|r_{−k}|), pred + 同]
  S2  conformal 非对称  [pred + q_{α/2}(r_{−k}), pred + q_{1−α/2}(r_{−k})]
  S3  正态近似   pred ± z_{1−α/2} · sd(r_{−k})

判据：覆盖越接近 0.80 越好，同覆盖率下**宽度越小越好**。
⚠ 也要报"覆盖率达标但宽度过大"的情形 —— 那种区间没有实用价值。

输出：27_区间校准/
"""
import os
import sys
import numpy as np
import pandas as pd
from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
OUT = os.path.join(BASE, '27_区间校准')
os.makedirs(OUT, exist_ok=True)

MERGED = os.path.join(BASE, '24_程序归属修正', 'HI_merged_28.csv')
MIXP = os.path.join(BASE, '25_品番混批诊断', 'prog_mix_profile.csv')

K, W = 20, 10
CUTS = [20, 40, 60, 80]
ALPHA = 0.20          # 名义 80%
DROP = (38, 51)       # 件数异常周期（见 24-00 §4）
PART = {'11': '02', '13': '02', '14': '02', '16': '03', '17': '03', '19': '03', '20': '03'}

LOG = []


def P(*a):
    s = ' '.join(str(x) for x in a)
    print(s)
    LOG.append(s)


hi = pd.read_csv(MERGED, encoding='utf-8-sig')
mix = pd.read_csv(MIXP, encoding='utf-8-sig').set_index('Cycle')

curves, prog = {}, {}
for (p, c), g in hi.groupby(['prog', 'life_cycle']):
    c = int(c)
    curves[c] = g.sort_values('piece_idx')['HI_L_spindle_load'].to_numpy(float)
    prog[c] = str(p)

POOL = [c for c in curves if c not in DROP]
P('样本：%d 周期（剔除 %s），共 %d 件'
  % (len(POOL), ','.join('c%d' % c for c in DROP), sum(len(curves[c]) for c in POOL)))


def part_of(c):
    return PART.get(prog[c], '?')


def rise_of(h, N):
    if N > len(h):
        return np.nan
    seg = h[max(0, N - W):N]
    seg = seg[np.isfinite(seg)]
    return float(np.median(seg) - 1.0) if len(seg) >= 3 else np.nan


def m0b_of(k, N, group='part'):
    """条件剩余中位：训练池按品番（推荐）或全体"""
    if group == 'part':
        sib = [o for o in POOL if o != k and part_of(o) == part_of(k)]
    else:
        sib = [o for o in POOL if o != k]
    cond = [len(curves[o]) - N for o in sib if len(curves[o]) > N]
    return float(np.median(cond)) if cond else np.nan, cond


def intervals(pred, preds, actuals, ks, N):
    """对每把刀给四个方案的区间"""
    out = {}
    for k in ks:
        others = [i for i in ks if i != k]
        r = np.array([actuals[i] - preds[i] for i in others], float)
        absr = np.abs(r)
        if len(r) < 3:
            out[k] = dict(S0=(np.nan, np.nan), S1=(np.nan, np.nan),
                          S2=(np.nan, np.nan), S3=(np.nan, np.nan))
            continue
        # S0 原版：训练池条件剩余的 P10/P90（中心不是 pred，直接用）
        _, cond = m0b_of(k, N)
        s0 = (float(np.percentile(cond, 10)), float(np.percentile(cond, 90))) if len(cond) >= 2 \
            else (np.nan, np.nan)
        # S1 jackknife+ 对称
        q = float(np.percentile(absr, 100 * (1 - ALPHA / 2)))
        s1 = (pred[k] - q, pred[k] + q)
        # S2 conformal 非对称
        lo = pred[k] + float(np.percentile(r, 100 * ALPHA / 2))
        hi2 = pred[k] + float(np.percentile(r, 100 * (1 - ALPHA / 2)))
        s2 = (lo, hi2)
        # S3 正态近似
        z = float(stats.norm.ppf(1 - ALPHA / 2))
        sd = float(np.std(r, ddof=1))
        s3 = (pred[k] - z * sd, pred[k] + z * sd)
        out[k] = dict(S0=s0, S1=s1, S2=s2, S3=s3)
    return out


rows = []
detail = []
for N in CUTS:
    ks = [c for c in POOL if len(curves[c]) > N]
    if len(ks) < 6:
        continue
    preds = {k: m0b_of(k, N)[0] for k in ks}
    actuals = {k: len(curves[k]) - N for k in ks}
    IV = intervals(preds, preds, actuals, ks, N)
    for k in ks:
        for S in ('S0', 'S1', 'S2', 'S3'):
            lo, hi2 = IV[k][S]
            ok = (lo <= actuals[k] <= hi2) if (np.isfinite(lo) and np.isfinite(hi2)) else np.nan
            detail.append(dict(CutoffN=N, Cycle=k, Scheme=S, Pred=round(preds[k], 2),
                               Actual=actuals[k], Lo=None if not np.isfinite(lo) else round(lo, 2),
                               Hi=None if not np.isfinite(hi2) else round(hi2, 2),
                               Width=None if not (np.isfinite(lo) and np.isfinite(hi2)) else round(hi2 - lo, 2),
                               Covered=ok))
D = pd.DataFrame(detail)
D.to_csv(os.path.join(OUT, 'interval_detail.csv'), index=False, encoding='utf-8-sig')

P('')
P('=' * 84)
P('区间表现（名义 %.0f%%）' % (100 * (1 - ALPHA)))
P('-' * 84)
P('%-4s %-5s %6s %10s %10s %10s %10s' %
  ('N', '方案', '刀数', '覆盖率', '中位宽', '均宽', '相对S0'))
P('-' * 84)
for N, g in D.groupby('CutoffN'):
    w0 = g[g.Scheme == 'S0'].Width.median()
    for S in ('S0', 'S1', 'S2', 'S3'):
        s = g[g.Scheme == S]
        cov = s.Covered.mean()
        rows.append(dict(CutoffN=N, Scheme=S, n=len(s), Coverage=round(float(cov), 3),
                         MedWidth=round(float(s.Width.median()), 2),
                         MeanWidth=round(float(s.Width.mean()), 2)))
        P('%-4d %-5s %6d %9.1f%% %10.1f %10.1f %9s'
          % (N, S, len(s), 100 * cov, s.Width.median(), s.Width.mean(),
             '—' if S == 'S0' else '%+.0f%%' % (100 * (s.Width.median() / w0 - 1))))
    P('')

R = pd.DataFrame(rows)
R.to_csv(os.path.join(OUT, 'interval_summary.csv'), index=False, encoding='utf-8-sig')

# ---------- 方案评判：偏离名义多少 + 宽度代价
P('=' * 84)
P('方案总评（4 个截断平均）')
P('-' * 84)
P('%-5s %10s %12s %12s %s' % ('方案', '平均覆盖', '偏离80%', '中位宽均值', '判断'))
for S in ('S0', 'S1', 'S2', 'S3'):
    s = R[R.Scheme == S]
    c = s.Coverage.mean()
    P('%-5s %9.1f%% %+11.1fpp %12.1f %s'
      % (S, 100 * c, 100 * (c - 0.8), s.MedWidth.mean(),
         '✅ 达标（±5pp 内）' if abs(c - 0.8) <= 0.05 else
         ('⚠ 偏窄' if c < 0.8 else '⚠ 偏宽')))
P('')

# ---------- 逐截断挑最优
P('=== 逐截断最优（覆盖最接近 80%，同覆盖取窄）===')
best = []
for N, g in R.groupby('CutoffN'):
    g2 = g.copy()
    g2['score'] = (g2.Coverage - 0.8).abs()
    g2 = g2.sort_values(['score', 'MedWidth'])
    b = g2.iloc[0]
    best.append(b)
    P('  N=%-3d → %s（覆盖 %.1f%%，中位宽 %.1f）'
      % (N, b.Scheme, 100 * b.Coverage, b.MedWidth))
P('')

# ---------- 加宽后的代价：区间宽度 vs 寿命中位
life_med = np.median([len(curves[c]) for c in POOL])
P('寿命中位 %.1f 件；若区间中位宽 > %.0f 件（=寿命中位），说明区间宽到无实用价值'
  % (life_med, life_med))
for S in ('S1', 'S2'):
    s = R[R.Scheme == S]
    P('  %s：最宽 %.1f 件（占寿命中位 %.0f%%）'
      % (S, s.MedWidth.max(), 100 * s.MedWidth.max() / life_med))

# ---------- α 敏感性：名义覆盖 ↔ 实际覆盖 ↔ 宽度（说明"覆盖与宽度不可兼得"）
P('')
P('=' * 84)
P('α 敏感性（S1 对称 conformal）：要多少覆盖，就得多宽')
P('-' * 84)
P('%-4s %10s %10s %10s %10s %12s' %
  ('N', '名义覆盖', '实际覆盖', '半宽q', '全宽', '全宽/寿命中位'))
sens = []
for N in CUTS:
    ks = [c for c in POOL if len(curves[c]) > N]
    if len(ks) < 6:
        continue
    preds = {k: m0b_of(k, N)[0] for k in ks}
    actuals = {k: len(curves[k]) - N for k in ks}
    for nom in (0.50, 0.70, 0.80, 0.90):
        a = 1 - nom
        covs, ws = [], []
        for k in ks:
            others = [i for i in ks if i != k]
            absr = np.abs([actuals[i] - preds[i] for i in others])
            q = float(np.percentile(absr, 100 * (1 - a / 2)))
            lo, hi2 = preds[k] - q, preds[k] + q
            covs.append(1.0 if lo <= actuals[k] <= hi2 else 0.0)
            ws.append(2 * q)
        sens.append(dict(CutoffN=N, Nominal=nom, Coverage=round(float(np.mean(covs)), 3),
                         HalfWidth=round(float(np.median(ws)) / 2, 2),
                         Width=round(float(np.median(ws)), 2),
                         RelToLife=round(float(np.median(ws)) / life_med, 3)))
        P('%-4d %9.0f%% %9.1f%% %10.1f %10.1f %11.0f%%'
          % (N, 100 * nom, 100 * np.mean(covs),
             np.median(ws) / 2, np.median(ws), 100 * np.median(ws) / life_med))
    P('')
pd.DataFrame(sens).to_csv(os.path.join(OUT, 'alpha_sensitivity.csv'), index=False,
                          encoding='utf-8-sig')

with open(os.path.join(OUT, 's39_console.txt'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(LOG) + '\n')
print()
print('[out] interval_detail.csv / interval_summary.csv / s39_console.txt')
