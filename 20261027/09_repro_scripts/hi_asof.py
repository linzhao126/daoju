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

import channel_map as CM

X_MIN, X_MAX, GRID_N = 19.0, 40.0, 40
Z_TOL, LOAD_FRAC, MIN_POINTS, DESC_TOL, MIN_SPAN, KEEP_FRAC, BASE_K = 1.0, 0.50, 8, 2.5, 5.0, 0.70, 10
ZREF_PIECES = 10
# ---------------------------------------------------------------- 通道映射（P1-08）
# 原来这里写死 'L_spindle_load' 等，等价于"本脚本只支持 4152 的 L 通道"。
# 现在走 channel_map：环境变量 CHANNEL=L|R 切换，字段名由映射层解析。
# 默认 L 时解析出的物理字段与原来完全一致，行为不变（Test-B 会验证）。
CHANNEL = os.environ.get('CHANNEL', 'L').upper()
CH = CM.ChannelMap(CHANNEL)
METRIC = CH.load
COLS = CH.cols(['time', 'allnum', 'x', 'z', 'load', 'spd'], extra=['life_cycle'])


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


def build_piece_index(raw_csv, cycles=None, cols=None):
    # ⚠ 不能写 `cols=COLS`：默认参数在**函数定义时**求值一次，之后改模块级 COLS 无效
    #    （切 R 通道时报 Usecols do not match，期望里还是 L_ 的列名）。P1-08 实测抓到的坑。
    cols = COLS if cols is None else cols
    d = pd.read_csv(raw_csv, usecols=cols, encoding='utf-8-sig')
    # ⚠ 原版写 `for c in cols[3:]`（按位置切片）——依赖列顺序，一旦调整 COLS 顺序就会
    #    漏掉某列或多转一列。改为显式按逻辑名列出数值列。
    for c in (CH.x, CH.z, CH.load, CH.spd, CH.allnum):
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors='coerce')
    d['_t'] = pd.to_datetime(d['_time'], format='%Y-%m-%d %H:%M:%S.%f', errors='coerce')
    d = d.dropna(subset=['life_cycle', CH.allnum, '_t'])
    if cycles is not None:
        d = d[d.life_cycle.isin(cycles)]
    idx = {}
    for cid, g in d.groupby('life_cycle'):
        pieces = sorted(int(a) for a in g[CH.allnum].unique())
        rows = {int(a): q.sort_values('_t').reset_index(drop=True)
                for a, q in g.groupby(CH.allnum)}
        idx[int(cid)] = dict(pieces=pieces, rows=rows)
    return idx


def z0_of(pieces, rows, zref_pieces=ZREF_PIECES):
    """z0 只用最前面 zref_pieces 件（先验常数），与 N 无关"""
    zs = []
    for a in pieces[:min(zref_pieces, len(pieces))]:
        sp = rows[a][rows[a][CH.spd].fillna(0) > 0]
        if len(sp):
            zs.append(sp[CH.z].round(1))
    if not zs:
        return None
    return float(pd.concat(zs).mode().iloc[0])


def compute_segments(pieces, rows, zref_pieces=ZREF_PIECES, metric=None, z0_override=None):
    """逐件几何定位（只依赖该件自身 + 前 10 件定的 z0）

    z0_override: 手工指定 z0（P1-08 移植标定时用；正常流程走 z0_of 自观测）
    """
    metric = METRIC if metric is None else metric       # 默认参数不能在定义时绑定
    z0 = float(z0_override) if z0_override is not None else z0_of(pieces, rows, zref_pieces)
    if z0 is None:
        return None, []
    cfg = _cfg()
    segs = []
    # ⚠ 适配垫片：`t1111_pipeline._xalign_piece` 内部 10+ 处写死 `L_` 字段名，
    #   且它是审核方 E01 证据的生成器，本轮不动。非 L 通道时把列名临时改写成 L_ 形式，
    #   只在调用它的那一瞬转换 —— **不能改 rows 本身**，因为 z0_of 用的是本通道列名。
    #   真正的解是给 `_xalign_piece` 加 channel_map 参数（需先报备）。
    need_shim = (CHANNEL != 'L')
    for i, a in enumerate(pieces, 1):
        df_in = CH.to_pipeline_names(rows[a]) if need_shim else rows[a]
        r = tp()._xalign_piece(df_in, z0, [metric], cfg)
        if r is not None and metric in r:
            # idx = 该件在周期内的**序号**（1-based）。
            # 必须按件序号截断，不能按"有效段的第 N 个"——否则某件被判废时，
            # 第 N 个有效段会落到第 N+1 件上，等于偷看未来（Test-B 抓到的正是这个）。
            segs.append(dict(idx=i, allnum=a, r=r))
    return z0, segs


def hi_from_segments(segs, N, mode='C1', base_k=BASE_K, metric=None):
    """给定段列表与截断件数 N，用**前缀**重算 grid / 覆盖 / 基线 / HI"""
    metric = METRIC if metric is None else metric       # 同上
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
