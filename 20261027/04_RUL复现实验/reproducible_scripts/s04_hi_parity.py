# -*- coding: utf-8 -*-
"""s04_hi_parity.py — P0-05(a) 线上 align_piece 与离线 xalign 的逐件一致性

两边口径的结构性差异（读代码得出，本脚本负责量化）
  1) X 网格：离线 = 每周期鲁棒交集 linspace(p90(x_lo), p10(x_hi), 40)
             线上 = 固定 19.0~40.0 / 40 点
  2) 段定位：离线 = Z带 → 转速>0 → 负载前置筛选 → **X 单调下降段 + 啄切容差 2.5mm**
             线上 = X 段 19~41 → Z带 → 排序插值 （**缺单调下降段与啄切容差**）
  3) 件统计：离线 = 40 网格点各自除以「前 10 件同点基线」，再取件内中位
             线上 = 件内负载中位 ÷ 「前 20 件中位」（标量比）
  4) 丢弃规则：离线 = 网格点覆盖率 <70% 置 NaN、整周期 <40 件不成周期
             线上 = 有效点 <20 或 X 跨度 <1mm 或端点缺 3mm 即该件作废

复用而非复制：以 import 方式调用 t1111_pipeline._xalign_piece 与 simulator.align_piece
输出：05_HI与换刀规则审计/HI_online_offline_parity.csv + 逐件明细
"""
import os, sys, importlib.util
import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
RAW = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv')
OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '05_HI与换刀规则审计'))
TL = os.path.join(ROOT, '2ndPrediction', 'tool_log')

CYC = [25, 26, 38]          # 短 / 中 / 长
METRIC = 'L_spindle_load'
K_CMP = 10                  # 两边都用前 10 件归一，隔离"管道差异"而非"归一化差异"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


tp = _load(os.path.join(ROOT, 't1111_pipeline.py'), 't1')
sys.path.insert(0, TL)
import simulator  # noqa: E402

cfg = dict(z_tol=tp.XALIGN_Z_TOL, grid_n=tp.XALIGN_GRID_N, base_k=K_CMP,
           head_k=tp.XALIGN_HEAD_K, min_pieces=tp.XALIGN_MIN_PIECES,
           min_points=tp.XALIGN_MIN_POINTS, desc_tol=tp.XALIGN_DESC_TOL,
           min_span=tp.XALIGN_MIN_SPAN, keep_frac=tp.XALIGN_KEEP_FRAC,
           load_frac=tp.XALIGN_LOAD_FRAC, drop_cap=tp.XALIGN_DROP_CAP,
           cap_frac=tp.XALIGN_CAP_FRAC, min_fill=tp.XALIGN_MIN_FILL)

cols = ['_time', 'life_cycle', 'L_allnum', 'L_abso_x', 'L_abso_z',
        'L_spindle_load', 'L_spindle_speed']
d = pd.read_csv(RAW, usecols=cols, encoding='utf-8-sig')
d = d[d.life_cycle.isin(CYC)].copy()
d['_t'] = pd.to_datetime(d['_time'], format='%Y-%m-%d %H:%M:%S.%f', errors='coerce')
for c in cols[3:]:
    d[c] = pd.to_numeric(d[c], errors='coerce')
d = d.sort_values('_t').reset_index(drop=True)
print('[raw] 选中周期 %s，共 %d 行' % (CYC, len(d)))

summary, detail = [], []
for cid in CYC:
    g0 = d[d.life_cycle == cid].sort_values('_t')
    sp = g0[g0['L_spindle_speed'].fillna(0) > 0]
    z0 = float(sp['L_abso_z'].round(1).mode().iloc[0])
    cfg['cap_ref'] = float(sp['L_spindle_speed'].max())

    off, on = {}, {}
    for an, g in g0.groupby('L_allnum'):
        r = tp._xalign_piece(g, z0, [METRIC], cfg)
        if r is not None and METRIC in r:
            off[int(an)] = r
        rows = [{'x_pos': q[0], 'z_pos': q[1], 'spindle_load': q[2]}
                for q in g[['L_abso_x', 'L_abso_z', 'L_spindle_load']].itertuples(index=False, name=None)]
        lm, npt, curve = simulator.align_piece(rows)
        if lm is not None:
            on[int(an)] = (lm, npt, curve)

    keys = sorted(set(off) & set(on))
    if len(keys) < 10:
        print('[skip] c%d 共同件数 %d，太少' % (cid, len(keys)))
        continue

    # ---- 离线：按原流程建周期网格 + 覆盖率掩码 + 逐点归一
    los = np.array([off[k]['x_lo'] for k in keys])
    his = np.array([off[k]['x_hi'] for k in keys])
    glo = float(np.percentile(los, 90)); ghi = float(np.percentile(his, 10))
    if ghi - glo < 1.0:
        glo, ghi = float(np.median(los)), float(np.median(his))
    grid = np.linspace(glo, ghi, cfg['grid_n'])
    M = np.full((len(keys), cfg['grid_n']), np.nan)
    for i, k in enumerate(keys):
        p = off[k]
        xs, vs = p['xs'], p[METRIC]
        ok = np.isfinite(vs) & np.isfinite(xs)
        msk = (grid >= p['x_lo']) & (grid <= p['x_hi']) & ok.any()
        M[i, msk] = np.interp(grid[msk], xs[ok], vs[ok])
    cov = np.mean(np.isfinite(M), axis=0) >= cfg['keep_frac']
    M[:, ~cov] = np.nan
    with np.errstate(invalid='ignore', divide='ignore'):
        base = np.nanmedian(M[:K_CMP, :], axis=0)
        HI_off = np.nanmedian(M / base.reshape(1, -1), axis=1)

    # ---- 线上：标量中位 + 前 10 件归一（为了可比，也取 10）
    lm_on = np.array([on[k][0] for k in keys], float)
    HI_on = lm_on / np.median(lm_on[:K_CMP])
    lm_off = np.array([np.nanmedian(off[k][METRIC]) for k in keys], float)
    HI_off_scalar = lm_off / np.median(lm_off[:K_CMP])

    for i, k in enumerate(keys):
        detail.append(dict(PhysicalLifeId='4152-L-T1111-c%d' % cid, AllNum=k, PieceIdx=i + 1,
                           Xlo_off=round(off[k]['x_lo'], 3), Xhi_off=round(off[k]['x_hi'], 3),
                           Npts_off=int(off[k]['n']), Npts_on=int(on[k][1]),
                           LoadMed_off=round(float(lm_off[i]), 4), LoadMed_on=round(float(lm_on[i]), 4),
                           LoadDiff_pct=round(100 * (lm_on[i] - lm_off[i]) / lm_off[i], 3) if lm_off[i] else np.nan,
                           HI_off=round(float(HI_off[i]), 5), HI_on=round(float(HI_on[i]), 5),
                           HI_off_scalar=round(float(HI_off_scalar[i]), 5)))
    D = pd.DataFrame([r for r in detail if r['PhysicalLifeId'].endswith('c%d' % cid)])

    summary.append(dict(
        PhysicalLifeId='4152-L-T1111-c%d' % cid, N_pieces_common=len(keys),
        Z0=z0, Grid_off_lo=round(glo, 3), Grid_off_hi=round(ghi, 3),
        Grid_on_lo=19.0, Grid_on_hi=40.0, Grid_span_diff=round((40.0 - 19.0) - (ghi - glo), 3),
        Npts_off_median=float(np.median([off[k]['n'] for k in keys])),
        Npts_on_median=float(np.median([on[k][1] for k in keys])),
        LoadMed_off_median=round(float(np.median(lm_off)), 3),
        LoadMed_on_median=round(float(np.median(lm_on)), 3),
        LoadMed_median_diff_pct=round(100 * (np.median(lm_on) - np.median(lm_off)) / np.median(lm_off), 2),
        LoadMed_MAPE_pct=round(float(np.mean(np.abs(lm_on - lm_off) / np.maximum(lm_off, 1e-9)) * 100), 2),
        HI_corr_pearson=round(float(np.corrcoef(HI_off, HI_on)[0, 1]), 4),
        HI_corr_spearman=round(float(pd.Series(HI_off).corr(pd.Series(HI_on), method='spearman')), 4),
        HI_max_abs_diff=round(float(np.max(np.abs(HI_off - HI_on))), 4),
        HI_last10_off=round(float(np.median(HI_off[-10:])), 4),
        HI_last10_on=round(float(np.median(HI_on[-10:])), 4),
        Trend_off_pct_per10=round(float(100 * (np.median(HI_off[-10:]) - np.median(HI_off[:10])) / np.median(HI_off[:10])), 2),
        Trend_on_pct_per10=round(float(100 * (np.median(HI_on[-10:]) - np.median(HI_on[:10])) / np.median(HI_on[:10])), 2)))

S = pd.DataFrame(summary)
DD = pd.DataFrame(detail)
os.makedirs(OUT, exist_ok=True)
S.to_csv(os.path.join(OUT, 'HI_online_offline_parity.csv'), index=False, encoding='utf-8-sig')
DD.to_csv(os.path.join(OUT, 'HI_parity_perpiece.csv'), index=False, encoding='utf-8-sig')
print('[out] HI_online_offline_parity.csv %d × %d' % S.shape)
print('[out] HI_parity_perpiece.csv        %d × %d' % DD.shape)
print()
print(S.to_string(index=False))
