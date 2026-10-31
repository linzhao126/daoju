# -*- coding: utf-8 -*-
"""s05_online_hi_signal.py — 用「线上 align_piece」重算全周期 HI，检验线上信号

为什么必须单独做
  s04 已证明：线上 HI 与离线 HI 的逐件相关只有 0.17~0.45，网格跨度差约 19mm。
  也就是说，过去所有「HI 有信号 / 无信号」的结论都来自**离线 xalign**，
  而线上系统跑的是另一条管道。线上信号有没有信号，此前从未验证。

做法
  1) 对 20 个周期逐件跑 simulator.align_piece（与 server.py 完全同一函数）
     → 每件 load_med + n_points + 40 点曲线
  2) 每周期 HI = load_med / median(前 20 件)，与 server.py:585-591 同口径
  3) 逐周期 Spearman r（件序 vs HI），与离线 xalign_HI.csv 的 r 并列
  4) 同一套 LOCO：M1-online = (1 + b×rel_rise)×M0，与 M0-A / M0-B 比

输出
  05_HI与换刀规则审计/online_pieces.csv        逐件（可复用缓存）
  05_HI与换刀规则审计/online_hi_signal.csv     逐周期信号强度（线上 vs 离线）
  04_RUL复现实验/predictions_loco_onlinehi.csv
  04_RUL复现实验/metrics_online_hi.csv
"""
import os, sys, importlib.util
import numpy as np
import pandas as pd

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import wsroot as _wsroot          # G2：不再数 '..'，按工作区特征向上定位
ROOT = _wsroot.find(_os.path.dirname(_os.path.abspath(__file__)))
RAW = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv')
HI_CSV = os.path.join(ROOT, 'out_T1111', 'xalign_HI.csv')
TL = os.path.join(ROOT, '2ndPrediction', 'tool_log')
OUT5 = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '05_HI与换刀规则审计'))
OUT4 = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '04_RUL复现实验'))

K, W, CUTS = 20, 10, [20, 40, 60, 80]

sys.path.insert(0, TL)
import simulator  # noqa: E402

cols = ['_time', 'life_cycle', 'L_allnum', 'L_abso_x', 'L_abso_z', 'L_spindle_load']
d = pd.read_csv(RAW, usecols=cols, encoding='utf-8-sig')
for c in cols[3:]:
    d[c] = pd.to_numeric(d[c], errors='coerce')
d = d.dropna(subset=['life_cycle', 'L_allnum'])
print('[raw] %d 行 / %d 周期' % (len(d), d.life_cycle.nunique()))

# ---------------- 逐件跑线上 align_piece
recs = []
for cid, g in d.groupby('life_cycle'):
    g = g.sort_values('_time')
    for an, q in g.groupby('L_allnum'):
        rows = [{'x_pos': r[0], 'z_pos': r[1], 'spindle_load': r[2]}
                for r in q[['L_abso_x', 'L_abso_z', 'L_spindle_load']].itertuples(index=False, name=None)]
        lm, npt, curve = simulator.align_piece(rows)
        recs.append(dict(PhysicalLifeId='4152-L-T1111-c%d' % cid, LifeCycle=int(cid),
                         AllNum=int(an), Rows=len(rows),
                         LoadMed=lm, NPoints=npt, OK=int(lm is not None)))
OP = pd.DataFrame(recs).sort_values(['LifeCycle', 'AllNum'])
OP.to_csv(os.path.join(OUT5, 'online_pieces.csv'), index=False, encoding='utf-8-sig')
print('[out] online_pieces.csv %d 行，有效件 %d / 总件 %d'
      % (len(OP), int(OP.OK.sum()), len(OP)))

# ---------------- 逐周期 HI 与信号强度
off = pd.read_csv(HI_CSV, encoding='utf-8-sig')
off_r = {}
for (p, c), g in off.groupby(['prog', 'life_cycle']):
    g = g.sort_values('piece_idx')
    y = g['HI_L_spindle_load'].to_numpy(float)
    idx = np.arange(1, len(y) + 1, dtype=float)
    r = pd.Series(y).corr(pd.Series(idx), method='spearman') if len(y) > 5 else np.nan
    off_r[int(c)] = (int(p), len(y), r)

sig = []
for cid, g in OP.groupby('LifeCycle'):
    g = g.sort_values('AllNum')
    v = g[g.OK == 1].LoadMed.to_numpy(float)
    if len(v) < 10:
        continue
    base = float(np.median(v[:K]))
    hi = v / base
    idx = np.arange(1, len(hi) + 1, dtype=float)
    r = pd.Series(hi).corr(pd.Series(idx), method='spearman')
    po, no, ro = off_r.get(int(cid), (np.nan, 0, np.nan))
    sig.append(dict(PhysicalLifeId='4152-L-T1111-c%d' % cid, LifeCycle=int(cid),
                    Prog_online=po, N_online=len(hi), N_offline=no,
                    HI_first10=round(float(np.median(hi[:10])), 4),
                    HI_last10=round(float(np.median(hi[-10:])), 4),
                    Rise_pct=round(100 * (np.median(hi[-10:]) - np.median(hi[:10])) / 1.0, 2),
                    r_online=round(float(r), 4), r_offline=round(float(ro), 4) if ro == ro else np.nan,
                    SameSign=('YES' if (r > 0) == (ro > 0) else 'NO') if ro == ro else ''))
SG = pd.DataFrame(sig).sort_values('LifeCycle')
SG.to_csv(os.path.join(OUT5, 'online_hi_signal.csv'), index=False, encoding='utf-8-sig')
print('\n=== 逐周期信号强度（线上 vs 离线）===')
print(SG.to_string(index=False))

# ---------------- 同一套 LOCO（M1-online）
cur = {}
for cid, g in OP.groupby('LifeCycle'):
    v = g[g.OK == 1].sort_values('AllNum').LoadMed.to_numpy(float)
    if len(v) >= max(K, W) + 5:
        cur[int(cid)] = v
ncyc_prog = {}
for cid in cur:
    p = off_r.get(cid, (None,))[0]
    ncyc_prog[cid] = p
cnt = pd.Series(list(ncyc_prog.values())).value_counts()
keep = sorted([c for c in cur if ncyc_prog[c] is not None and cnt[ncyc_prog[c]] >= 3])
L = {c: len(cur[c]) for c in keep}
print('\n[LOCO] 线上管道可用周期 %d，件数合计 %d' % (len(keep), sum(L.values())))

rows = []
for N in CUTS:
    sub = [c for c in keep if L[c] > N]
    F = {}
    for c in sub:
        hi = cur[c] / np.median(cur[c][:K])
        F[c] = float(np.median(hi[N - W:N]) - 1.0)
    for c in sub:
        pool = [o for o in keep if ncyc_prog[o] == ncyc_prog[c] and o != c]
        sib = [L[o] for o in pool]
        m0a = float(np.median(sib))
        cond = [x - N for x in sib if x > N]
        m0b = float(np.median(cond)) if cond else float(max(1.0, m0a - N))
        xv = np.array([F[o] for o in pool if o in F], float)
        yv = np.array([L[o] / m0a - 1.0 for o in pool if o in F], float)
        b = float((xv * yv).sum() / max((xv * xv).sum(), 1e-12))
        rows.append(dict(PhysicalLifeId='4152-L-T1111-c%d' % c, Program=ncyc_prog[c], CutoffN=N,
                         RelRise=round(F[c], 6), FitB=round(b, 4),
                         M0A=round(m0a - N, 2), M0B=round(m0b, 2),
                         M1online=round((1.0 + b * F[c]) * m0a - N, 2),
                         Actual=L[c] - N))
P = pd.DataFrame(rows)
for m in ('M0A', 'M0B', 'M1online'):
    P['Abs_' + m] = (P[m] - P.Actual).abs()
P.to_csv(os.path.join(OUT4, 'predictions_loco_onlinehi.csv'), index=False, encoding='utf-8-sig')

MS = []
for N, g in P.groupby('CutoffN'):
    for m in ('M0A', 'M0B', 'M1online'):
        MS.append(dict(CutoffN=N, Model=m, n=len(g), MAE=round(g['Abs_' + m].mean(), 2),
                       Bias=round(float((g[m] - g.Actual).mean()), 2),
                       Over=int((g[m] > g.Actual).sum())))
MS = pd.DataFrame(MS)
MS.to_csv(os.path.join(OUT4, 'metrics_online_hi.csv'), index=False, encoding='utf-8-sig')
print('\n=== 线上 HI 的 LOCO（与离线同口径）===')
print(MS.to_string(index=False))
print('\n[注] 本表 n 可能与离线表不同：线上管道对部分件判废，周期可用件数更少')
