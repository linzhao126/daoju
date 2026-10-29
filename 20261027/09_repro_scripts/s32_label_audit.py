# -*- coding: utf-8 -*-
"""s32_label_audit.py —— P0-01 标签与真值审计（用未过滤的全刀原始数据）

为什么必须用新文件
------------------
旧文件 `influxdb_4152_..._L_T1111_T1111_life_labeled.csv` 已经**按刀号过滤 + 打过 life_cycle**，
因此无法区分两件事：
    机床加工了多少件（产品件数）  vs  目标刀具实际参与了多少件
而这两者在全刀数据里是不同的量：一个 allnum 会被 8 把刀依次加工。

本脚本读未过滤的 `influxdb_4152_FanucNormalData_20261027_200034.csv`（8.2M 行 / 3.3GB），
按台账 60 条的时间窗逐周期统计审核方 P0-01 要求的 7 列。

口径声明（避免再次混淆）
------------------------
- `L_allnum` = **机床累计产品件号**（12 把刀共享同一序列），不是某把刀的件计数器。
- `MachinePieceCount`   = 窗内 allnum 跨度（max-min+1），含缺号
- `RawDistinctAllNum`   = 窗内 allnum 去重个数
- `ToolUseCount`        = 窗内**有 T1111 行**的 allnum 个数（刀具实际参与件数）
- `AlignedValidPieceCount` = 现有 xalign_HI.csv 的成功对齐件数
- `LedgerLife`          = 台账换刀原因里的"刀寿N"
- `GapCount`            = 窗内相邻记录间隔 > 1 小时的次数（**不是"采集中断"**，见 P0-04）
- `StartEndConfidence`  = 台账 EndTime 与窗内数据末行的偏差分级

⚠ 本脚本**不**把 gap 汇总成"累计中断小时"（P0-04 已否决该口径）。
   四分类留待 P0-04 单独实现：InterRecordGap / NonCuttingTime /
   AcquisitionUnknownInterval / ConfirmedAcquisitionLoss。

输出：21_新数据标签审计/label_audit_7col.csv + s32_console.log
"""
import os
import re
import csv
import sys
import datetime as dt
from bisect import bisect_right
from collections import defaultdict

import numpy as np

csv.field_size_limit(10 ** 7)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))
OUT = os.path.abspath(os.path.join(HERE, '..', '21_新数据标签审计'))
os.makedirs(OUT, exist_ok=True)

RAW = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261027_200034.csv')
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../20261027
# 台账（P1-07：0 号位已授权随包公开）→ 包内副本优先，仓库根仅作回退
XLS = os.path.join(BASE, '15_台账真值检验', 'input', '三厂换刀记录.xlsx')
if not os.path.exists(XLS):
    XLS = os.path.join(ROOT, '三厂换刀记录.xlsx')
HI = os.path.join(ROOT, 'out_T1111', 'xalign_HI.csv')

# 列索引（0-based，见表头）
I_T, I_AN, I_TN, I_MP = 0, 16, 31, 22

GAP_S = 60.0            # 仅用于"相邻记录间隔"的描述统计
GAP_LONG_S = 3600.0     # > 1h 计为长间隔（不是"采集丢失"）

TEXT_TIME_FIX = {
    '2026/9/416：05': '2026-09-04 16:05',
    '2026/9/18/16:26': '2026-09-18 16:26',
    '2026/10/26/9：20': '2026-10-26 09:20',
}

log = []
P = log.append


def parse_t(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, (dt.datetime,)):
        return v.replace(microsecond=0)
    s = str(v).strip()
    if s in ('', 'NaT', 'nan'):
        return None
    if s in TEXT_TIME_FIX:
        return dt.datetime.strptime(TEXT_TIME_FIX[s], '%Y-%m-%d %H:%M')
    s = s.replace('：', ':')
    s = re.sub(r'^(\d{4})/(\d{1,2})/(\d{1,2})[ /](\d{1,2}):(\d{2})', r'\1-\2-\3 \4:\5', s)
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return dt.datetime.strptime(s[:19], fmt)
        except Exception:
            continue
    return None


def load_ledger():
    import pandas as pd
    L = pd.read_excel(XLS, sheet_name=0)
    rows = []
    for _, r in L.iterrows():
        S = parse_t(r['StartTime'])
        E = parse_t(r['EndTime'])
        reason = str(r['换刀原因'])
        m = re.findall(r'(\d+)', reason)
        rows.append(dict(cycle=int(r['顺序号']), S=S, E=E,
                         form=('破损' if '破损' in reason else ('磨损' if '磨损' in reason else '未标')),
                         ledger_life=(int(m[-1]) if m else None),
                         reason=reason))
    return [x for x in rows if x['S'] is not None]


def load_aligned():
    import pandas as pd
    hi = pd.read_csv(HI, encoding='utf-8-sig')
    return hi.groupby('life_cycle').size().to_dict()


def main():
    ledger = load_ledger()
    aligned = load_aligned()

    P('台账：%d 条（S 可解析）' % len(ledger))
    P('xalign 对齐件数：%d 个周期' % len(aligned))

    # ---------- 时间窗：用 'YYYY-MM-DD HH:MM:SS' 字符串，字典序等于时间序
    #   8.2M 行若逐行 strptime 会超时（实测 >120s 被 SIGTERM）。
    wins = [(r['cycle'], r['S'].strftime('%Y-%m-%d %H:%M:%S'),
             r['E'].strftime('%Y-%m-%d %H:%M:%S') if r['E'] else None) for r in ledger]
    starts = [w[1] for w in wins]
    ends = [w[2] for w in wins]
    NW = len(wins)

    # ---------- 单遍扫描
    # 每周期状态
    # (周期, 主程序) 分组：同一时间窗内可能换品番，件数不能直接相加
    prog_st = defaultdict(lambda: dict(rows=0, t1111_rows=0,
                                       an=set(), an1111=set(),
                                       prog_txt='', t0=None, t1=None))
    st = defaultdict(lambda: dict(rows=0, t1111_rows=0,
                                  an=set(), an_t1111=set(),
                                  tools=set(),
                                  t0=None, t1=None,
                                  gap_n=0, gap_long=0, gap_max=0.0,
                                  prev_min=None, prev_sec=None, sec=None))
    n_rows = 0
    n_matched = 0
    t_min = t_max = None
    bad_an = 0
    ci = 0          # 当前周期指针
    n_parse = 0     # 实际解析时间的次数（性能自检）

    with open(RAW, encoding='utf-8-sig', newline='') as f:
        rd = csv.reader(f)
        head = next(rd)
        for row in rd:
            n_rows += 1
            if len(row) <= I_TN:
                continue
            key = row[I_T][:19]
            if len(key) < 19:
                continue
            if t_min is None or key < t_min:
                t_min = key
            if t_max is None or key > t_max:
                t_max = key

            # 指针推进（数据基本按时间有序）；乱序时用二分兜底
            while ci < NW - 1 and ends[ci] is not None and key >= ends[ci]:
                ci += 1
            if key < starts[ci]:
                j = bisect_right(starts, key) - 1
                if j < 0:
                    continue
                ci = j
            if ends[ci] is not None and key >= ends[ci]:
                continue          # 落在周期之间的空档
            n_matched += 1
            cyc = wins[ci][0]
            s = st[cyc]
            s['rows'] += 1
            tn = row[I_TN]
            if tn != 'T1111':
                tn = tn.strip()
            s['tools'].add(tn)
            a_txt = row[I_AN]
            try:
                an = int(a_txt)
            except Exception:
                try:
                    an = int(float(a_txt))
                except Exception:
                    bad_an += 1
                    an = None
            is1111 = (tn == 'T1111')
            if an is not None:
                s['an'].add(an)
                if is1111:
                    s['an_t1111'].add(an)
            if is1111:
                s['t1111_rows'] += 1
            # ---- 按主程序分组
            ptxt = row[I_MP] if len(row) > I_MP else ''
            prog = ptxt.split('(')[0].strip()
            ps = prog_st[(cyc, prog)]
            ps['rows'] += 1
            if not ps['prog_txt']:
                ps['prog_txt'] = ptxt
            if an is not None:
                ps['an'].add(an)
                if is1111:
                    ps['an1111'].add(an)
            if is1111:
                ps['t1111_rows'] += 1
            if ps['t0'] is None:
                ps['t0'] = key
            ps['t1'] = key
            if s['t0'] is None:
                s['t0'] = key
            s['t1'] = key

            # 间隔：同一分钟内必然 <60s，跳过解析
            minute = key[:16]
            if s['prev_min'] != minute:
                cur = dt.datetime.strptime(key, '%Y-%m-%d %H:%M:%S')
                n_parse += 1
                if s['prev_sec'] is not None:
                    g = (cur - s['prev_sec']).total_seconds()
                    if g > GAP_S:
                        s['gap_n'] += 1
                        if g > GAP_LONG_S:
                            s['gap_long'] += 1
                        if g > s['gap_max']:
                            s['gap_max'] = g
                s['prev_sec'] = cur
                s['prev_min'] = minute

    P('原始文件：%d 行，时间 %s → %s' % (n_rows, t_min, t_max))
    P('时间解析次数：%d（优化后按分钟级触发，而非逐行）' % n_parse)
    P('落在台账周期窗内：%d 行（%.1f%%，其余在周期之间的空档/无台账区间）'
      % (n_matched, 100.0 * n_matched / max(n_rows, 1)))
    P('allnum 解析失败：%d 行' % bad_an)
    P('')

    # ---------- 汇总
    recs = []
    for r in ledger:
        c = r['cycle']
        s = st.get(c)
        if not s or s['rows'] == 0:
            recs.append(dict(
                Cycle=c, Form=r['form'], LedgerLife=r['ledger_life'],
                MachinePieceCount=None, RawDistinctAllNum=None,
                AlignedValidPieceCount=aligned.get(c, 0), ToolUseCount=None,
                GapCount=None, LongGapCount=None, MaxGapMin=None,
                ToolsPresent=None, RowsAll=None, RowsT1111=None,
                DataStart=None, DataEnd=None, EndVsLedgerMin=None,
                StartEndConfidence='NO_DATA',
                DiffAlignedMinusLedger=None, DiffToolUseMinusLedger=None,
            ))
            continue
        an = s['an']
        span = (max(an) - min(an) + 1) if an else None
        end_vs = None
        if r['E'] is not None and s['t1'] is not None:
            end_vs = round((r['E'] - dt.datetime.strptime(s['t1'], '%Y-%m-%d %H:%M:%S')
                            ).total_seconds() / 60.0, 1)
        if end_vs is None:
            conf = 'END_UNKNOWN'
        elif abs(end_vs) <= 20:
            conf = 'MINUTE_LEVEL'
        elif end_vs > 0:
            conf = 'DATA_ENDS_EARLY'
        else:
            conf = 'DATA_ENDS_LATE'
        recs.append(dict(
            Cycle=c, Form=r['form'], LedgerLife=r['ledger_life'],
            MachinePieceCount=span, RawDistinctAllNum=len(an),
            AlignedValidPieceCount=aligned.get(c, 0),
            ToolUseCount=len(s['an_t1111']),
            GapCount=s['gap_n'], LongGapCount=s['gap_long'],
            MaxGapMin=round(s['gap_max'] / 60.0, 1),
            ToolsPresent=len(s['tools']), RowsAll=s['rows'], RowsT1111=s['t1111_rows'],
            DataStart=s['t0'], DataEnd=s['t1'],
            EndVsLedgerMin=end_vs, StartEndConfidence=conf,
            DiffAlignedMinusLedger=(aligned.get(c, 0) - r['ledger_life'])
            if r['ledger_life'] else None,
            DiffToolUseMinusLedger=(len(s['an_t1111']) - r['ledger_life'])
            if r['ledger_life'] else None,
        ))

    # ---------- 写 CSV
    cols = ['Cycle', 'Form', 'LedgerLife', 'MachinePieceCount', 'RawDistinctAllNum',
            'AlignedValidPieceCount', 'ToolUseCount', 'GapCount', 'LongGapCount',
            'MaxGapMin', 'ToolsPresent', 'RowsAll', 'RowsT1111',
            'DataStart', 'DataEnd', 'EndVsLedgerMin', 'StartEndConfidence',
            'DiffAlignedMinusLedger', 'DiffToolUseMinusLedger']
    p = os.path.join(OUT, 'label_audit_7col.csv')
    with open(p, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(recs)

    # ---------- 第二张表：按 (周期, 主程序) 分组
    prow = []
    for (c, prog), ps in prog_st.items():
        prow.append(dict(Cycle=c, Prog=prog, ProgName=ps['prog_txt'],
                         Rows=ps['rows'], RowsT1111=ps['t1111_rows'],
                         MachinePieceCount=(max(ps['an']) - min(ps['an']) + 1) if ps['an'] else None,
                         RawDistinctAllNum=len(ps['an']),
                         ToolUseCount=len(ps['an1111']),
                         T1111Share=round(len(ps['an1111']) / max(len(ps['an']), 1), 4),
                         DataStart=ps['t0'], DataEnd=ps['t1']))
    prow.sort(key=lambda x: (x['Cycle'], -(x['Rows'] or 0)))
    pth = os.path.join(OUT, 'label_audit_by_program.csv')
    with open(pth, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['Cycle', 'Prog', 'ProgName', 'Rows', 'RowsT1111',
                                          'MachinePieceCount', 'RawDistinctAllNum', 'ToolUseCount',
                                          'T1111Share', 'DataStart', 'DataEnd'])
        w.writeheader()
        w.writerows(prow)

    # ---------- 控制台报表
    have = [x for x in recs if x['RawDistinctAllNum']]
    P('有数据的周期：%d / %d（其余在 9/17 之前或落在台账空档）'
      % (len(have), len(recs)))
    P('')
    P('%-5s %-4s %6s %8s %8s %8s %8s %7s %9s  %s'
      % ('周期', '形态', '台账', '机床件', '去重', '对齐件', 'T1111件', '长间隔', '末行vs台账', '置信'))
    for x in have:
        P('%-5d %-4s %6s %8s %8s %8s %8s %7s %9s  %s'
          % (x['Cycle'], x['Form'], x['LedgerLife'] or '-',
             x['MachinePieceCount'], x['RawDistinctAllNum'],
             x['AlignedValidPieceCount'], x['ToolUseCount'],
             x['LongGapCount'], x['EndVsLedgerMin'], x['StartEndConfidence']))
    P('')

    # 关键对照：对齐件数 vs 机床件数 vs 刀具件数
    P('=== 关键对照（有数据且台账刀寿可提取）===')
    P('%-5s %6s %8s %8s | %s' % ('周期', '台账', '对齐件', 'T1111件', '三者关系'))
    big = []
    for x in have:
        if not x['LedgerLife']:
            continue
        a, m, t = x['AlignedValidPieceCount'], x['RawDistinctAllNum'], x['ToolUseCount']
        tag = []
        if a == t:
            tag.append('对齐=T1111')
        else:
            tag.append('对齐≠T1111(%+d)' % (a - t))
        if t != x['LedgerLife']:
            tag.append('T1111≠台账(%+d)' % (t - x['LedgerLife']))
        else:
            tag.append('T1111=台账')
        P('%-5d %6d %8d %8d | %s' % (x['Cycle'], x['LedgerLife'], a, t, '; '.join(tag)))
        if x['LedgerLife'] and abs(t - x['LedgerLife']) > 20:
            big.append(x)

    P('')
    P('=== T1111 件数与台账刀寿差 > 20 件的周期（P0-01 要逐把归因）===')
    if big:
        for x in big:
            P('  c%-4d 台账 %3d  机床件 %4d  T1111件 %4d  差 %+4d  末行vs台账 %s 分钟  长间隔 %d 次'
              % (x['Cycle'], x['LedgerLife'], x['MachinePieceCount'], x['ToolUseCount'],
                 x['ToolUseCount'] - x['LedgerLife'], x['EndVsLedgerMin'], x['LongGapCount']))
    else:
        P('  无（全部 ≤20 件）')

    P('')
    P('=== 每个周期内的主程序构成（件数不能跨品番相加）===')
    byc = defaultdict(list)
    for x in prow:
        byc[x['Cycle']].append(x)
    multi = 0
    for c in sorted(byc):
        ps = [x for x in byc[c] if x['RawDistinctAllNum']]
        if not ps:
            continue
        ps.sort(key=lambda x: -x['RawDistinctAllNum'])
        main = ps[0]
        n_prog = len(ps)
        if n_prog > 1:
            multi += 1
        tag = ' ← 多品番' if n_prog > 1 else ''
        P('  c%-4d %d 个程序 | 主 %-3s(%s) 件%4s T1111 %4s 占%4.0f%% | 其他: %s%s'
          % (c, n_prog, main['Prog'], (main['ProgName'].split('(')[1][:12] if '(' in main['ProgName'] else ''),
             main['RawDistinctAllNum'], main['ToolUseCount'], 100 * main['T1111Share'],
             ', '.join('%s(件%s)' % (x['Prog'], x['RawDistinctAllNum']) for x in ps[1:4]),
             tag))
    P('  → 有多个程序的周期：%d / %d' % (multi, len([c for c in byc if any(x['RawDistinctAllNum'] for x in byc[c])])))

    P('')
    P('=== 全局一致性 ===')
    ok = [x for x in have if x['LedgerLife'] and x['ToolUseCount'] is not None]
    if ok:
        d = np.array([x['ToolUseCount'] - x['LedgerLife'] for x in ok], float)
        da = np.array([x['AlignedValidPieceCount'] - x['LedgerLife'] for x in ok], float)
        dt_ = np.array([x['ToolUseCount'] - x['AlignedValidPieceCount'] for x in ok], float)
        P('  T1111件 - 台账刀寿    : 中位 %+.0f，|差| 中位 %.0f，max|差| %d（n=%d）'
          % (np.median(d), np.median(np.abs(d)), int(np.abs(d).max()), len(d)))
        P('  对齐件 - 台账刀寿     : 中位 %+.0f，|差| 中位 %.0f，max|差| %d'
          % (np.median(da), np.median(np.abs(da)), int(np.abs(da).max())))
        P('  T1111件 - 对齐件      : 中位 %+.0f，|差| 中位 %.0f，max|差| %d'
          % (np.median(dt_), np.median(np.abs(dt_)), int(np.abs(dt_.max()))))
        P('  → 若 |T1111件-对齐件| 远小于 |对齐件-台账|，说明差异主要来自'
          '"产品件数 vs 刀具参与件数"，而不是 xalign 对齐失败')

    txt = '\n'.join(log)
    with open(os.path.join(OUT, 's32_console.log'), 'w', encoding='utf-8') as f:
        f.write(txt + '\n')
    print(txt)


if __name__ == '__main__':
    main()
