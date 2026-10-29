# -*- coding: utf-8 -*-
"""s63_n04_nested_f.py —— N04：换刀分位 f 的**嵌套(out-of-sample)选择** + 逐参数敏感性

对应审核：Commit 0338a5d §3 **P0-04**、§5 **N04**。
  s61 的病灶：在**同一批 23 把**上扫 f 取 argmin，再报同批成本 → 样本内选择，
  "最优 f""f=0.9 贵 60.9%""每千件 0~4.6 次崩刃"都是"指定假设 + 样本内挑最优"的**模拟量**，
  不是真实发生率、也不是未见刀的外推结论。

本脚本做三件事（不改数据，只改**估计方式**与**命名**）：
  1) 嵌套选择：留一刀(LOCO)——用其余刀选 f*，在被留出的那把刀上结算，跨折聚合 →
     给"若按他刀定 f、遇到这把新刀"的**样本外期望成本**与 f* 的选择不稳定性。
  2) 逐参数敏感性：k（崩刃后延迟发现件数）、ρ（失效代价）、θ（崩刃位置）分开扫，
     每个数字都标 `SIMULATED`（假设崩刃点 + 假设成本参数）。
  3) c38 / c51 在/不在的两套结果对照（审核点名"是否进入"）。

命名红线：本文件所有"失效/崩刃/带病/成本"列**一律前缀 `模拟` 或标 `SIMULATED`**，
  与"实测破损/质量不良"绝对隔离；对外不得写"每千件真实崩刃次数"。
"""
import os
import sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
OUT = os.path.join(BASE, 'N04_R05术语与嵌套敏感性')
os.makedirs(OUT, exist_ok=True)

AUD = os.path.join(BASE, '附件_证据', '52_R02_延长分支审计', 'r02_grant_audit.csv')
FS = np.round(np.arange(0.50, 1.0001, 0.01), 2)
KS = [0, 1, 2, 3, 5]
RHOS = [1, 2, 5, 10, 20]
THETAS = [0.6, 0.8, 0.9, 1.0]
SUSPECT = [38, 51]
LOG = []


def P(*a):
    s = ' '.join(str(x) for x in a)
    print(s)
    LOG.append(s)


def load():
    d = pd.read_csv(AUD, encoding='utf-8-sig')
    d = d[d.Status == 'OK'].copy()
    d['Cycle'] = d['Cycle'].astype(int)
    return d.reset_index(drop=True)


def per_tool(d, f, k):
    """在 (f,k) 下逐刀的 模拟合格 / 模拟失效 / 模拟带病。崩刃点=Life-k（假设）。"""
    life = d['Life'].to_numpy(int)
    m0 = d['M0'].to_numpy(float)
    chip = np.maximum(1, life - k)
    lim = np.ceil(f * m0).astype(int)
    good = np.minimum(lim, chip)
    fail = (lim > chip).astype(int)
    sick = np.maximum(0, np.minimum(lim, life) - chip)
    return good, fail, sick


def agg_cost(d, f, k, rho):
    good, fail, _ = per_tool(d, f, k)
    return (len(d) + rho * fail.sum()) / max(1, good.sum())


def select_f(dtr, k, rho):
    """在训练子集上取 argmin_f（模拟成本）"""
    costs = [agg_cost(dtr, f, k, rho) for f in FS]
    return float(FS[int(np.argmin(costs))])


def nested_loco(d, k, rho):
    """留一刀：其余刀选 f*，在被留出刀上结算。返回 OOS 成本 + 每折选中的 f。"""
    num = den = 0.0
    sel = []
    for h in range(len(d)):
        dtr = d.iloc[[i for i in range(len(d)) if i != h]]
        f_star = select_f(dtr, k, rho)
        sel.append(f_star)
        dh = d.iloc[[h]]
        good, fail, _ = per_tool(dh, f_star, k)
        num += 1 + rho * fail.sum()
        den += good.sum()
    oos = num / max(1, den)
    f_in = select_f(d, k, rho)                      # 样本内 argmin（s61 口径）
    c_in = agg_cost(d, f_in, k, rho)
    c09 = agg_cost(d, 0.9, k, rho)
    return dict(k=k, rho=rho,
                样本内最优f=f_in, 样本内模拟成本=round(c_in, 4),
                嵌套OOS模拟成本=round(oos, 4),
                OOS相对样本内上浮pct=round(100 * (oos / c_in - 1), 1),
                选中f_中位=round(float(np.median(sel)), 3),
                选中f_P10P90='%.2f~%.2f' % (np.percentile(sel, 10), np.percentile(sel, 90)),
                f09模拟成本=round(c09, 4),
                f09相对样本内pct=round(100 * (c09 / c_in - 1), 1))


if __name__ == '__main__':
    P('=' * 90)
    P('N04　f 的嵌套选择 + 逐参数敏感性（全部 SIMULATED）')
    P('=' * 90)
    d = load()
    P('可判周期 %d 把；含疑似多重计数 c38/c51=%s' % (len(d), sorted(set(SUSPECT) & set(d.Cycle))))

    # ---- (1)(3) 嵌套 vs 样本内，全集 / 去 c38c51 两套
    rows_all, rows_drop = [], []
    for k in KS:
        for rho in RHOS:
            rows_all.append(nested_loco(d, k, rho))
            dd = d[~d.Cycle.isin(SUSPECT)]
            r = nested_loco(dd, k, rho); r['n'] = len(dd); rows_drop.append(r)
    RA = pd.DataFrame(rows_all); RA.insert(0, '样本', '全%d把' % len(d))
    RD = pd.DataFrame(rows_drop); RD.insert(0, '样本', '去c38c51')
    NE = pd.concat([RA, RD], ignore_index=True)
    NE['口径'] = 'SIMULATED(假设崩刃点=Life-k, 假设成本ρ)'
    NE.to_csv(os.path.join(OUT, 'N04_nested_vs_insample.csv'), index=False, encoding='utf-8-sig')
    P('\n[嵌套 OOS vs 样本内] 全集')
    P(RA[['k', 'rho', '样本内最优f', '嵌套OOS模拟成本', 'OOS相对样本内上浮pct',
          '选中f_P10P90', 'f09相对样本内pct']].to_string(index=False))
    P('\n[去 c38/c51 后] 与全集对比（看样本内 argmin 是否被个别刀撬动）')
    P(RD[['k', 'rho', '样本内最优f', '选中f_中位', '嵌套OOS模拟成本']].to_string(index=False))

    # 选择不稳定性汇总
    stab = RA.groupby('k')['OOS相对样本内上浮pct'].mean().round(2)
    P('\n[样本内 argmin 的乐观偏差] 各 k 的 OOS 相对样本内成本上浮(%)均值：')
    P(stab.to_string())

    # ---- (2) θ 敏感性（s54 崩刃位置口径），标 SIMULATED
    theta_rows = []
    life = d['Life'].to_numpy(int)
    for th in THETAS:
        cut = np.round(th * life).astype(int)
        for pol, lim in (('现状_用到台账终点', life),
                         ('纯计数_0.9M0', np.ceil(0.9 * d['M0'].to_numpy(float)).astype(int)),
                         ('纯计数_0.4M0', np.ceil(0.4 * d['M0'].to_numpy(float)).astype(int))):
            good = np.minimum(lim, cut)
            fail = (lim > cut).astype(int)
            sick = np.maximum(0, np.minimum(lim, life) - cut)
            for rho in (5, 10):
                theta_rows.append(dict(θ崩刃位置=th, 策略=pol, ρ=rho,
                                       模拟合格产出=int(good.sum()),
                                       模拟失效次数=int(fail.sum()),
                                       模拟带病件=int(sick.sum()),
                                       模拟成本=round((len(d) + rho * fail.sum()) / max(1, good.sum()), 4),
                                       口径='SIMULATED'))
    TH = pd.DataFrame(theta_rows)
    TH.to_csv(os.path.join(OUT, 'N04_theta_sensitivity.csv'), index=False, encoding='utf-8-sig')
    P('\n[θ×ρ 敏感性（模拟量，非真实发生率）]')
    P(TH[TH.ρ == 10].pivot(index='策略', columns='θ崩刃位置', values='模拟失效次数').to_string())

    with open(os.path.join(OUT, 's63_console.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG))
    P('\nDONE →', OUT)
