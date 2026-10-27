# -*- coding: utf-8 -*-
"""
【突破口探针】检查现有数据里三条尚未利用的线索
A. L_wear_x/z (刀具磨耗补偿) —— 若现场按工件尺寸手工修正过，则是免费的物理磨损量
B. L_spindle_speed vs L_spindle_speed_cmd 差值 —— 比厂家二次处理的 load% 更原始
C. L_abso_x/y/z + L_distance —— 能否反推实际切削路径 / 切深
"""
import pandas as pd, numpy as np

SRC = r'C:/abc/workBoddyHome刀具/influxdb_FanucNormalData_20260910_085752_4152.csv'

USE = ['_time', 'L_tool_number', 'L_main_program', 'L_allnum',
       'L_wear_no', 'L_wear_x', 'L_wear_z', 'L_geo_no', 'L_geo_x', 'L_geo_z',
       'L_spindle_speed', 'L_spindle_speed_cmd', 'L_spindle_load',
       'L_abso_x', 'L_abso_y', 'L_abso_z', 'L_distance',
       'L_tool_offset', 'L_rate1', 'L_feed_override', 'L_state']

out = []
W = out.append

chunks = []
for c in pd.read_csv(SRC, usecols=lambda x: x in USE, low_memory=False, chunksize=400000):
    chunks.append(c)
d = pd.concat(chunks, ignore_index=True)
del chunks

for col in ['L_wear_x', 'L_wear_z', 'L_geo_x', 'L_geo_z', 'L_spindle_speed',
            'L_spindle_speed_cmd', 'L_abso_x', 'L_abso_y', 'L_abso_z',
            'L_distance', 'L_tool_offset', 'L_rate1']:
    d[col] = pd.to_numeric(d[col], errors='coerce')

d['_t'] = pd.to_datetime(d['_time'], errors='coerce')
d = d.sort_values('_t').reset_index(drop=True)

W('=' * 78)
W('【突破口探针】设备 4152 —— 检查三条此前未利用的线索')
W('=' * 78)
W('总行数 = %d   时间 %s ~ %s' % (len(d), d._t.min(), d._t.max()))
W('')

# ---------------------------------------------------------------- A 磨耗补偿
W('#' * 78)
W('【A】刀具磨耗补偿 L_wear_x / L_wear_z  —— 是否已被现场使用？')
W('#' * 78)
W('')
W('[A-1] 全设备（所有刀号）这四个字段的非零情况:')
for col in ['L_wear_x', 'L_wear_z', 'L_geo_x', 'L_geo_z']:
    s = d[col]
    nz = (s != 0) & s.notna()
    W('  %-12s 非空=%-8d 非零=%-8d (%.2f%%)  唯一值数=%-6d  min=%-10.4f max=%-10.4f'
      % (col, s.notna().sum(), nz.sum(), 100 * nz.mean(), s.nunique(),
         s.min() if s.notna().any() else np.nan, s.max() if s.notna().any() else np.nan))
W('')

if d.L_wear_no.notna().any():
    W('[A-2] L_wear_no (磨耗补偿号) 分布 TOP15:')
    for k, v in d.L_wear_no.value_counts().head(15).items():
        W('    %-8s : %d 行' % (str(k), v))
    W('')

# 按刀号看补偿有没有被改动
W('[A-3] 各刀号的磨耗/几何补偿变化幅度（= 现场有没有手工修正过）')
W('  只看行数 >= 3000 的刀号')
W('')
W('  %-10s %-8s %-12s %-12s %-14s %-14s' % ('刀号', '行数', 'wear_x范围', 'wear_z范围', 'geo_x范围', 'geo_z范围'))
cnt = d.L_tool_number.astype(str).value_counts()
for tn in cnt[cnt >= 3000].index:
    g = d[d.L_tool_number.astype(str) == tn]
    wx = g.L_wear_x.dropna(); wz = g.L_wear_z.dropna()
    gx = g.L_geo_x.dropna();  gz = g.L_geo_z.dropna()
    def rng(s):
        return '%.4f~%.4f' % (s.min(), s.max()) if len(s) else '全空'
    W('  %-10s %-8d %-12s %-12s %-14s %-14s' % (tn, len(g), rng(wx), rng(wz), rng(gx), rng(gz)))
W('')

# ---------------------------------------------------------------- B 主轴滑差
W('#' * 78)
W('【B】主轴实际转速 vs 指令转速 —— 比 load% 更原始的负载表达')
W('#' * 78)
W('')
g = d[d.L_tool_number.astype(str) == 'T1111'].copy()
ss = g.L_spindle_speed; sc = g.L_spindle_speed_cmd
ok = ss.notna() & sc.notna()
W('  T1111 行数=%d  两项都非空=%d' % (len(g), ok.sum()))
W('  spindle_speed      : 中位=%.1f  范围 %.1f ~ %.1f' % (ss.median(), ss.min(), ss.max()))
W('  spindle_speed_cmd  : 中位=%.1f  范围 %.1f ~ %.1f' % (sc.median(), sc.min(), sc.max()))
diff = (sc - ss)[ok]
W('  差值 (cmd - 实际)   : 中位=%.2f  均值=%.2f  std=%.2f  abs>1占比=%.2f%%'
  % (diff.median(), diff.mean(), diff.std(), 100 * (diff.abs() > 1).mean()))
W('  完全相等(=0)占比   : %.2f%%' % (100 * (diff.abs() < 1e-9).mean()))
W('')
W('  判读：若实际值几乎恒等于指令值，说明该通道未被更新 / 或加工无滑差，此路不通。')
W('')

# 滑差 vs load% 的独立性 —— 若两者几乎相同则无新信息
if (diff.abs() > 1e-9).mean() > 0.01:
    ld = g.L_spindle_load[ok]
    diff2 = diff
    m = ld.notna()
    r = np.corrcoef(diff2[m], ld[m])[0, 1]
    W('  滑差 与 L_spindle_load 的相关 r = %.3f  (|r|>0.7 说明基本重复，无新信息)' % r)
else:
    W('  滑差几乎恒为 0 -> 不提供新信息')
W('')

# ---------------------------------------------------------------- C 坐标/距离
W('#' * 78)
W('【C】L_abso_x/y/z + L_distance —— 能否反推切削路径 / 切深')
W('#' * 78)
W('')
W('  %-14s %-10s %-12s %-14s %-14s %s' % ('字段', '非空', '唯一值数', 'min', 'max', 'std'))
for col in ['L_abso_x', 'L_abso_y', 'L_abso_z', 'L_distance', 'L_rate1']:
    s = d[col]
    W('  %-14s %-10d %-12d %-14.3f %-14.3f %.3f'
      % (col, s.notna().sum(), s.nunique(), s.min(), s.max(), s.std()))
W('')
if d.L_distance.notna().sum() > 100:
    s = d.L_distance.dropna()
    W('  L_distance 是否单调递增(全局行程表)? 前1000行差分>0占比 = %.2f%%'
      % (100 * (s.head(1000).diff().dropna() > 0).mean()))
W('')

# 单次调用内的 abso 行程 = 实际切削路径长度
W('[C-2] T1111 单次调用内的绝对坐标行程 (可代表实际切削路径长度):')
g11 = d[d.L_tool_number.astype(str) == 'T1111'].copy()
g11['call'] = (g11.L_tool_number.astype(str) != g11.L_tool_number.astype(str).shift()).cumsum()
g11 = g11[g11.L_spindle_speed.fillna(0) > 0]
rows = []
for cid, gg in list(g11.groupby('call'))[:400]:
    if len(gg) < 20:
        continue
    path = 0.0
    for ax in ['L_abso_x', 'L_abso_y', 'L_abso_z']:
        v = gg[ax].dropna()
        if len(v) > 1:
            path += np.abs(np.diff(v.values)).sum()
    rows.append((cid, len(gg), path, gg.L_spindle_load.mean()))
rd = pd.DataFrame(rows, columns=['call', 'n', 'path_mm', 'load_mean'])
if len(rd):
    W('  有效调用段 = %d 个' % len(rd))
    W('  行程 path_mm : 中位=%.2f  均值=%.2f  std=%.2f  CV=%.3f'
      % (rd.path_mm.median(), rd.path_mm.mean(), rd.path_mm.std(),
         rd.path_mm.std() / rd.path_mm.mean() if rd.path_mm.mean() else np.nan))
    W('  主轴负载均值 : 中位=%.2f  CV=%.3f'
      % (rd.load_mean.median(), rd.load_mean.std() / rd.load_mean.mean() if rd.load_mean.mean() else np.nan))
    if len(rd) > 50:
        from scipy.stats import spearmanr
        rr, pp = spearmanr(rd.path_mm, rd.load_mean)
        W('  行程 vs 主轴负载 Spearman r = %.3f (p=%.4f)' % (rr, pp))
    W('')
    W('  判读：若 path_mm 的 CV 明显小于 load 的 CV，说明行程被程序锁死 -> 也不提供区分度；')
    W('        若行程在各次调用间有明显差异，则它是一个未被利用的解释变量。')

open(r'C:/abc/workBoddyHome刀具/突破口探针.txt', 'w', encoding='utf-8').write('\n'.join(out))
print('WROKE', len(out))
