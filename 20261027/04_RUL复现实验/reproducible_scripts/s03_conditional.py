# -*- coding: utf-8 -*-
"""s03_conditional.py — P0-04 M0-B 条件生存基线 + M1-new（FOCAS 增量）

为什么必须做这一步
  原 M0-A 是「同程序其他周期寿命中位 − 当前件数」，这是**无条件**基线。
  但一把刀能活到第 N 件，本身就说明它比总体更长命（条件化信息）。
  正确做法：S(t | T > N)，即「已知活过 N 件」的条件剩余寿命分布。

三个模型（同一样本、同一截断点，严格可比）
  M0-A  无条件中位：median(train) − N
  M0-B  条件剩余中位：median{ L − N | L ∈ train, L > N }
        （全部训练刀都是完整寿命、无删失时，等价于 KM 条件中位）
        + Weibull-AFT 版本做交叉核对（MLE 网格搜索，无 scipy 依赖）
  M1-new M0-B × exp(g·z)，z = 预定义 FOCAS 特征的标准分，g 在训练折内拟合
        预定义 4 个候选（禁止事后搜索）：
          HI_now  截断点最近 10 件 HI 中位
          Trend10 最近 20 件相对前 10 件的斜率（每件 HI 变化量）
          Vol10   最近 10 件 HI 的标准差（负载波动度）
          Xresid  与训练集同 X 相位负载的偏离（先用 HI 序列近似，见 KnownLimits）
  区间：训练集条件分布的 10/90 分位，报覆盖率与宽度

输出
  predictions_loco_conditional.csv
  metrics_conditional.csv
  metrics_paired.csv              (合并 P0-03 baseline + P0-04 conditional)
"""
import os, hashlib
import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
HI_CSV = os.path.join(ROOT, 'out_T1111', 'xalign_HI.csv')
AUDIT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..',
                                     '03_原始数据与标签', 'life_audit_4152.csv'))
OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '04_RUL复现实验'))

K, W = 20, 10
CUTS = [20, 40, 60, 80]
RNG = np.random.default_rng(20261027)
NBOOT = 2000


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


DSHASH = sha256(HI_CSV)[:16]

hi = pd.read_csv(HI_CSV, encoding='utf-8-sig')
aud = pd.read_csv(AUDIT, encoding='utf-8-sig')
aud_idx = {r.PhysicalLifeId: r for r in aud.itertuples()}

curves = {}
for (p, c), g in hi.groupby(['prog', 'life_cycle']):
    g = g.sort_values('piece_idx')
    curves[(int(p), int(c))] = g['HI_L_spindle_load'].to_numpy(float)
ncyc = pd.Series([k[0] for k in curves]).value_counts()
keep = sorted([k for k in curves if ncyc[k[0]] >= 3 and len(curves[k]) >= max(K, W) + 5])
L = {k: len(curves[k]) for k in keep}


def pid(k):
    return '4152-L-T1111-c%d' % k[1]


def weibull_mle(x):
    """两参数 Weibull MLE（完整数据），网格搜索 k，闭式解 λ。无 scipy 依赖。"""
    x = np.asarray(x, float)
    n = len(x)
    lx = np.log(x)
    best = (None, -np.inf)
    for k in np.arange(0.5, 12.001, 0.01):
        lam = (np.mean(x ** k)) ** (1.0 / k)
        ll = n * (np.log(k) - np.log(lam)) + (k - 1) * lx.sum() - ((x / lam) ** k).sum()
        if ll > best[1]:
            best = (k, ll)
    k = best[0]
    lam = (np.mean(x ** k)) ** (1.0 / k)
    return float(k), float(lam)


def wb_cond_median(N, k, lam):
    return float(lam * ((N / lam) ** k + np.log(2.0)) ** (1.0 / k) - N)


def feats(y, N):
    """截断点可得的 4 个预定义特征（只用前 N 件）"""
    h = y[:N]
    return dict(
        HI_now=float(np.median(h[max(0, N - W):N])),
        Trend10=float((np.median(h[N - 10:N]) - np.median(h[N - 20:N - 10])) / 10.0),
        Vol10=float(np.std(h[max(0, N - W):N], ddof=1)),
        Xresid=np.nan)   # 见 KnownLimits：需要逐件 40 点曲线，port 到下一轮


rows = []
for N in CUTS:
    sub = [k for k in keep if L[k] > N]          # 只在「活过 N」的刀上测试
    F = {k: feats(curves[k], N) for k in sub}
    for k in sub:
        # 训练池 = 同程序「全部」其他周期，**包含在 N 之前就结束的短刀**
        # （排除短刀会让生存函数偏高，这是 M0-B 最易犯的错）
        pool = [o for o in keep if o[0] == k[0] and o != k]
        sib = [L[o] for o in pool]
        m0a = float(np.median(sib))
        cond = [x - N for x in sib if x > N]
        m0b = float(np.median(cond)) if cond else float(max(1.0, m0a - N))
        q10 = float(np.percentile(cond, 10)) if len(cond) >= 2 else np.nan
        q90 = float(np.percentile(cond, 90)) if len(cond) >= 2 else np.nan
        kk, lam = weibull_mle(sib)
        m0b_wb = wb_cond_median(N, kk, lam)
        # M1-new：单个预定义特征，乘性修正 pred = m0b * exp(g*z)
        preds = {}
        for f in ('HI_now', 'Trend10', 'Vol10'):
            xv = np.array([F[o][f] for o in pool if o in F], float)
            yv = np.array([L[o] - N for o in pool if o in F], float)
            ok = np.isfinite(xv) & (yv > 0)
            if ok.sum() < 5:
                preds[f] = np.nan
                continue
            z = (xv[ok] - xv[ok].mean()) / max(xv[ok].std(ddof=1), 1e-9)
            lr = np.log(yv[ok] / m0b)
            g = float((z * lr).sum() / max((z * z).sum(), 1e-12))
            g = float(np.clip(g, -0.5, 0.5))          # 收缩，防小样本爆炸
            zt = (F[k][f] - xv[ok].mean()) / max(xv[ok].std(ddof=1), 1e-9)
            preds[f] = float(m0b * np.exp(g * zt))
        a = aud_idx.get(pid(k))
        actual = L[k] - N
        rows.append(dict(
            PhysicalLifeId=pid(k), Program=k[0], CutoffN=N,
            TrainLifeIds=','.join(pid(o) for o in pool),
            LifeLabelType=getattr(a, 'NormLabel', ''), DataQuality=getattr(a, 'DataQualityGrade', ''),
            HI_now=round(F[k]['HI_now'], 6), Trend10=round(F[k]['Trend10'], 6),
            Vol10=round(F[k]['Vol10'], 6),
            M0A_PredRUL=round(m0a - N, 2),
            M0B_PredRUL=round(m0b, 2), M0B_Weibull_PredRUL=round(m0b_wb, 2),
            M1new_HI_PredRUL=round(preds['HI_now'], 2) if np.isfinite(preds['HI_now']) else np.nan,
            M1new_Trend_PredRUL=round(preds['Trend10'], 2) if np.isfinite(preds['Trend10']) else np.nan,
            M1new_Vol_PredRUL=round(preds['Vol10'], 2) if np.isfinite(preds['Vol10']) else np.nan,
            PI80_lo=round(q10, 2), PI80_hi=round(q90, 2),
            In_PI80=int(q10 <= actual <= q90) if np.isfinite(q10) and np.isfinite(q90) else np.nan,
            PI80_width=round(q90 - q10, 2) if np.isfinite(q10) and np.isfinite(q90) else np.nan,
            ActualRemainingToReplacement=actual,
            AlignedLife=L[k], Weibull_k=round(kk, 3), Weibull_lambda=round(lam, 3),
            DatasetHash=DSHASH))

P = pd.DataFrame(rows).sort_values(['CutoffN', 'Program', 'PhysicalLifeId'])
for c in ('M0A', 'M0B', 'M0B_Weibull', 'M1new_HI', 'M1new_Trend', 'M1new_Vol'):
    P['AbsErr_' + c] = (P[c + '_PredRUL'] - P.ActualRemainingToReplacement).abs()
for c in ('M0A', 'M0B', 'M0B_Weibull', 'M1new_HI', 'M1new_Trend', 'M1new_Vol'):
    P['Over_' + c] = (P[c + '_PredRUL'] > P.ActualRemainingToReplacement).astype(int)

MODELS = ['M0A', 'M0B', 'M0B_Weibull', 'M1new_HI', 'M1new_Trend', 'M1new_Vol']
MS = []
for N, d in P.groupby('CutoffN'):
    for m in MODELS:
        e = d['AbsErr_' + m].dropna()
        MS.append(dict(CutoffN=N, Model=m, n=len(e), MAE=round(e.mean(), 2),
                       MedAE=round(e.median(), 2),
                       RMSE=round(float(np.sqrt((e ** 2).mean())), 2),
                       Bias=round(float((d[m + '_PredRUL'] - d.ActualRemainingToReplacement).mean()), 2),
                       Over=int(d['Over_' + m].sum()),
                       PI80_Cover=round(float(d.In_PI80.mean()), 3) if d.In_PI80.notna().any() else np.nan,
                       PI80_Width=round(float(d.PI80_width.mean()), 2) if d.PI80_width.notna().any() else np.nan))
MS = pd.DataFrame(MS)

# ---------------- 配对 bootstrap（M1-new / M0-B 相对 M0-A）
pr = []
for N, d in P.groupby('CutoffN'):
    for m in ('M0B', 'M1new_HI', 'M1new_Trend', 'M1new_Vol'):
        diff = (d['AbsErr_' + m] - d.AbsErr_M0A).dropna().to_numpy()
        if len(diff) == 0:
            continue
        bs = np.array([RNG.choice(diff, size=len(diff), replace=True).mean() for _ in range(NBOOT)])
        pr.append(dict(CutoffN=N, Metric='DeltaMAE(%s-M0A)' % m, N_lives=len(diff),
                       Point=round(diff.mean(), 2),
                       CI_lo=round(float(np.percentile(bs, 2.5)), 2),
                       CI_hi=round(float(np.percentile(bs, 97.5)), 2),
                       ErrSD=round(float(diff.std(ddof=1)), 2),
                       Stage='P0-04 conditional',
                       Note='<0 表示该模型优于 M0-A；CI 跨 0 = 不可区分'))
PRc = pd.DataFrame(pr)

base = pd.read_csv(os.path.join(OUT, 'metrics_paired_baseline.csv'), encoding='utf-8-sig')
base['Stage'] = 'P0-03 baseline'
PR = pd.concat([base, PRc], ignore_index=True)
PR = PR[['Stage', 'CutoffN', 'Metric', 'N_lives', 'Point', 'CI_lo', 'CI_hi', 'ErrSD', 'Note']]
PR.to_csv(os.path.join(OUT, 'metrics_paired.csv'), index=False, encoding='utf-8-sig')
P.to_csv(os.path.join(OUT, 'predictions_loco_conditional.csv'), index=False, encoding='utf-8-sig')
MS.to_csv(os.path.join(OUT, 'metrics_conditional.csv'), index=False, encoding='utf-8-sig')

# ---------------- 同样本对比（关键）
# M1-new 只在「训练折内可用兄弟刀 >=5」时才有定义，N=60/80 只剩 prog20 →
# 若拿 M1 的 6 个样本去和 M0-B 的 13 个样本比 MAE，是拿最容易的一组冒充全体。
# 因此把三个模型限制到「M1 也有定义」的同一批刀上重算。
ss = []
for N, d in P.groupby('CutoffN'):
    d2 = d.dropna(subset=['M1new_HI_PredRUL'])
    if len(d2) == 0:
        continue
    for m in ('M0A', 'M0B', 'M1new_HI', 'M1new_Trend', 'M1new_Vol'):
        e = (d2[m + '_PredRUL'] - d2.ActualRemainingToReplacement).abs()
        ss.append(dict(CutoffN=N, Model=m, n_same=len(d2),
                       Programs=','.join(sorted(d2.Program.astype(str).unique())),
                       MAE_same=round(float(e.mean()), 2),
                       Bias_same=round(float((d2[m + '_PredRUL'] - d2.ActualRemainingToReplacement).mean()), 2)))
SS = pd.DataFrame(ss)
SS.to_csv(os.path.join(OUT, 'metrics_samesample.csv'), index=False, encoding='utf-8-sig')

print('[out] predictions_loco_conditional.csv %d × %d' % P.shape)
print('[out] metrics_conditional.csv           %d × %d' % MS.shape)
print('[out] metrics_paired.csv                %d × %d (baseline+conditional)' % PR.shape)
print('\n=== 模型对比（同一样本）===')
print(MS.to_string(index=False))
print('\n=== 相对 M0-A 的配对差 ===')
print(PRc[PRc.Metric.str.contains('M0B') | PRc.Metric.str.contains('HI')].to_string(index=False))
print('\n=== 区间覆盖率（80% 名义）===')
print(MS[MS.Model == 'M0B'][['CutoffN', 'n', 'PI80_Cover', 'PI80_Width']].to_string(index=False))
print('\n=== 同样本对比（仅 M1 有定义的刀，n 见 n_same）===')
print(SS.to_string(index=False) if len(SS) else '(无)')
print('\n=== M1-new 的可用样本量（暴露样本选择）===')
print(P.groupby('CutoffN').apply(
    lambda d: pd.Series({'n_test': len(d),
                         'n_M1_defined': int(d.M1new_HI_PredRUL.notna().sum()),
                         'programs_M1': ','.join(sorted(d.dropna(subset=['M1new_HI_PredRUL']).Program.astype(str).unique()))}),
    include_groups=False).to_string())
