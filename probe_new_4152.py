# -*- coding: utf-8 -*-
"""
新数据 influxdb_FanucNormalData_20260910_085752_4152.csv 只读体检
目的：确认覆盖范围、刀号分布、T1111 是否存在、磨损补偿行为
不做任何写库/建模，只输出报告
"""
import pandas as pd, numpy as np, sys, io

SRC = r'C:/abc/workBoddyHome刀具/influxdb_FanucNormalData_20260910_085752_4152.csv'
OUT = r'C:/Users/C12643/probe_new_4152.txt'

USE = ['_time', 'deviceName', 'L_tool_number', 'L_main_program', 'L_sub_program',
       'L_state', 'L_allnum', 'L_wear_no', 'L_wear_x', 'L_wear_z',
       'L_geo_x', 'L_geo_z', 'L_spindle_speed', 'L_spindle_load']

chunks = []
n = 0
for ch in pd.read_csv(SRC, usecols=lambda c: c in USE, low_memory=False,
                      chunksize=400_000):
    n += len(ch)
    chunks.append(ch)
df = pd.concat(chunks, ignore_index=True)
del chunks

lines = []
P = lines.append
P('=== 新数据体检: influxdb_FanucNormalData_20260910_085752_4152.csv ===')
P('')
P('[1] 规模')
P('  总行数: %d' % n)
P('  列数  : 39 (原始导出，无 L_tool_life / 无标注列)')
P('')

df['_t'] = pd.to_datetime(df['_time'], errors='coerce')
P('[2] 时间范围')
P('  起: %s' % df._t.min())
P('  止: %s' % df._t.max())
P('  跨度: %s' % (df._t.max() - df._t.min()))
P('')

P('[3] 设备')
P(str(df.deviceName.value_counts().head(10)))
P('')

P('[4] 刀号分布 (L_tool_number)')
vc = df.L_tool_number.value_counts()
P('  不同刀号数: %d' % len(vc))
P(str(vc.head(25)))
P('')
P('  T1111 是否出现: %s (行数 %d)' % ('是' if 'T1111' in vc.index else '否', int(vc.get('T1111', 0))))
P('  T2525 是否出现: %s (行数 %d)' % ('是' if 'T2525' in vc.index else '否', int(vc.get('T2525', 0))))
P('')

# 按天统计 T1111 出现情况
if 'T1111' in vc.index:
    sub = df[df.L_tool_number == 'T1111'].copy()
    P('[5] T1111 明细')
    P('  行数: %d' % len(sub))
    P('  时间: %s ~ %s' % (sub._t.min(), sub._t.max()))
    P('  按天行数:')
    d = sub.groupby(sub._t.dt.date).size()
    P(str(d))
    P('')
    P('  L_wear_x 唯一值: %s' % str(sorted(sub.L_wear_x.dropna().unique())[:20]))
    P('  L_wear_z 唯一值: %s' % str(sorted(sub.L_wear_z.dropna().unique())[:20]))
    P('  L_wear_no 唯一值: %s' % str(sorted(sub.L_wear_no.dropna().unique())[:20]))
    P('')

P('[6] 全局磨损补偿列 (L_wear_x / L_wear_z / L_geo_x) 变动情况')
for c in ['L_wear_x', 'L_wear_z', 'L_wear_no', 'L_geo_x', 'L_geo_z']:
    v = pd.to_numeric(df[c], errors='coerce')
    P('  %-10s nunique=%-6d min=%-12.4f max=%-12.4f' % (c, v.nunique(), v.min(), v.max()))
P('')

P('[7] 按 (刀号) 看磨损补偿是否随周期变化 —— 变化最多的前 15 个刀号')
g = df.copy()
g['wx'] = pd.to_numeric(g.L_wear_x, errors='coerce')
g['wz'] = pd.to_numeric(g.L_wear_z, errors='coerce')
agg = g.groupby('L_tool_number').agg(
    n=('wx', 'size'), wx_nu=('wx', 'nunique'), wz_nu=('wz', 'nunique'),
    wx_min=('wx', 'min'), wx_max=('wx', 'max'))
agg = agg.sort_values('wx_nu', ascending=False)
P(str(agg.head(15)))
P('')

P('[8] 主程序 / 子程序 分布 (top10)')
P('  main_program:')
P(str(df.L_main_program.value_counts().head(10)))
P('  sub_program:')
P(str(df.L_sub_program.value_counts().head(10)))
P('')

P('[9] L_state 分布')
P(str(df.L_state.value_counts()))
P('')

P('[10] L_allnum 连续性 (全局时序索引)')
an = pd.to_numeric(df.L_allnum, errors='coerce').dropna()
P('  min=%d max=%d nunique=%d' % (an.min(), an.max(), an.nunique()))
d = an.diff().dropna()
P('  diff 值计数 top10: %s' % str(d.value_counts().head(10).to_dict()))
P('  负跳变(回绕)次数: %d' % int((d < 0).sum()))

open(OUT, 'w', encoding='utf-8').write('\n'.join(lines))
print('written ->', OUT, 'rows=', n)
