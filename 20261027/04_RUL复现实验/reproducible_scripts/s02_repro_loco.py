# -*- coding: utf-8 -*-
"""s02_repro_loco.py — P0-03 严格复现原回执的 M0-A / M1-旧 负结果（逐刀）

口径（与原回执 v1.0 §3.1 完全一致，先复现再谈改进）
  样本      : out_T1111/xalign_HI.csv 中同程序 ≥3 个周期的全部周期
  分组      : 按 PhysicalLifeId 整组留一（LOCO），不随机分件
  截断      : 固定 N=20/40/60/80（不按真实寿命比例截断）
  特征      : 仅用前 N 件；HI = load_med/median(前20件)；rel_rise = 第 N-9~N 件 HI 中位 - 1
  M0-A      : 预测总寿命 = 同程序其余周期件数中位；RUL = M0 - N
  M1-旧     : (真实寿命/M0 - 1) = b × rel_rise，b 在训练折内过原点最小二乘；
              RUL = (1 + b×rel_rise) × M0 - N
  真值      : 该周期对齐后的件数（xalign 口径）。**标签口径 = 距下一次人工换刀，不是物理磨损**
禁止特征    : progress / life_used_pct_* / rul_sec / change_reason / real_failure 一律不入模

输出（04_RUL复现实验/）
  predictions_loco_baseline.csv  16 周期 × 4 截断 = 64 行
  metrics_baseline.csv           每截断点汇总
  metrics_paired.csv             M1-M0 配对差 + 生命周期 bootstrap
  metrics_strata.csv             分程序 / 分寿命档 / 单刀剔除敏感性
"""
import os, hashlib, json
import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
HI_CSV = os.path.join(ROOT, 'out_T1111', 'xalign_HI.csv')
AUDIT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..',
                                     '03_原始数据与标签', 'life_audit_4152.csv'))
OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '04_RUL复现实验'))

K, W = 20, 10
CUTS = [20, 40, 60, 80]
MODEL = 'M1_Old=v1.0-1param-ridge0'
RNG = np.random.default_rng(20261027)
NBOOT = 2000


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


DSHASH = sha256(HI_CSV)

hi = pd.read_csv(HI_CSV, encoding='utf-8-sig')
aud = pd.read_csv(AUDIT, encoding='utf-8-sig')
aud_idx = {r.PhysicalLifeId: r for r in aud.itertuples()}

curves = {}
for (p, c), g in hi.groupby(['prog', 'life_cycle']):
    g = g.sort_values('piece_idx')
    curves[(int(p), int(c))] = g['HI_L_spindle_load'].to_numpy(float)

ncyc = pd.Series([k[0] for k in curves]).value_counts()
keep = [k for k in curves if ncyc[k[0]] >= 3 and len(curves[k]) >= max(K, W) + 5]
keep.sort()
print('[sample] 全部周期 %d，纳入 %d（同程序 >=3 周期且件数>=25）'
      % (len(curves), len(keep)))
print('[sample] 件数合计 %d' % sum(len(curves[k]) for k in keep))
excluded = [k for k in curves if k not in keep]
print('[sample] 排除 %s' % ', '.join('prog%s-c%s(%d件)' % (k[0], k[1], len(curves[k])) for k in excluded))


def pid(k):
    return '4152-L-T1111-c%d' % k[1]


def life(k):
    return len(curves[k])


M0 = {}
for k in keep:
    M0[k] = float(np.median([life(o) for o in keep if o[0] == k[0] and o != k]))

rows = []
for N in CUTS:
    sub = [k for k in keep if life(k) > N]
    # ---- 先算每把刀在截断点的特征
    feat = {}
    for k in sub:
        y = curves[k]
        base = float(np.median(y[:K]))
        rel = y / base - 1.0
        feat[k] = float(np.median(rel[N - W:N]))
    # ---- 逐个留一
    for k in sub:
        tr = [o for o in sub if o != k]
        x = np.array([feat[o] for o in tr])
        yv = np.array([life(o) / M0[o] - 1.0 for o in tr])
        b = float((x * yv).sum() / max((x * x).sum(), 1e-12))
        m0 = M0[k]
        tot_m0 = m0
        tot_m1 = (1.0 + b * feat[k]) * m0
        a = aud_idx.get(pid(k))
        rows.append(dict(
            PhysicalLifeId=pid(k), Program=k[0], CutoffN=N,
            TrainLifeIds=','.join(pid(o) for o in tr),
            TestLifeId=pid(k),
            LifeLabelType=getattr(a, 'NormLabel', ''),
            DataQuality=getattr(a, 'DataQualityGrade', ''),
            HasValidCutoff=True,
            HI=round(float(np.median(curves[k][N - W:N])), 6),
            RelRise=round(feat[k], 6),
            FitB=round(b, 4),
            AlignedLife=life(k), M0_A_PredTotal=round(tot_m0, 2),
            M0_A_PredRUL=round(tot_m0 - N, 2),
            M1_Old_PredTotal=round(tot_m1, 2), M1_Old_PredRUL=round(tot_m1 - N, 2),
            ActualReplacementLife=life(k),
            ActualRemainingToReplacement=life(k) - N,
            Err_M0_A=round(tot_m0 - N - (life(k) - N), 2),
            Err_M1_Old=round(tot_m1 - N - (life(k) - N), 2),
            ModelVersion=MODEL, DatasetHash=DSHASH[:16]))

P = pd.DataFrame(rows).sort_values(['CutoffN', 'Program', 'PhysicalLifeId'])
P['Over_M0'] = (P.M0_A_PredRUL > P.ActualRemainingToReplacement).astype(int)
P['Over_M1'] = (P.M1_Old_PredRUL > P.ActualRemainingToReplacement).astype(int)
P['OverAmt_M0'] = (P.M0_A_PredRUL - P.ActualRemainingToReplacement).clip(lower=0)
P['OverAmt_M1'] = (P.M1_Old_PredRUL - P.ActualRemainingToReplacement).clip(lower=0)
P['AbsErr_M0'] = P.Err_M0_A.abs()
P['AbsErr_M1'] = P.Err_M1_Old.abs()
P['Diff_M1_minus_M0'] = P.AbsErr_M1 - P.AbsErr_M0


def summarise(d):
    return dict(n=len(d),
                MAE_M0=round(d.AbsErr_M0.mean(), 2), MAE_M1=round(d.AbsErr_M1.mean(), 2),
                MedAE_M0=round(d.AbsErr_M0.median(), 2), MedAE_M1=round(d.AbsErr_M1.median(), 2),
                RMSE_M0=round(float(np.sqrt((d.Err_M0_A ** 2).mean())), 2),
                RMSE_M1=round(float(np.sqrt((d.Err_M1_Old ** 2).mean())), 2),
                Bias_M0=round(d.Err_M0_A.mean(), 2), Bias_M1=round(d.Err_M1_Old.mean(), 2),
                Over_M0=int(d.Over_M0.sum()), Over_M1=int(d.Over_M1.sum()),
                OverAmt_M0=round(float(d.OverAmt_M0[d.Over_M0 == 1].mean()) if d.Over_M0.sum() else 0, 2),
                OverAmt_M1=round(float(d.OverAmt_M1[d.Over_M1 == 1].mean()) if d.Over_M1.sum() else 0, 2),
                Win_M1=int((d.Diff_M1_minus_M0 < 0).sum()),
                Tie=int((d.Diff_M1_minus_M0 == 0).sum()),
                Lose_M1=int((d.Diff_M1_minus_M0 > 0).sum()))


M = []
for N, d in P.groupby('CutoffN'):
    r = summarise(d)
    r['CutoffN'] = N
    M.append(r)
M = pd.DataFrame(M)[['CutoffN', 'n', 'MAE_M0', 'MAE_M1', 'MedAE_M0', 'MedAE_M1', 'RMSE_M0', 'RMSE_M1',
                      'Bias_M0', 'Bias_M1', 'Over_M0', 'Over_M1', 'OverAmt_M0', 'OverAmt_M1',
                      'Win_M1', 'Tie', 'Lose_M1']]

# ---------------- 配对 bootstrap（重采样「生命周期」，不是件）
pr = []
for N, d in P.groupby('CutoffN'):
    dif = d.Diff_M1_minus_M0.to_numpy()
    boots = np.array([RNG.choice(dif, size=len(dif), replace=True).mean() for _ in range(NBOOT)])
    lo, hi95 = np.percentile(boots, [2.5, 97.5])
    # MAE 的置信区间也必须用「误差」的 SD，不是寿命 SD（P0-03 明令）
    for tag, col in (('M0', 'AbsErr_M0'), ('M1', 'AbsErr_M1')):
        e = d[col].to_numpy()
        bm = np.array([RNG.choice(e, size=len(e), replace=True).mean() for _ in range(NBOOT)])
        pr.append(dict(CutoffN=N, Metric='MAE_' + tag, N_lives=len(e),
                       Point=round(e.mean(), 2), CI_lo=round(np.percentile(bm, 2.5), 2),
                       CI_hi=round(np.percentile(bm, 97.5), 2),
                       ErrSD=round(float(e.std(ddof=1)), 2),
                       Note='bootstrap over lives'))
    pr.append(dict(CutoffN=N, Metric='DeltaMAE(M1-M0)', N_lives=len(dif),
                   Point=round(dif.mean(), 2), CI_lo=round(lo, 2), CI_hi=round(hi95, 2),
                   ErrSD=round(float(dif.std(ddof=1)), 2),
                   Note='<0 表示 M1 更好；CI 跨 0 = 不可区分'))
PR = pd.DataFrame(pr)

# ---------------- 分层与敏感性
st = []
for N, d in P.groupby('CutoffN'):
    for p, g in d.groupby('Program'):
        st.append(dict(CutoffN=N, Stratum='Program=%s' % p, n=len(g),
                       MAE_M0=round(g.AbsErr_M0.mean(), 2), MAE_M1=round(g.AbsErr_M1.mean(), 2)))
    d2 = d.copy()
    d2['ter'] = pd.qcut(d2.ActualReplacementLife, 3, labels=['短', '中', '长'], duplicates='drop')
    for t, g in d2.groupby('ter', observed=True):
        st.append(dict(CutoffN=N, Stratum='LifeTercile=%s' % t, n=len(g),
                       MAE_M0=round(g.AbsErr_M0.mean(), 2), MAE_M1=round(g.AbsErr_M1.mean(), 2)))
    for _, rr in d.iterrows():
        g = d[d.PhysicalLifeId != rr.PhysicalLifeId]
        st.append(dict(CutoffN=N, Stratum='DropOne=%s' % rr.PhysicalLifeId, n=len(g),
                       MAE_M0=round(g.AbsErr_M0.mean(), 2), MAE_M1=round(g.AbsErr_M1.mean(), 2)))
ST = pd.DataFrame(st)

os.makedirs(OUT, exist_ok=True)
P.to_csv(os.path.join(OUT, 'predictions_loco_baseline.csv'), index=False, encoding='utf-8-sig')
M.to_csv(os.path.join(OUT, 'metrics_baseline.csv'), index=False, encoding='utf-8-sig')
PR.to_csv(os.path.join(OUT, 'metrics_paired_baseline.csv'), index=False, encoding='utf-8-sig')
ST.to_csv(os.path.join(OUT, 'metrics_strata.csv'), index=False, encoding='utf-8-sig')

print('\n[out] predictions_loco_baseline.csv %d × %d' % P.shape)
print('[out] metrics_baseline.csv         %d × %d' % M.shape)
print('[out] metrics_paired.csv           %d × %d' % PR.shape)
print('[out] metrics_strata.csv           %d × %d' % ST.shape)
print('\n=== 主表（逐截断点）===')
print(M.to_string(index=False))
print('\n=== 配对与区间 ===')
print(PR.to_string(index=False))
print('\n=== 分层（仅 Program 与 寿命档）===')
print(ST[~ST.Stratum.str.startswith('DropOne')].to_string(index=False))
print('\n=== DropOne 敏感性的极值 ===')
for N, g in ST[ST.Stratum.str.startswith('DropOne')].groupby('CutoffN'):
    print('N=%d  MAE_M0 ∈ [%.2f, %.2f]   MAE_M1 ∈ [%.2f, %.2f]   M1 更好的折数 %d/%d'
          % (N, g.MAE_M0.min(), g.MAE_M0.max(), g.MAE_M1.min(), g.MAE_M1.max(),
             int((g.MAE_M1 < g.MAE_M0).sum()), len(g)))
