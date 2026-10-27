# -*- coding: utf-8 -*-
"""
【磨耗补偿曲线探针】验证 L_wear_x/z 是否就是"免费的 VB"
关键问题：
  1. 补偿值是否随时间被手工调整（变化点多少）
  2. 调整是否单向累积（刀具磨损只增不减）
  3. 是否在某个事件点归零（= 换刀 / 重新对刀）
  4. 变化密度够不够做标签
"""
import pandas as pd, numpy as np

SRC = r'C:/abc/workBoddyHome刀具/influxdb_FanucNormalData_20260910_085752_4152.csv'
USE = ['_time', 'L_tool_number', 'L_wear_no', 'L_wear_x', 'L_wear_z',
       'L_geo_no', 'L_geo_x', 'L_geo_z', 'L_allnum', 'L_main_program',
       'L_spindle_speed', 'L_spindle_load']

out = []
W = out.append

chunks = []
for c in pd.read_csv(SRC, usecols=lambda x: x in USE, low_memory=False, chunksize=500000):
    # 只留有值的重点刀号，减少内存
    keep = c[c.L_tool_number.astype(str).isin(['T0404', 'T0303', 'T0505'])]
    if len(keep):
        chunks.append(keep)
d = pd.concat(chunks, ignore_index=True)
del chunks

for col in ['L_wear_x', 'L_wear_z', 'L_geo_x', 'L_geo_z', 'L_allnum',
            'L_spindle_speed', 'L_spindle_load']:
    d[col] = pd.to_numeric(d[col], errors='coerce')
d['_t'] = pd.to_datetime(d['_time'], errors='coerce')
d = d.sort_values('_t').reset_index(drop=True)

W('=' * 78)
W('【磨耗补偿曲线探针】L_wear_x/z 能否当作 VB 代理')
W('=' * 78)
W('数据窗口: %s ~ %s  (6 天)' % (d._t.min(), d._t.max()))
W('')

for TN in ['T0404', 'T0303', 'T0505']:
    g = d[d.L_tool_number.astype(str) == TN].copy()
    if len(g) < 100:
        continue
    W('#' * 78)
    W('【%s】行数=%d   占有效切削行的 %s' % (TN, len(g), ''))
    W('#' * 78)

    for col in ['L_wear_x', 'L_wear_z']:
        s = g[col]
        # 找出变化点
        chg = (s != s.shift()).fillna(False)
        n_chg = int(chg.sum())
        vals = s[chg].tolist()
        ts = g._t[chg].tolist()
        W('')
        W('  -- %s : 唯一值数=%d  变化次数=%d  范围 %s ~ %s'
          % (col, s.nunique(), n_chg, s.min(), s.max()))
        if n_chg == 0:
            W('     （从未调整）')
            continue
        W('     变化序列 (时间 / 新值):')
        for i, (t, v) in enumerate(zip(ts, vals)):
            if i >= 40:
                W('       ... 其余 %d 次省略' % (len(vals) - 40))
                break
            prev = vals[i - 1] if i > 0 else np.nan
            arrow = '' if i == 0 else ('  ↑+%.4f' % (v - prev) if v > prev else '  ↓%.4f' % (v - prev))
            W('       %s   %8.4f%s' % (str(t)[:19], v, arrow))
        # 单调性
        if len(vals) > 2:
            dv = np.diff([float(x) for x in vals])
            W('     单调性: 上升 %d 次 / 下降 %d 次 / 不变 %d 次'
              % ((dv > 1e-9).sum(), (dv < -1e-9).sum(), (np.abs(dv) <= 1e-9).sum()))
            W('     相邻变化间隔: 中位 %s' % (
                str(pd.Series(ts).diff().dropna().median()).split('.')[0] if len(ts) > 1 else 'n/a'))
    W('')

    # 是否同期还有 geo 补偿变化
    for col in ['L_geo_x', 'L_geo_z']:
        s = g[col]
        W('  -- %s : 唯一值数=%d 范围 %s ~ %s' % (col, s.nunique(), s.min(), s.max()))
    W('')

    # 切削时的负载与该补偿值的关系（粗看）
    gg = g[(g.L_spindle_speed.fillna(0) > 0)].copy()
    if len(gg) > 1000 and gg.L_wear_x.nunique() > 1:
        W('  -- 按 wear_x 分组看主轴负载均值:')
        for v, h in gg.groupby('L_wear_x'):
            if len(h) < 200:
                continue
            W('       wear_x=%8.4f  行数=%-7d  主轴负载均值=%6.2f  中位=%6.2f'
              % (v, len(h), h.L_spindle_load.mean(), h.L_spindle_load.median()))
    W('')

open(r'C:/abc/workBoddyHome刀具/磨耗补偿曲线探针.txt', 'w', encoding='utf-8').write('\n'.join(out))
print('OK')
