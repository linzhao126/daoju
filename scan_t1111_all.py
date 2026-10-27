# -*- coding: utf-8 -*-
"""
全量扫描工作区 CSV：找出所有含 T1111 的文件
输出: 文件 / 设备号 / 通道(L或R) / 时间范围 / T1111行数 / 刀号分布
"""
import pandas as pd, os, glob

BASE = r'C:/abc/workBoddyHome刀具'
OUT = r'C:/Users/C12643/scan_t1111_all.txt'
TARGETS = ['T1111', 'T2525']
SKIP_KW = ['_trash', 'scored', 'excel_parts', 'split_by_tool', 'parts']

files = sorted(glob.glob(os.path.join(BASE, '*.csv')))
files = [f for f in files if not any(k in f for k in SKIP_KW)]

res = []
for f in files:
    name = os.path.basename(f)
    size_mb = os.path.getsize(f) / 1024 / 1024
    try:
        head = pd.read_csv(f, nrows=0)
        cols = list(head.columns)
        toolcol = None
        chan = None
        for c in ('L_tool_number', 'R_tool_number', 'tool_number', 'L_tool_no', 'R_tool_no'):
            if c in cols:
                toolcol = c
                chan = c[0] if c[0] in ('L', 'R') else '?'
                break
        if toolcol is None:
            res.append((name, size_mb, None, None, None, None, '无刀号列'))
            continue
        use = [c for c in ['_time', 'deviceName', toolcol] if c in cols]
        dev = set(); tmin = None; tmax = None; cnt = {}
        for ch in pd.read_csv(f, usecols=use, low_memory=False, chunksize=500_000):
            if 'deviceName' in ch.columns:
                dev |= set(ch.deviceName.dropna().astype(str).unique())
            if '_time' in ch.columns:
                t = pd.to_datetime(ch['_time'], errors='coerce')
                a, b = t.min(), t.max()
                tmin = a if tmin is None or (pd.notna(a) and a < tmin) else tmin
                tmax = b if tmax is None or (pd.notna(b) and b > tmax) else tmax
            v = ch[toolcol].astype(str).str.strip().value_counts()
            for k, n in v.items():
                cnt[k] = cnt.get(k, 0) + int(n)
        hit = {k: cnt.get(k, 0) for k in TARGETS if cnt.get(k, 0) > 0}
        res.append((name, size_mb, ','.join(sorted(dev)) or '?', chan,
                    str(tmin)[:16] if tmin is not None else '?',
                    str(tmax)[:16] if tmax is not None else '?',
                    ('T1111=%d ' % hit['T1111'] if 'T1111' in hit else 'T1111=0 ') +
                    ('T2525=%d' % hit['T2525'] if 'T2525' in hit else '') +
                    ('  刀号:' + ','.join(sorted(cnt.keys())) if len(cnt) <= 14 else '')))
    except Exception as e:
        res.append((name, size_mb, None, None, None, None, 'ERR ' + str(e)[:60]))

L = []
L.append('=== 全量扫描: 含 T1111 / T2525 的文件 ===')
L.append('%-58s %8s %-8s %-4s %-17s %-17s %s' % ('文件', 'MB', '设备', '通道', '起始', '结束', '命中'))
L.append('-' * 150)
for r in res:
    L.append('%-58s %8.0f %-8s %-4s %-17s %-17s %s' % (
        r[0][:58], r[1], str(r[2])[:8], str(r[3]), str(r[4])[:17], str(r[5])[:17], r[6]))
L.append('')
L.append('结论行: 只有通道为 L 且 T1111>0 的文件可用于本次分析')
open(OUT, 'w', encoding='utf-8').write('\n'.join(L))
print('written ->', OUT)
