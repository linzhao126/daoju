# -*- coding: utf-8 -*-
"""
验证：T1111 的「刀具使用次数」能否从数据中精确重建
定义：L_tool_number 由 非T1111 -> T1111 的跳变 = 一次调用(选刀)
      variant A: 全部跳变
      variant B: 仅统计主轴转速>0 时的跳变（更接近实际切削调用）
对比用户人工记录的「刀具寿命100」
"""
import pandas as pd, numpy as np

SRC = r'C:/abc/workBoddyHome刀具/influxdb_FanucNormalData_20260910_085752_4152.csv'
OUT = r'C:/Users/C12643/probe_call_count.txt'
USE = ['_time', 'L_tool_number', 'L_allnum', 'L_spindle_speed', 'L_main_program']

chunks = []
for ch in pd.read_csv(SRC, usecols=lambda c: c in USE, low_memory=False,
                      chunksize=400_000):
    chunks.append(ch)
df = pd.concat(chunks, ignore_index=True)
del chunks
df['_t'] = pd.to_datetime(df['_time'], errors='coerce')
df = df.sort_values('_t').reset_index(drop=True)

is_t = df.L_tool_number.astype(str).str.strip() == 'T1111'
prev = is_t.shift(1, fill_value=False)
call_A = (is_t & ~prev)                      # 全部调用
sp = pd.to_numeric(df.L_spindle_speed, errors='coerce')
call_B = call_A & (sp > 0)                   # 主轴在转时的调用
df['call_A'] = call_A
df['call_B'] = call_B
df['piece'] = pd.to_numeric(df.L_allnum, errors='coerce')

# 换刀表（用户给，9/4 之后部分；原表 16 行时间写作 9/416：05 已修正）
BOUND = [
    ('c16*', '2026-09-04 09:03:19', '2026-09-04 16:05'),
    ('c17',  '2026-09-04 16:05',    '2026-09-06 17:30'),
    ('c18',  '2026-09-06 17:30',    '2026-09-07 16:00'),
    ('c19',  '2026-09-07 16:00',    '2026-09-08 11:00'),
    ('c20',  '2026-09-08 11:00',    '2026-09-09 11:00'),
    ('c21',  '2026-09-09 11:00',    '2026-09-10 08:20'),
    ('c22*', '2026-09-10 08:20',    '2026-09-10 08:58'),
]
RECORD = {'c17': 100, 'c18': 100, 'c19': 100, 'c20': 100, 'c21': 100, 'c22*': 125}

L = []
P = L.append
P('=== T1111 刀具使用次数重建验证 (设备 4152, 9/4~9/10) ===')
P('')
P('调用定义: L_tool_number 由 非T1111 -> T1111 的跳变')
P('  A = 全部跳变      B = 仅主轴转速>0 时的跳变')
P('')
P('%-6s %-21s %-9s %-9s %-9s %-8s %s' % ('周期', '区间', '调用A', '调用B', '工件数', '人工记', '差异(B-人工)'))
P('-' * 88)
for name, a, b in BOUND:
    m = (df._t >= a) & (df._t < b)
    g = df[m]
    ca = int(g.call_A.sum()); cb = int(g.call_B.sum())
    pc = int(g.piece.nunique())
    rec = RECORD.get(name)
    diff = ('%+.0f' % (cb - rec)) if rec is not None else '-'
    P('%-6s %-21s %-9d %-9d %-9d %-8s %s' % (
        name, a[5:16] + '~' + b[11:16], ca, cb, pc,
        str(rec) if rec is not None else '-', diff))
P('')
P('说明: 工件数 = 该周期内 L_allnum 唯一值数（设备级，含 T1111 不参与的工件）')
P('')

P('[附1] 全期合计')
P('  调用A = %d   调用B = %d   工件数 = %d   总行数 = %d' % (
    int(df.call_A.sum()), int(df.call_B.sum()), int(df.piece.nunique()), len(df)))
P('')
P('[附2] T1111 出现的时间连续性（相邻 T1111 行的时间间隔）')
tt = df.loc[is_t, '_t'].diff().dt.total_seconds().dropna()
P('  间隔中位 = %.3f 秒   90分位 = %.3f 秒   最大 = %.1f 秒' % (
    tt.median(), tt.quantile(0.9), tt.max()))
P('  >5秒的断点数 = %d  (采样若过稀，可能漏记调用)' % int((tt > 5).sum()))
P('')
P('[附3] 各刀号调用次数 (全期, variant A)')
d2 = df.L_tool_number.astype(str).str.strip()
ish = d2.ne(d2.shift(1))
cnt = d2[ish].value_counts().head(15)
P(str(cnt))
P('')
P('[附4] 主程序切换次数 (T1111 行内)')
mp = (df.loc[is_t, 'L_main_program'].astype(str)
      .ne(df.loc[is_t, 'L_main_program'].astype(str).shift(1))).sum()
P('  T1111 行内主程序切换 = %d 次' % int(mp))

open(OUT, 'w', encoding='utf-8').write('\n'.join(L))
print('written ->', OUT)
