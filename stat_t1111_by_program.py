# -*- coding: utf-8 -*-
"""
T1111 按主程序的完整统计
- 一次调用 = L_tool_number 由 非T1111 -> T1111 的一段连续切削
- 负载积分用真实时间积分 sum(load * dt)，比 mean*duration 更准
- 目标：回答「各程序加工量/工艺/负载差多少」以及「寿命差异能否被累计载荷解释」
"""
import pandas as pd, numpy as np

SRC = r'C:/abc/workBoddyHome刀具/influxdb_FanucNormalData_20260910_085752_4152.csv'
OUT_TXT = r'C:/abc/workBoddyHome刀具/T1111_主程序统计.txt'
OUT_CSV = r'C:/abc/workBoddyHome刀具/t1111_call_by_program.csv'

CH = ['L_spindle_load', 'L_X_servo_load', 'L_Y_servo_load', 'L_Z_servo_load',
      'L_A_servo_load', 'L_B_servo_load', 'L_C_servo_load', 'L_E_servo_load']
USE = ['_time', 'L_tool_number', 'L_spindle_speed', 'L_main_program', 'L_sub_program',
       'L_feed_override', 'L_spindle_override', 'L_allnum'] + CH

parts = []
for ch in pd.read_csv(SRC, usecols=lambda c: c in USE, low_memory=False,
                      chunksize=400_000):
    parts.append(ch)
df = pd.concat(parts, ignore_index=True)
del parts
df['_t'] = pd.to_datetime(df['_time'], errors='coerce')
df = df.sort_values('_t').reset_index(drop=True)
for c in CH + ['L_spindle_speed', 'L_feed_override', 'L_spindle_override']:
    df[c] = pd.to_numeric(df[c], errors='coerce')

is_t = df.L_tool_number.astype(str).str.strip() == 'T1111'
df['seg'] = ((is_t & ~is_t.shift(1, fill_value=False)).cumsum()).where(is_t)

BOUND = [
    ('c17', '2026-09-04 16:05', '2026-09-06 17:30'),
    ('c18', '2026-09-06 17:30', '2026-09-07 16:00'),
    ('c19', '2026-09-07 16:00', '2026-09-08 11:00'),
    ('c20', '2026-09-08 11:00', '2026-09-09 11:00'),
    ('c21', '2026-09-09 11:00', '2026-09-10 08:20'),
]

rows = []
for sid, g in df.dropna(subset=['seg']).groupby('seg'):
    gc = g[g.sp > 0] if False else g[g.L_spindle_speed > 0]
    if len(gc) < 5:
        continue
    gc = gc.sort_values('_t')
    dt = gc._t.diff().dt.total_seconds().fillna(0)
    r = {'seg': int(sid), 't0': gc._t.min(),
         'dur_s': (gc._t.max() - gc._t.min()).total_seconds(),
         'n_rows': len(gc),
         'sp_speed_mean': gc.L_spindle_speed.mean(),
         'sp_speed_max': gc.L_spindle_speed.max(),
         'feed_ovr': gc.L_feed_override.median(),
         'sp_ovr': gc.L_spindle_override.median(),
         'main_program': gc.L_main_program.mode().iloc[0],
         'sub_program': (gc.L_sub_program.mode().iloc[0]
                         if gc.L_sub_program.notna().any() else None)}
    for c in CH:
        v = gc[c].fillna(0)
        r[c + '_mean'] = v.mean()
        r[c + '_max'] = v.max()
        r[c + '_p95'] = v.quantile(0.95)
        r[c + '_int'] = float((v * dt).sum())     # 时间积分
    rows.append(r)
F = pd.DataFrame(rows).sort_values('t0').reset_index(drop=True)

F['cycle'] = None
for name, a, b in BOUND:
    m = (F.t0 >= pd.Timestamp(a)) & (F.t0 < pd.Timestamp(b))
    F.loc[m, 'cycle'] = name
F = F.dropna(subset=['cycle']).copy()
F['prog'] = F.main_program.astype(str).str.extract(r'^(\d+)')
F['call_idx'] = F.groupby('cycle').cumcount() + 1

L = []
P = L.append
P('=== T1111 按主程序统计 (设备4152 / L刀塔 / 2026-09-04~09-10) ===')
P('一次调用 = 刀号跳变到 T1111 的一段连续切削；仅统计主轴转速>0 的行')
P('负载积分 = sum(负载 x 采样间隔)，单位 负载%·秒')
P('')

P('[1] 按主程序汇总')
P('%-28s %8s %7s %10s %12s %10s %10s %8s' % (
    '主程序', '调用次数', '占比', '总时长h', '单次时长s(中位)', '主轴转速', '进给倍率', '主轴倍率'))
P('-' * 100)
tot = len(F)
vc = F.main_program.value_counts()
for mp, n in vc.items():
    g = F[F.main_program == mp]
    P('%-28s %8d %6.1f%% %10.2f %12.1f %10.0f %10.0f %8.0f' % (
        str(mp)[:28], n, 100.0 * n / tot, g.dur_s.sum() / 3600.0,
        g.dur_s.median(), g.sp_speed_mean.median(),
        g.feed_ovr.median(), g.sp_ovr.median()))
P('%-28s %8d %6.1f%% %10.2f %12.1f' % (
    '合计', tot, 100.0, F.dur_s.sum() / 3600.0, F.dur_s.median()))
P('')

P('[2] 各主程序的负载水平 (均值 / P95)')
P('%-24s %11s %11s %11s %11s %11s %11s' % ('通道', '程序12均值', '程序14均值', '程序20均值',
                                           '程序12_P95', '程序14_P95', '程序20_P95'))
P('-' * 92)
progs = ['12', '14', '20']
for c in CH:
    cells = []
    for suf in ['_mean', '_p95']:
        for p in progs:
            g = F[F.prog == p]
            cells.append(g[c + suf].mean() if len(g) else np.nan)
    P('%-24s %11.2f %11.2f %11.2f %11.2f %11.2f %11.2f' % (c[:24], *cells))
P('')

P('[3] 周期 × 主程序 交叉表 (调用次数)')
hdr = '%-8s' % '周期' + ''.join('%14s' % ('程序' + p) for p in progs) + '%10s %10s' % ('合计', '主程序构成')
P(hdr)
P('-' * 74)
cross = {}
for name, a, b in BOUND:
    g = F[F.cycle == name]
    if len(g) == 0:
        continue
    cells = [int((g.prog == p).sum()) for p in progs]
    comp = ' / '.join('%s:%d' % (p, c) for p, c in zip(progs, cells) if c > 0)
    P('%-8s' % name + ''.join('%14d' % c for c in cells) + '%10d   %s' % (len(g), comp))
P('')

P('[4] 单位调用的消耗对比 (每次调用的平均负载积分)')
P('%-24s %14s %14s %14s   %s' % ('通道', '程序12', '程序14', '程序20', '14/12 倍率'))
P('-' * 92)
for c in CH:
    m12 = F[F.prog == '12'][c + '_int'].mean() if (F.prog == '12').any() else np.nan
    m14 = F[F.prog == '14'][c + '_int'].mean() if (F.prog == '14').any() else np.nan
    m20 = F[F.prog == '20'][c + '_int'].mean() if (F.prog == '20').any() else np.nan
    ratio = (m14 / m12) if (m12 and not np.isnan(m12) and m12 != 0) else np.nan
    P('%-24s %14.1f %14.1f %14.1f   %s' % (
        c[:24], m12, m14, m20,
        ('%.2f' % ratio) if not np.isnan(ratio) else '-'))
P('')

P('[5] 关键问题：各周期「累计主轴负载积分」是否相近？')
P('   若相近 -> 寿命由累计载荷决定，可用「单位程序消耗」推算剩余次数')
P('   若相差很大 -> 还有载荷之外的因素（毛坯批次/刀具批次/操作习惯）')
P('')
P('%-8s %-26s %8s %16s %16s %12s' % ('周期', '主程序构成', '调用次数', '主轴负载积分', '单位调用积分', 'X伺服积分'))
P('-' * 96)
agg = []
for name, a, b in BOUND:
    g = F[F.cycle == name]
    if len(g) == 0:
        continue
    comp = ' / '.join('%s:%d' % (p, int((g.prog == p).sum()))
                      for p in progs if (g.prog == p).sum() > 0)
    si = g.L_spindle_load_int.sum(); xi = g.L_X_servo_load_int.sum()
    agg.append((name, len(g), si))
    P('%-8s %-26s %8d %16.0f %16.1f %12.0f' % (
        name, comp[:26], len(g), si, si / len(g), xi))
if agg:
    arr = np.array([a[2] for a in agg])
    cnt = np.array([a[1] for a in agg])
    P('')
    P('  累计主轴负载积分: 最小=%.0f  最大=%.0f  中位=%.0f  变异系数CV=%.2f' % (
        arr.min(), arr.max(), np.median(arr), arr.std(ddof=1) / arr.mean()))
    P('  调用次数        : 最小=%d  最大=%d  中位=%.0f  变异系数CV=%.2f' % (
        cnt.min(), cnt.max(), np.median(cnt), cnt.std(ddof=1) / cnt.mean()))
    P('')
    if arr.std(ddof=1) / arr.mean() < cnt.std(ddof=1) / cnt.mean():
        P('  >> 累计载荷的离散度小于调用次数的离散度：寿命差异【部分可被累计载荷解释】')
    else:
        P('  >> 累计载荷的离散度不小于调用次数：【无法用累计载荷解释寿命差异】')
P('')
P('[6] 说明')
P('  - 完整周期 5 个 (c17~c21)，c20/c21 为纯程序12，c18 为纯程序14')
P('  - 换刀时刻为人工记录，周期边界有小时级误差')
P('  - 程序20 仅出现在 c17 混合周期中，样本不足以单独下结论')

F.to_csv(OUT_CSV, index=False, encoding='utf-8-sig')
open(OUT_TXT, 'w', encoding='utf-8').write('\n'.join(L))
print('written ->', OUT_TXT)
print('         ->', OUT_CSV)
