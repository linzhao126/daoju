# -*- coding: utf-8 -*-
"""s34_data_equiv.py —— 新旧两批原始数据在重叠周期上是否等价

为什么要做这个
--------------
新原始数据（未过滤全刀，到 10-27）让可用周期从 17 增到 28。
但要合并进回测，必须先证明：**新数据在重叠周期（c25–c45）上，与旧 labeled 文件是同一批行**。
否则 28 周期的 HI 混着两种数据源，任何 MAE 都不可解释。

三层对比
--------
L1 全时段   旧文件全部行 vs 新文件 T1111 行（时间交集内）
L2 逐台账窗 每个周期的行数 / allnum 数 / 时间范围（窗定义取自台账，不依赖任一文件的 life_cycle）
L3 键级     (时间戳, allnum) 集合：交集 / 仅旧有 / 仅新有
L4 数值指纹 抽样 allnum，比 L_abso_x / L_abso_z / L_spindle_load 的逐行序列是否一致

⚠ 关键设计：L2 起**不使用任何文件的 life_cycle 列**，只用台账时间窗。
   这样能把"窗定义差异"与"数据本身差异"分开——旧文件的 life_cycle 来自一份
   可能只有 45 行的旧台账，与新台账 60 条的窗未必逐秒相同。

输出：23_两批数据等价性/
"""
import os
import csv
import re
import sys
import datetime as dt
from collections import defaultdict
from bisect import bisect_right

import numpy as np
import pandas as pd

csv.field_size_limit(10 ** 7)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))
OUT = os.path.abspath(os.path.join(HERE, '..', '23_两批数据等价性'))
os.makedirs(OUT, exist_ok=True)

OLD = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv')
NEW = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261027_200034.csv')
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../20261027
# 台账（P1-07：0 号位已授权随包公开）→ 包内副本优先，仓库根仅作回退
XLS = os.path.join(BASE, '15_台账真值检验', 'input', '三厂换刀记录.xlsx')
if not os.path.exists(XLS):
    XLS = os.path.join(ROOT, '三厂换刀记录.xlsx')

# 两文件前 39 列（0-based 0..38）同名同序，以下索引通用
I_T, I_AX, I_AZ, I_AN, I_LOAD, I_TN = 0, 12, 14, 16, 25, 31

TEXT_TIME_FIX = {
    '2026/9/416：05': '2026-09-04 16:05',
    '2026/9/18/16:26': '2026-09-18 16:26',
    '2026/10/26/9：20': '2026-10-26 09:20',
}

LOG = []


def P(*a):
    s = ' '.join(str(x) for x in a)
    print(s)
    LOG.append(s)


def parse_t(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    s = str(v).strip()
    if s in ('', 'NaT', 'nan'):
        return None
    if s in TEXT_TIME_FIX:
        return dt.datetime.strptime(TEXT_TIME_FIX[s], '%Y-%m-%d %H:%M')
    s = s.replace('：', ':')
    s = re.sub(r'^(\d{4})/(\d{1,2})/(\d{1,2})[ /](\d{1,2}):(\d{2})', r'\1-\2-\3 \4:\5', s)
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return dt.datetime.strptime(s[:19], fmt)
        except Exception:
            continue
    return None


def load_windows():
    """台账窗：[(cycle, 'YYYY-mm-dd HH:MM:SS', end_or_None)]"""
    L = pd.read_excel(XLS, sheet_name=0)
    w = []
    for _, r in L.iterrows():
        S = parse_t(r['StartTime'])
        E = parse_t(r['EndTime'])
        if S is None:
            continue
        w.append((int(r['顺序号']),
                  S.strftime('%Y-%m-%d %H:%M:%S'),
                  E.strftime('%Y-%m-%d %H:%M:%S') if E else None))
    w.sort(key=lambda x: x[1])
    return w


def read_old():
    """旧 labeled 文件：取需要的 5 列"""
    d = pd.read_csv(OLD, usecols=['_time', 'life_cycle', 'L_allnum',
                                  'L_abso_x', 'L_abso_z', 'L_spindle_load',
                                  'L_tool_number'], encoding='utf-8-sig')
    d['_tf'] = d['_time'].astype(str).str.strip()      # 完整时间戳 = 唯一键
    d['_ts'] = d['_tf'].str.slice(0, 19)               # 秒级 = 仅用于比台账窗
    d['L_allnum'] = pd.to_numeric(d['L_allnum'], errors='coerce')
    d['tn'] = d['L_tool_number'].astype(str).str.strip()
    return d


def scan_new(wins, keep_cycles):
    """流式扫新文件，只留 T1111 且落在 keep_cycles 的台账窗内"""
    starts = [w[1] for w in wins]
    ends = [w[2] for w in wins]
    NW = len(wins)
    ci = 0
    rows = []
    total = 0
    n_tool = 0
    n_win = 0
    with open(NEW, encoding='utf-8-sig', newline='') as f:
        rd = csv.reader(f)
        next(rd)
        for row in rd:
            total += 1
            if len(row) <= I_TN:
                continue
            if row[I_TN].strip() != 'T1111':
                n_tool += 1
                continue
            key = row[I_T][:19]
            if len(key) < 19:
                continue
            while ci < NW - 1 and ends[ci] is not None and key >= ends[ci]:
                ci += 1
            if key < starts[ci]:
                j = bisect_right(starts, key) - 1
                if j < 0:
                    continue
                ci = j
            if ends[ci] is not None and key >= ends[ci]:
                continue
            cyc = wins[ci][0]
            if cyc not in keep_cycles:
                n_win += 1
                continue
            rows.append((key, row[I_T].strip(), cyc, row[I_AN],
                         row[I_AX], row[I_AZ], row[I_LOAD]))
    P('扫描新文件 %d 行；非 T1111 跳过 %d；周期不匹配跳过 %d；保留 %d'
      % (total, n_tool, n_win, len(rows)))
    d = pd.DataFrame(rows, columns=['_ts', '_tf', 'life_cycle', 'L_allnum',
                                    'L_abso_x', 'L_abso_z', 'L_spindle_load'])
    d['L_allnum'] = pd.to_numeric(d['L_allnum'], errors='coerce')
    return d, dict(total=total, n_tool=n_tool, n_win=n_win)


def cycle_of_ts(wins, ts):
    """按台账窗反查周期（不在任何窗内返回 None）"""
    starts = [w[1] for w in wins]
    ends = [w[2] for w in wins]
    i = bisect_right(starts, ts) - 1
    if i < 0:
        return None
    if ends[i] is not None and ts >= ends[i]:
        return None
    return wins[i][0]


def main():
    wins = load_windows()
    P('台账窗 %d 条：c%d .. c%d' % (len(wins), wins[0][0], wins[-1][0]))
    P('')

    old = read_old()
    P('旧文件 %d 行 / %d 个 life_cycle；T1111 行 %d'
      % (len(old), old.life_cycle.nunique(), int((old.tn == 'T1111').sum())))

    t_old_min, t_old_max = old._ts.min(), old._ts.max()
    P('旧文件时间 %s → %s' % (t_old_min, t_old_max))

    # 重叠周期 = 台账窗起点落在旧文件时间范围内的周期
    keep = set()
    for c, S, E in wins:
        if S <= t_old_max and (E is None or E >= t_old_min):
            keep.add(c)
    P('与旧文件时间重叠的台账周期 %d 个：%s'
      % (len(keep), ','.join('c%d' % c for c in sorted(keep))))
    P('')

    new, stat = scan_new(wins, keep)

    # ---------- L2 逐台账窗
    P('=== L2 逐台账窗（只用台账时间窗，不用任何文件的 life_cycle）===')
    P('%-5s %-20s %-20s %9s %9s %8s %7s'
      % ('周期', '窗起', '窗止', '旧行数', '新行数', '行数差', '旧件/新件'))
    L2 = []
    for c, S, E in wins:
        if c not in keep:
            continue
        o = old[(old._ts >= S) & ((old._ts < E) if E else True)]
        n = new[new.life_cycle == c]
        d = len(n) - len(o)
        L2.append(dict(Cycle=c, WinStart=S, WinEnd=E,
                       OldRows=len(o), NewRows=len(n), RowDiff=d,
                       OldPieces=o.L_allnum.nunique(), NewPieces=n.L_allnum.nunique(),
                       OldT0=o._ts.min() if len(o) else None, OldT1=o._ts.max() if len(o) else None,
                       NewT0=n._ts.min() if len(n) else None, NewT1=n._ts.max() if len(n) else None))
        P('c%-4d %-20s %-20s %9d %9d %+8d  %6d/%d'
          % (c, S, E, len(o), len(n), d, o.L_allnum.nunique(), n.L_allnum.nunique()))
    T2 = pd.DataFrame(L2)
    T2.to_csv(os.path.join(OUT, 'L2_by_window.csv'), index=False, encoding='utf-8-sig')
    P('')
    P('行数完全相同（差=0）的周期：%d / %d'
      % (int((T2.RowDiff == 0).sum()), len(T2)))
    P('行数差中位 %+d，|差| 中位 %d，max %d'
      % (int(T2.RowDiff.median()), int(T2.RowDiff.abs().median()),
         int(T2.RowDiff.abs().max())))
    P('')

    # ---------- L3 键级 (时间戳, allnum)
    P('=== L3 键级 (完整毫秒时间戳, allnum) 集合对比 ===')
    P('   ⚠ 第一版用秒级时间戳做键：c30 一秒内平均 4.35 行 → 键不唯一 →')
    P('     下游 merge 变成笛卡尔积（80216 键 → 147 万行），"数值不一致 65.7%" 是假的。')
    P('     本版改用完整 _time（毫秒精度，实测唯一）。')
    L3 = []
    P('%-5s %9s %9s %9s %9s %9s' % ('周期', '旧键', '新键', '交集', '仅旧', '仅新'))
    for c, S, E in wins:
        if c not in keep:
            continue
        o = old[(old._ts >= S) & ((old._ts < E) if E else True)]
        n = new[new.life_cycle == c]
        ko = set(zip(o._tf, o.L_allnum.astype('Int64')))
        kn = set(zip(n._tf, n.L_allnum.astype('Int64')))
        inter = ko & kn
        L3.append(dict(Cycle=c, OldKeys=len(ko), NewKeys=len(kn),
                       Inter=len(inter), OnlyOld=len(ko - kn), OnlyNew=len(kn - ko)))
        P('c%-4d %9d %9d %9d %9d %9d'
          % (c, len(ko), len(kn), len(inter), len(ko - kn), len(kn - ko)))
    T3 = pd.DataFrame(L3)
    T3.to_csv(os.path.join(OUT, 'L3_key_sets.csv'), index=False, encoding='utf-8-sig')
    P('')
    tot_o = int(T3.OldKeys.sum()); tot_i = int(T3.Inter.sum())
    P('合计：旧键 %d，新键 %d，交集 %d → 旧键被新覆盖 %.2f%%'
      % (tot_o, int(T3.NewKeys.sum()), tot_i, 100.0 * tot_i / max(tot_o, 1)))
    P('')

    # ---------- L4 数值指纹：对交集键逐行比 3 个数值列
    P('=== L4 交集键上的数值指纹 ===')
    L4 = []
    P('%-5s %9s %10s %10s %10s' % ('周期', '可比行', 'X不等', 'Z不等', 'Load不等'))
    for c, S, E in wins:
        if c not in keep:
            continue
        o = old[(old._ts >= S) & ((old._ts < E) if E else True)].copy()
        n = new[new.life_cycle == c].copy()
        if not len(o) or not len(n):
            continue
        o['k'] = list(zip(o._tf, o.L_allnum.astype('Int64')))
        n['k'] = list(zip(n._tf, n.L_allnum.astype('Int64')))
        # 键必须唯一，否则 merge 会做笛卡尔积并静默给出"数值不一致"的假结论
        if o.k.duplicated().any() or n.k.duplicated().any():
            P('  c%-4d ⚠ 键重复（旧 %d / 新 %d）→ 跳过 L4'
              % (c, int(o.k.duplicated().sum()), int(n.k.duplicated().sum())))
            continue
        m = o.merge(n, on='k', suffixes=('_o', '_n'))
        assert len(m) == len(set(o.k) & set(n.k)), 'merge 行数 != 交集键数'
        if not len(m):
            L4.append(dict(Cycle=c, Rows=0, XDiff=0, ZDiff=0, LoadDiff=0))
            continue

        def ndiff(a, b):
            a = pd.to_numeric(a, errors='coerce').to_numpy(float)
            b = pd.to_numeric(b, errors='coerce').to_numpy(float)
            both = np.isfinite(a) & np.isfinite(b)
            ne = (~both) & (~(np.isnan(a) & np.isnan(b)))
            d = np.abs(a[both] - b[both]) > 1e-6
            return int(d.sum() + ne.sum())

        dx = ndiff(m.L_abso_x_o, m.L_abso_x_n)
        dz = ndiff(m.L_abso_z_o, m.L_abso_z_n)
        dl = ndiff(m.L_spindle_load_o, m.L_spindle_load_n)
        L4.append(dict(Cycle=c, Rows=len(m), XDiff=dx, ZDiff=dz, LoadDiff=dl))
        P('c%-4d %9d %10d %10d %10d' % (c, len(m), dx, dz, dl))
    T4 = pd.DataFrame(L4)
    T4.to_csv(os.path.join(OUT, 'L4_value_fingerprint.csv'), index=False, encoding='utf-8-sig')
    P('')
    n_all = int(T4.Rows.sum()); n_bad = int((T4.XDiff + T4.ZDiff + T4.LoadDiff).sum())
    P('交集键共 %d 行，数值不一致行合计 %d（%.4f%%）'
      % (n_all, n_bad, 100.0 * n_bad / max(n_all, 1)))
    P('')

    # ---------- L5 分组等价性：旧文件的 life_cycle vs 按新台账窗推导的周期
    #   L2/L3/L4 只证明了"行相同"，没证明"这些行被分到同一个周期"。
    #   旧文件的 life_cycle 来自一份可能只有 45 行的旧台账，窗边界未必与新台账一致。
    P('=== L5 分组等价性：旧 life_cycle vs 台账窗推导周期 ===')
    st_arr = np.array([w[1] for w in wins])
    en_arr = np.array([w[2] if w[2] else '9999-99-99 99:99:99' for w in wins])
    cyc_arr = np.array([w[0] for w in wins])
    tv = old._ts.values
    j = np.searchsorted(st_arr, tv, side='right') - 1
    inwin = (j >= 0) & (tv < en_arr[np.clip(j, 0, len(wins) - 1)])
    c_led = np.where(inwin, cyc_arr[np.clip(j, 0, len(wins) - 1)], -1)

    old2 = old.copy()
    old2['c_ledger'] = c_led
    n_agree = int((old2.life_cycle == old2.c_ledger).sum())
    n_inwin = int(inwin.sum())
    n_out = len(old2) - n_inwin
    P('旧文件 %d 行：落在某台账窗内 %d，窗外 %d，不匹配任何台账窗 %d'
      % (len(old2), n_inwin, 0, n_out))
    P('life_cycle 与台账窗一致 %d 行（占窗内 %.3f%%）'
      % (n_agree, 100.0 * n_agree / max(n_inwin, 1)))
    P('')
    mism = old2[old2.life_cycle != old2.c_ledger]
    if len(mism):
        P('不一致 %d 行，按 (旧 life_cycle → 台账窗周期) 分布：' % len(mism))
        for (a, b), n in mism.groupby(['life_cycle', 'c_ledger']).size().sort_values(ascending=False).items():
            P('   life_cycle=%-4d → 台账 %s : %d 行' % (a, ('c%d' % b) if b > 0 else '窗外', n))
        P('')
        P('   样例（前 5 行）：')
        P('   %-24s %-10s %-10s %s' % ('_time', '旧cycle', '台账窗', 'L_allnum'))
        for _, r in mism.head(5).iterrows():
            P('   %-24s %-10d %-10s %s' % (r._tf, r.life_cycle,
                                           ('c%d' % r.c_ledger) if r.c_ledger > 0 else '窗外',
                                           r.L_allnum))
        mism[['_tf', 'life_cycle', 'c_ledger', 'L_allnum']].to_csv(
            os.path.join(OUT, 'L5_cycle_mismatch.csv'), index=False, encoding='utf-8-sig')
    else:
        P('   ✅ 完全一致：旧 life_cycle == 按新台账窗推导的周期（逐行）')
    P('')

    # 旧文件 20 个 life_cycle 的头尾，与台账窗对照
    P('旧文件各 life_cycle 的边界 vs 台账窗边界（分钟差）：')
    P('%-10s %7s %-20s %-20s %10s %10s' % ('life_cycle', '行数', '数据起', '数据止', '起点差min', '止点差min'))
    wmap = {w[0]: w for w in wins}
    for c, g in old.groupby('life_cycle'):
        w = wmap.get(c)
        if not w:
            P('%-10d %7d %-20s %-20s %10s %10s' % (c, len(g), g._ts.min(), g._ts.max(), '无台账窗', ''))
            continue
        d0 = (dt.datetime.strptime(g._ts.min(), '%Y-%m-%d %H:%M:%S')
              - dt.datetime.strptime(w[1], '%Y-%m-%d %H:%M:%S')).total_seconds() / 60
        d1 = (dt.datetime.strptime(g._ts.max(), '%Y-%m-%d %H:%M:%S')
              - dt.datetime.strptime(w[2], '%Y-%m-%d %H:%M:%S')).total_seconds() / 60 if w[2] else np.nan
        P('%-10d %7d %-20s %-20s %+10.1f %+10.1f' % (c, len(g), g._ts.min(), g._ts.max(), d0, d1))
    P('')

    # ---------- L1 全时段总量
    P('=== L1 全时段总量 ===')
    ov = old[(old._ts >= t_old_min) & (old._ts <= t_old_max)]
    nv = new[(new._ts >= t_old_min) & (new._ts <= t_old_max)]
    P('旧文件全部行 %d；新文件在旧时间范围内（T1111、且落在台账窗）%d'
      % (len(ov), len(nv)))
    P('差 %+d（%.2f%%）' % (len(nv) - len(ov), 100.0 * (len(nv) - len(ov)) / max(len(ov), 1)))
    P('')
    P('注：L1 只作参考。旧文件包含台账窗外的行（如周期之间的空档），')
    P('    新文件的扫描已按台账窗裁剪，两者口径本就不同 → 判等价应以 L2/L3/L4 为准。')

    with open(os.path.join(OUT, 's34_console.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG) + '\n')
    print()
    print('[out] 已写 L2_by_window / L3_key_sets / L4_value_fingerprint / s34_console.txt')


if __name__ == '__main__':
    main()
