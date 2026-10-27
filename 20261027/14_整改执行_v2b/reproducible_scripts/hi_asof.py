# -*- coding: utf-8 -*-
"""hi_asof.py — FIX-02：因果（as_of_N）单件 HI 构造

为什么重写
  现行离线 `cmd_xalign` 为每个周期一次性算出 z0 / grid / 覆盖率掩码：
    z0      = 全周期 speed>0 行的 round(z,1) 众数        ← 看过整段
    glo/ghi = linspace(p90(x_lo), p10(x_hi))             ← 看过全部件
    cov     = mean(finite) >= 0.70                       ← 看过全部件
  所以"截断到前 N 件"只截断了**取值**，HI 的**构造**仍用了未来。

关键结构性观察（让本模块既能因果、又不慢）
  单件的几何定位 `_xalign_piece(g, z0)` 只依赖：
    ① 该件自己的行  ② z0
  而 z0 只取前 `zref_pieces` 件（先验常数 10），与 N 无关。
  ⇒ 因此"逐件段"可以一次算完，随后的 N 截断只是取前缀；
    真正需要按 N 重算的只有 grid / 覆盖率 / 基线 / HI 四步。
  这不是近似：对任意 N，前缀件的段与"只用前 N 件重算"逐位相同（Test-B 会断言）。

两种网格
  C1 自观测：grid_N = linspace(p90(x_lo[1..N]), p10(x_hi[1..N]), 40)
  C2 冻结  ：grid = linspace(19.0, 40.0, 40)   （部署前冻结，与线上同口径）
"""
import os
import numpy as np
import pandas as pd

X_MIN, X_MAX, GRID_N = 19.0, 40.0, 40
Z_TOL, LOAD_FRAC, MIN_POINTS, DESC_TOL, MIN_SPAN, KEEP_FRAC, BASE_K = 1.0, 0.50, 8, 2.5, 5.0, 0.70, 10
ZREF_PIECES = 10
METRIC = 'L_spindle_load'
COLS = ['_time', 'life_cycle', 'L_allnum', 'L_abso_x', 'L_abso_z', 'L_spindle_load', 'L_spindle_speed']


def _cfg(cap_ref=0.0):
    return dict(z_tol=Z_TOL, grid_n=GRID_N, base_k=BASE_K, head_k=15, min_pieces=40,
                min_points=MIN_POINTS, desc_tol=DESC_TOL, min_span=MIN_SPAN,
                keep_frac=KEEP_FRAC, load_frac=LOAD_FRAC, drop_cap=False, cap_frac=0.98,
                min_fill=0.50, cap_ref=cap_ref)


def load_tp():
    """复用 t1111_pipeline 的 _xalign_piece，不复制、不改主流水线"""
    import importlib.util
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
    spec = importlib.util.spec_from_file_location('t1111_pipeline',
                                                  os.path.join(root, 't1111_pipeline.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_TP = None


def tp():
    global _TP
    if _TP is None:
        _TP = load_tp()
    return _TP


def build_piece_index(raw_csv, cycles=None, cols=COLS):
    d = pd.read_csv(raw_csv, usecols=cols, encoding='utf-8-sig')
    for c in cols[3:]:
        d[c] = pd.to_numeric(d[c], errors='coerce')
    d['_t'] = pd.to_datetime(d['_time'], format='%Y-%m-%d %H:%M:%S.%f', errors='coerce')
    d = d.dropna(subset=['life_cycle', 'L_allnum', '_t'])
    if cycles is not None:
        d = d[d.life_cycle.isin(cycles)]
    idx = {}
    for cid, g in d.groupby('life_cycle'):
        pieces = sorted(int(a) for a in g.L_allnum.unique())
        rows = {int(a): q.sort_values('_t').reset_index(drop=True)
                for a, q in g.groupby('L_allnum')}
        idx[int(cid)] = dict(pieces=pieces, rows=rows)
    return idx


def z0_of(pieces, rows, zref_pieces=ZREF_PIECES):
    """z0 只用最前面 zref_pieces 件（先验常数），与 N 无关"""
    zs = []
    for a in pieces[:min(zref_pieces, len(pieces))]:
        sp = rows[a][rows[a]['L_spindle_speed'].fillna(0) > 0]
        if len(sp):
            zs.append(sp['L_abso_z'].round(1))
    if not zs:
        return None
    return float(pd.concat(zs).mode().iloc[0])


def compute_segments(pieces, rows, zref_pieces=ZREF_PIECES, metric=METRIC):
    """逐件几何定位（只依赖该件自身 + 前 10 件定的 z0）"""
    z0 = z0_of(pieces, rows, zref_pieces)
    if z0 is None:
        return None, []
    cfg = _cfg()
    segs = []
    for i, a in enumerate(pieces, 1):
        r = tp()._xalign_piece(rows[a], z0, [metric], cfg)
        if r is not None and metric in r:
            # idx = 该件在周期内的**序号**（1-based）。
            # 必须按件序号截断，不能按"有效段的第 N 个"——否则某件被判废时，
            # 第 N 个有效段会落到第 N+1 件上，等于偷看未来（Test-B 抓到的正是这个）。
            segs.append(dict(idx=i, allnum=a, r=r))
    return z0, segs


def hi_from_segments(segs, N, mode='C1', base_k=BASE_K, metric=METRIC):
    """给定段列表与截断件数 N，用**前缀**重算 grid / 覆盖 / 基线 / HI"""
    sub = [s for s in segs if s['idx'] <= int(N)]
    if len(sub) < 3:
        return dict(ok=False, reason='too_few_pieces')
    los = np.array([s['r']['x_lo'] for s in sub])
    his = np.array([s['r']['x_hi'] for s in sub])
    if mode == 'C2':
        glo, ghi = X_MIN, X_MAX
    else:
        glo = float(np.percentile(los, 90)); ghi = float(np.percentile(his, 10))
        if ghi - glo < 1.0:
            glo, ghi = float(np.median(los)), float(np.median(his))
    if ghi - glo < 1e-6:
        return dict(ok=False, reason='degenerate_grid')
    grid = np.linspace(glo, ghi, GRID_N)

    M = np.full((len(sub), GRID_N), np.nan)
    for i, s_ in enumerate(sub):
        r = s_['r']
        xs = np.asarray(r['xs'], float); vs = np.asarray(r[metric], float)
        ok = np.isfinite(xs) & np.isfinite(vs)
        if ok.sum() < 3:
            continue
        msk = (grid >= r['x_lo']) & (grid <= r['x_hi'])
        M[i, msk] = np.interp(grid[msk], xs[ok], vs[ok])
    cov = np.mean(np.isfinite(M), axis=0) >= KEEP_FRAC
    M[:, ~cov] = np.nan

    bk = min(base_k, M.shape[0])
    base = np.nanmedian(M[:bk, :], axis=0)
    with np.errstate(invalid='ignore', divide='ignore'):
        HI = M / base.reshape(1, -1)
    HI[~np.isfinite(HI)] = np.nan
    hi = np.nanmedian(HI, axis=1)
    return dict(ok=True, N=int(N), mode=mode, grid_lo=round(glo, 4), grid_hi=round(ghi, 4),
                n_grid_used=int(cov.sum()), hi=hi,
                allnums=[s_['allnum'] for s_ in sub], idxs=[s_['idx'] for s_ in sub])


def hi_asof(pieces, rows, N, mode='C1', zref_pieces=ZREF_PIECES, base_k=BASE_K):
    """便捷入口：一次算完（内部仍是"先段后前缀"，与 hi_from_segments 等价）"""
    z0, segs = compute_segments(pieces, rows, zref_pieces)
    if z0 is None:
        return dict(ok=False, reason='no_speed_rows')
    out = hi_from_segments(segs, N, mode, base_k)
    out.update(z0=z0, n_pieces_ok=len(segs),
               used=len([s for s in segs if s['idx'] <= int(N)]))
    return out
