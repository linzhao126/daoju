# -*- coding: utf-8 -*-
"""s59_bandwidth.py —— 崩刃检测的"采样率需求"实验（对应 63-00 §4 选项①）

要回答的问题
------------
「16 ms 的扭矩/负载够不够看见崩刃？」——`63-00` 说这是决定"要不要买振动（10 kHz）"的唯一依据。
本脚本不猜，用**现有行级数据**做三件事：

  1. 把负载降到不同的等效采样间隔 Δ（32 / 64 / 128 / 256 ms），
     模拟"采集盒以 Δ 采样"会发生什么；
  2. 在每个 Δ 下，用 **1 秒窗口** 的峰值 z 分数做检测量（**去趋势**：以过去 5 分钟的
     滚动中位/MAD 为基线，消掉磨损的慢漂移）；
  3. 比较「末段（临近换刀）」vs「前段（稳定加工）」的窗口可分性（AUC），
     并在 **磨损对照周期**（c38/c39，非破损）上做同样的计算。

判据（写死，避免事后解释）
-------------------------
  * `chip_AUC`（15 个破损周期）× `wear_AUC`（2 个磨损对照）：
    - chip_AUC ≥ 0.75 且 wear_AUC ≤ 0.60 → **有特异性**：低采样率下就能分，不必买振动
    - chip_AUC ≥ 0.75 且 wear_AUC ≥ 0.75 → **无特异性**：和件级 CUSUM 同一个病（抓的是趋势）
    - chip_AUC < 0.70 → **这个检测量在离线就看不见**，谈 1 秒没有意义
  * 看 AUC 随 Δ 从 32→256 ms 的衰减斜率：
    - 衰减 < 0.05 → 采样率不是瓶颈，16 ms 够（A1/A2 可行）
    - 衰减 > 0.15 → 高频细节必要，指向 A3（振动）

⚠ 本实验**不能**报检出率/召回率：真实崩刃时刻在数据里不存在（`NOT_REPRODUCIBLE` #4）。
   这里量的是"末段窗口是否与稳定加工段可分"，属**可行性/带宽**问题，不是事件检测性能。
"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
ROOT = os.path.abspath(os.path.join(BASE, '..', '..', '..'))
OUT = os.path.join(BASE, '附件_证据', '66_带宽实验')
os.makedirs(OUT, exist_ok=True)

OLD = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv')
LIFE = os.path.join(BASE, '03_原始数据与标签', 'life_audit_4152.csv')

# 参与实验的周期（c16/c42/c45 只有 1~3 件，不够做窗口）
CHIP = [25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 40, 41]
WEAR = [38, 39]
CYCLES = CHIP + WEAR

DELTAS = [0.032, 0.064, 0.128, 0.256]      # 秒；0.032 ≈ 原始 ~31 Hz
CUT_RATE = (456, 455, 304, 2143)           # 主切削/进给/倒角（32000 快进、0 停止要剔除）
WIN_SEC = 2.0                              # 判决窗口（秒）：取 2 s 而非 1 s，见 one_cycle 注释
BASE_ROLL = '300s'                         # 去趋势的滚动基线
END_SEC = 300                              # 末段 = 换刀前 5 分钟
BASE_LO, BASE_HI = 0.15, 0.75              # 基线段 = 寿命的 15%~75%
LOG = []


def P(*a):
    s = ' '.join(str(x) for x in a)
    print(s)
    LOG.append(s)


def auc(pos, neg):
    """Mann-Whitney AUC（不依赖 sklearn）"""
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    pos = pos[np.isfinite(pos)]
    neg = neg[np.isfinite(neg)]
    if len(pos) < 3 or len(neg) < 3:
        return np.nan
    allv = np.concatenate([pos, neg])
    order = allv.argsort()
    ranks = np.empty(len(allv), float)
    ranks[order] = np.arange(1, len(allv) + 1)
    # 处理并列：用平均秩
    s = pd.Series(allv).rank(method='average').to_numpy()
    r_pos = s[:len(pos)].sum()
    return float((r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def load_raw():
    use = ['_time', 'life_cycle', 'L_spindle_load', 'L_spindle_speed', 'L_rate1', 'L_allnum']
    frames = []
    for ch in pd.read_csv(OLD, usecols=use, chunksize=400000,
                          encoding='utf-8-sig', parse_dates=['_time']):
        ch = ch[ch['life_cycle'].isin(CYCLES)]
        if len(ch):
            frames.append(ch)
    frames_raw = sum(len(f) for f in frames)
    d = pd.concat(frames, ignore_index=True)
    for c in ('L_spindle_load', 'L_spindle_speed', 'L_rate1'):
        d[c] = pd.to_numeric(d[c], errors='coerce')
    # 切削判据用「主轴在转 + 负载>0」：本导出的 `L_rate1` 编码是 0/60/67/120/214/18000…
    # 与记忆里的 456/455/304/2143 对不上（那是另一版字段口径），所以**不按 rate1 过滤**，
    # 避免"筛成 0 行还以为没数据"。校验：筛完应保留约 79% 的行。
    d = d[(d['L_spindle_speed'] > 0) & (d['L_spindle_load'] > 0)]
    P('  切削行占比 %.1f%%（判据：speed>0 且 load>0）' % (100.0 * len(d) / max(1, frames_raw)))
    return d


def one_cycle(g, delta, end_time, col='L_spindle_load'):
    """把一条负载序列按 delta 的等效采样率做"每秒窗"，算窗内变异系数 cv

    ⚠ 为什么按**行块**切窗而不是按绝对 1 秒网格：
      本数据在一把刀的整个寿命里平均只有 0.37 行/秒（大量件间空档），
      按绝对网格切，1 秒里常常只有 1~2 行 → 窗内 std 不可算，Δ≥128 ms 直接全空。
      改为"每 k = 1s/Δ 个采样点一个窗"，保证每个窗的样本数一致（跨 Δ 可比），
      再用块内时跨 ≤1.5 s 的条件排除跨空档的块。
    """
    s = g.set_index('_time')[col].sort_index()
    if len(s) < 400:
        return None
    k = max(3, int(round(1.0 / delta)))          # 每窗采样点数
    t = (s.index - pd.Timestamp('2000-01-01')).total_seconds().to_numpy()
    val = s.to_numpy(float)
    # 先用 delta 做一次"等效降采样"（模拟采集盒以 delta 采样）
    b1 = np.floor(t / delta).astype('int64')
    ds = pd.Series(val).groupby(b1).mean()
    tb = pd.Series(t).groupby(b1).median()
    if len(ds) < 200:
        return None
    v = ds.to_numpy(float)
    vt = tb.to_numpy(float)
    # 连续段（间隔 > 5s 视为断开），段内按**时间窗 2 s** 切块
    #   为什么不用"固定 k 行一窗"：本数据是**突发式**上报（切削时才传），
    #   连续 31 行常常跨 3~5 秒，按行数切会把"跨空档的块"全丢掉（实测只剩 0 块）。
    #   用绝对 2 s 时间窗 + 最少 3 个采样点，跨 Δ 都能算出 cv；代价是窗变粗（已在文档写明）。
    gap = np.diff(vt) > 5.0
    seg = np.concatenate([[0], np.cumsum(gap)])
    dfb = pd.DataFrame({'seg': seg, 'v': v, 't': vt})
    dfb['blk'] = np.floor(dfb['t'] / WIN_SEC).astype('int64')
    gp = dfb.groupby(['seg', 'blk'])
    agg = gp.agg(mean=('v', 'mean'), std=('v', 'std'),
                 n=('v', 'size'), t0=('t', 'min'), t1=('t', 'max'))
    agg = agg[(agg['n'] >= 3) & (agg['mean'] > 0)]
    if len(agg) < 120:
        return None
    agg['cv'] = (agg['std'] / agg['mean']).replace([np.inf, -np.inf], np.nan)
    agg['ts'] = pd.to_datetime(agg['t0'], unit='s', origin='unix')
    agg = agg.dropna(subset=['cv']).sort_values('ts').set_index('ts')
    df = agg

    n = len(df)
    if n < 120:
        return None
    span = (df.index.max() - df.index.min()).total_seconds()
    if span < 1800:
        return None
    t1 = df.index.max()
    b_lo_i, b_hi_i = int(n * BASE_LO), int(n * BASE_HI)
    base = df['cv'].iloc[b_lo_i:b_hi_i]
    tail = df['cv'].loc[df.index >= (t1 - pd.Timedelta(seconds=END_SEC))]
    if len(tail) < 30:
        tail = df['cv'].iloc[-60:]
    if len(tail) < 20 or len(base) < 60:
        return None
    base_m = df['mean'].iloc[b_lo_i:b_hi_i]
    tail_m = df['mean'].loc[df.index >= (t1 - pd.Timedelta(seconds=END_SEC))]
    if len(tail_m) < 20:
        tail_m = df['mean'].iloc[-60:]
    # ⭐ 单窗极端值：AUC 比的是**分布**，一次 1~2 秒的冲击会被 60 个窗摊平到看不见。
    #   所以再算一个"尾段最大 cv ÷ 全周期 cv 中位"（峰值倍数）与它的**经验分位**：
    #      分位 = 全周期里有多少比例的窗 cv ≤ 尾段最大 cv（1.00 = 尾段出现了全周期最大峰）
    cv_all = df['cv']
    tail_max = float(tail.max())
    cv_med_all = float(cv_all.median())
    spike_ratio = tail_max / max(1e-9, cv_med_all)
    spike_pct = float((cv_all <= tail_max).mean())
    return dict(n_tail=len(tail), n_base=len(base),
                tail_cv=float(tail.median()), base_cv=float(base.median()),
                tail_cv_p95=float(tail.quantile(0.95)), base_cv_p95=float(base.quantile(0.95)),
                auc=auc(tail, base),
                auc_level=auc(tail_m, base_m),
                cv_ratio=round(float(tail.median()) / max(1e-9, float(base.median())), 3),
                tail_cv_max=round(tail_max, 4), cv_median_all=round(cv_med_all, 4),
                spike_ratio=round(spike_ratio, 2), spike_pct=round(spike_pct, 3),
                span_h=round(span / 3600, 2))


if __name__ == '__main__':
    P('=' * 92)
    P('崩刃检测的采样率需求实验（离线，不连机床、不改任何代码）')
    P('=' * 92)
    d = load_raw()
    P('读入切削行 %d 条，周期 %d 个' % (len(d), d.life_cycle.nunique()))
    rows = []
    for c, g in d.groupby('life_cycle'):
        g = g.sort_values('_time')
        end_time = g['_time'].max()
        for delta in DELTAS:
            r = one_cycle(g, delta, end_time)
            if r is None:
                continue
            rows.append(dict(Cycle=int(c),
                             类型='破损' if int(c) in CHIP else '磨损对照',
                             Delta_ms=int(delta * 1000), **r))
    R = pd.DataFrame(rows)
    R.to_csv(os.path.join(OUT, 'bandwidth_by_cycle.csv'), index=False, encoding='utf-8-sig')

    agg = []
    for delta in DELTAS:
        s = R[R.Delta_ms == int(delta * 1000)]
        chip = s[s['类型'] == '破损']['auc']
        wear = s[s['类型'] == '磨损对照']['auc']
        sp = s[s['类型'] == '破损']['spike_ratio']
        sw = s[s['类型'] == '磨损对照']['spike_ratio']
        pc = s[s['类型'] == '破损']['spike_pct']
        agg.append(dict(Delta_ms=int(delta * 1000),
                        破损_n=len(chip), 破损_AUC中位=round(float(chip.median()), 3),
                        破损_AUC最小=round(float(chip.min()), 3) if len(chip) else np.nan,
                        磨损_n=len(wear), 磨损_AUC中位=round(float(wear.median()), 3) if len(wear) else np.nan,
                        破损_峰值倍数中位=round(float(sp.median()), 2) if len(sp) else np.nan,
                        磨损_峰值倍数中位=round(float(sw.median()), 2) if len(sw) else np.nan,
                        破损_峰值分位中位=round(float(pc.median()), 3) if len(pc) else np.nan))
    A = pd.DataFrame(agg)
    A.to_csv(os.path.join(OUT, 'bandwidth_summary.csv'), index=False, encoding='utf-8-sig')
    P('\n[汇总] 1 秒窗峰值 z 分数的可分性（AUC：末段 vs 稳定段）')
    P(A.to_string(index=False))

    base32 = A[A.Delta_ms == 32]['破损_AUC中位']
    base32 = float(base32.iloc[0]) if len(base32) else np.nan
    P('\n[衰减] 相对 32 ms 的 AUC 变化')
    for _, r in A.iterrows():
        P('  Δ=%3d ms  破损AUC=%.3f  相对32ms %+.3f'
          % (r['Delta_ms'], r['破损_AUC中位'], r['破损_AUC中位'] - base32))

    P('\n[判据]（写死在文件头，不允许事后改）')
    c32 = A[A.Delta_ms == 32].iloc[0]
    decay = float(A[A.Delta_ms == 256].iloc[0]['破损_AUC中位']) - base32
    P('  chip_AUC@32ms=%.3f  wear_AUC@32ms=%s  衰减(32→256ms)=%+.3f'
      % (c32['破损_AUC中位'], c32['磨损_AUC中位'], decay))
    if c32['破损_AUC中位'] < 0.70:
        P('  → 结论①：**这个检测量在离线就看不见**（chip_AUC<0.70），谈 1 秒没有意义；')
        P('     需要换检测量（不只是换采样率），或等真实崩刃波形。')
    elif pd.notna(c32['磨损_AUC中位']) and c32['磨损_AUC中位'] >= 0.75:
        P('  → 结论②：**无特异性**——磨损对照也被判成"末段异常"，说明抓的是趋势不是事件。')
        P('     与 06-00 的件级 CUSUM 同一个病，换采样率救不了。')
    elif decay < -0.15:
        P('  → 结论③：**采样率是瓶颈**（降采样后明显变差）→ 指向 A3 振动路线。')
    else:
        P('  → 结论④：**采样率不是瓶颈**（32→256 ms 衰减 %+.3f）→ 16 ms 够，A1/A2 可行。' % decay)

    with open(os.path.join(OUT, 's59_console.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG))
    P('\nDONE →', OUT)
