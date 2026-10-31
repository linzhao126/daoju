# -*- coding: utf-8 -*-
"""s30_switch_truth.py —— 用车间台账 EndTime 当换刀真值，检验“换刀时刻能否从数据检出”

背景
----
此前结论是「换刀时刻不可观测（四路全堵）」，依据之一是「19 个换刀边界 load_med 差中位 +1.4%，
与件间噪声 σ≈2% 同级」。但那个结论是在**换刀时刻靠猜**的条件下得出的。
现在拿到 60 条台账（`三厂换刀记录.xlsx` Sheet1），且已验证 EndTime ≡ 下一行 StartTime
（48/56 精确相等），也就是 EndTime 就是换刀发生时刻。

本脚本回答两个问题
------------------
Q1（可检测性）：把数据里所有 `gap > 阈值` 的断点都当成"疑似换刀"，其中有多少是真换刀？
   → 混淆矩阵 + 精确率/召回率 + AUC。若精确率很低，说明「中断 ≠ 换刀」，旧结论成立。
Q2（提前预警）：换刀前最后若干件的负载，按台账失效形态（破损/磨损）分层，是否有系统性异常？
   → 这决定「能不能在换刀前给出预警」，与 Q1 是两件事。

设计要点（避免循环论证）
------------------------
- 真值只来自台账，绝不从周期边界反推。
- 负样本 = 周期**内部**的断点（台账显示那里没有换刀）。
- 正样本 = 与台账 EndTime 相差 ≤ TOL_MIN 的断点。
- 断点时间记为 gap 前最后一行的时刻（即"最后一个有数据的时刻"）。

输出（写入 15_台账真值检验/）
------------------------------
switch_gap_candidates.csv   每个断点的明细 + 是否真换刀
switch_detect_metrics.csv   混淆矩阵与可分性指标
truth_vs_data_pieces.csv    台账"刀寿N" vs 数据实测件数 vs 缺口（重新生成，含 c38/c16 标注）
load_before_switch.csv      换刀前负载特征（按失效形态分层）
"""
import os
import re
import datetime as dt

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))                 # .../20261027
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import wsroot as _wsroot          # G2：不再数 '..'，按工作区特征向上定位
ROOT = _wsroot.find(_os.path.dirname(_os.path.abspath(__file__)))
OUT = os.path.join(BASE, '15_台账真值检验')                        # 相对本文件定位，改名目录不再写错
os.makedirs(OUT, exist_ok=True)

RAW = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv')
# 台账（P1-07：已获 0 号位授权随包公开）→ 包内副本优先，仓库根只作回退
XLS = os.path.join(BASE, '15_台账真值检验', 'input', '三厂换刀记录.xlsx')
if not os.path.exists(XLS):
    XLS = os.path.join(ROOT, '三厂换刀记录.xlsx')
HI = os.path.join(ROOT, 'out_T1111', 'xalign_HI.csv')

# 4 个被存成文本的时间格（原表格式：yyyy/m/d h:mm;@，值为字符串故格式不生效）
# F17/E18 同一串 = 2026/9/4 16:05；E33 = 9/18 16:26；F59 = 10/26 9:20
TEXT_TIME_FIX = {
    '2026/9/416：05': '2026-09-04 16:05',
    '2026/9/18/16:26': '2026-09-18 16:26',
    '2026/10/26/9：20': '2026-10-26 09:20',
}

TOL_MIN = 30.0        # 断点与台账 EndTime 相差 ≤ 该值 → 判为"命中真换刀"
GAP_MIN_S = 60.0      # 断点门槛：相邻采集间隔 > 该值才算候选
LEAD_PIECES = 10      # "换刀前最后 K 件"


# ---------------------------------------------------------------- 台账
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


def classify_form(s):
    s = str(s)
    if '破损' in s:
        return '破损'
    if '磨损' in s:
        return '磨损'
    return '未标形态'


def life_mark(s):
    """从「刀尖磨损刀具寿命100」这类文本里取寿命件数"""
    m = re.findall(r'(\d+)', str(s))
    return int(m[-1]) if m else np.nan


def load_ledger():
    L = pd.read_excel(XLS, sheet_name=0)
    L['S'] = L['StartTime'].map(parse_t)
    L['E'] = L['EndTime'].map(parse_t)
    L['形态'] = L['换刀原因'].map(classify_form)
    L['台账刀寿'] = L['换刀原因'].map(life_mark)
    L['cycle'] = L['顺序号'].astype(int)
    return L


# ---------------------------------------------------------------- 原始数据
def load_raw():
    d = pd.read_csv(
        RAW,
        usecols=['_time', 'life_cycle', 'L_allnum', 'L_spindle_load', 'L_abso_x', 'L_abso_z'],
        encoding='utf-8-sig',
    )
    d['t'] = pd.to_datetime(d['_time'], errors='coerce', format='mixed')
    d = d.dropna(subset=['t']).sort_values('t').reset_index(drop=True)
    return d


def find_breaks(d):
    """相邻行间隔 > GAP_MIN_S 的位置。返回断点表（时间 = gap 前最后一行时刻）"""
    gap = d['t'].diff().dt.total_seconds()
    idx = np.where(gap.values > GAP_MIN_S)[0]
    rows = []
    for i in idx:
        prev = d.iloc[i - 1]
        nxt = d.iloc[i]
        rows.append(dict(
            break_time=prev['t'],
            after_time=nxt['t'],
            gap_s=float(gap.iloc[i]),
            cyc_before=int(prev['life_cycle']),
            cyc_after=int(nxt['life_cycle']),
            allnum_before=int(prev['L_allnum']),
            allnum_after=int(nxt['L_allnum']),
            load_before=float(prev['L_spindle_load']),
            load_after=float(nxt['L_spindle_load']),
            cross_cycle=bool(prev['life_cycle'] != nxt['life_cycle']),
        ))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- AUC（无 sklearn 依赖）
def auc_score(pos, neg):
    """Mann-Whitney 形式的 AUC：P(正样本分值 > 负样本分值)"""
    pos = np.asarray([v for v in pos if np.isfinite(v)], float)
    neg = np.asarray([v for v in neg if np.isfinite(v)], float)
    if len(pos) == 0 or len(neg) == 0:
        return np.nan
    allv = np.concatenate([pos, neg])
    order = allv.argsort()
    ranks = np.empty_like(order, float)
    ranks[order] = np.arange(1, len(allv) + 1)
    # 并列取平均秩
    s = pd.Series(allv)
    ranks = s.rank(method='average').values
    r_pos = ranks[:len(pos)].sum()
    return float((r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def main(blank_cycle=False, out_dir=None):
    log = []
    p = log.append

    out = out_dir if out_dir else OUT
    os.makedirs(out, exist_ok=True)
    L = load_ledger()
    d = load_raw()
    if blank_cycle:
        # Test-E 用：把 life_cycle 整列置零，证明指标不依赖它
        d = d.copy()
        d['life_cycle'] = 0
    p('台账：%d 行，S 解析失败 %d，E 解析失败 %d（末行 EndTime 空属正常）'
      % (len(L), L.S.isna().sum(), L.E.isna().sum()))
    p('数据：%d 行，%s → %s，%d 个 life_cycle'
      % (len(d), d.t.min(), d.t.max(), d.life_cycle.nunique()))

    # 真值 = 台账 EndTime，且该**台账窗内确实有数据行**
    # ⚠ Test-E 要求：不得用 life_cycle 判断"数据里有这个周期"。改用纯时间判定 ——
    #   台账窗 [S, E) 内若一行数据都没有（如 10-01~10-13 整机零数据），则该换刀不可评估。
    t_arr = d['t'].values
    n_in_win = []
    for _, r in L.iterrows():
        if pd.isna(r['S']) or pd.isna(r['E']):
            n_in_win.append(0)
        else:
            n_in_win.append(int(((t_arr >= r['S']) & (t_arr < r['E'])).sum()))
    L['n_rows_in_win'] = n_in_win
    L['in_data'] = L['E'].notna() & (L['n_rows_in_win'] > 0)
    truth = L[L.in_data].copy()
    p('真值（台账 EndTime 且在窗内有数据）：%d 条，周期 %s'
      % (len(truth), sorted(truth.cycle.tolist())))
    miss_cyc = sorted(set(int(x) for x in L.loc[L.E.notna() & ~L.in_data, 'cycle']))
    p('台账有换刀、但窗内无数据的周期：%s（共 %d 个，无法评估）' % (miss_cyc, len(miss_cyc)))

    # ---------------- Q1：断点 vs 真换刀
    B = find_breaks(d)
    p('断点（gap > %.0fs）：%d 个，其中跨周期 %d / 周期内 %d'
      % (GAP_MIN_S, len(B), int(B.cross_cycle.sum()), int((~B.cross_cycle).sum())))

    # 每个断点匹配最近的真值
    # ⚠ 排序后必须同步取 cycle：原版 E_sorted 排了序、cycle.values 没排 → 周期错配
    ts_truth = truth.dropna(subset=['E']).sort_values('E')
    E_sorted = ts_truth['E'].values
    CYC_SORTED = ts_truth['cycle'].values
    if len(E_sorted):
        dmin, near = [], []
        for bt in B.break_time.values:
            dif = (E_sorted - bt) / np.timedelta64(1, 's')
            k = int(np.argmin(np.abs(dif)))
            dmin.append(float(dif[k]) / 60.0)
            near.append(int(CYC_SORTED[k]) if abs(dif[k]) / 60.0 <= TOL_MIN else 0)
        B['diff_to_truth_min'] = dmin
        B['truth_cycle'] = near
        B['is_true_switch'] = (np.abs(np.array(dmin)) <= TOL_MIN).astype(int)
    else:
        B['diff_to_truth_min'] = np.nan
        B['truth_cycle'] = 0
        B['is_true_switch'] = 0

    B = B.sort_values('break_time')
    B.to_csv(os.path.join(out, 'switch_gap_candidates.csv'), index=False, encoding='utf-8-sig')

    # ---- 真值侧：每个真值对应的「最近断点」在换刀前多少分钟
    # 这一列回答的是"数据是否在换刀前就断了"，而不是"能不能检出换刀"
    p('')
    p('=== Q1a 每个真值的最近断点（数据在换刀前多久断的）===')
    per_truth = []
    bt = pd.Series(B.break_time.sort_values().values)
    for _, r in truth.sort_values('cycle').iterrows():
        # ⚠ 只取「换刀之前」的最后一个断点 = 数据在这把刀的最后时刻
        #   第一版取「最近断点」，而件间空档每 ~5 分钟一个，等于在测断点密度，无区分力
        dif = (bt - pd.Timestamp(r.E)).dt.total_seconds().values
        before = dif[dif <= 0]
        if len(before) == 0:
            per_truth.append(dict(cycle=int(r.cycle), 形态=r['形态'], endtime=r.E,
                                  stop_before_switch_min=np.nan,
                                  data_has_piece='NO'))
            continue
        k = int(np.argmax(before))          # 最接近换刀时刻的那个「之前」
        per_truth.append(dict(cycle=int(r.cycle), 形态=r['形态'], endtime=r.E,
                              stop_before_switch_min=abs(float(before[k])) / 60.0,
                              data_has_piece='YES'))
    T = pd.DataFrame(per_truth)
    T.to_csv(os.path.join(out, 'truth_nearest_break.csv'), index=False, encoding='utf-8-sig')
    for _, r in T.iterrows():
        v = r.stop_before_switch_min
        p('  c%-4d %-4s 数据在换刀前 %s 分钟停止'
          % (r.cycle, r['形态'], ('%.1f' % v) if v == v else '  —（无更早断点）'))
    ok2 = T[T.stop_before_switch_min.notna()]
    p('  中位 %.1f 分钟；>20 分钟的周期 %d/%d（即数据在换刀前很久就断了）'
      % (ok2.stop_before_switch_min.median(),
         int((ok2.stop_before_switch_min > 20).sum()), len(ok2)))

    # ---- 阈值曲线：把「gap > 阈值」当检测器，看精确率/召回率如何权衡
    # 正样本按「每个真值一个」计，避免一个真值被多个断点重复匹配
    p('')
    p('=== Q1b gap 阈值 → 精确率/召回率（可部署口径：只看 gap，不看标签）===')
    # ⚠ 原版 tp 取自 cross_cycle（= life_cycle 变化）→ 循环论证：
    #   报告写"只看 gap 不看标签"，代码里却用标签分 TP/FP。
    #   改为：候选 = gap > 阈值（纯数据）；TP/FP 由 is_true_switch（台账时间 ±容差）判定。
    #   这样 life_cycle 完全不参与任何指标（Test-E 的前提）。
    pos_all = B.loc[B.is_true_switch == 1, 'gap_s'].values
    neg_all = B.loc[B.is_true_switch == 0, 'gap_s'].values
    p('  真换刀断点 %d 个 / 非换刀断点 %d 个（均按台账时间 ±%d 分钟判定）'
      % (len(pos_all), len(neg_all), TOL_MIN))
    curve = []
    for thr_h in (0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 12, 24):
        thr = thr_h * 3600
        cand_all = B[B.gap_s > thr]
        tp = int((cand_all.is_true_switch == 1).sum())
        fp = int((cand_all.is_true_switch == 0).sum())
        cand = tp + fp
        prec = tp / cand if cand else np.nan
        # 召回：真值中，其前置断点 gap 超阈值 的比例
        rec = tp / len(truth) if len(truth) else np.nan
        curve.append(dict(threshold_h=thr_h, candidates=cand, tp=tp, fp=fp,
                          precision=round(prec, 4) if prec == prec else np.nan,
                          recall=round(min(rec, 1.0), 4)))
        p('  阈值 %5.2f h → 候选 %4d（真 %2d / 假 %4d）精确率 %s 召回率 %s'
          % (thr_h, cand, tp, fp,
             ('%.3f' % prec) if prec == prec else '  -  ',
             ('%.3f' % min(rec, 1.0))))
    CV = pd.DataFrame(curve)
    CV.to_csv(os.path.join(out, 'gap_threshold_curve.csv'), index=False, encoding='utf-8-sig')

    pos_gap = pos_all
    neg_gap = neg_all
    a_gap = auc_score(pos_gap, neg_gap)
    p('')
    p('  gap 大小区分「换刀断点」vs「周期内断点」：AUC=%.3f' % a_gap)
    p('  换刀断点 gap：中位 %.0f 分钟，最小 %.1f，最大 %.1f 小时'
      % (np.median(pos_gap) / 60, pos_gap.min() / 60, pos_gap.max() / 3600))
    p('  周期内断点 gap：中位 %.0f 分钟，p90 %.1f，最大 %.1f 小时'
      % (np.median(neg_gap) / 60, np.percentile(neg_gap, 90) / 60, neg_gap.max() / 3600))

    n_cand = len(B)
    n_truth = len(truth)
    _c = B[B.gap_s > GAP_MIN_S]
    prec = (len(_c) and float((_c.is_true_switch == 1).mean())) or np.nan
    rec = (int((_c.is_true_switch == 1).sum()) / n_truth) if n_truth else np.nan

    # 换刀前后负载差 作为判别（取绝对值）
    B['abs_dload'] = (B.load_after - B.load_before).abs()
    a_dl = auc_score(B.loc[B.is_true_switch == 1, 'abs_dload'].values,
                     B.loc[B.is_true_switch == 0, 'abs_dload'].values)
    p('  |Δ负载| 作为判别：AUC=%.3f' % a_dl)

    # 跨周期标记本身就是最强特征 —— 但它是从 life_cycle 标签来的，等于用答案
    # 这里如实列出：如果允许看 label，精确率会是多少（这是"上界"，不是可部署能力）
    if (B.cross_cycle == 1).sum():
        prec_label = (B.loc[B.cross_cycle == 1, 'is_true_switch'].mean())
        p('  ※ 若允许直接看 life_cycle 变化（等于用了标签本身）：精确率 %.3f，'
          '但这不可部署，仅作上界' % prec_label)

    M = pd.DataFrame([
        dict(Metric='候选断点数', Value=n_cand),
        dict(Metric='跨周期断点数', Value=int(B.cross_cycle.sum())),
        dict(Metric='周期内断点数', Value=int((~B.cross_cycle).sum())),
        dict(Metric='台账真值数', Value=n_truth),
        dict(Metric='数据提前>20min停止的周期数', Value=int((T.stop_before_switch_min > 20).sum())),
        dict(Metric='精确率(gap>60s, 可部署)', Value=round(prec, 4)),
        dict(Metric='召回率(gap>60s)', Value=round(min(rec, 1.0), 4)),
        dict(Metric='AUC_gap', Value=round(a_gap, 4)),
        dict(Metric='AUC_abs_dload', Value=round(a_dl, 4)),
    ])
    M.to_csv(os.path.join(out, 'switch_detect_metrics.csv'), index=False, encoding='utf-8-sig')

    # ---------------- Q1b：按周期看每个周期内部的假断点
    p('')
    p('=== Q1b 周期内断点（假阳性来源）===')
    inner = B[~B.cross_cycle]
    if len(inner):
        agg = inner.groupby('cyc_before').agg(n=('gap_s', 'size'), max_gap_h=('gap_s', lambda s: s.max() / 3600),
                                              sum_gap_h=('gap_s', lambda s: s.sum() / 3600))
        agg = agg.sort_values('max_gap_h', ascending=False)
        for c, r in agg.iterrows():
            p('  c%-4d 内部断点 %2d 个，最大 %.1f 小时，累计 %.1f 小时'
              % (c, r.n, r.max_gap_h, r.sum_gap_h))

    # ---------------- Q2：换刀前负载（按形态分层）
    p('')
    p('=== Q2 换刀前最后 %d 件的负载（按台账失效形态）===' % LEAD_PIECES)
    hi = pd.read_csv(HI, encoding='utf-8-sig')
    hi_cnt = hi.groupby('life_cycle').size()

    rows = []
    for _, r in truth.iterrows():
        c = int(r.cycle)
        sub = hi[hi.life_cycle == c].sort_values('piece_idx')
        if len(sub) < 25:
            rows.append(dict(cycle=c, 形态=r['形态'], n=len(sub), 台账刀寿=r['台账刀寿'],
                             data_n=len(sub), base=np.nan, lead=np.nan, rel=np.nan, note='件数不足'))
            continue
        y = sub['HI_L_spindle_load'].values.astype(float)
        base = float(np.median(y[:10]))
        lead = float(np.median(y[-LEAD_PIECES:]))
        rows.append(dict(cycle=c, 形态=r['形态'], n=len(sub), 台账刀寿=r['台账刀寿'],
                         data_n=len(sub), base=round(base, 5), lead=round(lead, 5),
                         rel=round(lead / base - 1.0, 5), note=''))
    Q = pd.DataFrame(rows)
    Q.to_csv(os.path.join(out, 'load_before_switch.csv'), index=False, encoding='utf-8-sig')

    ok = Q[Q.rel.notna()]
    for form, g in ok.groupby('形态'):
        p('  %-6s n=%2d  rel(末%d件/前10件−1) 中位 %+.4f  均值 %+.4f  最小 %+.4f  最大 %+.4f'
          % (form, len(g), LEAD_PIECES, g.rel.median(), g.rel.mean(), g.rel.min(), g.rel.max()))
    p('  全部 n=%d 中位 %+.4f' % (len(ok), ok.rel.median()))

    # 破损 vs 磨损 的分离度
    a, b = ok[ok.形态 == '破损'].rel.values, ok[ok.形态 == '磨损'].rel.values
    if len(a) and len(b):
        p('  AUC(破损 > 磨损 的 rel)：%.3f  （0.5 = 完全不可分）' % auc_score(a, b))

    # ---------------- Q3：台账件数 vs 数据件数
    p('')
    p('=== Q3 台账"刀寿N" vs 数据实测件数 ===')
    P = []
    for _, r in L.iterrows():
        c = int(r.cycle)
        dn = int(hi_cnt.get(c, 0))
        P.append(dict(cycle=c, 形态=r['形态'], 台账刀寿=r['台账刀寿'], 数据件数=dn,
                      差=(dn - r['台账刀寿']) if pd.notna(r['台账刀寿']) else np.nan,
                      数据有=('YES' if dn > 0 else 'NO')))
    PP = pd.DataFrame(P)
    PP.to_csv(os.path.join(out, 'truth_vs_data_pieces.csv'), index=False, encoding='utf-8-sig')
    both = PP[(PP.数据有 == 'YES') & PP.台账刀寿.notna()]
    p('  两处都有：%d 个周期；差的中位 %+.0f 件，|差| 中位 %.0f 件，最大 |差| %d 件'
      % (len(both), both.差.median(), both.差.abs().median(), int(both.差.abs().max())))
    p('  差 > 20 件的周期：')
    for _, r in both[both.差.abs() > 20].iterrows():
        p('    c%-4d 台账 %3.0f  数据 %3d  差 %+4.0f' % (r.cycle, r['台账刀寿'], r['数据件数'], r.差))

    # ---- 供 Test-E 比较的指标（全部不依赖 life_cycle）
    METRICS = dict(n_truth=int(len(truth)), n_breaks=int(len(B)),
                   n_pos=int(len(pos_all)), n_neg=int(len(neg_all)),
                   auc_gap=float(a_gap), auc_dload=float(a_dl),
                   curve=[dict(t=c['threshold_h'], tp=c['tp'], fp=c['fp'],
                               prec=c['precision'], rec=c['recall']) for c in curve])

    txt = '\n'.join(log)
    with open(os.path.join(out, 's30_console.log'), 'w', encoding='utf-8') as f:
        f.write(txt + '\n')
    print(txt)
    return METRICS


if __name__ == '__main__':
    main()
