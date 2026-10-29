# -*- coding: utf-8 -*-
"""s43_model_intervals.py —— P1-06：四个模型各自的预测区间（M1 不得借 M0 区间）

审核 P1-06 原文
  「模型须带不确定性：M0-B 覆盖 56–64% 仅探索；**M1 不许借 M0 区间** → 全表加区间列」

此前的状态（13_整改执行_v2）
  - `bt_core.nested_predict` 只给 M0-B 一对 `PI_lo/PI_hi`（= 训练池条件剩余的 P10–P90）
  - M0-A / M1-old / M1-new 的 `PI_Note` 全部写 `PI_NOT_DEFINED(未校准，不套用 M0-B 区间)`
  → 等于四个模型里三个没有不确定性。**"不借用"做对了，"没有区间"没补齐。**

本脚本补的就是这一块：每个模型用自己的 LOO 残差校准自己的区间。

方法（三档，全部只用本模型自己的残差 r = actual − pred）
  S1  留一对称分位数        PI = pred ± q_{1−α/2}( |r_{−k}| )
      ⚠ 不是严格 jackknife+。严格构造要逐个 i 用 pred_{−i} 配自身残差，本实现只用
        q(|r_{−k}|) 当半宽 → 不继承 Barber 2021 的有限样本覆盖保证（审核 P1-MET-02）。
  S2  conformal 非对称     PI = pred + q_{α/2}(r_{−k}) ~ pred + q_{1−α/2}(r_{−k})
  S3  正态近似            PI = pred ± z_{1−α/2} · sd(r_{−k})
其中 r_{−k} = 同 Source / 同 CutoffN / 同分组（prog 或 part）里**除测试刀 k 之外**全部刀的残差。
注意 CalScope 只影响"用谁的残差定宽度"，**不改变预测值**；因此不同 CalScope 下 MAE 的差异
来自样本子集（残差池不足 3 个的刀被剔除），不能读成"分组方式影响预测精度"。

为什么 r_{−k} 站得住
  pred_j 由 `bt_core.nested_predict(j)` 产生，其训练池已剔除 j（外层留一）；所以 r_j 是
  合法的留一残差，不是 in-sample 残差。再把 k 自己剔掉，闭式区间不依赖 k 的真值。

为什么不做"按 CutoffN 全池"校准
  四个模型的误差量级差 2~5 倍（M1-new 的 MAE 在 N=20 能到 48），跨截断混池会让 N=20 的
  宽区间去覆盖 N=80 的窄误差，覆盖率数字好看但没意义。**逐 (Source, N, 品番组) 单独校准。**

输出（43_模型区间与不确定性/）
  pred_all_models_pi.csv        逐刀逐模型：pred / PI_lo / PI_hi / width / covered / n_cal
  metrics_all_models_pi.csv     逐 (Source, N, Model)：MAE + 三档覆盖率 + 中位宽度 + Wilson CI
  pi_vs_baseline.csv            与原 P10–P90 区间（仅 M0-B 有）的对照
  s43_console.txt
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)                                    # .../20261027
OUT = os.path.join(BASE, '43_模型区间与不确定性')
PRED = os.path.join(BASE, '13_整改执行_v2', 'predictions_v2')
os.makedirs(OUT, exist_ok=True)

MODELS = ('M0A', 'M0B', 'M1Old', 'M1New')
PART_MAP = {}
_pm = os.path.join(BASE, '25_品番混批诊断', 'd1_prog_part_map.csv')
if os.path.exists(_pm):
    for _, r in pd.read_csv(_pm, encoding='utf-8-sig').iterrows():
        PART_MAP[int(r.Prog)] = str(r.Part)
ALPHA = 0.20                     # 名义 80% 区间
Z = 1.2815515655446004           # 正态 90 分位
MIN_CAL = 3                      # 残差池少于 3 个 → 不校准，标 PI_UNIDENTIFIED


def wilson(k, n, z=1.959963985):
    """覆盖率的小样本区间（Wilson），n 小的时候必须报，否则 0.8 这个数看着比实际硬"""
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


rows, metrics, base_rows = [], [], []
for src in ('legacy', 'asofC1', 'asofC2'):
    p = os.path.join(PRED, 'pred_%s.csv' % src)
    if not os.path.exists(p):
        print('[skip] 缺 %s' % p)
        continue
    P = pd.read_csv(p, encoding='utf-8-sig')

    # ---- 先算每个 (N, 模型) 的残差表，供留一取池
    for N, dN in P.groupby('CutoffN'):
        # 残差 r_k = actual − pred_k（只在 pred 有定义时有意义）
        for m in MODELS:
            dN['R_' + m] = dN.ActualRUL - dN[m]
        for m in MODELS:
            d = dN.dropna(subset=[m, 'ActualRUL']).copy()
            d['Part'] = d.Program.map(lambda p: PART_MAP.get(int(p), 'P%d' % int(p)))
            # 逐刀留一区间；两种校准口径都算（prog=现行分组 / part=推荐分组）
            got = []
            for scope in ('prog', 'part'):
                col = d.Program if scope == 'prog' else d.Part
                for _, r in d.iterrows():
                    key = r.Program if scope == 'prog' else r.Part
                    pool = d[(col == key) & (d.PhysicalLifeId != r.PhysicalLifeId)]
                    res = pool['R_' + m].dropna().to_numpy(float)
                    ncal = len(res)
                    base = dict(Source=src, CutoffN=N, Model=m, CalScope=scope,
                                PhysicalLifeId=r.PhysicalLifeId, Program=r.Program,
                                Part=r.Part, ActualRUL=r.ActualRUL, Pred=float(r[m]),
                                n_cal=ncal, S0_lo=r['PI_lo'], S0_hi=r['PI_hi'])
                    if ncal < MIN_CAL:
                        got.append(dict(base, S1_lo=np.nan, S1_hi=np.nan,
                                        S2_lo=np.nan, S2_hi=np.nan,
                                        S3_lo=np.nan, S3_hi=np.nan,
                                        Status='PI_UNIDENTIFIED'))
                        continue
                    pred = float(r[m])
                    q_abs = float(np.quantile(np.abs(res), 1 - ALPHA))
                    lo2, hi2 = np.quantile(res, [ALPHA / 2, 1 - ALPHA / 2])
                    sd = float(res.std(ddof=1))
                    got.append(dict(base,
                                    S1_lo=pred - q_abs, S1_hi=pred + q_abs,
                                    S2_lo=pred + lo2, S2_hi=pred + hi2,
                                    S3_lo=pred - Z * sd, S3_hi=pred + Z * sd,
                                    Status='OK'))
            G = pd.DataFrame(got)
            rows.append(G)

            # ---- 汇总（覆盖率 + 中位宽度），按校准口径分开
            for scope in ('prog', 'part'):
                ok = G[(G.Status == 'OK') & (G.CalScope == scope)]
                n = len(ok)
                rec = dict(Source=src, CutoffN=N, Model=m, CalScope=scope, n=n,
                           MAE=round(float((ok.Pred - ok.ActualRUL).abs().mean()), 2) if n else np.nan,
                           MedCalPool=int(ok.n_cal.median()) if n else 0)
                for s in ('S1', 'S2', 'S3'):
                    if not n:
                        rec[s + '_Cover'] = np.nan
                        rec[s + '_Width'] = np.nan
                        rec[s + '_CIlo'] = np.nan
                        rec[s + '_CIhi'] = np.nan
                        continue
                    cov = ((ok[s + '_lo'] <= ok.ActualRUL) & (ok.ActualRUL <= ok[s + '_hi']))
                    rec[s + '_Cover'] = round(float(cov.mean()), 3)
                    rec[s + '_Width'] = round(float((ok[s + '_hi'] - ok[s + '_lo']).median()), 1)
                    lo, hi = wilson(int(cov.sum()), n)
                    rec[s + '_CIlo'], rec[s + '_CIhi'] = round(lo, 3), round(hi, 3)
                # 原版 P10–P90（只有 M0-B 有，用来量化"借区间"有多差）
                if m == 'M0B' and n:
                    c0 = ((ok.S0_lo <= ok.ActualRUL) & (ok.ActualRUL <= ok.S0_hi))
                    rec['S0_Cover'] = round(float(c0.mean()), 3)
                    rec['S0_Width'] = round(float((ok.S0_hi - ok.S0_lo).median()), 1)
                else:
                    rec['S0_Cover'] = np.nan
                    rec['S0_Width'] = np.nan
                metrics.append(rec)

ALL = pd.concat(rows, ignore_index=True)
ALL.to_csv(os.path.join(OUT, 'pred_all_models_pi.csv'), index=False, encoding='utf-8-sig')
M = pd.DataFrame(metrics).sort_values(['Source', 'CutoffN', 'Model'])
M.to_csv(os.path.join(OUT, 'metrics_all_models_pi.csv'), index=False, encoding='utf-8-sig')

print('=' * 100)
print('逐 (Source, N, Model, 校准口径) 覆盖率（名义 %d%%，留一残差校准）' % int(100 * (1 - ALPHA)))
print('=' * 100)
for src in M.Source.unique():
    print('\n--- %s ---' % src)
    d = M[M.Source == src]
    print(d[['CutoffN', 'Model', 'CalScope', 'n', 'MedCalPool', 'MAE',
             'S0_Cover', 'S1_Cover', 'S2_Cover', 'S3_Cover',
             'S1_Width', 'S3_Width']].to_string(index=False))

# ---- 与"借 M0-B 区间"的对照
#   原实现里 bt_core 只给 M0-B 一对 PI_lo/PI_hi（S0 = 条件剩余 P10–P90）。
#   若把这些列直接贴着 M1 的预测值用（开发中最容易犯的错），区间半宽来自 M0-B、
#   中心来自 M1 → 得到的就是下面这行 Borrow 覆盖率。与 M1 自校准的 Own 对比，
#   差额才是"借区间"的真实代价。分母只取两边都 Status=OK 且同口径的行。
b = []
for src in M.Source.unique():
    for N in sorted(M.CutoffN.unique()):
        d = ALL[(ALL.Source == src) & (ALL.CutoffN == N) & (ALL.Status == 'OK')]
        for scope in ('prog', 'part'):
            ds = d[d.CalScope == scope]
            m0 = ds[ds.Model == 'M0B'].set_index('PhysicalLifeId')
            for m in ('M1Old', 'M1New'):
                s = ds[ds.Model == m].set_index('PhysicalLifeId')
                common = s.index.intersection(m0.index)
                common = [c for c in common if np.isfinite(m0.loc[c, 'S0_hi'])]
                if not len(common):
                    continue
                half = (m0.loc[common, 'S0_hi'] - m0.loc[common, 'S0_lo']) / 2.0
                act = s.loc[common, 'ActualRUL']
                pred = s.loc[common, 'Pred']
                borrow = float((((pred - half) <= act) & (act <= (pred + half))).mean())
                own = float(((s.loc[common, 'S1_lo'] <= act) & (act <= s.loc[common, 'S1_hi'])).mean())
                b.append(dict(Source=src, CutoffN=N, CalScope=scope, Model=m, n=len(common),
                              MedBorrowWidth=round(float((2 * half).median()), 1),
                              Cover_BorrowM0B=round(borrow, 3), Cover_Own=round(own, 3),
                              Delta=round(own - borrow, 3)))
B = pd.DataFrame(b)
B.to_csv(os.path.join(OUT, 'pi_vs_baseline.csv'), index=False, encoding='utf-8-sig')
print('\n' + '=' * 100)
print('反事实：把 M0-B 的 P10–P90 半宽贴到 M1 预测上（审核明令禁止），会得到什么覆盖率')
print('=' * 100)
print(B.to_string(index=False) if len(B) else '（无交集）')
print('\nDelta>0 表示 M1 用自己的区间比借 M0-B 的区间覆盖更好（负=借来的反而更"好"，'
      '但那是因为 M0-B 区间宽到没有判别力，见 MedBorrowWidth 列）')

print('\n[out] pred_all_models_pi.csv / metrics_all_models_pi.csv / pi_vs_baseline.csv')
