# -*- coding: utf-8 -*-
"""s52_r02_grant_audit.py —— R02：HI 延长分支（grant 锁存）的逐件审计

审核要求（R02）
--------------
「HI 延长分支完整审计：grant 触发、锁存后变化、冷热启动、跨程序逐件例。」

为什么必须做
-----------
线上 `server.py` 的延长逻辑是：

    if used >= b:                     # b = ceil(0.4 * M0)
        granted = (hi_now - 1.0) >= TAU   # TAU = 1%
        kv_set(gkey, "1" if granted else "0")   # ← 只评估一次，锁存

问题在于：这**只评估一次**，之后无论 HI 怎么变都不再复判。
如果评估那一刻正好遇上负载噪声（单件 σ≈2%），锁存的结论可能整个生命周期都错。
本脚本要把这件事量化：**锁存 vs 不锁存，结论会差多少把刀。**

口径（与 server.py 逐字对齐）
---------------------------
K_BASE=20  新刀基线取前 20 件
W=10       hi_now = 最近 10 件 HI 的中位
TAU=0.01   HI 相对基线上升 ≥1% 才放行
BASE_F=0.4 复核点 b = ceil(0.4 * M0)
EXT_F=0.9  延长线 h = ceil(0.9 * M0)

注：`HI_merged_28.csv` 的 `HI_L_spindle_load` 已按各自周期的前 10 件归一，
与线上"以 base_allnum 之后的件做基线"在同一把刀内等价。
"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
OUT = os.path.join(BASE, '附件_证据', '52_R02_延长分支审计')
os.makedirs(OUT, exist_ok=True)

HI = os.path.join(BASE, '24_程序归属修正', 'HI_merged_28.csv')

K_BASE, W, TAU = 20, 10, 0.01
BASE_F, EXT_F = 0.4, 0.9
MIN_N_BASIC, MIN_N_P85 = 3, 5
DROP = (38, 51)          # 两把件数与台账严重不符，不进基准池（但仍审计其信号）

h = pd.read_csv(HI, encoding='utf-8-sig')
curves = {}
prog_of = {}
for (p, c), g in h.groupby(['prog', 'life_cycle']):
    curves[int(c)] = g.sort_values('piece_idx')['HI_L_spindle_load'].to_numpy(float)
    prog_of[int(c)] = int(p)

print('=' * 100)
print('R02　HI 延长分支（grant 锁存）审计')
print('=' * 100)
print('周期 %d 个，件数合计 %d' % (len(curves), sum(len(v) for v in curves.values())))
print('参数：K_BASE=%d  W=%d  TAU=%.2f  b=%.1f·M0  h=%.1f·M0  基准池剔除 %s'
      % (K_BASE, W, TAU, BASE_F, EXT_F, list(DROP)))


def hi_now_at(v, n):
    """线上口径：最近 W 件的 HI 中位（n = 已用件数，取 [n-W, n)）"""
    seg = v[max(0, n - W):n]
    seg = seg[np.isfinite(seg)]
    return float(np.median(seg)) if len(seg) >= 3 else None


rows, curve_rows = [], []
for c in sorted(curves):
    v = curves[c]
    life = len(v)
    p = prog_of[c]
    pool = [x for x in curves if prog_of[x] == p and x != c and x not in DROP]
    n_hist = len(pool)
    rec = dict(Cycle=c, Prog=p, Life=life, NHist=n_hist,
               InDrop=(c in DROP), Status='', M0=np.nan,
               B=np.nan, H=np.nan, HiAtB=np.nan, Granted=None,
               LimitIfGranted=np.nan,
               Flips=np.nan, FirstFlipAt=np.nan,
               ReachedB=None, ReachedH=None)
    if n_hist < MIN_N_BASIC:
        rec['Status'] = 'NO_HIST'          # 同程序历史不足 3 把 → 线上不判
        rows.append(rec)
        continue
    m0 = float(np.median([len(curves[x]) for x in pool]))
    rec['M0'] = round(m0, 1)
    b = int(np.ceil(BASE_F * m0))
    hh = int(np.ceil(EXT_F * m0))
    rec['B'], rec['H'] = b, hh
    rec['ReachedB'] = life >= b
    rec['ReachedH'] = life >= hh
    if not rec['ReachedB']:
        rec['Status'] = 'NEVER_REACHED_B'
        rows.append(rec)
        continue

    hn_b = hi_now_at(v, b)
    rec['HiAtB'] = round(hn_b, 4) if hn_b is not None else np.nan
    g = bool(hn_b is not None and (hn_b - 1.0) >= TAU)
    rec['Granted'] = g
    rec['LimitIfGranted'] = hh if g else b
    rec['Status'] = 'OK'

    # 锁存 vs 不锁存：从 b 到 min(h, life) 逐件复判，统计翻转次数
    flips, first = 0, np.nan
    prev = g
    top = min(hh, life)
    for n in range(b + 1, top + 1):
        hn = hi_now_at(v, n)
        if hn is None:
            continue
        gn = bool((hn - 1.0) >= TAU)
        curve_rows.append(dict(Cycle=c, Prog=p, N=n, HiNow=round(hn, 4),
                               GrantedNow=gn, GrantedLocked=g))
        if gn != prev:
            flips += 1
            if np.isnan(first):
                first = n
            prev = gn
    rec['Flips'] = flips
    rec['FirstFlipAt'] = first
    rows.append(rec)

A = pd.DataFrame(rows)
C = pd.DataFrame(curve_rows)
A.to_csv(os.path.join(OUT, 'r02_grant_audit.csv'), index=False, encoding='utf-8-sig')
C.to_csv(os.path.join(OUT, 'r02_rejudge_curve.csv'), index=False, encoding='utf-8-sig')

print('\n' + '-' * 100)
print('逐周期（按主程序分组，同程序历史做基准）')
print('-' * 100)
print(A.to_string(index=False))

# ---------------- 汇总
ok = A[(A.Status == 'OK')]
print('\n' + '=' * 100)
print('汇总')
print('=' * 100)
print('可判周期（同程序历史 ≥3）: %d / %d' % (len(A[A.Status != 'NO_HIST']), len(A)))
print('  其中走到复核点 b 的      : %d' % len(ok))
print('  从未走到 b（寿命太短）    : %d' % int((A.Status == 'NEVER_REACHED_B').sum()))
print('  同程序历史不足           : %d   ← 线上只会显示「—」' % int((A.Status == 'NO_HIST').sum()))

if len(ok):
    # Granted 列因混有 None 而是 object dtype，`~` 会当成取负索引 → 显式转 bool
    _g = ok.Granted.astype(bool)
    gr, ng = ok[_g], ok[~_g]
    print('\n放行（granted=True）: %d 把，其中真的活过 h 的 %d 把（%.0f%%）'
          % (len(gr), int(gr.ReachedH.sum()), 100 * gr.ReachedH.mean() if len(gr) else 0))
    print('拦截（granted=False）: %d 把，其中活过 h 的 %d 把（%.0f%%）← 这些是"本可延长却被提前换"'
          % (len(ng), int(ng.ReachedH.sum()), 100 * ng.ReachedH.mean() if len(ng) else 0))
    print('\n锁存 vs 逐件复判：')
    print('  发生过翻转的周期: %d / %d' % (int((ok.Flips > 0).sum()), len(ok)))
    print('  翻转次数 中位 %.0f  最大 %.0f' % (ok.Flips.median(), ok.Flips.max()))
    fj = ok[ok.Flips > 0]
    if len(fj):
        print('  首次翻转发生的件数（相对 b）:')
        for _, r in fj.iterrows():
            print('    c%-3d b=%-3d 首次翻转子 %-4.0f 共翻 %d 次' % (r.Cycle, r.B, r.FirstFlipAt, r.Flips))

# ---------------- 机制：hi_now 在 b 附近的噪声有多大
print('\n' + '=' * 100)
print('机制：复核点附近 hi_now 的抖动 vs 判据 TAU=1%')
print('=' * 100)
sd_rows = []
for c in sorted(curves):
    r = A[A.Cycle == c]
    if not len(r) or r.iloc[0].Status != 'OK':
        continue
    b = int(r.iloc[0].B)
    v = curves[c]
    vals = [hi_now_at(v, n) for n in range(b, min(b + 11, len(v) + 1))]
    vals = [x for x in vals if x is not None]
    if len(vals) >= 5:
        sd_rows.append(dict(Cycle=c, B=b, N=len(vals),
                            HiRange=round(max(vals) - min(vals), 4),
                            HiSd=round(float(np.std(vals, ddof=1)), 4)))
S = pd.DataFrame(sd_rows)
S.to_csv(os.path.join(OUT, 'r02_hi_noise.csv'), index=False, encoding='utf-8-sig')
if len(S):
    print('b 之后 10 件内 hi_now 的波动：')
    print('  极差 中位 %.4f（%.2f%%）  最大 %.4f（%.2f%%）'
          % (S.HiRange.median(), 100 * S.HiRange.median(),
             S.HiRange.max(), 100 * S.HiRange.max()))
    print('  标准差 中位 %.4f（%.2f%%）' % (S.HiSd.median(), 100 * S.HiSd.median()))
    print('  → 判据 TAU=1%%；若极差中位 > 1%%，说明**评估点选在哪一件，结论就可能翻转**')
    n_over = int((S.HiRange > TAU).sum())
    print('  极差 > 1%% 的周期：%d / %d（%.0f%%）' % (n_over, len(S), 100 * n_over / len(S)))

# ---------------- 反事实：改在 h 处复判（而不是在 b 处锁存）
print('\n' + '=' * 100)
print('反事实：把复核点从 b 挪到 h（0.9·M0），结论会变几把')
print('=' * 100)
rows2 = []
for c in sorted(curves):
    r = A[A.Cycle == c]
    if not len(r) or r.iloc[0].Status != 'OK':
        continue
    r = r.iloc[0]
    v = curves[c]
    b, hh = int(r.B), int(r.H)
    gb = bool(r.Granted)
    gh = None
    if len(v) >= hh:
        hn = hi_now_at(v, hh)
        if hn is not None:
            gh = bool((hn - 1.0) >= TAU)
    rows2.append(dict(Cycle=c, Prog=int(r.Prog), Life=int(r.Life), B=b, H=hh,
                      GrantedAtB=gb, GrantedAtH=gh,
                      Changed=(gh is not None and gh != gb),
                      ReachedH=bool(len(v) >= hh)))
F = pd.DataFrame(rows2)
F.to_csv(os.path.join(OUT, 'r02_counterfactual_h.csv'), index=False, encoding='utf-8-sig')
print(F.to_string(index=False))
n_ch = int(F.Changed.sum())
n_cmp = int(F.GrantedAtH.notna().sum())
print('\n可比较的周期 %d 个，结论改变 %d 个（%.0f%%）' % (n_cmp, n_ch, 100 * n_ch / n_cmp if n_cmp else 0))

# ---------------- 四种规则的产能/风险权衡
print('\n' + '=' * 100)
print('四种规则的件数代价（提前换 vs 超期用）')
print('=' * 100)
M = A[A.Status == 'OK'].copy().merge(F[['Cycle', 'GrantedAtH']], on='Cycle', how='left')
for cc in ('B', 'H', 'Life'):
    M[cc] = M[cc].astype(int)


def _both(r):
    return bool(r['Granted']) and bool(r['GrantedAtH']) if pd.notna(r['GrantedAtH']) else False


rules = {
    'b 处锁存（现状）': [int(r['H']) if bool(r['Granted']) else int(r['B']) for _, r in M.iterrows()],
    'b 与 h 都放行': [int(r['H']) if _both(r) else int(r['B']) for _, r in M.iterrows()],
    '纯计数（一律到 h）': [int(r['H']) for _, r in M.iterrows()],
    '纯计数（一律到 b）': [int(r['B']) for _, r in M.iterrows()],
}
tr = []
for k, lims in rules.items():
    early = sum(max(0, int(r['Life']) - l) for (_, r), l in zip(M.iterrows(), lims))
    over = sum(max(0, l - int(r['Life'])) for (_, r), l in zip(M.iterrows(), lims))
    tr.append(dict(Rule=k, EarlyCut=int(early), Overrun=int(over), Total=int(early + over)))
T = pd.DataFrame(tr)
T.to_csv(os.path.join(OUT, 'r02_rule_tradeoff.csv'), index=False, encoding='utf-8-sig')
print(T.to_string(index=False))
print('\n注：提前换 = 寿命还有剩就换（浪费产能）；超期 = 用到超过人工换刀点（风险）')
print('  → 现状规则相对"纯计数到 h"：少超期 %d 件，多提前换 %d 件'
      % (int(T[T.Rule == '纯计数（一律到 h）'].Overrun.iloc[0] - T[T.Rule == 'b 处锁存（现状）'].Overrun.iloc[0]),
         int(T[T.Rule == 'b 处锁存（现状）'].EarlyCut.iloc[0] - T[T.Rule == '纯计数（一律到 h）'].EarlyCut.iloc[0])))
print('  → 这个取舍值不值，取决于"超期一件"比"提前换一件"贵多少（= ρ 的一端），本数据不含')

# ---------------- 冷启动影响（无 tool_events 时 base = min_allnum-1）
print('\n' + '=' * 100)
print('冷/热启动的影响面')
print('=' * 100)
import sqlite3
db = os.path.join(BASE, '03_原始数据与标签', 'tool_db_snapshot', 'tool.db.readonly_copy')
if os.path.exists(db):
    cn = sqlite3.connect(db)
    n_ev = cn.execute("SELECT COUNT(*) FROM tool_events").fetchone()[0]
    n_pc = cn.execute("SELECT COUNT(*) FROM pieces").fetchone()[0]
    print('线上快照库：tool_events %d 行，pieces %d 行' % (n_ev, n_pc))
    print('→ tool_events 为 0 表示**从未有人点过"已换新刀"**，所有刀龄都走冷启动（base = 最小 allnum − 1）')
    print('→ 冷启动下 base_src=cold，页面用「≥N」显示；judge_ok 不检查起点是否人工确认（审核 P0-MOD-04，未修）')
else:
    print('（快照库不存在，跳过）')

print('\n[out] %s/r02_grant_audit.csv  r02_rejudge_curve.csv' % OUT)
