# -*- coding: utf-8 -*-
"""s07_chipping.py — P0-06 崩刃 A：数据清单 + 离线候选区间 + 换刀复位检验

严守三条
  1) 不使用「真实崩刃时刻」——数据里没有；只给**候选区间**。
  2) 阈值只用「每把刀前 20% 段」定（假定健康），再应用到后半段；
     同时报该健康段自身的误报率，避免"先看标签再挑点"。
  3) 不报「1 秒检出率」——离线回放测不到端到端延迟。

检测量（三条通道并列，不挑最好的那一条当结论）
  med  = 逐件 40 点曲线中位（与线上 load_med 同量纲）
  peak = 逐件 40 点曲线最大值（崩刃更可能表现为尖峰）
  cv   = 逐件 40 点曲线标准差 / 中位（波动度）

噪声尺度（第一版翻车的地方）
  用 median(|v-base|)*1.4826 会在平滑序列上趋近 0 → z 爆炸 → 健康段 80% 误报。
  改用**一阶差分的 MAD** 作为创新噪声估计，并设绝对下限 1%（已知单件噪声约 2%）。

输出（06_崩刃A专项/）
  chipping_data_inventory.csv / chipping_candidates.csv / chipping_reset_check.csv
  chipping_cases/*.png
"""
import os, sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import wsroot as _wsroot          # G2：不再数 '..'，按工作区特征向上定位
ROOT = _wsroot.find(_os.path.dirname(_os.path.abspath(__file__)))
RAW = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv')
AUD = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '03_原始数据与标签', 'life_audit_4152.csv'))
OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '06_崩刃A专项'))
CASES = os.path.join(OUT, 'chipping_cases')
TL = os.path.join(ROOT, '2ndPrediction', 'tool_log')

K, HEALTH_FRAC, CUSUM_K, CUSUM_H, PERSIST = 20, 0.25, 0.5, 6.0, 3
NOISE_FLOOR = 0.01          # 相对噪声下限 1%
sys.path.insert(0, TL)
import simulator  # noqa: E402

# ---------------- 逐件统计（从 raw 重算，只保留曲线统计量）
cols = ['_time', 'life_cycle', 'L_allnum', 'L_abso_x', 'L_abso_z', 'L_spindle_load']
d = pd.read_csv(RAW, usecols=cols, encoding='utf-8-sig')
for c in cols[3:]:
    d[c] = pd.to_numeric(d[c], errors='coerce')
d = d.dropna(subset=['life_cycle', 'L_allnum'])

recs = []
for cid, g in d.groupby('life_cycle'):
    g = g.sort_values('_time')
    for an, q in g.groupby('L_allnum'):
        rows = [{'x_pos': r[0], 'z_pos': r[1], 'spindle_load': r[2]}
                for r in q[['L_abso_x', 'L_abso_z', 'L_spindle_load']].itertuples(index=False, name=None)]
        lm, npt, curve = simulator.align_piece(rows)
        if lm is None:
            continue
        cvals = np.asarray(curve, float)
        recs.append(dict(LifeCycle=int(cid), AllNum=int(an), Med=lm, Npt=npt,
                         Peak=float(np.nanmax(cvals)),
                         Cv=float(np.nanstd(cvals, ddof=1) / lm) if lm else np.nan))
PS = pd.DataFrame(recs).sort_values(['LifeCycle', 'AllNum'])
print('[pieces] %d 件（20 周期）' % len(PS))

aud = pd.read_csv(AUD, encoding='utf-8-sig')
lab = {int(str(r.PhysicalLifeId).split('-c')[-1]): (r.NormLabel, r.ChangeReasonRaw) for r in aud.itertuples()}


def cusum(v, nh, chan):
    """返回 dict(detected, i0, i1, mag, z, base, scale)"""
    v = np.asarray(v, float)
    base = float(np.median(v[:nh]))
    if chan == 'cv':
        scale = max(1.4826 * np.median(np.abs(np.diff(v[:nh]))) / np.sqrt(2), 0.002)
    else:
        scale = max(1.4826 * np.median(np.abs(np.diff(v[:nh]))) / np.sqrt(2), NOISE_FLOOR * abs(base))
    z = (v - base) / scale
    k, h = CUSUM_K, CUSUM_H
    sp = np.zeros(len(z)); sn = np.zeros(len(z))
    for i in range(1, len(z)):
        sp[i] = max(0.0, sp[i - 1] + z[i] - k)
        sn[i] = max(0.0, sn[i - 1] - z[i] - k)
    alarm = (sp > h) | (sn > h)
    if not alarm.any():
        return dict(detected=False, i0=None, i1=None, mag=float(np.max(np.abs(z))), z=z, base=base, scale=scale)
    i0 = int(np.argmax(alarm))
    i1 = i0
    while i1 + 1 < len(z) and (sp[i1 + 1] > 0 or sn[i1 + 1] > 0):
        i1 += 1
    det = (i1 - i0 + 1) >= PERSIST
    return dict(detected=det, i0=i0 if det else None, i1=i1 if det else None,
                mag=float(np.max(np.abs(z[i0:i1 + 1]))), z=z, base=base, scale=scale)


TMAX = pd.to_datetime(aud.EndTime).max()
CH = ['med', 'peak', 'cv']
inv, cand, curves = [], [], {}
for cid in sorted(PS.LifeCycle.unique()):
    g = PS[PS.LifeCycle == cid]
    curves[cid] = {ch: g[{'med': 'Med', 'peak': 'Peak', 'cv': 'Cv'}[ch]].to_numpy(float) for ch in CH}
for _, a in aud.iterrows():
    c = int(str(a.PhysicalLifeId).split('-c')[-1])
    n = len(curves.get(c, {}).get('med', []))
    inv.append(dict(PhysicalLifeId=a.PhysicalLifeId, LifeCycle=c, NormLabel=a.NormLabel,
                    ChangeReasonRaw=a.ChangeReasonRaw, StartTime=a.StartTime, EndTime=a.EndTime,
                    ObservedPieces=a.ObservedPieceCount, UsablePieces=n,
                    DaoShou=(pd.Series([a.ChangeReasonRaw]).str.extract(r'刀[寿寿]\s*(\d+)')[0].iloc[0]
                             if pd.notna(a.ChangeReasonRaw) else ''),
                    DataQuality=a.DataQualityGrade,
                    ReplacedBeforeReadout=('YES' if pd.Timestamp(a.EndTime) >= TMAX - pd.Timedelta(hours=1) else 'NO'),
                    Note=('刀尖破损' if '破损' in str(a.ChangeReasonRaw) else
                          ('刀尖磨损' if '磨损' in str(a.ChangeReasonRaw) else '其他'))))

row = {}
for c, chs in curves.items():
    n = len(chs['med'])
    r = dict(PhysicalLifeId='4152-L-T1111-c%d' % c, LifeCycle=c, N=n,
             NormLabel=lab.get(c, ('', ''))[0])
    if n < 25:
        r.update(Detected='NA(件数不足)')
        row[c] = r
        continue
    nh = max(K, int(n * HEALTH_FRAC))
    r['HealthRefPieces'] = nh
    for ch in CH:
        res = cusum(chs[ch], nh, ch)
        r['%s_Detected' % ch] = 'YES' if res['detected'] else 'NO'
        r['%s_MagZ' % ch] = round(res['mag'], 2)
        r['%s_Start' % ch] = (res['i0'] + 1) if res['detected'] else None
        r['%s_End' % ch] = (res['i1'] + 1) if res['detected'] else None
        r['%s_StartPct' % ch] = round(100 * res['i0'] / n, 1) if res['detected'] else None
        r['%s_Persist' % ch] = (res['i1'] - res['i0'] + 1) if res['detected'] else None
        # 健康段自体误报：把阈值前移，只看前半段
        h2 = max(10, nh // 2)
        fa = cusum(chs[ch][:nh], h2, ch)['detected']
        r['%s_HealthFA' % ch] = 'YES' if fa else 'NO'
    row[c] = r
C = pd.DataFrame(list(row.values())).sort_values('LifeCycle')

# ---------------- 复位检验
reset = []
cs = sorted(curves)
for i in range(len(cs) - 1):
    a, b = cs[i], cs[i + 1]
    la, lb = lab.get(a, ('', ''))[0], lab.get(b, ('', ''))[0]
    va, vb = curves[a]['med'], curves[b]['med']
    reset.append(dict(FromLife='c%d' % a, ToLife='c%d' % b,
                      FromLabel=la, ToLabel=lb,
                      Last5Med=round(float(np.median(va[-5:])), 1),
                      NextFirst5Med=round(float(np.median(vb[:5])), 1),
                      Drop_pct=round(100 * (np.median(va[-5:]) - np.median(vb[:5])) / np.median(va[-5:]), 2),
                      Last5Peak=round(float(np.median(curves[a]['peak'][-5:])), 1),
                      NextFirst5Peak=round(float(np.median(curves[b]['peak'][:5])), 1)))
RES = pd.DataFrame(reset)

os.makedirs(CASES, exist_ok=True)
pd.DataFrame(inv).to_csv(os.path.join(OUT, 'chipping_data_inventory.csv'), index=False, encoding='utf-8-sig')
C.to_csv(os.path.join(OUT, 'chipping_candidates.csv'), index=False, encoding='utf-8-sig')
RES.to_csv(os.path.join(OUT, 'chipping_reset_check.csv'), index=False, encoding='utf-8-sig')

bd = C[C.NormLabel == 'CHIPPING_FAILURE']
fig, axes = plt.subplots(5, 3, figsize=(16, 15))
for ax, (_, r) in zip(axes.ravel(), bd.iterrows()):
    c = int(r.LifeCycle)
    v = curves[c]['med']; hi = v / np.median(v[:K])
    ax.plot(np.arange(1, len(hi) + 1), hi, lw=0.9, color='#2b6cb0', label='med')
    ax2 = ax.twinx()
    pk = curves[c]['peak']; ax2.plot(np.arange(1, len(pk) + 1), pk / np.median(pk[:K]), lw=0.5, color='#dd6b20', alpha=0.6)
    ax2.set_yticks([])
    ax.axhline(1.0, color='gray', lw=0.6, ls='--')
    if r.get('med_Detected') == 'YES':
        ax.axvspan(r['med_Start'], r['med_End'], color='#c53030', alpha=0.22)
    ttl = '%s n=%d' % (r.PhysicalLifeId[-4:], r.N)
    if 'med_StartPct' in r and pd.notna(r.get('med_StartPct')):
        ttl += '  med@%d%% z=%.1f' % (r['med_StartPct'], r['med_MagZ'])
    ax.set_title(ttl, fontsize=9); ax.tick_params(labelsize=7)
fig.suptitle('Chipping-flagged lives: HI(med, blue) + peak(orange); red = CUSUM candidate window', fontsize=12)
fig.tight_layout(rect=[0, 0, 1, 0.97])
fig.savefig(os.path.join(CASES, 'all_events.png'), dpi=130)
plt.close(fig)

# ---------------- 逐事件单图（用修好的检测器重画，覆盖第一版的旧图）
for _, r in C.iterrows():
    c = int(r.LifeCycle)
    if c not in curves:
        continue
    v = curves[c]['med']; hi = v / np.median(v[:K])
    pk = curves[c]['peak']; hp = pk / np.median(pk[:K])
    fig, ax = plt.subplots(figsize=(8.5, 3.2))
    ax.plot(np.arange(1, len(hi) + 1), hi, lw=1.0, color='#2b6cb0', label='med (normalized)')
    ax.plot(np.arange(1, len(hp) + 1), hp, lw=0.7, color='#dd6b20', alpha=0.65, label='peak (normalized)')
    ax.axhline(1.0, color='gray', lw=0.6, ls='--')
    if r.get('med_Detected') == 'YES':
        ax.axvspan(r['med_Start'], r['med_End'], color='#c53030', alpha=0.20, label='med candidate')
    if r.get('peak_Detected') == 'YES':
        ax.axvspan(r['peak_Start'], r['peak_End'], color='#805ad5', alpha=0.12, label='peak candidate')
    ax.set_xlabel('piece index'); ax.set_ylabel('normalized load')
    ax.set_title('%s  label=%s  n=%d  (no true break time available)' % (r.PhysicalLifeId, r.NormLabel, r.N))
    ax.legend(fontsize=7, loc='upper left')
    fig.tight_layout()
    fig.savefig(os.path.join(CASES, 'case_c%d.png' % c), dpi=130)
    plt.close(fig)

print('\n[out] chipping_data_inventory.csv %d × %d' % (pd.DataFrame(inv).shape))
print('[out] chipping_candidates.csv     %d × %d' % C.shape)
print('[out] chipping_reset_check.csv    %d × %d' % RES.shape)
print('\n=== 破损周期：三通道并列 ===')
cols_show = ['PhysicalLifeId', 'N', 'med_Detected', 'med_StartPct', 'med_MagZ', 'med_Persist',
             'peak_Detected', 'peak_StartPct', 'peak_MagZ', 'cv_Detected', 'cv_MagZ']
print(bd[[c for c in cols_show if c in bd.columns]].to_string(index=False))
print('\n=== 对照（非破损周期）===')
nd = C[C.NormLabel != 'CHIPPING_FAILURE']
print(nd[[c for c in ['PhysicalLifeId', 'N', 'NormLabel', 'med_Detected', 'med_MagZ', 'peak_Detected', 'peak_MagZ'] if c in nd.columns]].to_string(index=False))
print('\n=== 检出率 / 健康段误报率 ===')
for ch in CH:
    if ch + '_Detected' not in bd:
        continue
    d1 = (bd[ch + '_Detected'] == 'YES').sum(); n1 = bd[ch + '_Detected'].isin(['YES', 'NO']).sum()
    f1 = (bd[ch + '_HealthFA'] == 'YES').sum()
    print('%-5s 破损检出 %2d/%2d = %5.1f%%   健康段误报 %2d/%2d = %5.1f%%'
          % (ch, d1, n1, 100 * d1 / max(n1, 1), f1, n1, 100 * f1 / max(n1, 1)))
print('\n=== 换刀前后负载（med / peak）===')
print(RES.to_string(index=False))
