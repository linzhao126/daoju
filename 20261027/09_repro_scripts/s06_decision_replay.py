# -*- coding: utf-8 -*-
"""s06_decision_replay.py — P0-05(b)(c) C2 策略逐刀回放 + 安全边界敏感性

三件必须分开的事（任务书 Q4 的核心）
  A. 未越过人工换刀点  ← 可直接算
  B. 未产生不良品      ← 数据里没有，只能假设「崩刃发生在寿命的某个比例处」做敏感性
  C. 未发生崩刃        ← 15/20 个周期明确标「刀尖破损」，C 显然不成立

本脚本输出 A（严格可算），并用假设把 B 的暴露量算成区间，不冒充 C。

策略
  STATUS_QUO       用到失效才换（基准）
  COUNT_0.4        纯计数，到 0.4×M0 必换
  TWO_offline      到 b 时若「历史上曾达 τ」→ 延到 h，否则 b      （离线/原报告口径）
  TWO_online       到 b 时若「最近 10 件中位 ≥ τ」→ 延到 h，否则 b （线上可实现口径）
  TWO_onlineHI     同上但信号换成「线上 align_piece 的 HI」        （线上真实信号）
  每个策略同时给 round 与 ceil 两种取整

输出
  05_HI与换刀规则审计/decision_replay_per_life.csv
  05_HI与换刀规则审计/decision_rho_sensitivity.csv
  05_HI与换刀规则审计/decision_break_sensitivity.csv
"""
import os
import numpy as np
import pandas as pd

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import wsroot as _wsroot          # G2：不再数 '..'，按工作区特征向上定位
ROOT = _wsroot.find(_os.path.dirname(_os.path.abspath(__file__)))
HI_CSV = os.path.join(ROOT, 'out_T1111', 'xalign_HI.csv')
OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '05_HI与换刀规则审计'))
ONL = os.path.join(OUT, 'online_pieces.csv')

K, W, TAU = 20, 10, 0.01
BASE_F, EXT_F = 0.4, 0.9
RHOS = [2, 5, 10, 20, 50]

# ---------------- 离线 HI
off = pd.read_csv(HI_CSV, encoding='utf-8-sig')
cur_off, prog_of = {}, {}
for (p, c), g in off.groupby(['prog', 'life_cycle']):
    cur_off[int(c)] = g.sort_values('piece_idx')['HI_L_spindle_load'].to_numpy(float)
    prog_of[int(c)] = int(p)
cnt = pd.Series(list(prog_of.values())).value_counts()
keep = sorted([c for c in cur_off if cnt[prog_of[c]] >= 3 and len(cur_off[c]) >= max(K, W) + 5])

# ---------------- 线上 HI（可选）
cur_on = {}
if os.path.exists(ONL):
    op = pd.read_csv(ONL, encoding='utf-8-sig')
    for c, g in op[op.OK == 1].groupby('LifeCycle'):
        v = g.sort_values('AllNum').LoadMed.to_numpy(float)
        if len(v) >= max(K, W) + 5:
            cur_on[int(c)] = v

L = {c: len(cur_off[c]) for c in keep}
print('[sample] 周期 %d，件数 %d' % (len(keep), sum(L.values())))
print('[sample] 线上 HI 可用周期 %d' % len(cur_on))


def m0_of(c, pool_lives):
    return float(np.median([x for o, x in pool_lives.items() if prog_of[o] == prog_of[c] and o != c]))


def sig_reached_offline(hi, b, tau):
    """历史上是否曾达阈（含 b 之前任一位置），离线原报告口径"""
    w = pd.Series(hi / np.median(hi[:K]) - 1.0).rolling(W, min_periods=W).median().to_numpy()
    idx = np.where(np.arange(len(w)) >= max(K, W))[0]
    hit = idx[w[idx] >= tau]
    return bool(len(hit)) and (hit[0] + 1) <= b


def sig_reached_online(hi, b, tau, k_norm=K):
    """线上口径：到 b 件时看最近 10 件中位"""
    if b > len(hi):
        return False
    rel = hi / np.median(hi[:k_norm]) - 1.0
    return float(np.median(rel[max(0, b - W):b])) >= tau


def rnd(x, mode):
    return int(np.ceil(x)) if mode == 'ceil' else int(round(x))


rows = []
for mode in ('round', 'ceil'):
    for c in keep:
        m0 = m0_of(c, L)
        b, h = rnd(BASE_F * m0, mode), rnd(EXT_F * m0, mode)
        life = L[c]
        hi = cur_off[c]
        dec = {
            'STATUS_QUO': life,
            'COUNT_0.4': b,
            'TWO_offline': h if sig_reached_offline(hi, b, TAU) else b,
            'TWO_online': h if sig_reached_online(hi, b, TAU) else b,
        }
        if c in cur_on:
            hi2 = cur_on[c]
            dec['TWO_onlineHI'] = h if sig_reached_online(hi2, b, TAU) else b
        for name, lim in dec.items():
            lim = int(lim)
            fail = lim >= life
            used = life if fail else lim
            rows.append(dict(Strategy=name, Rounding=mode, PhysicalLifeId='4152-L-T1111-c%d' % c,
                             Program=prog_of[c], Life=life, M0=round(m0, 1), b=b, h=h,
                             Limit=lim, UsedPieces=used, Failure=int(fail),
                             WastedPieces=max(0, life - lim) if not fail else 0,
                             OverrunPieces=max(0, lim - life) if fail else 0,
                             SignalFired='' if name in ('STATUS_QUO', 'COUNT_0.4')
                             else ('YES' if lim == h else 'NO')))
R = pd.DataFrame(rows)

# ---------------- ρ 敏感性
sen = []
for mode in ('round', 'ceil'):
    for rh in RHOS:
        for name, g in R[R.Rounding == mode].groupby('Strategy'):
            cost = (len(g) + g.Failure.sum() * rh) / g.UsedPieces.sum()
            sen.append(dict(Rounding=mode, Strategy=name, rho=rh, n_tools=len(g),
                            Failures=int(g.Failure.sum()), Pieces=int(g.UsedPieces.sum()),
                            CostPerPiece=round(float(cost), 4),
                            vs_StatusQuo=round(float(
                                cost / ((len(g) + len(g) * rh) / g.Life.sum())), 2)))
SEN = pd.DataFrame(sen)

# ---------------- 崩刃时点敏感性（B：不良品暴露）
# 假设真实崩刃发生在寿命的 f 比例处；策略允许用到 Limit，则带病加工件数 = max(0, min(Limit,life) - f*life)
brk = []
for f in (0.7, 0.8, 0.9):
    for mode in ('round', 'ceil'):
        for name, g in R[R.Rounding == mode].groupby('Strategy'):
            expo = np.maximum(0, g.UsedPieces - f * g.Life)
            brk.append(dict(BreakFrac=f, Rounding=mode, Strategy=name, n_tools=len(g),
                            DefectivePiecesLow=int(np.floor(expo.sum())),
                            LivesWithExposure=int((expo > 0).sum()),
                            ExposurePerLife=round(float(expo.mean()), 2)))
BRK = pd.DataFrame(brk)

os.makedirs(OUT, exist_ok=True)
R.to_csv(os.path.join(OUT, 'decision_replay_per_life.csv'), index=False, encoding='utf-8-sig')
SEN.to_csv(os.path.join(OUT, 'decision_rho_sensitivity.csv'), index=False, encoding='utf-8-sig')
BRK.to_csv(os.path.join(OUT, 'decision_break_sensitivity.csv'), index=False, encoding='utf-8-sig')

print('\n[out] decision_replay_per_life.csv %d × %d' % R.shape)
print('[out] decision_rho_sensitivity.csv %d × %d' % SEN.shape)
print('[out] decision_break_sensitivity.csv %d × %d' % BRK.shape)
print('\n=== ρ=10 策略汇总（round 口径）===')
print(SEN[(SEN.Rounding == 'round') & (SEN.rho == 10)][
    ['Strategy', 'n_tools', 'Failures', 'Pieces', 'CostPerPiece', 'vs_StatusQuo']].to_string(index=False))
print('\n=== round vs ceil 对 b/h 的影响 ===')
print(R[R.Rounding == 'round'][['PhysicalLifeId', 'M0', 'b', 'h']].drop_duplicates().merge(
    R[R.Rounding == 'ceil'][['PhysicalLifeId', 'b', 'h']], on='PhysicalLifeId',
    suffixes=('_round', '_ceil')).query('b_round != b_ceil or h_round != h_ceil').to_string(index=False))
print('\n=== 崩刃时点敏感性（不良品暴露件数）===')
print(BRK[BRK.Rounding == 'round'].to_string(index=False))
print('\n=== 每把刀的信号触发与浪费 ===')
print(R[(R.Rounding == 'round')][['PhysicalLifeId', 'Life', 'M0', 'b', 'h',
                                  'Strategy', 'Limit', 'Failure', 'WastedPieces', 'SignalFired']]
      .pivot_table(index=['PhysicalLifeId', 'Life', 'M0', 'b', 'h'], columns='Strategy',
                   values='Limit').to_string())
