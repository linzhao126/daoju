# -*- coding: utf-8 -*-
"""s25_chipping_row.py — FIX-05：崩刃 A 的因果 + 相位残差重做

v1 的三条病灶与对治
  ① 健康段用最终寿命 n            → 模板固定用**前 10 件**（先验常数，与 n 无关）
  ② det 靠 CUSUM>0 累积            → **连续 3 个越阈** + 冷却 20 件
  ③ 件级三个标量（med/peak/cv）    → **件内 40 点相位残差**（逐网格点减模板，再取最大偏离）

为什么不用"原始行"直接比
  实测同一 X 上有两个工步，负载双峰（中位 1606 / p90 5286）。
  不做 X/Z 相位对齐，残差会被工步差异淹没（第一版阈值被顶到 6000+，全周期零事件）。
  这里用 `_xalign_piece` 已对齐的 40 点曲线做残差 —— 当前数据能达到的最接近"行级"的口径。
  真正的逐采样点实时检测需要边缘推理 + recv_ts，见 latency 文档。

指标（按审核要求改名）
  弱标签异常候选覆盖率（不叫事件召回率）；误报双口径；≤1 秒不报。

输出（14_整改执行_v2b/chipping/）
  chipping_row_candidates.csv / chipping_row_events.csv / chipping_row_metrics.csv / row_level_candidates.png
"""
import os, sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(__file__))
import hi_asof as HA

HERE = os.path.dirname(__file__)
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))
RAW = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv')
AUD = os.path.abspath(os.path.join(HERE, '..', '03_原始数据与标签', 'life_audit_4152.csv'))
OUT = os.path.abspath(os.path.join(HERE, '..', '14_整改执行_v2b', 'chipping'))
os.makedirs(OUT, exist_ok=True)

TPL = 10
HEALTH_FRAC = 0.25
CONSEC = 3
COOLDOWN = 20
K_CUS, H_CUS = 0.5, 6.0
GRID = np.linspace(19.0, 40.0, 40)

aud = pd.read_csv(AUD, encoding='utf-8-sig')
aud['LifeCycle'] = aud.PhysicalLifeId.str.extract(r'-c(\d+)$')[0].astype(int)
LBL = dict(zip(aud.LifeCycle, aud.NormLabel))

idx = HA.build_piece_index(RAW)
CYCLES = sorted([c for c in idx if len(idx[c]['pieces']) >= 15])
print('[idx] 可算周期 %d' % len(CYCLES))

CACHE = {}


def cycle_score(c):
    if c in CACHE:
        return CACHE[c]
    pieces, rows = idx[c]['pieces'], idx[c]['rows']
    z0, segs = HA.compute_segments(pieces, rows)
    if not segs or len(segs) < 15:
        CACHE[c] = None
        return None
    M = np.full((len(segs), 40), np.nan)
    for i, s in enumerate(segs):
        r = s['r']
        xs = np.asarray(r['xs'], float); vs = np.asarray(r[HA.METRIC], float)
        ok = np.isfinite(xs) & np.isfinite(vs)
        if ok.sum() < 3:
            continue
        msk = (GRID >= r['x_lo']) & (GRID <= r['x_hi'])
        M[i, msk] = np.interp(GRID[msk], xs[ok], vs[ok])
    cov = np.mean(np.isfinite(M[:min(TPL, len(M))]), axis=0) >= 0.7
    if cov.sum() < 10:
        CACHE[c] = None
        return None
    M[:, ~cov] = np.nan

    T = np.nanmedian(M[:TPL], axis=0)
    R = M - T
    base = R[:TPL]
    scale = max(1.4826 * float(np.nanmedian(np.abs(base - np.nanmedian(base)))), 1e-6)
    # 统计量：件内 40 点的**中位绝对偏离**（比 max 稳健；max 被单点尖刺主导）
    with np.errstate(invalid='ignore'):
        score = np.nanmedian(np.abs(R), axis=1)

    nh = max(TPL, int(len(score) * HEALTH_FRAC))
    # 参考段必须**排除前 TPL 件**：它们是模板本身，残差≈0 不具代表性
    ref = score[TPL:nh][np.isfinite(score[TPL:nh])]
    if len(ref) < 5:
        CACHE[c] = None
        return None
    mu = float(np.median(ref))
    sd = max(1.4826 * float(np.median(np.abs(ref - mu))), 1e-6)
    thr = mu + 4.0 * sd          # 仅用于画图与"单点越限"，事件判定走 CUSUM
    z = (score - mu) / sd
    sp = np.zeros(len(z)); sn = np.zeros(len(z))
    for i in range(1, len(z)):
        zi = z[i] if np.isfinite(z[i]) else 0.0
        sp[i] = max(0.0, sp[i - 1] + zi - K_CUS)
        sn[i] = max(0.0, sn[i - 1] - zi - K_CUS)
    # 事件 = CUSUM 漂移越限 且 最近 CONSEC 个 z 同号且 >0.3（排除单点尖刺）
    events = []; last = -10 ** 9
    for i in range(CONSEC, len(score)):
        if i - last <= COOLDOWN:
            continue
        seg3 = z[i - CONSEC + 1:i + 1]
        if not np.all(np.isfinite(seg3)):
            continue
        drift_up = (sp[i] > H_CUS) and np.all(seg3 > 0.3)
        drift_dn = (sn[i] > H_CUS) and np.all(seg3 < -0.3)
        if drift_up or drift_dn:
            j = i - CONSEC + 1
            events.append(dict(StartPiece=j + 1, EndPiece=i + 1,
                               MaxScore=round(float(np.nanmax(score[j:i + 1])), 2),
                               MaxZ=round(float(z[i]), 2)))
            last = i
    t0 = min(rows[a]['_t'].min() for a in pieces)
    t1 = max(rows[a]['_t'].max() for a in pieces)
    hours = (t1 - t0).total_seconds() / 3600.0
    out = dict(n=len(segs), score=score, thr=thr, nh=nh, events=events, hours=hours, z0=z0)
    CACHE[c] = out
    return out


CAND, EV = [], []
for c in CYCLES:
    r = cycle_score(c)
    if r is None:
        continue
    lab = LBL.get(c, '')
    ne = len(r['events'])
    CAND.append(dict(LifeCycle=c, NormLabel=lab, Pieces=r['n'], Z0=r['z0'],
                     CuttingHours=round(r['hours'], 2), HealthRefPieces=r['nh'],
                     Threshold=round(r['thr'], 2), Events=ne,
                     FirstStart=(r['events'][0]['StartPiece'] if ne else None),
                     FirstStartPct=round(100 * r['events'][0]['StartPiece'] / r['n'], 1) if ne else None,
                     MaxScore=(max(e['MaxScore'] for e in r['events']) if ne else None),
                     FA_perHour=round(ne / max(r['hours'], 1e-9), 2),
                     FA_per1000Pieces=round(1000 * ne / r['n'], 1)))
    for e in r['events']:
        EV.append(dict(LifeCycle=c, NormLabel=lab, **e))

C = pd.DataFrame(CAND).sort_values('LifeCycle')
E = pd.DataFrame(EV)
C.to_csv(os.path.join(OUT, 'chipping_row_candidates.csv'), index=False, encoding='utf-8-sig')
E.to_csv(os.path.join(OUT, 'chipping_row_events.csv'), index=False, encoding='utf-8-sig')

bd = C[C.NormLabel == 'CHIPPING_FAILURE']
wd = C[C.NormLabel == 'PHYSICAL_WEAR_FAILURE']
M = pd.DataFrame([
    dict(Metric='弱标签异常候选覆盖率(破损周期)',
         Value=round(float((bd.Events > 0).mean()), 3) if len(bd) else None, n=len(bd),
         Note='不叫召回率——无真实崩刃时刻'),
    dict(Metric='磨损对照周期也报警比例',
         Value=round(float((wd.Events > 0).mean()), 3) if len(wd) else None, n=len(wd),
         Note='特异性，越接近 0 越好'),
    dict(Metric='误报密度(次/切削小时)', Value=round(float(C.FA_perHour.mean()), 2), n=len(C), Note='全周期均值'),
    dict(Metric='误报密度(次/千件)', Value=round(float(C.FA_per1000Pieces.mean()), 1), n=len(C), Note='全周期均值'),
    dict(Metric='模板件数', Value=TPL, n='-', Note='先验常数，与周期总长无关'),
    dict(Metric='≤1秒检出', Value='NOT_REPORTED', n='-', Note='无真实事件时刻且无实时链路'),
])
M.to_csv(os.path.join(OUT, 'chipping_row_metrics.csv'), index=False, encoding='utf-8-sig')
print('\n=== 逐周期 ===')
print(C.to_string(index=False))
print('\n=== 指标 ===')
print(M.to_string(index=False))

fig, axes = plt.subplots(4, 3, figsize=(15, 11))
for ax, c in zip(axes.ravel(), bd.LifeCycle.tolist()):
    r = cycle_score(c)
    if r is None:
        continue
    ax.plot(np.arange(1, r['n'] + 1), r['score'], lw=0.8, color='#2b6cb0')
    ax.axhline(r['thr'], color='#c53030', lw=0.8, ls='--')
    ax.axvline(r['nh'], color='gray', lw=0.6, ls=':')
    for e in r['events']:
        ax.axvspan(e['StartPiece'], e['EndPiece'], color='#c53030', alpha=0.3)
    ax.set_title('c%d n=%d ev=%d' % (c, r['n'], len(r['events'])), fontsize=9)
    ax.tick_params(labelsize=6)
fig.suptitle('Per-piece max phase residual (robust z); dashed=threshold, dotted=health ref end', fontsize=11)
fig.tight_layout(rect=[0, 0, 1, 0.96])
fig.savefig(os.path.join(OUT, 'row_level_candidates.png'), dpi=130)
plt.close(fig)
print('\n[out] chipping_row_* + row_level_candidates.png')
