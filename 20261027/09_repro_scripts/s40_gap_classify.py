"""
s40_gap_classify.py —— P0-04：采集缺口四分类（替换"gap>60s 累计缺口"的错口径）

【为什么重做】
`s32_label_audit.py` 用 `sum(gap > 60s)` 当"累计缺口"。但实测：
    件内间隔   p50 = 0.02s   p99 = 1.0s    max ≈ 300s
    件间间隔   p50 = 303.6s  p90 = 324.9s  p95 = 637s
  → **>60s 的间隔占件间间隔的 98.9%**
即"gap>60s"把每件之间正常的 5 分钟节拍全算成了缺口。原报告"c38 累计缺口 59.6 小时、
实际只采到 2.8 小时"因此作废（审核 P0-04）。

【四分类定义】（审核要求）
  InterRecordGap              采样节律 / 正常生产节拍      gap ≤ P_GAP（默认 600s）
  NonCuttingTime              本刀在等，机床在加工别的刀    gap > P_GAP 且期间有机床其他刀的行
  ConfirmedAcquisitionLoss    采集真的丢了                gap > P_GAP 且整机零数据、但台账显示有生产
  AcquisitionUnknownInterval  无法判定                    gap > P_GAP 且整机零数据、且无法确认为丢失

【可判定性分级】输出里带 `EvidenceLevel`
  DATA_CONFIRMED    有全刀数据（3.3GB），逐段实测
  PARTIAL           无全刀数据，长间隔一律归 AcquisitionUnknownInterval，并标注

用法
  python s40_gap_classify.py                # 自动探测 3.3GB 是否在仓库根
  python s40_gap_classify.py --no-raw       # 强制降级
  python s40_gap_classify.py --raw PATH     # 指定全刀文件

输出（默认写到 ../30_缺口四分类/）
  gap_class_per_cycle.csv    逐周期四类时长/次数
  gap_class_detail.csv       每个长间隔的明细（周期、起止、gap、分类、依据）
  s40_console.txt            控制台全文副本
"""
import os
import sys
import csv
import argparse
import datetime as dt
from collections import defaultdict

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))          # 20261027/
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import wsroot as _wsroot          # G2：不再数 '..'，按工作区特征向上定位
ROOT = _wsroot.find(_os.path.dirname(_os.path.abspath(__file__)))
OUT = os.path.join(BASE, '30_缺口四分类')

RAW_OLD = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv')
RAW_NEW = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261027_200034.csv')
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../20261027
# 台账（P1-07：0 号位已授权随包公开）→ 包内副本优先，仓库根仅作回退
LEDGER = os.path.join(BASE, '15_台账真值检验', 'input', '三厂换刀记录.xlsx')
if not os.path.exists(LEDGER):
    LEDGER = os.path.join(ROOT, '三厂换刀记录.xlsx')
EQUIV_L2 = os.path.join(BASE, '23_两批数据等价性', 'L2_by_window.csv')

# ---- 间隔分界（依据见文件头实测分位）
P_GAP = 600.0           # ≤600s 视为采样节律/正常节拍（覆盖件内 max 300s、件间 p90 325s）
P_LONG = 3600.0         # >1h 单独标记，便于与旧口径对照

# 全刀原始数据（3.3GB）的覆盖起点。早于该时刻的长间隔**无法判定**——
# 不是"整机零数据"，而是"我们手上没有那段时间的全刀数据"。
# 实测：c25/c26/c27/c28/c29 的长间隔都落在 09-14~09-16，正是这个盲区。
RAW_NEW_START = pd.Timestamp('2026-09-17 00:00:00')

LOG = []


def P(s=''):
    LOG.append(str(s))
    print(s)


# ------------------------------------------------------------------ 台账时间解析
FIX = {
    '2026/9/416：05': '2026-09-04 16:05',
    '2026/9/18/16:26': '2026-09-18 16:26',
    '2026/10/26/9：20': '2026-10-26 09:20',
}


def parse_t(v):
    """台账时间解析：三种脏格式（中文冒号、斜杠、日期与时刻粘连）必须显式修"""
    import re
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return pd.NaT
    if isinstance(v, (pd.Timestamp, dt.datetime)):
        return pd.Timestamp(v)
    t = str(v).strip().replace('：', ':')
    if t in FIX:
        return pd.Timestamp(FIX[t])
    t = re.sub(r'^(\d{4})/(\d{1,2})/(\d{1,2})[ /](\d{1,2}):(\d{2})$', r'\1-\2-\3 \4:\5', t)
    return pd.to_datetime(t, errors='coerce')


def load_ledger():
    L = pd.read_excel(LEDGER, sheet_name=0)
    L['S'] = L['StartTime'].map(parse_t)
    L['E'] = L['EndTime'].map(parse_t)
    L = L.rename(columns={'顺序号': 'cycle'})
    L['Form'] = L['换刀原因'].map(
        lambda s: '破损' if '破损' in str(s) else ('磨损' if '磨损' in str(s) else '未标'))
    P('台账 %d 行；S 解析失败 %d，E 解析失败 %d（末行 E 为空正常）'
      % (len(L), int(L.S.isna().sum()), int(L.E.isna().sum())))
    return L


# ------------------------------------------------------------------ 旧 labeled（T1111 行）
def load_old():
    # 本脚本针对固定的 L 通道数据源（4152/T1111）；跨通道版本见 channel_map.py
    d = pd.read_csv(RAW_OLD, usecols=['_time', 'life_cycle', 'L_allnum'], encoding='utf-8-sig')
    d['t'] = pd.to_datetime(d['_time'], errors='coerce', format='mixed')
    d = d.dropna(subset=['t']).sort_values(['life_cycle', 't'])
    P('旧 labeled：%d 行 / %d 周期' % (len(d), d.life_cycle.nunique()))
    return d


# ------------------------------------------------------------------ 全刀数据：只取「时间戳」用于判定
def scan_other_tools(windows, path):
    """扫全刀文件，记录落在每个候选长间隔内的**非 T1111** 行数。

    ⚠ 第一版对每行遍历全部长间隔 → O(8.2M × 60) 直接超时。
      改为「按时间排序 + 指针推进」：长间隔互不重叠，指针只前进不后退 → O(n)。
    """
    # ⚠ 用 idx 做键，不用 (cyc, t0, t1)：t0 是毫秒 Timestamp，strftime('%H:%M:%S') 会截掉毫秒，
    #    两者永远不会相等 → 第一版全部匹配失败、四个类别都算成 0。
    segs = []
    for (idx, t0, t1) in windows:
        if (t1 - t0).total_seconds() > P_GAP:
            segs.append([idx, t0.strftime('%Y-%m-%d %H:%M:%S'),
                         t1.strftime('%Y-%m-%d %H:%M:%S'), 0, 0])
    if not segs:
        return segs
    segs.sort(key=lambda x: x[1])
    P('扫全刀文件统计 %d 个长间隔…' % len(segs))

    lo, hi = segs[0][1], max(s[2] for s in segs)
    n_row = 0
    ci = 0
    ns = len(segs)
    with open(path, encoding='utf-8-sig', newline='') as f:
        rd = csv.reader(f)
        next(rd)
        for row in rd:
            if len(row) <= 31:
                continue
            ts = row[0][:19]
            if ts < lo or ts > hi:
                continue
            n_row += 1
            while ci < ns and ts >= segs[ci][2]:
                ci += 1
            if ci >= ns:
                break
            if ts >= segs[ci][1]:
                if row[31].strip() == 'T1111':
                    segs[ci][4] += 1
                else:
                    segs[ci][3] += 1
    P('  扫描窗口内行数 %d' % n_row)
    return segs


# ------------------------------------------------------------------ 主逻辑
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--raw', default=None, help='全刀原始文件路径')
    ap.add_argument('--no-raw', action='store_true', help='强制不读全刀文件（降级）')
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    P('=' * 88)
    P('P0-04　采集缺口四分类（替代 gap>60s 的错口径）')
    P('=' * 88)
    P('')

    L = load_ledger()
    d = load_old()

    # 台账里有窗、且数据里有行的周期
    have = set(int(x) for x in d.life_cycle.unique())
    Lw = L[L.cycle.isin(have) & L.S.notna()].copy()
    P('台账窗可用且数据里有行的周期：%d 个' % len(Lw))
    P('')

    # ---------- 逐周期逐 gap 分类
    det = []
    for _, r in Lw.sort_values('cycle').iterrows():
        c = int(r['cycle'])
        g = d[d.life_cycle == c]
        gap = g.t.diff().dt.total_seconds()
        same = g.L_allnum.eq(g.L_allnum.shift())
        prev_t = g.t.shift()
        for i in np.where(gap.values > 0)[0]:
            gv = float(gap.iloc[i])
            t0 = prev_t.iloc[i]
            t1 = g.t.iloc[i]
            cls = 'InterRecordGap' if gv <= P_GAP else ('LONG_PENDING')
            det.append(dict(Cycle=c, Form=r['Form'], T0=t0, T1=t1, GapSec=gv,
                            SamePiece=bool(same.iloc[i]) if pd.notna(same.iloc[i]) else None,
                            Class=cls, Basis=''))
    D = pd.DataFrame(det)
    P('相邻间隔总数 %d；≤%.0fs 的 %d 个，>%.0fs 的 %d 个'
      % (len(D), P_GAP, int((D.GapSec <= P_GAP).sum()), P_GAP, int((D.GapSec > P_GAP).sum())))
    P('')

    # ---------- 长间隔：判定来源
    long = D[D.Class == 'LONG_PENDING'].copy().reset_index(drop=True)
    P('长间隔（>%.0fs）共 %d 个，合计 %.1f 小时'
      % (P_GAP, len(long), long.GapSec.sum() / 3600))
    P('')

    # 已确认的整机零数据期（由 21-00 / s32 按天统计得出，硬编码于此并注明来源）
    # 依据：21_新数据标签审计/21-00 §2 —— 按天行数统计，10/01~10/08 与 10/10~10/13 为 0
    ZERO_PERIODS = [
        (pd.Timestamp('2026-10-01 00:00'), pd.Timestamp('2026-10-09 00:00'),
         '10/01–10/08 零数据；10/01–10/07 为国庆停产'),
        (pd.Timestamp('2026-10-09 23:59'), pd.Timestamp('2026-10-14 00:00'),
         '10/10–10/13 零数据；台账有换刀记录 → 有生产但无数据'),
    ]

    def in_zero(t0, t1):
        for a, b, why in ZERO_PERIODS:
            if t1 > a and t0 < b:
                return why
        return None

    # 全刀数据判定（可选）
    raw = None if args.no_raw else (args.raw or (RAW_NEW if os.path.exists(RAW_NEW) else None))
    if raw and os.path.exists(raw):
        segs = scan_other_tools([(i, x.T0, x.T1) for i, x in long.iterrows()], raw)
        byidx = {s_[0]: s_ for s_ in segs}
        miss = 0
        for i, x in long.iterrows():
            if i not in byidx:
                miss += 1
                continue
            _, _, _, n_other, n_1111 = byidx[i]
            if n_other > 0:
                long.at[i, 'Class'] = 'NonCuttingTime'
                long.at[i, 'Basis'] = '期间机床其他刀有 %d 行 → 本刀未参与' % n_other
            elif x.T1 < RAW_NEW_START:
                long.at[i, 'Class'] = 'AcquisitionUnknownInterval'
                long.at[i, 'Basis'] = '早于全刀数据起点(09-17)，无全刀数据可判'
            else:
                why = in_zero(x.T0, x.T1)
                if why:
                    long.at[i, 'Class'] = 'ConfirmedAcquisitionLoss'
                    long.at[i, 'Basis'] = why
                else:
                    long.at[i, 'Class'] = 'AcquisitionUnknownInterval'
                    long.at[i, 'Basis'] = '整机零数据又不在已知中断期 → 无法判定（停机/交接班/丢数）'
        if miss:
            P('  ⚠ %d 个长间隔未匹配到扫描结果' % miss)
        ev = 'DATA_CONFIRMED'
    else:
        for i, x in long.iterrows():
            why = in_zero(x.T0, x.T1)
            if why:
                long.at[i, 'Class'] = 'ConfirmedAcquisitionLoss'
                long.at[i, 'Basis'] = why
            elif x.T1 < RAW_NEW_START:
                long.at[i, 'Class'] = 'AcquisitionUnknownInterval'
                long.at[i, 'Basis'] = '早于全刀数据起点(09-17)，无全刀数据可判'
            else:
                long.at[i, 'Class'] = 'AcquisitionUnknownInterval'
                long.at[i, 'Basis'] = '降级模式：无全刀数据，无法区分「本刀未参与」与「采集丢失」'
        ev = 'PARTIAL'
    P('可判定性：%s' % ev)
    P('')

    # ---------- 兜底：不允许有 LONG_PENDING 静默漏掉（第一版就是这么变成"四类全 0"的）
    left = int((long.Class == 'LONG_PENDING').sum())
    if left:
        raise RuntimeError('仍有 %d 个长间隔未分类，拒绝汇总' % left)

    # ---------- 汇总
    D2 = pd.concat([D[D.Class != 'LONG_PENDING'], long], ignore_index=True)
    D2 = D2.sort_values(['Cycle', 'T0'])

    rows = []
    for c, g in D2.groupby('Cycle'):
        r0 = Lw[Lw.cycle == c].iloc[0]
        dur_h = (r0.E - r0.S).total_seconds() / 3600 if pd.notna(r0.E) else np.nan
        rec = dict(Cycle=int(c), Form=r0['Form'],
                   LedgerHours=round(dur_h, 2) if dur_h == dur_h else np.nan,
                   TotalGaps=len(g),
                   Num_InterRecord=int((g.Class == 'InterRecordGap').sum()),
                   Hours_InterRecord=round(float(g.loc[g.Class == 'InterRecordGap', 'GapSec'].sum()) / 3600, 2),
                   Num_NonCutting=int((g.Class == 'NonCuttingTime').sum()),
                   Hours_NonCutting=round(float(g.loc[g.Class == 'NonCuttingTime', 'GapSec'].sum()) / 3600, 2),
                   Num_Unknown=int((g.Class == 'AcquisitionUnknownInterval').sum()),
                   Hours_Unknown=round(float(g.loc[g.Class == 'AcquisitionUnknownInterval', 'GapSec'].sum()) / 3600, 2),
                   Num_ConfirmedLoss=int((g.Class == 'ConfirmedAcquisitionLoss').sum()),
                   Hours_ConfirmedLoss=round(float(g.loc[g.Class == 'ConfirmedAcquisitionLoss', 'GapSec'].sum()) / 3600, 2),
                   MaxGapHours=round(float(g.GapSec.max()) / 3600, 2),
                   EvidenceLevel=ev)
        rows.append(rec)
    S = pd.DataFrame(rows).sort_values('Cycle')
    S.to_csv(os.path.join(OUT, 'gap_class_per_cycle.csv'), index=False, encoding='utf-8-sig')
    # ⚠ 明细只落**长间隔**（>P_GAP）。全量落盘会把 38 万条 0.02 秒的件内采样节律写进去，
    #   文件涨到 33 MB 且没有任何审计价值（那些行的分类恒为 InterRecordGap）。
    Dlong = D2[D2.Class != 'InterRecordGap'].copy()
    Dlong.to_csv(os.path.join(OUT, 'gap_class_detail.csv'), index=False, encoding='utf-8-sig')
    P('[out] 明细 %d 行（仅 >%.0fs 的长间隔；件内节律 %d 行只在汇总里计数）'
      % (len(Dlong), P_GAP, int((D2.Class == 'InterRecordGap').sum())))

    P('=== 逐周期四分类（小时）===')
    P('%-6s %-5s %8s %10s %10s %10s %10s %9s'
      % ('周期', '形态', '台账h', '采样节律', '非切削', '无法判定', '确认丢失', '最大间隔h'))
    for _, r in S.iterrows():
        P('c%-5d %-5s %8.2f %10.2f %10.2f %10.2f %10.2f %9.2f'
          % (r.Cycle, r.Form, r.LedgerHours, r.Hours_InterRecord, r.Hours_NonCutting,
             r.Hours_Unknown, r.Hours_ConfirmedLoss, r.MaxGapHours))
    P('')
    P('=== 合计 ===')
    for k in ('Hours_InterRecord', 'Hours_NonCutting', 'Hours_Unknown', 'Hours_ConfirmedLoss'):
        P('  %-22s %8.1f 小时' % (k.replace('Hours_', ''), S[k].sum()))
    P('  %-22s %8.1f 小时（旧口径 sum(gap>60s)，作废）'
      % ('gap>60s 合计', D.GapSec[D.GapSec > 60].sum() / 3600))
    P('')

    # ---------- 与旧口径对照：为什么 59.6h 是假的
    P('=== 旧口径为什么错 ===')
    P('  sum(gap>60s) 把**件间正常节拍**（中位 303.6s）全部计入。')
    P('  >60s 的间隔占件间间隔的 98.9%，所以累计缺口约等于整个周期的生产时间。')
    P('  本口径改为：≤%.0fs 归 InterRecordGap（正常节律），不再算缺口。' % P_GAP)
    P('')
    old_c38 = D[(D.Cycle == 38) & (D.GapSec > 60)].GapSec.sum() / 3600
    new_c38 = S.loc[S.Cycle == 38, 'Hours_ConfirmedLoss'].sum() + S.loc[S.Cycle == 38, 'Hours_Unknown'].sum()
    P('  c38 对照：旧口径 %.1f h → 新口径（确认丢失+无法判定）%.2f h' % (old_c38, new_c38))
    P('')
    P('=== 一个容易误解的点 ===')
    P('  整机 13 天零数据（10/01~10/13）**不体现为周期内的长间隔**，而是整周期缺失：')
    P('  c43/c44/c46/c47 在数据里一行都没有 → 不在本表的任何周期内，所以 ConfirmedLoss=0。')
    P('  因此"ConfirmedAcquisitionLoss=0"的含义是：**已采到的周期内部，没有可确认的采集丢失**，')
    P('  不是"采集没有丢过"。')
    P('')

    with open(os.path.join(OUT, 's40_console.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG) + '\n')
    P('[out] gap_class_per_cycle.csv / gap_class_detail.csv / s40_console.txt')


if __name__ == '__main__':
    main()
