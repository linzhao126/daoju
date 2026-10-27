# -*- coding: utf-8 -*-
"""
补充检验：末期突变型退化（相关系数抓不到的形态）
做法：每个周期取 前20% 调用 vs 后20% 调用，比较相对基线倍数
     跨周期做 Wilcoxon 符号秩 + 效应量
并出趋势图
"""
import pandas as pd, numpy as np
from scipy import stats
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

for f in ('Microsoft YaHei', 'SimHei', 'SimSun'):
    try:
        font_manager.findfont(f, fallback_to_default=False)
        plt.rcParams['font.sans-serif'] = [f]
        break
    except Exception:
        continue
plt.rcParams['axes.unicode_minus'] = False

F = pd.read_csv(r'C:/abc/workBoddyHome刀具/t1111_call_features.csv',
                parse_dates=['t0', 't1'])
CH = ['L_spindle_load', 'L_X_servo_load', 'L_Y_servo_load', 'L_Z_servo_load',
      'L_A_servo_load', 'L_B_servo_load', 'L_C_servo_load', 'L_E_servo_load']
FEATS = [c + '_mean' for c in CH] + [c + '_max' for c in CH] + \
        [c + '_std' for c in CH] + ['dur_s']

L = []
P = L.append
P('=== 补充检验：末期突变型退化 (前20% vs 后20%) ===')
P('')
P('说明: 相关系数只能抓单调上升。若刀具是「前期平稳、末期突变」，')
P('      Spearman r 会接近 0，但首尾对比会显著。此检验专门抓这种形态。')
P('')

rows = []
for c in FEATS:
    col = c + '_rel'
    if col not in F.columns:
        continue
    head_v, tail_v, cyc = [], [], []
    for cyc_name, g in F.groupby('cycle'):
        g = g.dropna(subset=[col]).sort_values('call_idx')
        n = len(g)
        if n < 15:
            continue
        k = max(3, int(n * 0.2))
        head_v.append(g[col].iloc[:k].median())
        tail_v.append(g[col].iloc[-k:].median())
        cyc.append(cyc_name)
    head_v = np.array(head_v); tail_v = np.array(tail_v)
    if len(head_v) < 3:
        continue
    diff = tail_v - head_v
    try:
        w, p = stats.wilcoxon(tail_v, head_v)
    except Exception:
        w, p = np.nan, np.nan
    d = diff.mean() / (diff.std(ddof=1) + 1e-9)      # Cohen's d (paired)
    same_sign = max(int((diff > 0).sum()), int((diff < 0).sum()))
    rows.append((c, head_v.mean(), tail_v.mean(), diff.mean(), d, p, same_sign, len(diff)))

rows.sort(key=lambda x: -abs(x[4]))
P('%-24s %9s %9s %9s %8s %9s %6s' % ('特征', '前段中位', '后段中位', '差值', "Cohen'd", 'p值', '同号'))
P('-' * 88)
for c, h, t, dd, d, p, ss, n in rows:
    P('%-24s %9.4f %9.4f %+9.4f %8.2f %9.3f %3d/%d' % (c[:24], h, t, dd, d, p, ss, n))
P('')
P('判据: |Cohen\'s d| >= 0.8 (大效应) 且 同号 >= 4/5 且 p < 0.10')
best = [r for r in rows if abs(r[4]) >= 0.8 and r[6] >= 4 and r[5] < 0.10]
P('通过判据的特征数 = %d' % len(best))
if best:
    for r in best:
        P('   *** %s  前段=%.4f  后段=%.4f  d=%.2f  p=%.3f' % (r[0], r[1], r[2], r[4], r[5]))
else:
    P('   未发现末期突变型退化信号。')
P('')

# ---------- 图 ----------
KEY = ['L_spindle_load_mean', 'L_spindle_load_max', 'L_C_servo_load_max',
       'L_X_servo_load_mean', 'L_Z_servo_load_mean', 'dur_s']
KEY = [k for k in KEY if k + '_rel' in F.columns]
fig, axes = plt.subplots(2, 3, figsize=(15, 8))
for ax, c in zip(axes.ravel(), KEY):
    col = c + '_rel'
    for cyc_name, g in F.groupby('cycle'):
        g = g.dropna(subset=[col]).sort_values('call_idx')
        if len(g) < 10:
            continue
        # 滑动平均，避免点太密看不清
        w = max(3, len(g) // 20)
        y = g[col].rolling(w, center=True, min_periods=1).mean()
        ax.plot(g.call_idx / g.n_call, y, alpha=0.75, lw=1.2, label=cyc_name)
    ax.axhline(1.0, color='gray', ls='--', lw=0.8)
    ax.set_title(c, fontsize=10)
    ax.set_xlabel('周期内进度')
    ax.set_ylabel('相对前5次倍数')
    ax.legend(fontsize=7, ncol=2)
plt.tight_layout()
PNG = r'C:/abc/workBoddyHome刀具/t1111_按调用次数_趋势图.png'
plt.savefig(PNG, dpi=110)
P('图已保存: %s' % PNG)
P('')
P('读图方法: 若刀在变钝，曲线应随进度稳定向上爬离 1.0 这条虚线。')
P('          若只是围绕 1.0 上下乱跳，说明传感器看不出刀的状态变化。')

open(r'C:/abc/workBoddyHome刀具/调用次数对齐_末期突变检验报告.txt', 'w',
     encoding='utf-8').write('\n'.join(L))
print('done')
