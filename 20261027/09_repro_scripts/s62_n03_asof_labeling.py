# -*- coding: utf-8 -*-
"""s62_n03_asof_labeling.py —— N03：逐刀 as_of 真值映射 + A/C2 分口径复评

对应审核：Commit 0338a5d §3 P0-01、§5 N03。
  症结：`s53_r04_grade_split.py` 的 `run_pool()` 用 `LIFE={c:len(v['hi'])}`、
        `ActualRUL=LIFE[k]-N`，把 **aligned 条数**当成回测真值，与文档承诺
        "AlignedPieces≠物理寿命"未落实。本脚本**不改列名**，而是把三种终点口径
        真正拆开打分，并逐刀声明 C3 真值是否可得。

三个终点口径（同一刃口互不相等，是本文件要暴露的事实）：
  A  aligned 条数     = len(HI)                —— s53 误用的"寿命"
  C2 observed 参与件数 = allnum 首末跨度         —— "距数据侧记录换刀"的件数（阶段演示口径）
  台账 ledger 写死刀寿 = 人工填写"刀寿NNN"/P01   —— **设定值**，不是实测（c25 写 120 而 observed 仅 52）

C3 个体化物理寿命（独立失效/工艺判废依据）：本批 **全部 E2（仅人工文字）**，
  HasQualityAnomaly=UNKNOWN、无尺寸/粗糙度数值 → **C3 真值无一可得**，不打分、不验收。

产出（写 `N03_逐刀真值映射/`）：
  N03_asof_per_edge_truth.csv   —— 逐刃口 as_of 真值映射表（三口径 + 依据级别 + 删失 + 缺口 + C3 可得性）
  N03_pred_two_targets.csv      —— 逐刀逐截断的 A / C2 两口径预测明细（同一泄漏隔离核）
  N03_score_compare.csv         —— A / C2 分别的 n / MAE / MedAE / Bias（含分母）
  N03_missing_excluded.csv      —— 算不出 / 被剔除的行与原因
  s62_console.txt               —— 运行日志原件
可重跑：`PY=python run_all.sh`（见 N07）或 `python s62_n03_asof_labeling.py`
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_core as BT

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
OUT = os.path.join(BASE, 'N03_逐刀真值映射')
os.makedirs(OUT, exist_ok=True)

P01 = os.path.join(BASE, '34_P0-01收尾', 'P01_evidence_all60.csv')
LIFE_AUDIT = os.path.join(BASE, '03_原始数据与标签', 'life_audit_4152.csv')
HI28 = os.path.join(BASE, '24_程序归属修正', 'HI_merged_28.csv')

# 与 s53 保持一致的截断（aligned 索引）与子集，便于对照；命名沿用不改下游
CUTS = [20, 40, 60, 80]
W = 10
STRICT = [28, 29, 30, 31, 39, 40, 49, 50, 56, 59]   # ±8 十把（D-07）
GOLD = [30, 39]                                      # ±3 黄金两把
LOG = []


def P(*a):
    s = ' '.join(str(x) for x in a)
    print(s)
    LOG.append(s)


# ---------- 逐刃口 as_of 真值表 ----------
def edge_truth_table():
    h = pd.read_csv(HI28, encoding='utf-8-sig')
    la = pd.read_csv(LIFE_AUDIT, encoding='utf-8-sig')
    la['c'] = la.PhysicalLifeId.str.extract(r'c(\d+)')[0].astype('Int64')
    la['ledger_written'] = la.ChangeReasonRaw.str.extract(r'刀[尖具]?寿?\D*(\d+)')[0].astype(float)
    la_map = {int(r['c']): r for _, r in la.iterrows() if pd.notna(r['c'])}
    p01 = pd.read_csv(P01, encoding='utf-8-sig').set_index('Cycle')

    rows = []
    for c, g in h.groupby('life_cycle'):
        c = int(c)
        g = g.sort_values('piece_idx')
        n_ali = int(len(g))
        an = g['allnum'].to_numpy(int)
        span = int(an.max() - an.min() + 1) if n_ali else 0
        a = la_map.get(c)
        prog = int(g['prog'].iloc[0]) if n_ali else np.nan
        seg = (int(a['ProgramSwitchRows']) + 1) if (a is not None and pd.notna(a.get('ProgramSwitchRows'))) else 'UNKNOWN'
        ledger_written = float(a['ledger_written']) if (a is not None and pd.notna(a['ledger_written'])) else np.nan
        ledger_p01 = float(p01.loc[c, 'LedgerLife']) if c in p01.index and pd.notna(p01.loc[c, 'LedgerLife']) else np.nan
        ledger = ledger_written if pd.notna(ledger_written) else ledger_p01
        # 终点事件类型 / C3 依据
        if a is not None:
            reason = str(a['ChangeReasonRaw'])
            break_yes = str(a['HasTipBreakage']).upper().startswith('Y')
            qual = str(a['HasQualityAnomaly'])
            evid = str(a['LabelEvidenceLevel'])
            planned = str(a['IsPlannedLifeOnly']).upper().startswith('Y')
            rc = bool(a['RightCensored'])
            lc = bool(a['LeftTruncated'])
            ic = bool(a['IntervalCensored'])
            gap = str(a['GapAmbiguity'])
        else:
            reason = '（无 life_audit 记录）'
            break_yes, qual, evid, planned = False, '无', 'E1(仅默认台账值,无失效判据)', True
            rc = lc = ic = False
            gap = '无'
        # C3：需要独立失效/工艺判废的**数值**依据（尺寸/粗糙度/断刃实测）。
        #   本批无论有无尽 life_audit，最高只到 E2（人工文字）/E1（默认台账值），
        #   无任何数值判废记录 → C3 真值逐刀均不可得（c3_avail 恒为 False）。
        c3_avail = False
        if break_yes:
            ev_type = '刀尖破损（人工文字）'
        elif '磨损' in reason:
            ev_type = '刀尖磨损（人工文字）'
        else:
            ev_type = reason
        rows.append(dict(
            PhysicalLifeId=('4152-L-T1111-c%d' % c) if a is None else a['PhysicalLifeId'],
            Cycle=c, 主程序=prog, 程序段数seg=seg,
            A_aligned条数=n_ali, C2_observed参与件数=span,
            台账写死刀寿=ledger,
            缺口件数_observed_minus_aligned=span - n_ali,
            终点事件类型=ev_type,
            HasTipBreakage=('YES' if break_yes else 'NO'),
            HasQualityAnomaly=qual,
            依据级别=evid, 是否仅计划寿命=('YES' if planned else 'NO'),
            左截断=lc, 右删失=rc, 区间删失=ic, 缺口歧义=gap,
            C3_独立失效判据=('可得' if c3_avail else '不可得(无尺寸/粗糙度数值)'),
            C3_可验收=('否' if not c3_avail else '待定'),
        ))
    df = pd.DataFrame(rows).sort_values('Cycle')
    df.to_csv(os.path.join(OUT, 'N03_asof_per_edge_truth.csv'), index=False, encoding='utf-8-sig')
    P('[T] 逐刃口 as_of 真值表 → N03_asof_per_edge_truth.csv  (%d 行)' % len(df))
    P('    C3 可验收计数：', df.C3_可验收.value_counts().to_dict())
    P('    三口径互不相等的刃口数：',
      int(((df.A_aligned条数 != df.C2_observed参与件数) |
           (df.C2_observed参与件数 != df.台账写死刀寿)).sum()), '/', len(df))
    return df


def rise_of(hi, N):
    if N > len(hi):
        return np.nan
    seg = hi[max(0, N - W):N]
    seg = seg[np.isfinite(seg)]
    return float(np.median(seg) - 1.0) if len(seg) >= 3 else np.nan


# ---------- 两口径打分：模型在 aligned 空间跑（同 s53、同一泄漏隔离核），C2 由件数比换算 ----------
def score_two_targets():
    h = pd.read_csv(HI28, encoding='utf-8-sig')
    D = {}
    for c, g in h.groupby('life_cycle'):
        g = g.sort_values('piece_idx')
        an = g['allnum'].to_numpy(int)
        D[int(c)] = dict(prog=int(g['prog'].iloc[0]), hi=g['HI_L_spindle_load'].to_numpy(float),
                         ali=int(len(g)), an=an, an0=int(an.min()), anmax=int(an.max()))
    LIFE_A = {c: v['ali'] for c, v in D.items()}
    PROG = {c: v['prog'] for c, v in D.items()}
    eval_set = sorted(D)
    pred_rows, miss = [], []
    for N in CUTS:
        RISE = {c: rise_of(D[c]['hi'], N) for c in D}
        for k in eval_set:
            v = D[k]
            if v['ali'] <= N:
                miss.append(dict(CutoffN=N, Cycle=k, 原因='aligned %d ≤ 截断 %d' % (v['ali'], N)))
                continue
            r = BT.nested_predict(LIFE_A, PROG, k, N, RISE)
            if r is None:
                miss.append(dict(CutoffN=N, Cycle=k, 原因='同程序训练刀 <2（aligned 空间）'))
                continue
            span = v['anmax'] - v['an0'] + 1
            pieces_N = int(v['an'][N - 1] - v['an0'] + 1)          # 前 N 个 aligned 行对应的 observed 件号
            ratio = span / v['ali'] if v['ali'] else np.nan        # 件/条 比（近似换算，逐刀不同）
            true_A = v['ali'] - N
            true_C2 = span - pieces_N
            for m in ('M0A', 'M0B', 'M1Old', 'M1New'):
                pa = r[m]
                pred_rows.append(dict(
                    CutoffN=N, Cycle=k, Program=v['prog'],
                    模型=m,
                    A_true剩余aligned条数=true_A,
                    A_pred剩余aligned条数=(round(pa, 2) if np.isfinite(pa) else np.nan),
                    A_abs_err=(round(abs(pa - true_A), 2) if np.isfinite(pa) else np.nan),
                    件比_ratio=round(ratio, 3),
                    C2_true剩余参与件数=true_C2,
                    C2_pred剩余参与件数=(round(pa * ratio, 2) if np.isfinite(pa) else np.nan),
                    C2_abs_err=(round(abs(pa * ratio - true_C2), 2) if np.isfinite(pa) else np.nan),
                    训练刀数=len(r['S']),
                ))
    PR = pd.DataFrame(pred_rows)
    PR.to_csv(os.path.join(OUT, 'N03_pred_two_targets.csv'), index=False, encoding='utf-8-sig')
    pd.DataFrame(miss).to_csv(os.path.join(OUT, 'N03_missing_excluded.csv'), index=False, encoding='utf-8-sig')
    P('[S] 两口径逐刀预测明细 → N03_pred_two_targets.csv  (%d 行)' % len(PR))

    # 分口径汇总（全 28 / 严格∩有HI / 黄金），分别报 A 与 C2 的 MAE/MedAE/Bias 与分母
    strict_in = [c for c in STRICT if c in D]
    gold_in = [c for c in GOLD if c in D]
    summ = []
    for tag, es in (('全28', set(D)), ('严格±8∩HI', set(strict_in)), ('黄金±3', set(gold_in))):
        sub = PR[PR.Cycle.isin(es)]
        for N, dd in sub.groupby('CutoffN'):
            for m in ('M0A', 'M0B', 'M1Old', 'M1New'):
                r = dd[dd.模型 == m].dropna(subset=['A_abs_err', 'C2_abs_err'])
                if not len(r):
                    continue
                summ.append(dict(子集=tag, CutoffN=N, 模型=m, n=len(r),
                                 A_MAE=round(r.A_abs_err.mean(), 2),
                                 A_MedAE=round(r.A_abs_err.median(), 2),
                                 A_Bias=round(float((r.A_pred剩余aligned条数 - r.A_true剩余aligned条数).mean()), 2),
                                 C2_MAE=round(r.C2_abs_err.mean(), 2),
                                 C2_MedAE=round(r.C2_abs_err.median(), 2),
                                 C2_Bias=round(float((r.C2_pred剩余参与件数 - r.C2_true剩余参与件数).mean()), 2)))
    SU = pd.DataFrame(summ)
    SU.to_csv(os.path.join(OUT, 'N03_score_compare.csv'), index=False, encoding='utf-8-sig')
    P('[S] 分口径复评汇总 → N03_score_compare.csv')
    if len(SU):
        P(SU.to_string(index=False))
    return PR, SU


if __name__ == '__main__':
    P('=' * 70)
    P('N03 逐刀 as_of 真值映射 + A/C2 分口径复评')
    P('=' * 70)
    edge_truth_table()
    score_two_targets()
    P('')
    P('结论要点：')
    P('  1) 同一刃口的 aligned条数 / observed参与件数 / 台账写死刀寿 三者互不相等，')
    P('     s53 用 len(hi) 当"寿命"只是三口径之一，不能等同于物理寿命。')
    P('  2) 本批终点依据全部 E2（仅人工文字），无尺寸/粗糙度等独立失效判据 →')
    P('     C3 个体化物理寿命真值**逐刀均不可得**，故 A/C2 只是演示口径，不作 C3 验收。')
    P('  3) A 与 C2 分开打分：同一模型核、同一截断，因件/条换算比不同而误差不同，')
    P('     证明确实是"两个量纲"而非改列名。')
    with open(os.path.join(OUT, 's62_console.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG))
    P('DONE →', OUT)
