# -*- coding: utf-8 -*-
"""s22_grid_trace.py — FIX-06：网格矛盾正式结案

要回答的问题
  2026-10-12 生成 `xalign_HI.csv` 与 `xalign.txt` 时，X 网格到底怎么算的？
  审核方 E01 说 18.79~19.18mm（有 xalign.txt 为证）；
  我方 s04 说 ≈0mm（用今天代码复算）。
  两者必有一个是版本差异造成的。

做法
  1) 从 `out_T1111/xalign.txt` 解析当年 17 个周期的 GridLo/GridHi（历史真值）
  2) 用 5 种网格定义各自复算 17 个周期
  3) 逐个比对：哪种定义能复现历史 GridLo
  4) 再用每种定义生成的 HI 与历史 CSV 求相关，双重确认
  5) 同网格(19-40)下重算"离线段定位 vs 线上 align_piece"的 parity（修掉 c25 的重复匹配）

输出（14_整改执行_v2b/metrics/）
  grid_definitions.csv        5 种定义 × 17 周期的 GridLo/GridHi
  grid_match_to_history.csv   与历史值的偏差
  grid_mode_corr.csv          各定义 HI 与历史 CSV 的相关
  parity_samegrid.csv         同网格下的在线/离线逐件一致性
  grid_trace_summary.md       结案说明
"""
import os, re, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import hi_asof as H

HERE = os.path.dirname(__file__)
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))
RAW = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv')
XTXT = os.path.join(ROOT, 'out_T1111', 'xalign.txt')
HI_CSV = os.path.join(ROOT, 'out_T1111', 'xalign_HI.csv')
ONL = os.path.abspath(os.path.join(HERE, '..', '05_HI与换刀规则审计', 'online_pieces.csv'))
OUT = os.path.abspath(os.path.join(HERE, '..', '14_整改执行_v2b', 'metrics'))
os.makedirs(OUT, exist_ok=True)

# ---------------- 1) 解析历史真值
hist = {}
for line in open(XTXT, encoding='utf-8', errors='ignore'):
    m = re.match(r'^\s*(\d+)\s+(\d+)\s+(\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(\d+)', line)
    if m:
        c = int(m.group(1))
        hist[c] = dict(prog=int(m.group(2)), pieces=int(m.group(3)), z0=float(m.group(4)),
                       glo=float(m.group(5)), ghi=float(m.group(6)), gridpts=int(m.group(7)))
H = pd.DataFrame.from_dict(hist, orient='index').reset_index().rename(columns={'index': 'Cycle'})
print('[hist] 从 xalign.txt 解析到 %d 个周期；GridLo 范围 %.2f~%.2f'
      % (len(H), H.glo.min(), H.glo.max()))

# ---------------- 2) 载入原始行
idx = H_idx = H.build_piece_index(RAW) if False else None   # 名字占位，避免与 DataFrame H 冲突
import importlib
HA = importlib.import_module('hi_asof')
idx = HA.build_piece_index(RAW)
CYCLES = sorted([c for c in idx if len(idx[c]['pieces']) >= 20])
print('[idx] %d 个可算周期' % len(CYCLES))

# 预先算好"段"（一次），后面各定义只改网格
SEG = {}
for c in CYCLES:
    z0, segs = HA.compute_segments(idx[c]['pieces'], idx[c]['rows'])
    SEG[c] = dict(z0=z0, segs=segs)


def grid_of(segs, mode, clip=None):
    """按不同定义算网格；clip=下限（用于复现历史可能存在的 X 裁剪）"""
    us = [(s['r']['x_lo'], s['r']['x_hi']) for s in segs]
    if clip is not None:
        us = [(max(lo, clip), hi) for lo, hi in us if hi > clip]
    if not us:
        return None
    los = np.array([u[0] for u in us]); his = np.array([u[1] for u in us])
    if mode == 'p90p10':
        glo, ghi = np.percentile(los, 90), np.percentile(his, 10)
        if ghi - glo < 1.0:
            glo, ghi = np.median(los), np.median(his)
    elif mode == 'p50':
        glo, ghi = np.median(los), np.median(his)
    elif mode == 'minmax':
        glo, ghi = los.min(), his.max()
    elif mode == 'fixed1940':
        glo, ghi = 19.0, 40.0
    else:
        raise ValueError(mode)
    return float(glo), float(ghi)


DEFS = [('D1_p90_noClip', 'p90p10', None),
        ('D2_p90_clip19', 'p90p10', 19.0),
        ('D3_p50_noClip', 'p50', None),
        ('D4_minmax', 'minmax', None),
        ('D5_fixed1940', 'fixed1940', None)]

rows = []
for c in CYCLES:
    for name, mode, clip in DEFS:
        g = grid_of(SEG[c]['segs'], mode, clip)
        if g is None:
            continue
        rows.append(dict(Cycle=c, Def=name, GridLo=round(g[0], 3), GridHi=round(g[1], 3),
                         Span=round(g[1] - g[0], 3)))
G = pd.DataFrame(rows)
G.to_csv(os.path.join(OUT, 'grid_definitions.csv'), index=False, encoding='utf-8-sig')

# ---------------- 3) 与历史比对
cmp_rows = []
for _, r in H.iterrows():
    c = int(r.Cycle)
    sub = G[G.Cycle == c]
    for _, s in sub.iterrows():
        cmp_rows.append(dict(Cycle=c, Def=s.Def, HistLo=r.glo, DefLo=s.GridLo,
                             dLo=round(s.GridLo - r.glo, 3), dHi=round(s.GridHi - r.ghi, 3)))
CMP = pd.DataFrame(cmp_rows)
CMP.to_csv(os.path.join(OUT, 'grid_match_to_history.csv'), index=False, encoding='utf-8-sig')
print('\n=== 各定义复现历史 GridLo 的偏差（|dLo| 中位 / 最大） ===')
S = CMP.groupby('Def').agg(n=('dLo', 'size'), MedAbsDLo=('dLo', lambda x: x.abs().median()),
                           MaxAbsDLo=('dLo', lambda x: x.abs().max()),
                           MedDLo=('dLo', 'median')).round(3).reset_index()
print(S.to_string(index=False))
best = S.sort_values('MedAbsDLo').iloc[0]['Def']
print('\n最接近历史口径的定义：**%s**（|dLo| 中位 %.3f mm）' % (best, float(S.sort_values('MedAbsDLo').iloc[0].MedAbsDLo)))

# ---------------- 4) 每种定义生成 HI，与历史 CSV 求相关
old = pd.read_csv(HI_CSV, encoding='utf-8-sig')
corr_rows = []
for name, mode, clip in DEFS:
    cs = []
    for c in CYCLES:
        segs = SEG[c]['segs']
        if clip is not None:
            # 复现 X 裁剪：只保留 x_hi > clip 的件，并把 x_lo 抬到 clip
            segs2 = []
            for s in segs:
                r = dict(s['r'])
                if r['x_hi'] <= clip:
                    continue
                r['x_lo'] = max(r['x_lo'], clip)
                segs2.append(dict(idx=s['idx'], allnum=s['allnum'], r=r))
            segs = segs2
        if mode == 'fixed1940':
            rr = HA.hi_from_segments(segs, 10 ** 6, 'C2')
        elif mode == 'p90p10':
            rr = HA.hi_from_segments(segs, 10 ** 6, 'C1')
        else:
            # p50 / minmax 需要自定义网格：临时包装
            g = grid_of(segs, mode)
            if g is None:
                continue
            orig = HA.hi_from_segments
            # 直接用 C1 的流程但替换网格：简单起见按段重算
            sub = segs
            grid = np.linspace(g[0], g[1], HA.GRID_N)
            M = np.full((len(sub), HA.GRID_N), np.nan)
            for i, s_ in enumerate(sub):
                r = s_['r']
                xs = np.asarray(r['xs'], float); vs = np.asarray(r[HA.METRIC], float)
                ok = np.isfinite(xs) & np.isfinite(vs)
                if ok.sum() < 3:
                    continue
                msk = (grid >= r['x_lo']) & (grid <= r['x_hi'])
                M[i, msk] = np.interp(grid[msk], xs[ok], vs[ok])
            cov = np.mean(np.isfinite(M), axis=0) >= HA.KEEP_FRAC
            M[:, ~cov] = np.nan
            base = np.nanmedian(M[:min(10, len(M)), :], axis=0)
            with np.errstate(invalid='ignore', divide='ignore'):
                HIx = M / base.reshape(1, -1)
            rr = dict(ok=True, hi=np.nanmedian(HIx, axis=1), allnums=[s_['allnum'] for s_ in sub])
        if not rr.get('ok'):
            continue
        ref = old[old.life_cycle == c].sort_values('piece_idx')
        n = min(len(ref), len(rr['hi']))
        if n < 10:
            continue
        a = ref.HI_L_spindle_load.to_numpy(float)[:n]; b = np.asarray(rr['hi'], float)[:n]
        ok = np.isfinite(a) & np.isfinite(b)
        if ok.sum() > 5:
            cs.append(float(np.corrcoef(a[ok], b[ok])[0, 1]))
    corr_rows.append(dict(Def=name, N_cycles=len(cs), MedCorr=round(float(np.median(cs)), 4) if cs else np.nan,
                          MinCorr=round(float(np.min(cs)), 4) if cs else np.nan,
                          MaxCorr=round(float(np.max(cs)), 4) if cs else np.nan))
CR = pd.DataFrame(corr_rows).sort_values('MedCorr', ascending=False)
CR.to_csv(os.path.join(OUT, 'grid_mode_corr.csv'), index=False, encoding='utf-8-sig')
print('\n=== 各定义生成的 HI 与历史 CSV 的相关 ===')
print(CR.to_string(index=False))

# ---------------- 5) 同网格 parity（修 c25 重复匹配）
O = pd.read_csv(ONL, encoding='utf-8-sig')
O['LoadMed'] = pd.to_numeric(O.LoadMed, errors='coerce')
O = O[O.OK == 1].drop_duplicates(['LifeCycle', 'AllNum'])
O['HI_on'] = O.groupby('LifeCycle')['LoadMed'].transform(lambda s: s / np.nanmedian(s.iloc[:20]))
pr = []
for c in (25, 26, 38):
    segs = SEG[c]['segs']
    sub = []
    for s in segs:
        if s['r']['x_hi'] <= 19.0:
            continue
        r = dict(s['r']); r['x_lo'] = max(r['x_lo'], 19.0)
        sub.append(dict(idx=s['idx'], allnum=s['allnum'], r=r))
    rr = HA.hi_from_segments(sub, 10 ** 6, 'C2')
    if not rr.get('ok'):
        continue
    off = pd.DataFrame(dict(AllNum=rr['allnums'], HI_off=rr['hi'])).drop_duplicates('AllNum')
    on = O[O.LifeCycle == c][['AllNum', 'HI_on']]
    m = off.merge(on, on='AllNum').dropna()
    if len(m) < 10:
        continue
    r_ = float(np.corrcoef(m.HI_off, m.HI_on)[0, 1])
    pr.append(dict(Cycle=c, Matched=len(m), Corr=round(r_, 4),
                   MedAbsDiff=round(float((m.HI_off - m.HI_on).abs().median()), 4),
                   EndDiff=round(float(abs(np.median(m.HI_off[-10:]) - np.median(m.HI_on[-10:]))), 4)))
PR = pd.DataFrame(pr)
PR.to_csv(os.path.join(OUT, 'parity_samegrid.csv'), index=False, encoding='utf-8-sig')
print('\n=== 同网格(19-40) parity：离线段定位 vs 线上 ===')
print(PR.to_string(index=False))
print('\n[out] grid_definitions / grid_match_to_history / grid_mode_corr / parity_samegrid')
