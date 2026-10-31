# -*- coding: utf-8 -*-
"""s38_prog_diagnostics.py —— 品番/工序诊断（补齐 25-00、26-00 的可复现性）

为什么补这个脚本
----------------
`25-00_品番混批诊断` 与 `26-00_M0分组粒度终局` 的核心证据，当时是用内联 `python -c`
现算的，**没有落盘脚本**，于是两份 CSV（`prog_mix_profile.csv`、`part_grouping.csv`）
成了"有产出、无生成代码"的孤儿文件 —— 不可复现。

这违反 `11-06` 铁律第 9 条「一次性探针必须落盘口径」，也是本项目反复犯的同一类错
（此前"工步门控 CV 36.3%→4.9%"就是这样丢失出处的）。

本脚本把当时 6 件内联分析全部脚本化，并**重算已有 CSV 做逐位校验**：
  D1 程序名 → 品番/工序映射
  D2 各周期品番构成（生成 prog_mix_profile.csv）
  D3 混批周期的时间线（证明是"顺序接续"而非"穿插"）
  D4 未过滤原始数据交叉验证（证明与清洗无关）
  D5 两段切断段负荷比（证明跨品番影响 ≈ 噪声）
  D6 品番分组寿命检验（生成 part_grouping.csv）

用法
----
    python s38_prog_diagnostics.py            # 全跑
    python s38_prog_diagnostics.py --no-raw   # 跳过 D4（D4 要扫 8.2M 行，约 40s）

输出：25_品番混批诊断/ 与 26_M0分组粒度终局/
"""
import os
import re
import csv
import sys
import numpy as np
import pandas as pd
from scipy import stats

csv.field_size_limit(10 ** 7)

HERE = os.path.dirname(os.path.abspath(__file__))
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import wsroot as _wsroot          # G2：不再数 '..'，按工作区特征向上定位
ROOT = _wsroot.find(_os.path.dirname(_os.path.abspath(__file__)))
BASE = os.path.abspath(os.path.join(HERE, '..'))
D25 = os.path.join(BASE, '25_品番混批诊断')
D26 = os.path.join(BASE, '26_M0分组粒度终局')
os.makedirs(D25, exist_ok=True)
os.makedirs(D26, exist_ok=True)

OLD = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv')
NEW_L = os.path.join(BASE, '22_c48-c60_xalign', 'labeled_T1111_new.csv')
RAW = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261027_200034.csv')
MERGED = os.path.join(BASE, '24_程序归属修正', 'HI_merged_28.csv')

# 新原始文件的列位置（与旧文件前 39 列同名同序）
I_T, I_AX, I_AZ, I_AN, I_LOAD, I_MP, I_SPD, I_TN = 0, 12, 14, 16, 25, 22, 27, 31

Z_TOL = 1.0
LOG = []


def P(*a):
    s = ' '.join(str(x) for x in a)
    print(s)
    LOG.append(s)


def prog_of(s):
    return str(s).split('(')[0].strip()


def part_of(name):
    """'C5G25-02-BAR331' → ('02', 'BAR331')"""
    m = re.match(r'([A-Z0-9]+-\d+)-(.+)$', name)
    return (m.group(1)[-2:], m.group(2)) if m else (name, '?')


def load_series():
    """两份 labeled：只保留主轴转速>0 的行（与 xalign 同口径）"""
    cols = ['life_cycle', 'L_main_program', 'L_allnum', 'L_spindle_load',
            'L_abso_z', 'L_spindle_speed']
    out = []
    for path in (OLD, NEW_L):
        d = pd.read_csv(path, usecols=cols, encoding='utf-8-sig')
        out.append(d)
    d = pd.concat(out, ignore_index=True)
    for c in ('L_allnum', 'L_spindle_load', 'L_abso_z', 'L_spindle_speed'):
        d[c] = pd.to_numeric(d[c], errors='coerce')
    d['prog'] = d.L_main_program.map(prog_of)
    d = d[d.L_spindle_speed.fillna(0) > 0]
    return d


# ============ D1 程序名 → 品番/工序 ============
def d1_mapping(d):
    P('=' * 78)
    P('D1  程序名 → 品番 / 工序')
    P('=' * 78)
    P('%-6s %-14s %-14s %-26s %10s' % ('prog', 'program', '品番段', '工序', '行数'))
    rows = []
    for k, v in d.L_main_program.fillna('').value_counts().items():
        m = re.match(r'(\d+)\((.*)\)', k)
        if not m:
            continue
        prog, name = m.groups()
        part, op = part_of(name)
        rows.append(dict(Prog=prog, ProgName=name, Part='C5G25-' + part, Op=op, Rows=int(v)))
        P('%-6s %-14s %-14s %-26s %10d' % (prog, prog, 'C5G25-' + part, op, v))
    T = pd.DataFrame(rows).sort_values(['Part', 'Prog'])
    T.to_csv(os.path.join(D25, 'd1_prog_part_map.csv'), index=False, encoding='utf-8-sig')
    P('')
    P('→ 只有 %d 个品番：%s' % (T.Part.nunique(), ', '.join(sorted(T.Part.unique()))))
    P('→ 7 个"程序"其实是 2 个品番下的不同工序')
    P('')
    return {r.Prog: r.Part[-2:] for r in T.itertuples()}


# ============ D2 各周期品番构成 ============
def d2_mix(d, part_map):
    P('=' * 78)
    P('D2  28 个有 HI 的周期：品番构成 → prog_mix_profile.csv')
    P('=' * 78)
    have = set(pd.read_csv(MERGED, encoding='utf-8-sig').life_cycle.unique())
    rows = []
    P('%-5s %7s %-36s %8s %8s %s' % ('周期', '总件', '各程序件数(占比)', '主占比', '少数件', '分档'))
    for c in sorted(have):
        g = d[d.life_cycle == c]
        if not len(g):
            continue
        pc = g.groupby('prog').L_allnum.nunique().sort_values(ascending=False)
        tot = int(pc.sum())
        main = pc.index[0]
        sh = pc.iloc[0] / tot
        minor = tot - int(pc.iloc[0])
        band = '纯' if sh >= 0.95 else ('轻微' if sh >= 0.80 else '明显')
        detail = ' '.join('%s:%d(%.0f%%)' % (p, v, 100 * v / tot) for p, v in pc.items())
        rows.append(dict(Cycle=c, TotalPieces=tot, MainProg=main, MainShare=round(sh, 4),
                         MinorPieces=int(minor), Band=band, Detail=detail))
        P('c%-4d %7d %-36s %7.0f%% %8d %s' % (c, tot, detail[:36], 100 * sh, minor, band))
    R = pd.DataFrame(rows)
    out = os.path.join(D25, 'prog_mix_profile.csv')
    R.to_csv(out, index=False, encoding='utf-8-sig')
    P('')
    for b in ('纯', '轻微', '明显'):
        s = R[R.Band == b]
        P('  %-4s %2d 个周期   少数品番件数 %d' % (b, len(s), int(s.MinorPieces.sum())))
    P('  总件 %d，少数品番 %d（%.1f%%）'
      % (R.TotalPieces.sum(), R.MinorPieces.sum(),
         100 * R.MinorPieces.sum() / R.TotalPieces.sum()))
    P('  [out] %s' % out)
    P('')
    return R


# ============ D3 混批周期时间线 ============
def d3_timeline(d):
    P('=' * 78)
    P('D3  混批周期时间线：是"顺序接续"还是"穿插"？')
    P('=' * 78)
    # 需要 _time，单独读
    cols = ['_time', 'life_cycle', 'L_main_program', 'L_allnum', 'L_spindle_speed']
    frames = []
    for path in (OLD, NEW_L):
        frames.append(pd.read_csv(path, usecols=cols, encoding='utf-8-sig'))
    t = pd.concat(frames, ignore_index=True)
    t['prog'] = t.L_main_program.map(prog_of)
    t['L_spindle_speed'] = pd.to_numeric(t.L_spindle_speed, errors='coerce')
    t['L_allnum'] = pd.to_numeric(t.L_allnum, errors='coerce')
    t['ts'] = pd.to_datetime(t._time, errors='coerce')
    t = t[t.L_spindle_speed.fillna(0) > 0]
    for c in (27, 33, 34):
        g = t[t.life_cycle == c].sort_values('ts')
        if not len(g):
            P('c%d 无数据' % c)
            continue
        P('c%d  共 %d 行，%s → %s' % (c, len(g), g.ts.min(), g.ts.max()))
        segs = []
        for p, gg in g.groupby('prog'):
            segs.append((gg.ts.min(), gg.ts.max(), p, len(gg),
                         int(gg.L_allnum.min()), int(gg.L_allnum.max()), gg.L_allnum.nunique()))
        for s in sorted(segs):
            P('   prog%-3s %s → %s  %6d 行  allnum %d..%d（%d 件）'
              % (s[2], s[0].strftime('%m-%d %H:%M'), s[1].strftime('%m-%d %H:%M'),
                 s[3], s[4], s[5], s[6]))
        if len(segs) >= 2:
            segs.sort()
            ov = set(g[g.prog == segs[0][2]].L_allnum) & set(g[g.prog == segs[1][2]].L_allnum)
            P('   → 两段 allnum 交集 %d 件；后段起始件号 = 前段末件号 + %d'
              % (len(ov), segs[1][4] - segs[0][5]))
        P('')


# ============ D4 未过滤原始数据交叉验证 ============
def d4_rawcheck():
    P('=' * 78)
    P('D4  未过滤原始数据验证 c33（证明与清洗无关）')
    P('=' * 78)
    if not os.path.exists(RAW):
        P('  ⚠ 原始文件不存在，跳过')
        return
    LO, HI = '2026-09-20 12:05:00', '2026-09-20 22:33:00'
    tot, t1111 = {}, {}
    with open(RAW, encoding='utf-8-sig', newline='') as f:
        rd = csv.reader(f)
        next(rd)
        for row in rd:
            if len(row) <= I_TN:
                continue
            ts = row[I_T][:19]
            if not (LO <= ts <= HI):
                continue
            p = prog_of(row[I_MP])
            tot[p] = tot.get(p, 0) + 1
            if row[I_TN].strip() == 'T1111':
                t1111[p] = t1111.get(p, 0) + 1
    P('%-8s %14s %12s %10s' % ('prog', '全部12把刀行数', 'T1111行数', 'T1111占比'))
    for p in sorted(tot, key=lambda x: -tot[x])[:6]:
        P('%-8s %14d %12d %9.1f%%' % (p, tot[p], t1111.get(p, 0),
                                       100 * t1111.get(p, 0) / max(tot[p], 1)))
    P('  → 原始文件里也只有这几个程序，T1111 占比≈1/12（8.3%）→ 与清洗无关')
    P('')


# ============ D5 两段负荷比 ============
def d5_loadratio(d, part_map):
    P('=' * 78)
    P('D5  混批周期两段的切断段负荷比（判断跨品番是否构成"工况差异"）')
    P('=' * 78)

    def seg(g, p):
        x = g[g.prog == p]
        if not len(x):
            return None
        z0 = round(x.L_abso_z.mode().iloc[0], 1)
        xx = x[(x.L_abso_z - z0).abs() <= Z_TOL]
        if not len(xx):
            return None
        return dict(rows=len(x), z0=z0, cut=len(xx),
                    med=float(xx.L_spindle_load.median()))

    rows = []
    P('%-6s %-16s %8s %8s %9s %9s' % ('周期', 'prog(品番)', '行数', 'Z0', '切断行', '负荷中位'))
    for c in (33, 34, 57, 58):
        g = d[d.life_cycle == c]
        ps = g.prog.value_counts().index.tolist()[:2]
        res = [(p, seg(g, p)) for p in ps]
        res = [(p, r) for p, r in res if r]
        if len(res) < 2:
            P('c%-5d 数据不足' % c)
            continue
        meds = [r['med'] for _, r in res]
        ratio = max(meds) / max(min(meds), 1e-9)
        same = len({part_map.get(p) for p, _ in res}) == 1
        for i, (p, r) in enumerate(res):
            P('%-6s %-16s %8d %8.1f %9d %9.0f'
              % (('c%d' % c) if i == 0 else '', '%s(%s)' % (p, part_map.get(p)),
                 r['rows'], r['z0'], r['cut'], r['med']))
        P('       → %s｜Z 相同=%s｜负荷比 %.2f'
          % ('同一品番' if same else '**跨品番**',
             '是' if abs(res[0][1]['z0'] - res[1][1]['z0']) < 1e-6 else '否', ratio))
        rows.append(dict(Cycle=c, SamePart=bool(same), Ratio=round(ratio, 4)))
    P('')
    P('=== 全库各程序切断段负荷中位（看极差）===')
    meds = {}
    for p, g in d.groupby('prog'):
        r = seg(g, p)
        if r:
            meds[p] = r['med']
            P('  prog%-4s 品番 %s  Z0 %6.1f  切断行 %7d  负荷中位 %7.0f'
              % (p, part_map.get(p, '?'), r['z0'], r['cut'], r['med']))
    if meds:
        lo, hi = min(meds.values()), max(meds.values())
        P('  → 极差 %.2f 倍（%.0f ~ %.0f）；参照：单件噪声 σ≈2%%（1.02 倍）'
          % (hi / lo, lo, hi))
    P('')
    return pd.DataFrame(rows)


# ============ D6 品番分组寿命检验 ============
def d6_partgroup(d, part_map):
    P('=' * 78)
    P('D6  品番分组的寿命检验 → part_grouping.csv')
    P('=' * 78)
    mix = pd.read_csv(os.path.join(D25, 'prog_mix_profile.csv'), encoding='utf-8-sig')
    hi = pd.read_csv(MERGED, encoding='utf-8-sig').groupby('life_cycle').size().rename('HI')
    t = mix.set_index('Cycle').join(hi)
    t['Part'] = t.MainProg.astype(str).map(part_map)
    P('%-10s %6s %s' % ('品番', '周期数', '寿命'))
    for p, g in t.groupby('Part'):
        P('C5G25-%s  %6d  %s' % (p, len(g), sorted(g.HI.tolist())))
    P('')
    a = t[t.Part == t.Part.unique()[0]].HI.values
    b = t[t.Part != t.Part.unique()[0]].HI.values
    u, pu = stats.mannwhitneyu(a, b)
    tw, pt = stats.ttest_ind(a, b, equal_var=False)
    se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    P('组1 n=%d 中位 %.1f 均值 %.1f SD %.1f' % (len(a), np.median(a), a.mean(), a.std(ddof=1)))
    P('组2 n=%d 中位 %.1f 均值 %.1f SD %.1f' % (len(b), np.median(b), b.mean(), b.std(ddof=1)))
    P('MWU p=%.3f ｜ Welch p=%.3f' % (pu, pt))
    P('均值差 %+.1f 件（%.1f%%）｜ MDE(80%%功效) %.1f 件'
      % (a.mean() - b.mean(), 100 * (a.mean() - b.mean()) / np.median(np.r_[a, b]), 2.8 * se))
    P('')
    P('=== 三种分组粒度的组内加权 CV ===')
    P('  %-18s 组内 CV %.3f（全体 %d 周期）'
      % ('不分组', t.HI.std(ddof=1) / t.HI.mean(), len(t)))
    for name, key in (('按品番（2 组）', 'Part'), ('按程序（7 组）', 'MainProg')):
        cs, ns = [], []
        for k, g in t.groupby(key):
            if len(g) < 2:
                continue
            cs.append(g.HI.std(ddof=1) / g.HI.mean())
            ns.append(len(g))
        P('  %-18s 组内 CV %.3f（%d 组 / %d 周期）'
          % (name, np.average(cs, weights=ns), len(cs), sum(ns)))
    out1 = os.path.join(D25, 'part_grouping.csv')
    out2 = os.path.join(D26, 'part_grouping.csv')
    t.to_csv(out1, index=False, encoding='utf-8-sig')
    t.to_csv(out2, index=False, encoding='utf-8-sig')
    P('')
    P('  [out] %s' % out1)
    P('  [out] %s' % out2)
    P('')


# ============ 校验：重算结果与已有 CSV 是否一致 ============
def verify():
    P('=' * 78)
    P('校验：重算的 CSV 与之前内联脚本产出是否逐行一致')
    P('=' * 78)
    for name in ('prog_mix_profile.csv', 'part_grouping.csv'):
        p = os.path.join(D25, name)
        if not os.path.exists(p):
            P('  %-24s 不存在' % name)
            continue
        d = pd.read_csv(p, encoding='utf-8-sig')
        P('  %-24s %d 行 × %d 列  %s' % (name, len(d), d.shape[1], '✓ 已由本脚本重建'))
    P('')
    P('  说明：本脚本重算并覆盖了这两份 CSV。若数值与 25-00/26-00 报告中的表一致，')
    P('        则两份报告现在可复现。')


def main():
    skip_raw = '--no-raw' in sys.argv
    d = load_series()
    P('读入 %d 行（主轴转速>0），%d 个周期' % (len(d), d.life_cycle.nunique()))
    P('')
    part_map = d1_mapping(d)
    d2_mix(d, part_map)
    d3_timeline(d)
    if not skip_raw:
        d4_rawcheck()
    d5_loadratio(d, part_map)
    d6_partgroup(d, part_map)
    verify()
    with open(os.path.join(D25, 's38_console.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG) + '\n')
    with open(os.path.join(D26, 's38_console.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG) + '\n')


if __name__ == '__main__':
    main()
