# -*- coding: utf-8 -*-
"""
T1111 按「刀具调用次数」对齐的磨损趋势检验 (设备 4152 / L通道 / 9-4~9-10)

核心思路
  1. 一次调用 = L_tool_number 由 非T1111 -> T1111 的一段连续切削
  2. 每次调用算一组特征（各负载通道的 均值/峰值/标准差 + 持续时长）
  3. 周期内按调用序号排队；为消除加工程序造成的基线差异，
     用「本周期前 BASE_N 次调用的均值」做分母，转成相对倍数
  4. 检验：相对倍数 随 调用序号 是否单调变化（Spearman 相关）
     判据：|r| >= 0.3 且 各周期方向一致
"""
import pandas as pd, numpy as np, json
from scipy import stats

SRC = r'C:/abc/workBoddyHome刀具/influxdb_FanucNormalData_20260910_085752_4152.csv'
OUT_TXT = r'C:/abc/workBoddyHome刀具/调用次数对齐_趋势检验报告.txt'
OUT_CSV = r'C:/abc/workBoddyHome刀具/t1111_call_features.csv'
BASE_N = 5

CH = ['L_spindle_load', 'L_X_servo_load', 'L_Y_servo_load', 'L_Z_servo_load',
      'L_A_servo_load', 'L_B_servo_load', 'L_C_servo_load', 'L_E_servo_load']
USE = ['_time', 'L_tool_number', 'L_spindle_speed', 'L_main_program'] + CH

# ---------- 1. 读数据 ----------
parts = []
for ch in pd.read_csv(SRC, usecols=lambda c: c in USE, low_memory=False,
                      chunksize=400_000):
    parts.append(ch)
df = pd.concat(parts, ignore_index=True)
del parts
df['_t'] = pd.to_datetime(df['_time'], errors='coerce')
df = df.sort_values('_t').reset_index(drop=True)
for c in CH:
    df[c] = pd.to_numeric(df[c], errors='coerce')
df['sp'] = pd.to_numeric(df.L_spindle_speed, errors='coerce')

# ---------- 2. 切分调用段 ----------
is_t = df.L_tool_number.astype(str).str.strip() == 'T1111'
seg_id = ((is_t & ~is_t.shift(1, fill_value=False)).cumsum()).where(is_t)
df['seg'] = seg_id

# ---------- 3. 周期边界（用户换刀表） ----------
BOUND = [
    ('c17', '2026-09-04 16:05', '2026-09-06 17:30'),
    ('c18', '2026-09-06 17:30', '2026-09-07 16:00'),
    ('c19', '2026-09-07 16:00', '2026-09-08 11:00'),
    ('c20', '2026-09-08 11:00', '2026-09-09 11:00'),
    ('c21', '2026-09-09 11:00', '2026-09-10 08:20'),
]

# ---------- 4. 每次调用聚合特征 ----------
rows = []
for sid, g in df.dropna(subset=['seg']).groupby('seg'):
    gc = g[g.sp > 0]                      # 只在切削的行上统计
    if len(gc) < 5:
        continue
    t0, t1 = gc._t.min(), gc._t.max()
    r = {'seg': int(sid), 't0': t0, 't1': t1,
         'dur_s': (t1 - t0).total_seconds(), 'n_rows': len(gc)}
    for c in CH:
        v = gc[c].dropna()
        r[c + '_mean'] = v.mean()
        r[c + '_max'] = v.max()
        r[c + '_std'] = v.std()
    r['main_program'] = gc.L_main_program.mode().iloc[0] if len(gc) else None
    rows.append(r)
F = pd.DataFrame(rows).sort_values('t0').reset_index(drop=True)

# ---------- 5. 归属周期 + 周期内调用序号 ----------
F['cycle'] = None
for name, a, b in BOUND:
    m = (F.t0 >= pd.Timestamp(a)) & (F.t0 < pd.Timestamp(b))
    F.loc[m, 'cycle'] = name
F = F.dropna(subset=['cycle']).copy()
F['call_idx'] = F.groupby('cycle').cumcount() + 1
F['n_call'] = F.groupby('cycle')['call_idx'].transform('max')
F['prog'] = F.groupby('cycle')['call_idx'].transform('progress') if False else F['call_idx'] / F['n_call']

# ---------- 6. 相对倍数（消除程序/周期基线差异） ----------
FEATS = [c + '_mean' for c in CH] + [c + '_max' for c in CH] + \
        [c + '_std' for c in CH] + ['dur_s']
for c in FEATS:
    base = F.groupby('cycle')[c].transform(lambda s: s.iloc[:BASE_N].mean())
    F[c + '_rel'] = F[c] / base.replace(0, np.nan)
    F[c + '_rel'] = F[c + '_rel'].replace([np.inf, -np.inf], np.nan)

# ---------- 7. 趋势检验 ----------
L = []
P = L.append
P('=== T1111 按刀具调用次数对齐的磨损趋势检验 ===')
P('设备 4152 / L通道 / 2026-09-04 ~ 09-10')
P('')
P('[1] 样本')
P('  调用次数合计 = %d   周期数 = %d' % (len(F), F.cycle.nunique()))
P('  %-6s %-8s %-10s %-10s %s' % ('周期', '调用次数', '起始', '结束', '主程序'))
for name, a, b in BOUND:
    g = F[F.cycle == name]
    if len(g) == 0: continue
    mp = g.main_program.value_counts().head(2).to_dict()
    P('  %-6s %-8d %-10s %-10s %s' % (
        name, len(g), str(g.t0.min())[5:16], str(g.t0.max())[5:16], str(mp)))
P('')
P('[2] 判据')
P('  对每个周期分别算 Spearman r(相对倍数, 调用序号)')
P('  有效信号 = |周期 r 的中位| >= 0.3  且  各周期 r 同号 (>= 4/5)')
P('')
P('[3] 结果 (按 |r中位| 降序)')
P('%-24s %8s %8s %8s %8s %8s %8s   %s' % ('特征', 'c17', 'c18', 'c19', 'c20', 'c21', 'r中位', '判定'))
P('-' * 108)

res = []
for c in FEATS:
    col = c + '_rel'
    rs = {}
    for name, a, b in BOUND:
        g = F[F.cycle == name].dropna(subset=[col])
        if len(g) < 8:
            rs[name] = np.nan; continue
        r, _ = stats.spearmanr(g.call_idx, g[col])
        rs[name] = r
    vals = [v for v in rs.values() if not np.isnan(v)]
    if not vals:
        continue
    med = float(np.median(vals))
    pos = sum(1 for v in vals if v > 0)
    neg = sum(1 for v in vals if v < 0)
    same = max(pos, neg)
    ok = (abs(med) >= 0.3) and (same >= 4)
    res.append((c, rs, med, same, ok))

res.sort(key=lambda x: -abs(x[2]))
n_ok = 0
for c, rs, med, same, ok in res:
    n_ok += 1 if ok else 0
    P('%-24s %8.2f %8.2f %8.2f %8.2f %8.2f %8.2f   %s' % (
        c[:24], rs['c17'], rs['c18'], rs['c19'], rs['c20'], rs['c21'], med,
        '*** 通过 ***' if ok else ''))
P('')
P('[4] 汇总')
P('  检验特征数 = %d   通过判据 = %d' % (len(res), n_ok))
P('  |r中位| 最大值 = %.3f (特征: %s)' % (abs(res[0][2]), res[0][0]) if res else '')
P('')
if n_ok == 0:
    P('  结论: 未发现任何通道存在跨周期一致的单调退化趋势。')
    P('        与 T2525 (设备2496) 的结论一致 —— 现有传感器无法反映刀具渐进磨损。')
else:
    P('  结论: 存在候选信号，需进一步验证。')
P('')
P('[5] 说明与局限')
P('  - 完整周期仅 %d 个，样本偏少；换刀时刻为人工记录，周期边界存在小时级误差' % F.cycle.nunique())
P('  - 相对倍数以各周期前 %d 次调用的均值为基准' % BASE_N)
P('  - c17/c19 为混合加工程序周期，其基线可能不稳')

F.to_csv(OUT_CSV, index=False, encoding='utf-8-sig')
open(OUT_TXT, 'w', encoding='utf-8').write('\n'.join(L))
print('written ->', OUT_TXT)
print('         ->', OUT_CSV)
