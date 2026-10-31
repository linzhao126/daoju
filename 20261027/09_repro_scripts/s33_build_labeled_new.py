# -*- coding: utf-8 -*-
"""s33_build_labeled_new.py —— 从未过滤全刀原始数据生成 T1111 的 labeled 文件（供 xalign）

背景
----
旧 labeled 文件 `..._L_T1111_T1111_life_labeled.csv` 只到 2026-10-09（c45）。
新的未过滤原始数据到 2026-10-27，含台账 c48–c60 共 13 个周期，但没有任何标签。

本脚本做三件事：
1. 只保留 `L_tool_number == 'T1111'` 的行（全刀数据里 T1111 仅占 6%）；
2. 按台账 60 条的时间窗打 `life_cycle`（与 s32 同一套窗逻辑，区间 [S, E)）；
3. 输出 xalign 需要的列，并补一列派生的 `life_used_pct_cut`（仅供回测，不能入模）。

⚠ 口径声明
- `L_allnum` 是**机床产品件号**（12 把刀共享），不是 T1111 的件计数器。
  对 T1111 的行做 groupby 时，一个 allnum 即"这把刀参与过的一件产品"。
- `life_used_pct_cut` 由"该周期内件序 / 该周期总件数 × 100"派生，**是标签的近似**，
  不得作为特征（与旧文件的同名列性质不同，旧列来自真实切削时间占比）。

输出：22_c48-c60_xalign/labeled_T1111_new.csv
"""
import os
import re
import csv
import sys
import datetime as dt

import numpy as np

csv.field_size_limit(10 ** 7)

HERE = os.path.dirname(os.path.abspath(__file__))
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import wsroot as _wsroot          # G2：不再数 '..'，按工作区特征向上定位
ROOT = _wsroot.find(_os.path.dirname(_os.path.abspath(__file__)))
OUT = os.path.abspath(os.path.join(HERE, '..', '22_c48-c60_xalign'))
os.makedirs(OUT, exist_ok=True)

RAW = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261027_200034.csv')
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../20261027
# 台账（P1-07：0 号位已授权随包公开）→ 包内副本优先，仓库根仅作回退
XLS = os.path.join(BASE, '15_台账真值检验', 'input', '三厂换刀记录.xlsx')
if not os.path.exists(XLS):
    XLS = os.path.join(ROOT, '三厂换刀记录.xlsx')

# 默认只取 c48 及之后（前面的周期已有旧 labeled 文件覆盖）
ONLY_FROM = int(sys.argv[1]) if len(sys.argv) > 1 else 48

I_T, I_AX, I_AZ, I_AN = 0, 12, 14, 16
I_CIR, I_MP, I_SPD, I_LOAD, I_TN = 18, 22, 27, 25, 31

TEXT_TIME_FIX = {
    '2026/9/416：05': '2026-09-04 16:05',
    '2026/9/18/16:26': '2026-09-18 16:26',
    '2026/10/26/9：20': '2026-10-26 09:20',
}

KEEP_COLS = ['_time', 'life_cycle', 'L_allnum', 'L_abso_x', 'L_abso_z',
             'L_spindle_load', 'L_spindle_speed', 'L_main_program',
             'life_used_pct_cut', 'L_tool_number']


def parse_t(v):
    if v is None:
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


def main():
    import pandas as pd
    L = pd.read_excel(XLS, sheet_name=0)
    wins = []
    for _, r in L.iterrows():
        S = parse_t(r['StartTime'])
        E = parse_t(r['EndTime'])
        if S is None:
            continue
        c = int(r['顺序号'])
        wins.append((c, S.strftime('%Y-%m-%d %H:%M:%S'),
                     E.strftime('%Y-%m-%d %H:%M:%S') if E else None))
    wins.sort(key=lambda x: x[1])
    starts = [w[1] for w in wins]
    ends = [w[2] for w in wins]
    NW = len(wins)

    from bisect import bisect_right
    rows = []
    n_skip_tool = 0
    n_skip_prog = 0
    ci = 0
    total = 0
    with open(RAW, encoding='utf-8-sig', newline='') as f:
        rd = csv.reader(f)
        next(rd)
        for row in rd:
            total += 1
            if len(row) <= I_TN:
                continue
            if row[I_TN].strip() != 'T1111':
                n_skip_tool += 1
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
            if cyc < ONLY_FROM:
                n_skip_prog += 1
                continue
            rows.append((row[I_T], cyc, row[I_AN], row[I_AX], row[I_AZ],
                         row[I_LOAD], row[I_SPD], row[I_MP], row[I_TN]))

    print('扫描 %d 行；非 T1111 跳过 %d；早于 c%d 跳过 %d；保留 %d'
          % (total, n_skip_tool, ONLY_FROM, n_skip_prog, len(rows)))

    d = pd.DataFrame(rows, columns=['_time', 'life_cycle', 'L_allnum', 'L_abso_x',
                                    'L_abso_z', 'L_spindle_load', 'L_spindle_speed',
                                    'L_main_program', 'L_tool_number'])
    d['_t'] = pd.to_datetime(d['_time'], errors='coerce')
    d = d.dropna(subset=['_t']).sort_values(['life_cycle', '_t'])
    # 派生 life_used_pct_cut：该周期内 allnum 序 / 该周期 allnum 总数 × 100
    g = d.groupby('life_cycle')['L_allnum']
    ranks = g.rank(method='dense')
    nun = g.transform('nunique')
    d['life_used_pct_cut'] = (ranks / nun * 100).round(3)
    d = d[KEEP_COLS]

    p = os.path.join(OUT, 'labeled_T1111_new.csv')
    d.to_csv(p, index=False, encoding='utf-8-sig')
    print('写出 %s：%d 行，%d 个周期' % (p, len(d), d['life_cycle'].nunique()))
    print()
    print('%-5s %7s %8s %-20s %-20s' % ('周期', '行数', '件数', '起', '止'))
    for c, gg in d.groupby('life_cycle'):
        print('%-5d %7d %8d %-20s %-20s'
              % (c, len(gg), gg['L_allnum'].nunique(),
                 str(gg['_t'].min())[:19], str(gg['_t'].max())[:19]))


if __name__ == '__main__':
    main()
