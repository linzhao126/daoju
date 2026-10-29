# -*- coding: utf-8 -*-
"""s31_form_stratified_rul.py —— 按台账失效形态（破损/磨损）分层重做 RUL（审核 P1-03）

必须先讲清的一件事
------------------
失效形态（破损/磨损）是**换刀之后**才填的台账字段。线上不可能知道当前这把刀会以哪种
形态失效。所以"按形态分层的 RUL"**不是可部署的预测器**，它是一个诊断：
    —— 破损与磨损是不是两种不同的寿命机制？
如果是（寿命分布/Weibull 形状显著不同）→ 该用两套分布建模；
如果否 → 分层无价值，单分布即可，不要为此增加复杂度。

分两层做
--------
Part 1  台账级（n=60，不依赖 FOCAS 数据）：形态分组的寿命对比 + Weibull
Part 2  刀寿分布形态：是否集中在设定值附近（这决定"是否提前失效"能否成为标签）
Part 3  数据级分层可行性：prog × 形态 交叉，同程序同形态的训练池够不够
Part 4  强行分层回测：能分层的那部分刀上，分层 vs 不分层 的 MAE 对比
Part 5  替代问法的样本量评估

复用约定
--------
- 回测核心：bt_core.nested_predict（不改它，它同时是 Test-A 的验证对象）
- HI 源：asofC2（FIX-02 因果化后的主口径）
- Weibull：复用 s03_conditional.weibull_mle
"""
import os
import re
import sys
import datetime as dt

import numpy as np
import pandas as pd
from scipy import stats

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
import bt_core as BT


def weibull_mle(x):
    """两参数 Weibull MLE（完整数据），网格搜索 k、闭式解 λ。

    ⚠ 与 `s03_conditional.py:70` 同公式，此处**内联**而非 import：
       s03 没有 `if __name__ == '__main__'` 保护，import 会连带执行它的整套回测
       （实测会重跑一遍 predictions/metrics 并覆盖 13_整改执行_v2 的产物）。
       为保证本轮不触碰历史产物，此处复制一份；两处如需改公式必须同步。
    """
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

ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../20261027
# 台账（P1-07：0 号位已授权随包公开）→ 包内副本优先，仓库根仅作回退
XLS = os.path.join(BASE, '15_台账真值检验', 'input', '三厂换刀记录.xlsx')
if not os.path.exists(XLS):
    XLS = os.path.join(ROOT, '三厂换刀记录.xlsx')
HI_CSV = os.path.join(ROOT, 'out_T1111', 'xalign_HI.csv')
ASOF = os.path.abspath(os.path.join(HERE, '..', '13_整改执行_v2', 'hi_asof', 'hi_asof_long.csv'))
OUT = os.path.abspath(os.path.join(HERE, '..', '16_形态分层RUL'))
os.makedirs(OUT, exist_ok=True)

K, W = 20, 10
CUTS = [20, 40, 60, 80]
SETPOINT = 100.0       # 台账里出现最多的"刀寿"值（32/56 次），当作操作工的目标件数

TEXT_TIME_FIX = {
    '2026/9/416：05': '2026-09-04 16:05',
    '2026/9/18/16:26': '2026-09-18 16:26',
    '2026/10/26/9：20': '2026-10-26 09:20',
}

log = []
P = log.append


# ------------------------------------------------------------------ 台账
def parse_t(v):
    if pd.isna(v):
        return pd.NaT
    if isinstance(v, (pd.Timestamp, dt.datetime)):
        return pd.Timestamp(v)
    t = str(v).strip().replace('：', ':')
    if t in TEXT_TIME_FIX:
        return pd.Timestamp(TEXT_TIME_FIX[t])
    t = re.sub(r'^(\d{4})/(\d{1,2})/(\d{1,2})[ /](\d{1,2}):(\d{2})$', r'\1-\2-\3 \4:\5', t)
    return pd.to_datetime(t, errors='coerce')


def form_of_reason(s):
    s = str(s)
    if '破损' in s:
        return '破损'
    if '磨损' in s:
        return '磨损'
    return '未标'


def life_of_reason(s):
    m = re.findall(r'(\d+)', str(s))
    return int(m[-1]) if m else np.nan


def load_ledger():
    L = pd.read_excel(XLS, sheet_name=0)
    L['S'] = L['StartTime'].map(parse_t)
    L['E'] = L['EndTime'].map(parse_t)
    L['形态'] = L['换刀原因'].map(form_of_reason)
    L['刀寿'] = L['换刀原因'].map(life_of_reason)
    L['在机h'] = (L['E'] - L['S']).dt.total_seconds() / 3600
    L['cycle'] = L['顺序号'].astype(int)
    return L


def b10(k, lam):
    """Weibull B10 寿命（10% 失效分位）"""
    return float(lam * (-np.log(0.9)) ** (1.0 / k))


def part1(L):
    P('=' * 72)
    P('Part 1  台账级形态对比（n=60，不依赖 FOCAS 数据）')
    P('=' * 72)

    rows = []
    for f in ('破损', '磨损', '未标'):
        g = L[L['形态'] == f]
        x = g['刀寿'].dropna().values.astype(float)
        h = g['在机h'].dropna().values.astype(float)
        if len(x) >= 3:
            k, lam = weibull_mle(x)
            # ⚠ k 触到网格上界 12 → 拟合退化（数据高度集中），参数不可解释为"形状"
            k_flag = 'BOUNDARY' if k >= 11.99 else 'ok'
        else:
            k, lam = np.nan, np.nan
            k_flag = 'n/a'
        rows.append(dict(形态=f, n=len(g), 刀寿可提取=len(x),
                         刀寿中位=np.median(x) if len(x) else np.nan,
                         刀寿均值=x.mean() if len(x) else np.nan,
                         刀寿标准差=x.std(ddof=1) if len(x) > 1 else np.nan,
                         刀寿最小=x.min() if len(x) else np.nan,
                         刀寿最大=x.max() if len(x) else np.nan,
                         在机h中位=np.median(h) if len(h) else np.nan,
                         Weibull_k=k, Weibull_lam=lam,
                         Weibull_k_flag=k_flag,
                         B10=b10(k, lam) if np.isfinite(k) else np.nan))
    T1 = pd.DataFrame(rows)
    T1.to_csv(os.path.join(OUT, 'form_life_summary.csv'), index=False, encoding='utf-8-sig')

    for _, r in T1.iterrows():
        P('  %-4s n=%2d  刀寿 中位 %5.1f 均值 %6.1f (%.1f~%.0f)  |  在机 %5.2f h  |  Weibull k=%5.2f λ=%6.1f  B10=%5.1f'
          % (r['形态'], r.n, r.刀寿中位, r.刀寿均值, r.刀寿最小, r.刀寿最大,
             r.在机h中位, r.Weibull_k, r.Weibull_lam, r.B10))
    P('')
    P('  ⛔ 上表 Weibull 参数**不可用**，两个原因都必须写在报告里：')
    P('     ① k 全部顶到网格上界 12（见 Weibull_k_flag=BOUNDARY）→ 拟合退化，参数没有"形状"含义；')
    P('     ② 但方法前提存疑：Weibull 要求观测是失效事件，而本台账 %d/%d 条刀寿恰好=100。'
      % (int((L['刀寿'] == SETPOINT).sum()), int(L['刀寿'].notna().sum())))
    P('        这些「=100」的成因（计划换刀 / 工艺判废阈值 / 寿命设定 / 不同程序共用目标）')
    P('        **无法由台账区分**（需换刀时的实测刃口状态与尺寸判定，当前没有）。')
    P('        → 若实为计划换刀，则为右删失，需竞争风险模型；若为经验寿命到限，则前提成立。')
    P('        → 在无法区分之前，k/λ/B10 一律标注为**探索性、不可引用**。')

    a = L.loc[L['形态'] == '破损', '刀寿'].dropna().values.astype(float)
    b = L.loc[L['形态'] == '磨损', '刀寿'].dropna().values.astype(float)
    P('')
    P('  --- 破损 vs 磨损 的分布检验（刀寿，件） ---')
    if len(a) >= 3 and len(b) >= 3:
        u, pu = stats.mannwhitneyu(a, b, alternative='two-sided')
        ks, pk = stats.ks_2samp(a, b)
        tt, pt = stats.ttest_ind(a, b, equal_var=False)
        P('  n=破损 %d / 磨损 %d' % (len(a), len(b)))
        P('  Mann-Whitney U = %.1f  p = %.4f' % (u, pu))
        P('  KS  D = %.4f  p = %.4f' % (ks, pk))
        P('  Welch t = %.3f  p = %.4f' % (tt, pt))
        P('  均值差 %.2f 件（破损 %.2f − 磨损 %.2f），差占中位寿命的 %.1f%%'
          % (a.mean() - b.mean(), a.mean(), b.mean(),
             100 * (a.mean() - b.mean()) / np.median(np.concatenate([a, b]))))
    else:
        pu = pk = pt = np.nan
        P('  样本不足，跳过')

    ha = L.loc[L['形态'] == '破损', '在机h'].dropna().values.astype(float)
    hb = L.loc[L['形态'] == '磨损', '在机h'].dropna().values.astype(float)
    if len(ha) >= 3 and len(hb) >= 3:
        u2, pu2 = stats.mannwhitneyu(ha, hb, alternative='two-sided')
        P('  在机时长：MWU p = %.4f（中位 %.2f h vs %.2f h）' % (pu2, np.median(ha), np.median(hb)))

    # 统计功效：这个样本量能检出多大差异？
    if len(a) >= 3 and len(b) >= 3:
        sp = np.sqrt(((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1)) / (len(a) + len(b) - 2))
        mde = (stats.norm.ppf(0.975) + stats.norm.ppf(0.80)) * sp * np.sqrt(1 / len(a) + 1 / len(b))
        P('  最小可检出差异（80%% 功效, α=0.05）= %.1f 件（合并 SD=%.1f）' % (mde, sp))
        P('  → 如果真实机制差异小于 %.1f 件，本样本无论怎样都测不出来' % mde)

    return T1, dict(p_mwu=pu, p_ks=pk, p_t=pt)


def part2(L):
    P('')
    P('=' * 72)
    P('Part 2  刀寿分布形态：是否集中在设定值附近')
    P('=' * 72)
    x = L['刀寿'].dropna().values.astype(float)
    vc = pd.Series(x).value_counts().sort_index()
    P('  可提取刀寿 %d 条；不同取值 %d 个' % (len(x), len(vc)))
    P('  频次（值:次数）：' + '  '.join('%d:%d' % (int(k), v) for k, v in vc.items()))
    P('')
    n100 = int((x == SETPOINT).sum())
    n_lt = int((x < SETPOINT).sum())
    n_gt = int((x > SETPOINT).sum())
    P('  = %d：%2d 条（%.1f%%）   < %d：%2d 条（%.1f%%）   > %d：%2d 条（%.1f%%）'
      % (SETPOINT, n100, 100 * n100 / len(x), SETPOINT, n_lt, 100 * n_lt / len(x),
         SETPOINT, n_gt, 100 * n_gt / len(x)))

    # 刀寿是否随顺序号变化（若在变，说明设定值被逐步调整，跨期不可比）
    sub = L.dropna(subset=['刀寿'])
    rho, prho = stats.spearmanr(sub['cycle'], sub['刀寿'])
    P('  刀寿 vs 顺序号 Spearman rho=%+.3f  p=%.4f' % (rho, prho))
    q = sub.groupby(pd.cut(sub['cycle'], [0, 15, 30, 45, 60]), observed=True)['刀寿'].agg(['size', 'median'])
    P('  按阶段看：' + '  '.join('%s→n=%d中位%.0f' % (str(i), r['size'], r['median']) for i, r in q.iterrows()))

    P('')
    P('  解读：')
    P('    - 刀寿高度集中在 %d。其成因有四种可能，**台账本身无法区分**：' % SETPOINT)
    P('      (a) 按计划件数换刀；(b) 工艺判废阈值就是 100；(c) 机床寿命设定=100；')
    P('      (d) 不同程序共用同一目标件数。')
    P('    - 台账 `是否真实失效` 全填"是"，若为按定义填写则该列不含信息（不能据此反推成因）。')
    P('    - 偏离 %d 的样本可记为"低于/高于目标件数"，但**不得直接称为"提前物理失效"/"超期使用"**'
      % SETPOINT)
    return dict(n100=n100, n_lt=n_lt, n_gt=n_gt, rho=rho, p_rho=prho)


def part3(L):
    P('')
    P('=' * 72)
    P('Part 3  数据级分层可行性：prog × 形态')
    P('=' * 72)
    hi = pd.read_csv(HI_CSV, encoding='utf-8-sig')
    seg = hi.groupby(['life_cycle', 'prog']).size().reset_index(name='n')
    main = seg.sort_values('n').groupby('life_cycle').tail(1).set_index('life_cycle')['prog']

    J = L[L['cycle'].isin(hi.life_cycle.unique())][['cycle', '形态', '刀寿']].copy()
    J['prog'] = J['cycle'].map(main)
    J = J.dropna(subset=['prog'])
    J['prog'] = J['prog'].astype(int)
    J.to_csv(os.path.join(OUT, 'cycle_form_prog.csv'), index=False, encoding='utf-8-sig')

    ct = pd.crosstab(J['prog'], J['形态'], margins=True)
    P('  有 HI 的周期 %d 个（台账 %d 个，其余被 min_pieces 过滤或数据未覆盖）' % (len(J), len(L)))
    for line in ct.to_string().split('\n'):
        P('  ' + line)
    P('')
    P('  同 prog 同形态的训练池大小（分层后每把刀能用几个兄弟刀）：')
    ok_cnt = 0
    for (p, f), g in J.groupby(['prog', '形态']):
        n = len(g)
        P('    prog%-3d %-4s n=%d → 留一后训练池 %d 个 %s'
          % (p, f, n, n - 1, '（够）' if n - 1 >= 2 else '（不足，需 ≥2）'))
        if n - 1 >= 2:
            ok_cnt += n
    P('')
    by_form = {}
    for f in ('破损', '磨损'):
        sub = J[J['形态'] == f]
        n_ok = 0
        for p, g in sub.groupby('prog'):
            if len(g) - 1 >= 2:
                n_ok += len(g)
        by_form[f] = (n_ok, len(sub))
    P('  → 能真正分层的刀（按形态拆开看）：')
    for f, (n_ok, n_tot) in by_form.items():
        P('     %-4s %d / %d 把可分层  %s'
          % (f, n_ok, n_tot, '' if n_ok else '← 一把都不可能'))
    P('  → 合计 **%d / %d 把**，但**全部集中在"破损"组**' % (ok_cnt, len(J)))
    if by_form.get('磨损', (0, 0))[0] == 0:
        P('  ⛔ 磨损组 0 把可分层 —— 同程序内磨损刀最多 1 个，留一后训练池为空。')
        P('     所以「按形态分层」实际只做到「破损组内部按程序分层」，')
        P('     与不分层的差别仅限于"把 2 把磨损刀剔出池子"。')

    # 最严格样本 Version A 上的情形
    try:
        va = pd.read_csv(os.path.join(HERE, '..', '14_整改执行_v2b', 'dataset', 'sample_versions.csv'),
                         encoding='utf-8-sig')
        a_ids = [int(s.split('-c')[1]) for s in va[va.Version == 'A_strict'].Cycles.iloc[0].split(',')]
        ja = J[J['cycle'].isin(a_ids)]
        P('')
        P('  Version A（%d 把，最严格样本）的形态分布：%s'
          % (len(ja), ja['形态'].value_counts().to_dict()))
        if (ja['形态'] == '磨损').sum() <= 1:
            P('  → 磨损组只剩 %d 把，分层彻底不可能' % (ja['形态'] == '磨损').sum())
    except Exception as e:
        P('  (Version A 读取失败: %s)' % e)

    return J, main


def part4(L, main):
    P('')
    P('=' * 72)
    P('Part 4  强行分层回测：能分层的那部分刀，分层 vs 不分层')
    P('=' * 72)

    legacy = {}
    for (p, c), g in pd.read_csv(HI_CSV, encoding='utf-8-sig').groupby(['prog', 'life_cycle']):
        legacy[int(c)] = dict(prog=int(p), hi=g.sort_values('piece_idx')['HI_L_spindle_load'].to_numpy(float))
    asof = {}
    d = pd.read_csv(ASOF, encoding='utf-8-sig')
    for (mode, N, c), g in d.groupby(['Mode', 'N', 'LifeCycle']):
        asof[(mode, int(N), int(c))] = g.sort_values('PieceIdx')['HI'].to_numpy(float)

    prog_of = {c: v['prog'] for c, v in legacy.items()}
    life_all = {c: len(v['hi']) for c, v in legacy.items()}
    form = {int(r.cycle): r['形态'] for _, r in L.iterrows()}
    form_of = {c: form.get(c, '未知') for c in legacy}

    P('  可用刀 %d 把，形态分布 %s' % (len(legacy), pd.Series([form_of[c] for c in legacy]).value_counts().to_dict()))

    def rise_of(hi, N):
        if N > len(hi):
            return np.nan
        seg = hi[max(0, N - W):N]
        seg = seg[np.isfinite(seg)]
        return float(np.median(seg) - 1.0) if len(seg) >= 3 else np.nan

    def series_for(c, N):
        Lc = life_all[c]
        out = np.full(Lc, np.nan)
        h = asof.get(('C2', N, c))
        if h is not None:
            m = min(len(h), Lc)
            out[:m] = h[:m]
        return out

    rows = []
    for N in CUTS:
        RISE = {c: rise_of(series_for(c, N), N) for c in legacy}
        # 全体里活过 N 的刀才可评估
        sub = [c for c in legacy if life_all[c] > N]
        for k in sub:
            flat = BT.nested_predict(life_all, prog_of, k, N, RISE)
            if flat is None:
                continue
            # 分层：训练池只保留同形态
            fk = form_of[k]
            pool = {o: life_all[o] for o in life_all if form_of[o] == fk}
            pool_prog = {o: prog_of[o] for o in pool}
            n_sib = len([o for o in pool if pool_prog[o] == prog_of[k] and o != k])
            strat = BT.nested_predict(pool, pool_prog, k, N, RISE) if n_sib >= 2 else None
            act = life_all[k] - N
            rows.append(dict(
                N=N, cycle=k, prog=prog_of[k], 形态=fk, n_sib_strat=n_sib,
                strat_ok=int(strat is not None),
                M0B_flat=flat['M0B'], M0B_strat=(strat['M0B'] if strat else np.nan),
                M1New_flat=flat['M1New'], M1New_strat=(strat['M1New'] if strat else np.nan),
                Actual=act,
            ))
    R = pd.DataFrame(rows)
    R.to_csv(os.path.join(OUT, 'stratified_backtest.csv'), index=False, encoding='utf-8-sig')

    P('')
    P('  ⚠ 「全样本 MAE」与「分层 MAE」样本不同（可分层的刀本来就更容易预测），')
    P('    两者**不可直接比较**。唯一有效的比较是**同样本**列。')
    P('')
    P('  %-4s %6s %8s %10s %14s %14s %8s'
      % ('N', '全样本', '可分层', '同样本(把)', '同样本·扁平MAE', '同样本·分层MAE', '差'))
    for N in CUTS:
        g = R[R['N'] == N]
        if not len(g):
            continue
        ok = g[g.strat_ok == 1]
        e_flat_ok = (ok.M0B_flat - ok.Actual).abs().mean() if len(ok) else np.nan
        e_strat = (ok.M0B_strat - ok.Actual).abs().mean() if len(ok) else np.nan
        P('  %-4d %6d %8d %10d %14s %14s %8s'
          % (N, len(g), len(ok), len(ok),
             ('%.2f' % e_flat_ok) if len(ok) else '—',
             ('%.2f' % e_strat) if len(ok) else '—',
             ('%+.2f' % (e_strat - e_flat_ok)) if len(ok) else '—'))

    P('')
    P('  强制分层 vs 不分层 的配对差（只在可分层样本上）：')
    okall = R[R.strat_ok == 1].dropna(subset=['M0B_strat'])
    if len(okall):
        d0 = (okall.M0B_strat - okall.Actual).abs() - (okall.M0B_flat - okall.Actual).abs()
        P('    样本 %d 条，MAE 差 %+.2f 件，中位差 %+.2f' % (len(d0), d0.mean(), d0.median()))
        if len(d0) >= 5:
            # ⚠ 审核 P1-03：同一把刀在 4 个截断点各出现一次，不是 4 个独立样本。
            #   原版按**行**重采样 → 同一把刀被重复计入，CI 被低估（偏窄，容易假阳性）。
            #   改为**按刀聚类**：先算每把刀的平均差，再对刀重采样。
            _tmp = okall.assign(
                _diff=np.asarray(d0, float),
                _tool=okall['prog'].astype(str) + '-c' + okall['cycle'].astype(str))
            per_tool = _tmp.groupby('_tool')['_diff'].mean()
            rng = np.random.default_rng(20261027)
            bs = [float(per_tool.sample(len(per_tool), replace=True,
                                        random_state=int(rng.integers(1e9))).mean())
                  for _ in range(2000)]
            lo, hi = np.percentile(bs, [2.5, 97.5])
            P('    bootstrap 95%% CI [%+.2f, %+.2f]（按刀聚类，n_刀=%d / n_行=%d）  %s'
              % (lo, hi, len(per_tool), len(d0),
                 '跨 0（无显著差异）' if lo < 0 < hi else '不跨 0'))
            P('    ⚠ 原版按行重采样得到更窄的 CI；本行已改为按刀聚类（P1-03）')
    else:
        P('    无可分层样本 —— 无法比较')
    return R


def part5(L, J):
    P('')
    P('=' * 72)
    P('Part 5  替代问法的样本量评估')
    P('=' * 72)
    x = L['刀寿'].dropna().values.astype(float)
    n_lt = int((x < SETPOINT).sum())
    n_ge = int((x >= SETPOINT).sum())
    P('  问题：能否用 HI 预测"这次会提前失效"（刀寿 < %d）？' % SETPOINT)
    P('  台账级标签：提前 %d 条 / 不提前 %d 条 → 若做二分类，正类占比 %.1f%%'
      % (n_lt, n_ge, 100 * n_lt / len(x)))
    n_lt_d = int((J['刀寿'] < SETPOINT).sum())
    P('  但只有 %d 把刀有 HI 数据，其中"提前"仅 %d 把' % (len(J), n_lt_d))
    P('  → 二分类同样不可行：正类样本 %d 个，连一个验证折都撑不住。' % n_lt_d)
    P('')
    P('  最小样本需求（粗估）：')
    P('    二分类要稳定评估 AUC，正类至少需要 ~30 个（经验下限）。')
    P('    当前 %d 个 → 需再积累约 %d 次"提前失效"事件。' % (n_lt_d, max(0, 30 - n_lt_d)))
    rate = (100.0 * n_lt / len(x)) if len(x) else 0.0
    P('    台账 67 天产生 60 次换刀，其中提前失效占 %.1f%% → 约每 %.1f 天 1 次'
      % (rate, 67 / max(n_lt, 1)))
    if n_lt:
        days = (30 - n_lt_d) * (67 / n_lt) if n_lt_d < 30 else 0
        P('    按此速率，凑够 30 个"提前失效"还需约 %.0f 天（单台单刀）' % days)
        # Sheet2 列出 4 台设备共 8 把刀；若全部接入，事件速率近似按刀数放大
        N_TOOLS = 8
        P('    但调查表列出 4 台设备共 %d 把刀；若全部接入采集，事件速率近似 ×%d → 约 %.0f 天'
          % (N_TOOLS, N_TOOLS, days / N_TOOLS))
        P('    （乐观估计：各刀换刀频率不同，实际会慢于线性放大）')
    return dict(n_lt=n_lt, n_ge=n_ge, n_lt_d=n_lt_d)


def main():
    L = load_ledger()
    P('台账：%d 行，形态 %s，刀寿可提取 %d 条'
      % (len(L), L['形态'].value_counts().to_dict(), L['刀寿'].notna().sum()))

    T1, pv = part1(L)
    p2 = part2(L)
    J, main_map = part3(L)
    R = part4(L, main_map)
    p5 = part5(L, J)

    P('')
    P('=' * 72)
    P('结论')
    P('=' * 72)
    if np.isfinite(pv.get('p_mwu', np.nan)):
        P('  1. 破损 vs 磨损 的寿命分布：MWU p=%.4f / KS p=%.4f / Welch p=%.4f'
          % (pv['p_mwu'], pv['p_ks'], pv['p_t']))
        P('     %s' % ('三者都不显著 → 没有证据表明是两种寿命机制'
                      if min(pv['p_mwu'], pv['p_ks'], pv['p_t']) > 0.05
                      else '至少一个显著 → 需进一步看效应量'))
    P('  2. 分层在数据上结构不可行：同程序内形态几乎单一（prog20 全破损）')
    P('  3. 刀寿 %d 条中 %d 条正好等于 %d（%.0f%%）→ 其成因（计划换刀/工艺判废阈值/寿命设定/共用目标）'
      % (len(L['刀寿'].dropna()), p2['n100'], SETPOINT, 100 * p2['n100'] / len(L['刀寿'].dropna())))
    P('     **无法由台账区分**，需换刀时实测刃口状态与尺寸判定')
    P('  4. 因此本报告**不主张**按形态分层建模（限于统计层面：层间未检出差异）；')
    P('     是否分层属技术路线问题，按 G0-04 应由 0 号位裁决，本脚本不作路线结论')

    txt = '\n'.join(log)
    with open(os.path.join(OUT, 's31_console.log'), 'w', encoding='utf-8') as f:
        f.write(txt + '\n')
    print(txt)


if __name__ == '__main__':
    main()
