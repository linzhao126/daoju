# -*- coding: utf-8 -*-
"""s60_channel_scan.py —— 把所有还活着的软件通道，在"瞬态"维度上一次扫完

为什么有这个脚本
----------------
2026-10-29 现场反馈：**DTRQ / TCMD 读不出来**（机器上取不到）。那么"读高分辨率扭矩"这条路封了。
问题变成：**还剩哪个通道可能在 1 秒级看见崩刃？**

`66-00` 只测了 `L_spindle_load` 一个通道。但同一份原始数据里还有 6 个伺服负载通道没测过：
    L_X / L_Y / L_Z / L_A / L_B / L_C / L_E_servo_load
其中 A 只有 2 个取值、B 恒 0（等于没有），实际可用的候选是 **X / Z / C / E**。

本脚本用**与 66-00 完全相同的判据**（同一套 2 秒窗 cv、同一批 15 破损 + 2 磨损对照、
同一个 AUC 与峰值倍数），把通道 × 采样率扫一遍，输出一张"还有没有希望"的表。

判据（与 66-00 一致，写死）
--------------------------
  有效通道 = 破损 AUC 中位 ≥ 0.75 **且** 磨损对照 AUC ≤ 0.60
  若所有通道都不满足 → 软件通道（现有字段）全部关闭，只剩 ①厂商标定报警 ②振动
"""
import os
import sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from s59_bandwidth import one_cycle, auc, CHIP, WEAR   # 复用同一套统计，避免两套口径

BASE = os.path.abspath(os.path.join(HERE, '..'))
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import wsroot as _wsroot          # G2：不再数 '..'，按工作区特征向上定位
ROOT = _wsroot.find(_os.path.dirname(_os.path.abspath(__file__)))
OUT = os.path.join(BASE, '附件_证据', '67_通道扫描')
os.makedirs(OUT, exist_ok=True)

OLD = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv')
CHANNELS = ['L_spindle_load', 'L_X_servo_load', 'L_Z_servo_load',
            'L_C_servo_load', 'L_E_servo_load', 'L_Y_servo_load']
DELTAS = [0.032, 0.128]
CYCLES = CHIP + WEAR
LOG = []


def P(*a):
    s = ' '.join(str(x) for x in a)
    print(s)
    LOG.append(s)


def load_all():
    use = ['_time', 'life_cycle', 'L_spindle_speed'] + CHANNELS
    frames = []
    for ch in pd.read_csv(OLD, usecols=use, chunksize=400000,
                          encoding='utf-8-sig', parse_dates=['_time']):
        ch = ch[ch['life_cycle'].isin(CYCLES)]
        if len(ch):
            frames.append(ch)
    d = pd.concat(frames, ignore_index=True)
    d['L_spindle_speed'] = pd.to_numeric(d['L_spindle_speed'], errors='coerce')
    for c in CHANNELS:
        d[c] = pd.to_numeric(d[c], errors='coerce')
    # 只保留"主轴在转"的行；各通道各自再要求 >0
    d = d[d['L_spindle_speed'] > 0]
    return d


if __name__ == '__main__':
    P('=' * 96)
    P('通道扫描：现有 6 个字段里，还有没有哪个能在 1 秒级看见崩刃')
    P('=' * 96)
    d = load_all()
    P('主轴在转的行 %d；周期 %d 个' % (len(d), d.life_cycle.nunique()))
    P('通道取值概况：')
    for c in CHANNELS:
        v = d[c]
        P('  %-18s 非空%.0f%%  唯一值%5d  中位%8.1f' % (c, 100 * v.notna().mean(), v.nunique(), v.median()))

    rows = []
    for c in CHANNELS:
        sub = d[d[c] > 0]
        for delta in DELTAS:
            for cyc, g in sub.groupby('life_cycle'):
                g = g.sort_values('_time')
                r = one_cycle(g, delta, g['_time'].max(), col=c)
                if r is None:
                    continue
                rows.append(dict(Channel=c, Delta_ms=int(delta * 1000), Cycle=int(cyc),
                                 类型='破损' if int(cyc) in CHIP else '磨损对照', **r))
    R = pd.DataFrame(rows)
    R.to_csv(os.path.join(OUT, 'channel_scan_by_cycle.csv'), index=False, encoding='utf-8-sig')

    agg = []
    for c in CHANNELS:
        for delta in DELTAS:
            s = R[(R.Channel == c) & (R.Delta_ms == int(delta * 1000))]
            chip = s[s['类型'] == '破损']['auc']
            wear = s[s['类型'] == '磨损对照']['auc']
            sp = s[s['类型'] == '破损']['spike_ratio']
            sw = s[s['类型'] == '磨损对照']['spike_ratio']
            ok = (len(chip) and len(wear) and chip.median() >= 0.75 and wear.median() <= 0.60)
            agg.append(dict(Channel=c, Delta_ms=int(delta * 1000),
                            破损_n=len(chip), 磨损_n=len(wear),
                            破损_AUC中位=round(float(chip.median()), 3) if len(chip) else np.nan,
                            破损_AUC最大=round(float(chip.max()), 3) if len(chip) else np.nan,
                            磨损_AUC中位=round(float(wear.median()), 3) if len(wear) else np.nan,
                            破损_峰值倍数=round(float(sp.median()), 2) if len(sp) else np.nan,
                            磨损_峰值倍数=round(float(sw.median()), 2) if len(sw) else np.nan,
                            判据_通过=bool(ok)))
    A = pd.DataFrame(agg)
    A.to_csv(os.path.join(OUT, 'channel_scan_summary.csv'), index=False, encoding='utf-8-sig')
    P('\n[通道 × 采样率] 判据：破损 AUC≥0.75 且 磨损 AUC≤0.60')
    P(A.to_string(index=False))

    passed = A[A['判据_通过']]
    P('\n' + '=' * 96)
    if len(passed):
        P('✅ 有通道通过：%s' % ', '.join('%s@%dms' % (r.Channel, r.Delta_ms) for r in passed.itertuples()))
        P('   → 下一步：用该通道做 1 秒判决原型（不需要新硬件）。')
    else:
        P('❌ **没有一个通道通过**。现有 6 个字段（主轴 + 5 个伺服负载）在 2 秒窗上都无特异性。')
        P('   → 软件通道（现有字段）到此为止。剩下两条路，都需外部条件：')
        P('     ① 厂商标定报警：FANUC 30i/31i-B 内置 Cutting Load Monitor（负载表 1~4 ms 采样，')
        P('        110~130% 警告 / 140~200% 报警），走 PMC 报警位 → 不需自己读原始值；需 option，问机床厂/供应商。')
        P('     ② 振动/声发射（A3）：D-06 允许提方案，采购另报批。')
    P('\n注：本扫描用的是**旧批 labeled**（c25~c41，含 15 破损 + 2 磨损），')
    P('    Δ 只取 32/128 ms 两档（66-00 已证明采样率不是瓶颈，多档无信息增量）。')

    with open(os.path.join(OUT, 's60_console.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG))
    P('\nDONE →', OUT)
