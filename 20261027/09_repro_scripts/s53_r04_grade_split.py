# -*- coding: utf-8 -*-
"""s53_r04_grade_split.py —— R04：严格 8 把 / 宽松 26 把分列输出

审核 R04 原文：
  「60 把完整 7 列明细；严格 8 把与宽松 26 把分别输出预测结果、无法计算的原因与分母；
    复核 c35、c38、c51 等边界差；避免将 AlignedPieceCount 当物理寿命。」

三块产出：
  §A  R04_lifecycle_7col.csv     —— 60 周期 × 7 列（LedgerLife / ObservedToolPieces /
                                     AlignedPieces / InvocationCount / BoundaryEvidence /
                                     UsableGrade / DenominatorNote）
  §B  R04_pred_split.csv         —— 两套口径 × 4 截断 × 4 模型 的 n / MAE / MedAE / Bias / Cover
      R04_strict8_detail.csv     —— 严格 8 把逐刀逐截断的预测明细（含"算不出"的行）
  §C  R04_boundary_recheck.csv   —— c35/c38/c51/c29 台账窗 vs 数据窗 vs 件数比
      R04_step_test.csv          —— 件级负载变点统计（多计假说的可检验推论）

口径说明（写在最前面，防止再被混用）：
  LedgerLife         = 台账"刀寿"（可能是**设定值**，不一定是实测件数）
  ObservedToolPieces = 数据侧 T1111 参与的件数（allnum 去重）—— 只有 3.3GB 覆盖的周期能算
  AlignedPieces      = xalign 出 HI 的件数（≤ ObservedToolPieces，缺 X 段的件会掉）
  InvocationCount    = 该刃口在本周期内的**调用段数**（目前只有 20 个周期有分段数据，
                       其余记 UNKNOWN；用"程序段切换数+1"作代理，不冒充真实调用计数）
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_core as BT

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
ROOT = os.path.abspath(os.path.join(BASE, '..', '..', '..'))
OUT = os.path.join(BASE, '附件_证据', '54_R04_分级输出')
os.makedirs(OUT, exist_ok=True)

P01 = os.path.join(BASE, '34_P0-01收尾', 'P01_evidence_all60.csv')
LIFE_AUDIT = os.path.join(BASE, '03_原始数据与标签', 'life_audit_4152.csv')
HI28 = os.path.join(BASE, '24_程序归属修正', 'HI_merged_28.csv')
PRED_B = os.path.join(BASE, '24_程序归属修正', 'pred_B_merged28_oldprog.csv')
OLD_LABELED = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv')
NEW_LABELED = os.path.join(BASE, '22_c48-c60_xalign', 'labeled_T1111_new.csv')
LABEL_AUDIT = os.path.join(BASE, '21_新数据标签审计', 'label_audit_7col.csv')

# ⭐ 0 号位 D-07（2026-10-29）：门限维持 ±8，10 把，另标 ±3 黄金样本。
#   原来的 8 把漏了 c28/c29——34-00 的"严格"隐含了一条额外条件「必须有 3.3GB 覆盖」，
#   但件数完全可以和**同一批次**比，不需要两批都在。
#   ⚠ 另修正：s53 早期版本曾同时用 3.3GB 件数和旧批 Aligned 两套源，
#   导致 c29 被当成"少计 −71"（实为覆盖起点截断），见 54-00 §5。
STRICT8 = [28, 29, 30, 31, 39, 40, 49, 50, 56, 59]   # 10 把（±8 容差，命名沿用以免改动下游）
GOLD3 = [30, 39]                                       # ±3 黄金样本（件数差 0 / 1）
LOOSE_DROP = [38, 51]
CUTS = [20, 40, 60, 80]
K, W = 20, 10
LOG = []


def P(*a):
    s = ' '.join(str(x) for x in a)
    print(s)
    LOG.append(s)


# ==================== §A  60 行 7 列 ====================
def build_7col():
    p01 = pd.read_csv(P01, encoding='utf-8-sig')
    la = pd.read_csv(LIFE_AUDIT, encoding='utf-8-sig')
    la['c'] = la.PhysicalLifeId.str.extract(r'c(\d+)')[0].astype('Int64')

    inv = {}
    for _, r in la.iterrows():
        if pd.isna(r['c']):
            continue
        seg = r.get('ProgramSwitchRows')
        inv[int(r['c'])] = ('%d段(PROXY)' % (int(seg) + 1)) if pd.notna(seg) else 'UNKNOWN'

    rows = []
    for _, r in p01.iterrows():
        c = int(r['Cycle'])
        led = r['LedgerLife']
        obs = r['ToolUse']
        ali = r['Aligned']
        bnd = r['Boundary']
        if pd.notna(r.get('d0')):
            bnd = '%s(d0=%+.0f,d1=%+.0f min)' % (r['Boundary'], r['d0'], r['d1'])
        # 分母归属
        if c in GOLD3:
            den = '±3黄金'
        elif c in STRICT8:
            den = '严格±8'
        elif r['Usable'].startswith('可正式验收'):
            den = '—'
        elif r['Usable'].startswith('可描述'):
            den = '可描述'
        else:
            den = '不入任何回测分母'
        if r['Usable'].startswith('可正式验收') and c not in STRICT8:
            den = '±8但原判定不一致'
        rows.append(dict(
            Cycle=c,
            形态=r['Form'],
            LedgerLife=led if pd.notna(led) else '—',
            ObservedToolPieces=obs if pd.notna(obs) else '—',
            AlignedPieces=ali if pd.notna(ali) else '—',
            InvocationCount=inv.get(c, 'UNKNOWN'),
            PhysicalEdgeBoundaryEvidence=bnd,
            BoundaryDelta_min=('%+.0f / %+.0f' % (r['d0'], r['d1'])) if pd.notna(r.get('d0')) else '—',
            UsableGrade=r['Usable'],
            DenominatorNote=den,
            # 件数比对用的是哪批数据（D-07：允许同批次比，不要求两批都在）
            件数比对来源=('3.3GB' if pd.notna(obs) and c not in (28, 29) else ('旧批次' if pd.notna(ali) else '—')),
            AlignedOverLedger=(round(float(ali) / float(led), 3) if (pd.notna(ali) and pd.notna(led) and float(led) > 0) else '—'),
            ObservedOverLedger=(round(float(obs) / float(led), 3) if (pd.notna(obs) and pd.notna(led) and float(led) > 0) else '—'),
        ))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, 'R04_lifecycle_7col.csv'), index=False, encoding='utf-8-sig')
    P('[A] 60 行 7 列 → R04_lifecycle_7col.csv')
    P('    分母分布：', df.DenominatorNote.value_counts().to_dict())
    return df


# ==================== §B  严格 8 / 宽松 26 ====================
def build_hi_dict():
    h = pd.read_csv(HI28, encoding='utf-8-sig')
    d = {}
    for c, g in h.groupby('life_cycle'):
        g = g.sort_values('piece_idx')
        d[int(c)] = dict(prog=int(g['prog'].iloc[0]),
                         hi=g['HI_L_spindle_load'].to_numpy(float))
    return d


def rise_of(hi, N):
    if N > len(hi):
        return np.nan
    seg = hi[max(0, N - W):N]
    seg = seg[np.isfinite(seg)]
    return float(np.median(seg) - 1.0) if len(seg) >= 3 else np.nan


def run_pool(tag, d, eval_set, pool_set):
    """训练池 = pool_set；评估集 = eval_set。两者可以不同（这是本文件的核心对照）。

    ⚠ pool_set 必须真正裁掉 LIFE/PROG，否则"两头都严格"会退化成"只在严格集上评分、
      仍在宽松池上训练"——那正是审核说的"外层测试集没有真正隔离"。
    """
    LIFE = {c: len(v['hi']) for c, v in d.items()}
    PROG = {c: v['prog'] for c, v in d.items()}
    LIFE_P = {c: LIFE[c] for c in pool_set if c in LIFE}
    PROG_P = {c: PROG[c] for c in pool_set if c in PROG}
    keep = sorted(set(pool_set) | set(eval_set))
    rows, miss = [], []
    for N in CUTS:
        RISE = {c: rise_of(d[c]['hi'], N) for c in keep if c in d}
        for k in sorted(eval_set):
            if k not in d:
                miss.append(dict(Tag=tag, CutoffN=N, Cycle=k, Reason='HI 表无此周期'))
                continue
            if LIFE[k] <= N:
                miss.append(dict(Tag=tag, CutoffN=N, Cycle=k,
                                 Reason='寿命 %d ≤ 截断 %d（该刀到不了这个截断点）' % (LIFE[k], N)))
                continue
            r = BT.nested_predict(LIFE_P, PROG_P, k, N, RISE)
            if r is None:
                n_same = sum(1 for c, p in PROG_P.items()
                             if p == PROG[k] and LIFE_P[c] > N and c != k)
                miss.append(dict(Tag=tag, CutoffN=N, Cycle=k,
                                 Reason='训练池(本口径)同程序且寿命>N 的刀只有 %d 把 < min_fit=2' % n_same))
                continue
            rows.append(dict(Tag=tag, CutoffN=N, Cycle=k, Program=PROG[k],
                             TrainIds=','.join('c%d' % o for o in r['S']), TrainN=len(r['S']),
                             Life=LIFE[k], ActualRUL=LIFE[k] - N,
                             M0A=r['M0A'], M0B=r['M0B'],
                             M1Old=r['M1Old'], M1New=r['M1New'],
                             PI_lo=r['PI_lo'], PI_hi=r['PI_hi']))
    R = pd.DataFrame(rows)
    if len(R):
        for m in ('M0A', 'M0B', 'M1Old', 'M1New'):
            R['Abs' + m] = (R[m] - R.ActualRUL).abs()
        R['InPI'] = ((R.PI_lo <= R.ActualRUL) & (R.ActualRUL <= R.PI_hi)).astype('Int64')
    return R, pd.DataFrame(miss)


def summ(R):
    out = []
    if not len(R):
        return pd.DataFrame(out)
    for N, d in R.groupby('CutoffN'):
        for m in ('M0A', 'M0B', 'M1Old', 'M1New'):
            r = d.dropna(subset=[m])
            if not len(r):
                continue
            e = r['Abs' + m]
            out.append(dict(Tag=R.Tag.iloc[0], CutoffN=N, Model=m, n=len(r),
                            MAE=round(e.mean(), 2), MedAE=round(e.median(), 2),
                            Bias=round(float((r[m] - r.ActualRUL).mean()), 2),
                            Over=int((r[m] > r.ActualRUL).sum()),
                            Cover=(round(float(r.InPI.mean()), 3) if m == 'M0B' else None)))
    return pd.DataFrame(out)


def build_split():
    d = build_hi_dict()
    all28 = sorted(d)
    loose26 = [c for c in all28 if c not in LOOSE_DROP]
    strict8 = [c for c in STRICT8 if c in d]

    jobs = [
        # tag, 评估集, 训练池
        ('L26_eval_L26', loose26, loose26),   # 宽松 26 自评（= 26-00/24-00 的口径）
        ('S8_eval_L26', strict8, loose26),    # **评估集严格、训练池宽松**（能算 7~8 把）
        ('S8_eval_S8', strict8, strict8),     # **两头都严格**（只剩 prog11 4 把）
    ]
    preds, misses, sums = [], [], []
    for tag, ev, pool in jobs:
        R, M = run_pool(tag, d, ev, pool)
        preds.append(R)
        misses.append(M)
        sums.append(summ(R))
        P('[B] %-14s 评估 %d 把 / 训练池 %d 把 → 预测行 %d'
          % (tag, len(ev), len(pool), len(R)))
        if len(sums[-1]):
            P(sums[-1].to_string(index=False))
    PR = pd.concat([p for p in preds if len(p)], ignore_index=True)
    MI = pd.concat([m for m in misses if len(m)], ignore_index=True)
    SU = pd.concat([s for s in sums if len(s)], ignore_index=True)
    PR.to_csv(os.path.join(OUT, 'R04_pred_split_detail.csv'), index=False, encoding='utf-8-sig')
    MI.to_csv(os.path.join(OUT, 'R04_pred_missing_reason.csv'), index=False, encoding='utf-8-sig')
    SU.to_csv(os.path.join(OUT, 'R04_pred_split.csv'), index=False, encoding='utf-8-sig')
    P('[B] 算不出的行 %d 条 → R04_pred_missing_reason.csv' % len(MI))
    return PR, MI, SU, d


# ==================== §C  边界复核 + 变点检验 ====================
def load_cycle_pieces(cycles):
    """只读需要的 5 列，按 cycle 过滤后返回 每件(cycle, allnum) 的首末时刻 + 切削行负载

    两个数据源都要读：旧 labeled 覆盖 c25~c47，新 labeled 覆盖 c48~c60。
    不合并成一份，因为两批的切片窗不同（这正是 R04 要复核的东西）。
    """
    use_o = ['_time', 'life_cycle', 'L_allnum', 'L_spindle_load', 'L_spindle_speed']
    fails = []
    for path in (OLD_LABELED, NEW_LABELED):
        frames = []
        for ch in pd.read_csv(path, usecols=use_o, chunksize=400000,
                              encoding='utf-8-sig', parse_dates=['_time']):
            ch = ch[ch['life_cycle'].isin(cycles)]
            if len(ch):
                frames.append(ch)
        if frames:
            d = pd.concat(frames, ignore_index=True)
            d['L_spindle_load'] = pd.to_numeric(d['L_spindle_load'], errors='coerce')
            d['L_spindle_speed'] = pd.to_numeric(d['L_spindle_speed'], errors='coerce')
            d['src'] = os.path.basename(path)
            fails.append(d)
    return pd.concat(fails, ignore_index=True) if fails else pd.DataFrame()


def boundary_recheck(cycles):
    """边界复核 + 覆盖截断排查

    ⚠ 方法学更正（2026-10-29 晚）：
      `life_audit_4152.csv` 的 StartTime/EndTime 本身**来自数据窗**，不是台账窗。
      用它跟数据窗比差恒为 0，等于自证。所以这里不再自己重算边界，
      一律引用 `P01_evidence_all60.csv` 里已经用台账比过的 `d0/d1`（口径见 s42）。
      本文新增的只有一件事：**判断"件数不一致"里有没有我们自己覆盖起点造成的假阳性**。
    """
    p01 = pd.read_csv(P01, encoding='utf-8-sig').set_index('Cycle')
    aud = pd.read_csv(LABEL_AUDIT, encoding='utf-8-sig').set_index('Cycle')
    d = load_cycle_pieces(cycles)

    aud['DS'] = pd.to_datetime(aud['DataStart'], errors='coerce')
    cov_start = aud['DS'].min()
    rows = []
    for c in sorted(cycles):
        if c not in p01.index:
            continue
        r = p01.loc[c]
        g = d[d.life_cycle == c]
        n_old = int(g['L_allnum'].nunique()) if len(g) else 0
        a = aud.loc[c] if c in aud.index else None
        ds = pd.to_datetime(a['DataStart'], errors='coerce') if a is not None else pd.NaT
        left_trunc = bool(pd.notna(ds) and abs((ds - cov_start).total_seconds()) < 60)
        led = float(r['LedgerLife']) if pd.notna(r['LedgerLife']) else np.nan
        obs33 = float(r['ToolUse']) if pd.notna(r['ToolUse']) else np.nan
        diff33 = abs(obs33 - led) if pd.notna(obs33) else np.nan
        diff_old = abs(n_old - led) if n_old else np.nan
        if left_trunc and np.isfinite(diff_old) and diff_old <= 8:
            verdict = '覆盖截断假阳性：旧批次件数 %d vs 台账 %.0f，差 %.0f → 应恢复为可验收' % (n_old, led, diff_old)
        elif np.isfinite(diff33) and diff33 <= 8 and r['Boundary'] == 'EXACT':
            verdict = '一致，可验收'
        else:
            verdict = '件数不一致，未闭合（等车间）'
        rows.append(dict(
            Cycle=c, 形态=r['Form'], 台账刀寿=led,
            Boundary=r['Boundary'], d0_min=round(float(r['d0']), 1), d1_min=round(float(r['d1']), 1),
            ToolUse_3p3GB=obs33, Aligned_旧批次=float(n_old),
            AlignedPieces_官方表=(float(r['Aligned']) if pd.notna(r['Aligned']) else np.nan),
            件数比_3p3GB=round(obs33 / led, 3) if np.isfinite(obs33) else np.nan,
            件数比_旧批次=round(n_old / led, 3),
            Hours_NonCutting=(float(r['Hours_NonCutting']) if pd.notna(r['Hours_NonCutting']) else np.nan),
            数据起点_3p3GB=ds, 覆盖起点=cov_start, 左截断=left_trunc,
            判定建议=verdict))
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(OUT, 'R04_boundary_recheck.csv'), index=False, encoding='utf-8-sig')
    P('[C] 边界复核 → R04_boundary_recheck.csv')
    P(out.drop(columns=['覆盖起点']).to_string(index=False))
    return d


def step_test(d, cycles, ledger):
    """件级负载变点：每件切削行负载中位 → 扫描 drop(m) 曲线

    多计假说的可检验推论：若 c38/c51 期间真的换过一次刀而台账没记，
    则 drop 曲线应在 m ≈ 台账刀寿 处出现局部极大（新刀基线重置）；
    单刃口对照周期（c30/c39/c40/c50/c56）在该位置不该有同等分位。
    统计量用「该位置 drop 在整条曲线里的分位」而不是绝对幅度，抵消周期之间的量级差。
    """
    rows, curve = [], []
    w = 10
    for c in cycles:
        g = d[d.life_cycle == c]
        g = g[(g['L_spindle_speed'] > 0) & (g['L_spindle_load'] > 0)]
        if not len(g):
            rows.append(dict(Cycle=c, 件数=0, 备注='无切削行'))
            continue
        seq = g.groupby('L_allnum')['L_spindle_load'].median().sort_index()
        v = seq.to_numpy(float)
        L = len(v)
        if L < 30:
            rows.append(dict(Cycle=c, 件数=L, 备注='件数 <30，无法扫描'))
            continue
        base = float(np.median(v))
        ms = list(range(w, L - w))
        drops = np.array([np.median(v[m - w:m]) - np.median(v[m:m + w]) for m in ms])
        for m, dp in zip(ms, drops):
            curve.append(dict(Cycle=c, m=m, drop=round(float(dp), 2)))
        imax = int(np.argmax(drops))
        led = ledger.get(c)
        m_led = int(round(led)) if led else None
        if m_led is not None and w <= m_led < L - w:
            i_led = ms.index(m_led)
            d_led = drops[i_led]
            pct = float((drops <= d_led).mean())
        else:
            d_led, pct = np.nan, np.nan
        # 统一相对位置 55%：因为「台账刀寿」在单刃口周期上≈件数，窗口放不下，
        # 只能用固定相对位置做跨周期可比的零分布。
        m55 = int(round(0.55 * L))
        i55 = ms.index(m55) if m55 in ms else None
        d55 = float(drops[i55]) if i55 is not None else np.nan
        p55 = float((drops <= d55).mean()) if i55 is not None else np.nan
        # 功效：把换刀实测台阶（−2.84%，见 15-00 load_before_switch）注入 55% 处，
        # 看扫描曲线能否把它认成最大下台阶。认不出 = 这个检验没有功效，不能用来否定多计。
        step = -0.0284 * base
        v2 = v.copy()
        if i55 is not None:
            v2[m55:] += step
            d2 = np.array([np.median(v2[m - w:m]) - np.median(v2[m:m + w]) for m in ms])
            recovered = bool(np.argmax(d2) == i55)
            gap = float(np.max(d2) - d2[i55])
        else:
            recovered, gap = False, np.nan
        rows.append(dict(Cycle=c, 件数=L, 基线中位=round(base, 1),
                         最大下台阶=round(float(drops[imax]), 1),
                         最大下台阶相对幅度=round(float(drops[imax]) / base, 4),
                         台阶位置件号=ms[imax], 台阶位置占件数比=round(ms[imax] / L, 3),
                         台账刀寿=led,
                         台账位置下台阶=round(float(d_led), 1) if np.isfinite(d_led) else np.nan,
                         台账位置下台阶相对幅度=round(float(d_led) / base, 4) if np.isfinite(d_led) else np.nan,
                         台账位置下台阶分位=round(float(pct), 3) if np.isfinite(pct) else np.nan,
                         p55下台阶相对幅度=round(d55 / base, 4) if np.isfinite(d55) else np.nan,
                         p55分位=round(p55, 3) if np.isfinite(p55) else np.nan,
                         注入2_84pct台阶能否认出=recovered,
                         注入后与最大台阶差距=round(gap, 1) if np.isfinite(gap) else np.nan,
                         备注=''))
    out = pd.DataFrame(rows).sort_values('Cycle')
    out.to_csv(os.path.join(OUT, 'R04_step_test.csv'), index=False, encoding='utf-8-sig')
    pd.DataFrame(curve).to_csv(os.path.join(OUT, 'R04_step_curve.csv'), index=False, encoding='utf-8-sig')
    P('[C] 件级变点检验 → R04_step_test.csv / R04_step_curve.csv')
    P(out.to_string(index=False))
    return out


if __name__ == '__main__':
    P('=' * 70)
    P('R04 严格 8 / 宽松 26 分列输出')
    P('=' * 70)
    df7 = build_7col()
    PR, MI, SU, d = build_split()
    chk = [29, 35, 38, 51]
    ctrl = [30, 39, 40, 50, 56, 49]
    dd = boundary_recheck(chk)
    ledger = {}
    p01 = pd.read_csv(P01, encoding='utf-8-sig')
    for _, r in p01.iterrows():
        if pd.notna(r['LedgerLife']):
            ledger[int(r['Cycle'])] = float(r['LedgerLife'])
    dall = load_cycle_pieces(sorted(set(chk + ctrl)))
    step_test(dall, sorted(set(chk + ctrl)), ledger)
    with open(os.path.join(OUT, 's53_console.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG))
    P('DONE →', OUT)
