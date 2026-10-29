"""
s42_p01_evidence.py —— P0-01 收尾：逐周期 7 列全表 + 逐把证据链

【本轮要解决的三件事】
1. **修"假 NO_DATA"**：`21-00` 把 5 个周期标成 `NO_DATA`（c16/c25/c26/c27/c28），
   实际它们有数据、只是**超出 3.3 GB 全刀数据的起点（2026-09-17）**。
   旧 labeled 里 c25 有 7,907 行、c26 有 11,835 行……把它们当"没数据"会误删样本。
2. **给 5 把重点刀写证据链**：c26 / c35 / c37 / c38 / c41 —— 这 5 把的"数据件数 vs 台账刀寿"
   差 25~95 件，是审核点名要逐把说明的。
3. **给出可用性判定**：每把明确"能用于正式验收 / 只能描述 / 不可用"，及理由。

【输入】
  21_新数据标签审计/label_audit_7col.csv    （3.3 GB 全刀口径的 7 列）
  23_两批数据等价性/L2_by_window.csv         （数据实际起止 vs 台账窗，分钟差）
  30_缺口四分类/gap_class_per_cycle.csv      （四分类缺口）
  25_品番混批诊断/prog_mix_profile.csv       （品番构成）
  out_T1111/xalign_HI.csv                    （旧 17 周期对齐件数）
  22_c48-c60_xalign/xalign_HI_new_c49c60.csv （新 11 周期对齐件数）
  三厂换刀记录.xlsx                           （台账真值）
  旧 labeled                                  （判 OLD_ONLY / BOTH）

【输出】
  34_P0-01收尾/P01_evidence_all60.csv     全 60 周期宽表
  34_P0-01收尾/P01_evidence_5cases.md     5 把重点刀的证据链
  34_P0-01收尾/s42_console.txt
"""
import os
import re
import sys
import datetime as dt

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
ROOT = os.path.abspath(os.path.join(BASE, '..', '..', '..'))
OUT = os.path.join(BASE, '34_P0-01收尾')

AUDIT = os.path.join(BASE, '21_新数据标签审计', 'label_audit_7col.csv')
L2 = os.path.join(BASE, '23_两批数据等价性', 'L2_by_window.csv')
GAPC = os.path.join(BASE, '30_缺口四分类', 'gap_class_per_cycle.csv')
MIX = os.path.join(BASE, '25_品番混批诊断', 'prog_mix_profile.csv')
HI_OLD = os.path.join(ROOT, 'out_T1111', 'xalign_HI.csv')
HI_NEW = os.path.join(BASE, '22_c48-c60_xalign', 'xalign_HI_new_c49c60.csv')
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../20261027
# 台账（P1-07：0 号位已授权随包公开）→ 包内副本优先，仓库根仅作回退
LEDGER = os.path.join(BASE, '15_台账真值检验', 'input', '三厂换刀记录.xlsx')
if not os.path.exists(LEDGER):
    LEDGER = os.path.join(ROOT, '三厂换刀记录.xlsx')
RAW_OLD = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv')

NEW_DATA_START = '2026-09-17 00:00:00'   # 3.3 GB 全刀数据的起点
TOL_PIECES = 8                            # |差| ≤ 8 件视为一致（21-00 实测 |差| 中位 = 8）
FOCUS = [26, 35, 37, 38, 41]              # 审核点名要逐把说明的 5 把

FIX = {'2026/9/416：05': '2026-09-04 16:05',
       '2026/9/18/16:26': '2026-09-18 16:26',
       '2026/10/26/9：20': '2026-10-26 09:20'}

LOG = []


def P(s=''):
    LOG.append(str(s))
    print(s)


def parse_t(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return pd.NaT
    if isinstance(v, (pd.Timestamp, dt.datetime)):
        return pd.Timestamp(v)
    t = str(v).strip().replace('：', ':')
    if t in FIX:
        return pd.Timestamp(FIX[t])
    t = re.sub(r'^(\d{4})/(\d{1,2})/(\d{1,2})[ /](\d{1,2}):(\d{2})$', r'\1-\2-\3 \4:\5', t)
    return pd.to_datetime(t, errors='coerce')


def main():
    os.makedirs(OUT, exist_ok=True)
    P('=' * 96)
    P('P0-01 收尾　逐周期 7 列全表 + 逐把证据链')
    P('=' * 96)
    P('')

    # ---------------- 台账
    L = pd.read_excel(LEDGER, sheet_name=0).rename(columns={'顺序号': 'Cycle'})
    L['S'] = L['StartTime'].map(parse_t)
    L['E'] = L['EndTime'].map(parse_t)
    L['Form'] = L['换刀原因'].map(
        lambda s: '破损' if '破损' in str(s) else ('磨损' if '磨损' in str(s) else '未标'))
    L['LedgerLife'] = L['换刀原因'].map(
        lambda s: int(re.findall(r'(\d+)', str(s))[-1]) if re.findall(r'(\d+)', str(s)) else np.nan)
    P('台账：%d 行（1..%d）' % (len(L), int(L.Cycle.max())))

    # ---------------- 旧 labeled 的周期与行数（判 OLD_ONLY）
    d = pd.read_csv(RAW_OLD, usecols=['_time', 'life_cycle'], encoding='utf-8-sig')
    d['t'] = pd.to_datetime(d['_time'], errors='coerce', format='mixed')
    old = d.groupby('life_cycle').agg(OldRows=('t', 'size'),
                                      OldT0=('t', 'min'), OldT1=('t', 'max'))
    old.index.name = 'Cycle'
    P('旧 labeled：%d 行 / %d 周期（%s ~ %s）'
      % (len(d), old.shape[0], d.t.min(), d.t.max()))

    # ---------------- 3.3 GB 审计表
    A = pd.read_csv(AUDIT, encoding='utf-8-sig')
    P('审计表（3.3GB）：%d 行' % len(A))

    # ---------------- 对齐件数（两批 HI）
    hi_rows = []
    for path, src in ((HI_OLD, 'old'), (HI_NEW, 'new')):
        if os.path.exists(path):
            h = pd.read_csv(path, usecols=['life_cycle'], encoding='utf-8-sig')
            for c, n in h.groupby('life_cycle').size().items():
                hi_rows.append(dict(Cycle=int(c), HI_Src=src, AlignedPieces=int(n)))
    HI = pd.DataFrame(hi_rows)
    P('对齐件数：旧 %d 周期 / 新 %d 周期'
      % ((HI.HI_Src == 'old').sum() if len(HI) else 0,
         (HI.HI_Src == 'new').sum() if len(HI) else 0))

    # ---------------- 边界（L2）
    B = pd.DataFrame()
    if os.path.exists(L2):
        b = pd.read_csv(L2, encoding='utf-8-sig')
        b = b[['Cycle', 'OldT0', 'OldT1', 'WinStart', 'WinEnd']].copy()
        b['d0'] = (pd.to_datetime(b.OldT0) - pd.to_datetime(b.WinStart)).dt.total_seconds() / 60
        b['d1'] = (pd.to_datetime(b.OldT1) - pd.to_datetime(b.WinEnd)).dt.total_seconds() / 60
        B = b[['Cycle', 'd0', 'd1']]
        P('边界实测：%d 周期' % len(B))

    # ---------------- 缺口四分类
    G = pd.DataFrame()
    if os.path.exists(GAPC):
        g = pd.read_csv(GAPC, encoding='utf-8-sig')
        G = g[['Cycle', 'Hours_NonCutting', 'Hours_Unknown', 'Hours_ConfirmedLoss',
               'MaxGapHours', 'EvidenceLevel']]
        P('缺口四分类：%d 周期' % len(G))

    # ---------------- 品番构成
    M = pd.DataFrame()
    if os.path.exists(MIX):
        m = pd.read_csv(MIX, encoding='utf-8-sig')
        M = m[['Cycle', 'MainProg', 'MainShare', 'Band', 'MinorPieces']]
        P('品番构成：%d 周期' % len(M))

    # ---------------- 合并
    W = L[['Cycle', 'Form', 'LedgerLife', 'S', 'E']].copy()
    W = W.merge(A.drop(columns=[c for c in ('Form', 'LedgerLife') if c in A.columns]),
                on='Cycle', how='left')
    W = W.merge(old, on='Cycle', how='left')
    W = W.merge(HI, on='Cycle', how='left')
    if len(B):
        W = W.merge(B, on='Cycle', how='left')
    if len(G):
        W = W.merge(G, on='Cycle', how='left')
    if len(M):
        W = W.merge(M, on='Cycle', how='left')

    # ---------------- 数据覆盖判定（修"假 NO_DATA"）
    has_old = W.OldRows.fillna(0) > 0
    has_new = W.MachinePieceCount.notna()
    W['Coverage'] = np.select(
        [has_old & has_new, has_old & ~has_new, ~has_old & has_new],
        ['BOTH', 'OLD_ONLY', 'NEW_ONLY'], default='NONE')
    P('')
    P('=== 数据覆盖分布（这是修正"假 NO_DATA"的依据）===')
    P(W.Coverage.value_counts().to_string())
    fake_no = W[(W.Coverage == 'OLD_ONLY')]
    P('')
    P('⚠ 被 21-00 误标为 NO_DATA、实际有旧数据的周期：%d 个 → %s'
      % (len(fake_no), sorted(fake_no.Cycle.tolist())))
    for _, r in fake_no.iterrows():
        P('   c%-4d 旧数据 %6d 行（%s → %s），台账刀寿 %s'
          % (r.Cycle, r.OldRows, str(r.OldT0)[:16], str(r.OldT1)[:16],
             ('%d' % r.LedgerLife) if pd.notna(r.LedgerLife) else '—'))

    # ---------------- 三个件数口径 + 差
    W['ToolUse'] = W.ToolUseCount
    W['Aligned'] = W.AlignedPieces
    W['Diff_ToolUse'] = W.ToolUse - W.LedgerLife
    W['Diff_Aligned'] = W.Aligned - W.LedgerLife

    # ---------------- 边界补算：NEW_ONLY 周期（c48-c60）不在 L2 里，用 21-00 的 DataStart/DataEnd
    #   L2_by_window.csv 只覆盖"旧数据存在的周期"（15 个 BOTH），新周期需要另一条路径，
    #   否则会被误判成"边界未验"（第一版就是这么产生 9 个 UNVERIFIED 的）。
    if 'DataStart' in W.columns and 'DataEnd' in W.columns:
        miss = W.d0.isna() if 'd0' in W.columns else pd.Series(True, index=W.index)
        if 'd0' not in W.columns:
            W['d0'] = np.nan
            W['d1'] = np.nan
            miss = pd.Series(True, index=W.index)
        n_fill = 0
        for i in W.index[miss]:
            ds, de = W.at[i, 'DataStart'], W.at[i, 'DataEnd']
            S_, E_ = W.at[i, 'S'], W.at[i, 'E']
            if pd.isna(S_) or not isinstance(ds, str) or not ds:
                continue
            try:
                t0 = pd.Timestamp(ds)
            except Exception:
                continue
            W.at[i, 'd0'] = (t0 - S_).total_seconds() / 60
            if pd.notna(E_) and isinstance(de, str) and de:
                try:
                    W.at[i, 'd1'] = (pd.Timestamp(de) - E_).total_seconds() / 60
                except Exception:
                    pass
            n_fill += 1
        P('边界补算（用 21-00 的 DataStart/DataEnd）：%d 个周期' % n_fill)

    # ---------------- 边界分级
    def boundary(r):
        if r.Coverage == 'NONE':
            return 'NO_DATA_AT_ALL'
        if not pd.notna(r.get('d0')):
            return 'UNVERIFIED'
        d0, d1 = r.d0, r.d1
        late = d0 > 60
        early = pd.notna(d1) and d1 < -60
        if late and early:
            return 'BOTH_TRUNCATED'
        if late:
            return 'LATE_START'
        if early:
            return 'EARLY_STOP'
        return 'EXACT'
    W['Boundary'] = W.apply(boundary, axis=1)

    # ---------------- 差因归因（规则化，每把可追溯）
    def why(r):
        if r.Coverage == 'NONE':
            return '无数据，不可比'
        dv = r.Diff_ToolUse if pd.notna(r.Diff_ToolUse) else np.nan
        if pd.isna(dv):
            return '缺 T1111 参与件数，不可比'
        if abs(dv) <= TOL_PIECES:
            return '一致（|差|≤%d）' % TOL_PIECES
        if dv < -TOL_PIECES:
            if r.Boundary in ('LATE_START', 'BOTH_TRUNCATED'):
                return '少计：数据起点晚于台账，前段未采到（LATE_START）'
            if r.Boundary == 'EARLY_STOP':
                return '少计：数据早于台账结束即停采（EARLY_STOP）'
            return '少计：边界实测正常，原因未明'
        # dv > +TOL_PIECES
        if r.get('Band') in ('明显',):
            return '多计：跨品番（主品番仅 %.0f%%），HI 只覆盖其中一段' % (100 * r.MainShare)
        return '多计：边界正常、品番单一，原因未明（需车间确认期间是否换过刀）'
    W['DiffReason'] = W.apply(why, axis=1)

    # ---------------- 可用性判定
    def usable(r):
        if r.Coverage == 'NONE':
            return '不可用（无数据）'
        bad = []
        if r.Boundary in ('LATE_START', 'EARLY_STOP', 'BOTH_TRUNCATED'):
            bad.append('边界不完整')
        if r.Boundary == 'UNVERIFIED':
            bad.append('边界未验')
        # ⚠ 第一版只判 `Diff_ToolUse > TOL`（多计），漏了"少计"——
        #   结果 c29（台账 103 / 实际 32 件）被判成"可正式验收"。
        #   无论多计还是少计，只要 |差| > TOL 且边界无解释，就不能进正式验收。
        if pd.notna(r.get('Diff_ToolUse')) and abs(r.Diff_ToolUse) > TOL_PIECES:
            bad.append('件数不一致(%+.0f)' % r.Diff_ToolUse)
        if r.get('Band') == '明显':
            bad.append('跨品番混算')
        if r.Coverage == 'OLD_ONLY':
            bad.append('仅旧数据（3.3GB 不覆盖）')
        if not bad:
            return '可正式验收'
        if len(bad) == 1 and bad[0].startswith('仅旧数据'):
            return '可描述，不参与 3.3GB 口径统计'
        return '不可用（%s）' % '；'.join(bad)
    W['Usable'] = W.apply(usable, axis=1)

    # ---------------- 排序输出
    cols = ['Cycle', 'Form', 'LedgerLife', 'Coverage', 'OldRows',
            'MachinePieceCount', 'ToolUse', 'Aligned', 'Diff_ToolUse', 'Diff_Aligned',
            'Boundary', 'd0', 'd1',
            'Hours_NonCutting', 'Hours_Unknown', 'Hours_ConfirmedLoss', 'MaxGapHours',
            'MainProg', 'MainShare', 'Band',
            'DiffReason', 'Usable', 'HI_Src']
    cols = [c for c in cols if c in W.columns]
    W = W.sort_values('Cycle')
    W[cols].to_csv(os.path.join(OUT, 'P01_evidence_all60.csv'),
                   index=False, encoding='utf-8-sig')

    P('')
    P('=== 可用性汇总 ===')
    P(W.Usable.value_counts().to_string())
    P('')

    # ---------------- 5 把重点刀
    P('=' * 96)
    P('5 把重点刀的证据链（审核点名：件数与台账差 25~95）')
    P('=' * 96)
    md = ['# P0-01 收尾：5 把重点刀证据链', '',
          '> 生成脚本：`09_repro_scripts/s42_p01_evidence.py`　全 60 周期宽表：`P01_evidence_all60.csv`',
          '> 日期：2026-10-29', '',
          '## 读法', '',
          '每把给四段：**台账真值 → 数据侧三个件数口径 → 差因归因 → 可用性判定**。', '',
          '三个件数口径（G0-02 要求分开）：', '',
          '| 口径 | 含义 |', '|---|---|',
          '| `MachinePieceCount` | 机床加工件数（allnum 跨度，多件产品） |',
          '| `ToolUse` | 刀具参与件数（有 T1111 行的 allnum 数） |',
          '| `Aligned` | 对齐成功件数（xalign 出 HI 的件） |', '']

    for c in FOCUS:
        r = W[W.Cycle == c]
        if not len(r):
            continue
        r = r.iloc[0]
        P('')
        P('--- c%d ---' % c)
        P('  台账：%s，刀寿 %s，窗 %s → %s'
          % (r.Form, ('%d' % r.LedgerLife) if pd.notna(r.LedgerLife) else '—',
             str(r.S)[:16], str(r.E)[:16] if pd.notna(r.E) else '（未闭合）'))
        P('  覆盖：%s（旧 %s 行 / 新 %s 行）'
          % (r.Coverage, int(r.OldRows) if pd.notna(r.OldRows) else 0,
             int(r.MachinePieceCount) if pd.notna(r.MachinePieceCount) else '—'))
        P('  件数：机床 %s / 刀具 %s / 对齐 %s  → 差(ToolUse−台账) %s'
          % (('%.0f' % r.MachinePieceCount) if pd.notna(r.MachinePieceCount) else '—',
             ('%.0f' % r.ToolUse) if pd.notna(r.ToolUse) else '—',
             ('%.0f' % r.Aligned) if pd.notna(r.Aligned) else '—',
             ('%+.0f' % r.Diff_ToolUse) if pd.notna(r.Diff_ToolUse) else '—'))
        P('  边界：%s（d0=%s, d1=%s 分钟）'
          % (r.Boundary,
             ('%+.0f' % r.d0) if pd.notna(r.get('d0')) else '—',
             ('%+.0f' % r.d1) if pd.notna(r.get('d1')) else '—'))
        if pd.notna(r.get('Hours_NonCutting')):
            P('  缺口：非切削 %.1f h / 无法判定 %.1f h / 确认丢失 %.1f h'
              % (r.Hours_NonCutting, r.Hours_Unknown, r.Hours_ConfirmedLoss))
        P('  品番：%s（主占 %.0f%%，%s）'
          % (('%s' % int(r.MainProg)) if pd.notna(r.get('MainProg')) else '—',
             (100 * r.MainShare) if pd.notna(r.get('MainShare')) else np.nan,
             r.get('Band', '—')))
        P('  差因：%s' % r.DiffReason)
        P('  判定：%s' % r.Usable)

        md += ['', '## c%d　%s，台账刀寿 %s' % (
                   c, r.Form, ('%d' % r.LedgerLife) if pd.notna(r.LedgerLife) else '—'), '',
               '| 项 | 值 |', '|---|---|',
               '| 台账时间窗 | %s → %s |' % (str(r.S)[:16],
                                            str(r.E)[:16] if pd.notna(r.E) else '未闭合'),
               '| 数据覆盖 | `%s`（旧 %s 行 / 新 %s 行）|' % (
                   r.Coverage, int(r.OldRows) if pd.notna(r.OldRows) else 0,
                   int(r.MachinePieceCount) if pd.notna(r.MachinePieceCount) else '—'),
               '| 机床件数 `MachinePieceCount` | %s |' % (
                   ('%.0f' % r.MachinePieceCount) if pd.notna(r.MachinePieceCount) else '—'),
               '| 刀具参与件数 `ToolUse` | %s |' % (
                   ('%.0f' % r.ToolUse) if pd.notna(r.ToolUse) else '—'),
               '| 对齐件数 `Aligned` | %s |' % (
                   ('%.0f' % r.Aligned) if pd.notna(r.Aligned) else '—'),
               '| **差（ToolUse − 台账）** | **%s** |' % (
                   ('%+.0f' % r.Diff_ToolUse) if pd.notna(r.Diff_ToolUse) else '—'),
               '| 边界证据 | `%s`（d0=%s, d1=%s 分钟）|' % (
                   r.Boundary,
                   ('%+.0f' % r.d0) if pd.notna(r.get('d0')) else '—',
                   ('%+.0f' % r.d1) if pd.notna(r.get('d1')) else '—'),
               '| 缺口四分类 | 非切削 %s h / 无法判定 %s h / 确认丢失 %s h |' % (
                   ('%.1f' % r.Hours_NonCutting) if pd.notna(r.get('Hours_NonCutting')) else '—',
                   ('%.1f' % r.Hours_Unknown) if pd.notna(r.get('Hours_Unknown')) else '—',
                   ('%.1f' % r.Hours_ConfirmedLoss) if pd.notna(r.get('Hours_ConfirmedLoss')) else '—'),
               '| 品番构成 | %s（主占 %s，%s）|' % (
                   ('%s' % int(r.MainProg)) if pd.notna(r.get('MainProg')) else '—',
                   ('%.0f%%' % (100 * r.MainShare)) if pd.notna(r.get('MainShare')) else '—',
                   r.get('Band', '—')),
               '', '**差因归因**：%s' % r.DiffReason,
               '', '**可用性**：`%s`' % r.Usable]

    with open(os.path.join(OUT, 'P01_evidence_5cases.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(md) + '\n')

    P('')
    P('[out] P01_evidence_all60.csv（%d 行）/ P01_evidence_5cases.md' % len(W))
    with open(os.path.join(OUT, 's42_console.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG) + '\n')


if __name__ == '__main__':
    main()
