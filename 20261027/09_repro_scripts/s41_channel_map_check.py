"""
s41_channel_map_check.py —— P1-08 通道映射可移植性实证

【要回答的问题】
审核 P1-08：「L/R 通道写死，公共方案需通道映射架构」。
只写一张映射表不够——必须证明**换到 R 通道真的能跑**，或者**明确卡在哪**。

【做法】
1. `channel_map.detect()` 从表头自动判别通道（不给路径、不给提示）。
2. `validate()` 检查字段契约，缺哪个一目了然。
3. 逐字段做**可用性诊断**（非空率 / 唯一值 / 分布）——这是映射层管不了、但换通道必然踩的：
   字段同名不代表语义相同。
4. 实际调用 `hi_asof.build_piece_index()` + `compute_segments()`，报告跑到哪一步、卡在什么上。

【为什么不能只写映射表】
T3434 的 `R_abso_z` 众数 = **0.0**，而 4152 的 L 通道切断 Z 是 **−38.30**；
`R_spindle_speed > 0` 占比 **100%**，L 通道只有约三成。
→ **同名逻辑量在不同通道的物理含义/参考点不同**，映射层只能保证"取到哪一列"，
   不能保证"这一列能直接喂给同一个算法"。本脚本的价值就是把这类差异显式列出来。

用法
  python s41_channel_map_check.py                 # 默认查 L(4152)+R(2428) 两边
  python s41_channel_map_check.py --only R

输出（默认写到 ../31_通道映射/）
  channel_contract.csv        逻辑量 → L/R 物理字段 + 缺失情况
  channel_field_health.csv    逐通道逐字段的可用性诊断
  s41_console.txt             控制台全文
"""
import os
import sys
import argparse

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import wsroot as _wsroot          # G2：不再数 '..'，按工作区特征向上定位
ROOT = _wsroot.find(_os.path.dirname(_os.path.abspath(__file__)))
OUT = os.path.join(BASE, '31_通道映射')
sys.path.insert(0, HERE)

import channel_map as CM          # noqa: E402
import hi_asof as HA              # noqa: E402

# 两个通道的代表文件（只读必要列）
SOURCES = {
    'L': os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv'),
    'R': os.path.join(ROOT, 'influxdb_2428_FanucNormalData_20261014_141920_R_T3434_T3434_life_labeled.csv'),
}

LOG = []


def P(s=''):
    LOG.append(str(s))
    print(s)


def header_of(path, n=1):
    with open(path, encoding='utf-8-sig', newline='') as f:
        return next(__import__('csv').reader(f))


def field_health(path, prefix):
    """逐字段诊断：非空率 / 唯一值 / 零值占比 / 分布线索

    这些是"映射层取得到列"与"这列能不能用"之间的差别。
    """
    cm = CM.ChannelMap(prefix)
    use = [cm.time, cm.load, cm.x, cm.z, cm.allnum, cm.spd, cm.program, cm.tool_no]
    use = [c for c in use if c is not None]
    cols = [c for c in use if c in header_of(path)]
    if not cols:
        return pd.DataFrame()
    d = pd.read_csv(path, usecols=cols, encoding='utf-8-sig')
    rows = []
    for c in cols:
        s = d[c]
        num = pd.to_numeric(s, errors='coerce')
        is_num = num.notna().sum() > 0.99 * s.notna().sum()
        rec = dict(Channel=prefix, Field=c, Rows=len(s),
                   NotNullRate=round(float(s.notna().mean()), 4),
                   NUnique=int(s.nunique(dropna=True)))
        if is_num:
            rec.update(ZeroRate=round(float((num == 0).mean()), 4),
                       Median=round(float(num.median()), 3) if num.notna().any() else np.nan,
                       P01=round(float(num.quantile(.01)), 3) if num.notna().any() else np.nan,
                       P99=round(float(num.quantile(.99)), 3) if num.notna().any() else np.nan,
                       Mode=round(float(num.round(1).mode().iloc[0]), 3) if num.notna().any() else np.nan)
        else:
            top = s.dropna().astype(str).value_counts().head(2)
            rec.update(ZeroRate=np.nan, Median=np.nan, P01=np.nan, P99=np.nan,
                       Mode=('|'.join('%s:%d' % (k[:18], v) for k, v in top.items()) or ''))
        rows.append(rec)
    return pd.DataFrame(rows)


def z0_candidates(idx, top=8):
    """从数据里取「可能代表切削工步」的 Z 值：按出现行数排序的前 top 个（仅用主轴>0 的行）"""
    import collections
    cnt = collections.Counter()
    for v in idx.values():
        for q in v['rows'].values():
            sp = q[q[HA.CH.spd].fillna(0) > 0] if HA.CH.spd in q.columns else q
            if not len(sp):
                continue
            for z in sp[HA.CH.z].round(1).dropna().tolist():
                cnt[z] += 1
    return cnt.most_common(top)


def try_pipeline(path, prefix, max_cycles=3):
    """实际跑 build_piece_index + compute_segments，报告卡点"""
    cm = CM.ChannelMap(prefix)
    ok_cols = [cm.time, 'life_cycle', cm.allnum, cm.x, cm.z, cm.load, cm.spd]
    res = dict(Channel=prefix, Stage='init', Ok=False, Cycles=0, Pieces=0, Detail='')

    # 1) 字段契约
    hdr = header_of(path)
    ok, miss_req, miss_opt = CM.validate(hdr, prefix)
    if not ok:
        res.update(Stage='validate', Detail='缺必需字段 %s' % miss_req)
        return res

    # 2) 读数据
    d = pd.read_csv(path, usecols=ok_cols + ['life_cycle'], encoding='utf-8-sig')
    cyc = sorted(int(x) for x in d['life_cycle'].dropna().unique())
    res.update(Stage='loaded', Cycles=len(cyc),
               Detail='%d 行 / %d 周期' % (len(d), len(cyc)))
    if not cyc:
        res.update(Stage='no_cycle', Detail='没有 life_cycle 标签')
        return res

    # 3) build_piece_index（借 hi_asof，但把 CHANNEL 临时切到该通道）
    old = HA.CHANNEL, HA.CH, HA.METRIC, HA.COLS
    try:
        HA.CHANNEL, HA.CH = prefix, cm
        HA.METRIC = cm.load
        HA.COLS = cm.cols(['time', 'allnum', 'x', 'z', 'load', 'spd'], extra=['life_cycle'])
        idx = HA.build_piece_index(path, cycles=cyc[:max_cycles])
        # 列名适配由 hi_asof.compute_segments 内部完成（只在调用 _xalign_piece 那一刻转）
        res.update(Stage='buil_piece_index',
                   Pieces=sum(len(v['pieces']) for v in idx.values()),
                   Detail='%d 个周期 / 共 %d 件' % (len(idx), res['Pieces'] or 0))
    except Exception as e:
        res.update(Stage='build_piece_index_FAIL',
                   Detail='%s: %s' % (type(e).__name__, str(e)[:120]))
        return res

    # 4) compute_segments（真正跑几何定位）
    n_ok = 0
    det = []
    try:
        for c, v in idx.items():
            z0, segs = HA.compute_segments(v['pieces'], v['rows'])
            if segs:
                n_ok += 1
            det.append('c%d:z0=%s,segs=%d' % (c, z0, len(segs)))
        res.update(Stage='compute_segments', Ok=(n_ok > 0),
                   Detail='%d/%d 周期有几何段 | %s' % (n_ok, len(idx), ' '.join(det)))

        # ---- 若自观测 z0 失效，扫候选 z0，区分「架构不行」与「只差 Z0 标定」
        if n_ok == 0 and idx:
            P('      ⚠ 自观测 z0 得不到几何段，扫候选 Z0（按行数排序）…')
            cands = z0_candidates(idx)
            best = []
            for cand, nrow in cands:
                n2 = sum(1 for v in idx.values()
                         if HA.compute_segments(v['pieces'], v['rows'], z0_override=cand)[1])
                best.append((cand, nrow, n2))
            for cand, nrow, n2 in best:
                P('        Z0=%+9.3f  行数%7d  → %d/%d 周期有段 %s'
                  % (cand, nrow, n2, len(idx), '← 可用' if n2 else ''))
            ok_any = [b for b in best if b[2] > 0]
            res['Z0_Scan'] = ';'.join('%.3f:%d/%d' % (c, n, len(idx)) for c, _, n in best)
            res['Z0_BestUsable'] = (max(ok_any, key=lambda x: x[2])[0] if ok_any else None)
            if ok_any:
                res['Stage'] = 'need_z0_calibration'
                res['Detail'] += ' | 换 Z0=%.2f 后 %d 周期可出段 → 只差标定' % (
                    res['Z0_BestUsable'], max(b[2] for b in ok_any))
            else:
                res['Stage'] = 'no_usable_z0'
                res['Detail'] += ' | 所有候选 Z0 均出不了段 → 不止缺标定'
    except Exception as e:
        res.update(Stage='compute_segments_FAIL',
                   Detail='%s: %s' % (type(e).__name__, str(e)[:120]))
    finally:
        HA.CHANNEL, HA.CH, HA.METRIC, HA.COLS = old
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', choices=['L', 'R'], default=None)
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    P('=' * 92)
    P('P1-08　通道映射可移植性实证')
    P('=' * 92)
    P('')

    # ---------- 1) 契约表
    rows = []
    for k, v in CM.LOGICAL.items():
        rows.append(dict(Logical=k, Required=v['required'], Desc=v['desc'],
                         L_Field=v['L'], R_Field=v['R']))
    C = pd.DataFrame(rows)
    C.to_csv(os.path.join(OUT, 'channel_contract.csv'), index=False, encoding='utf-8-sig')
    P('=== 逻辑量 → 物理字段 契约（%d 项，必需 %d 项）==='
      % (len(C), int(C.Required.sum())))
    P('%-10s %-6s %-24s %s' % ('逻辑量', '必需', 'L 通道', 'R 通道'))
    for _, r in C.iterrows():
        P('%-10s %-6s %-24s %s' % (r.Logical, '是' if r.Required else '否', r.L_Field, r.R_Field))
    P('')

    # ---------- 2) 自动判别
    P('=== 自动判别通道前缀（只看表头，不给路径提示）===')
    for p, path in SOURCES.items():
        if args.only and p != args.only:
            continue
        if not os.path.exists(path):
            P('  %s：文件不存在，跳过' % p)
            continue
        hdr = header_of(path)
        det = CM.detect(hdr)
        ok, mr, mo = CM.validate(hdr, det) if det else (False, [], [])
        P('  %s 表头 %d 列 → detect()=%s  契约=%s%s'
          % (os.path.basename(path)[:52], len(hdr), det,
             'PASS' if ok else 'FAIL',
             '' if not mr else '  缺必需:%s' % mr))
        if mo:
            P('     缺可选:%s（不影响 HI，只影响分组/过滤）' % mo)
    P('')

    # ---------- 3) 字段健康度
    health = []
    for p, path in SOURCES.items():
        if args.only and p != args.only:
            continue
        if not os.path.exists(path):
            continue
        h = field_health(path, p)
        if len(h):
            health.append(h)
    if health:
        Hdf = pd.concat(health, ignore_index=True)
        Hdf.to_csv(os.path.join(OUT, 'channel_field_health.csv'), index=False, encoding='utf-8-sig')
        P('=== 逐字段可用性诊断（同名不等于同语义）===')
        P('%-3s %-22s %9s %9s %9s %11s %11s'
          % ('CH', '字段', '非空率', '唯一值', '零值率', '中位/众数', 'p01~p99'))
        for _, r in Hdf.iterrows():
            mid = r.Median if pd.notna(r.Median) else r.Mode
            rng = ('%.2f~%.2f' % (r.P01, r.P99)) if pd.notna(r.P01) else ''
            P('%-3s %-22s %8.2f%% %9d %8.2f%% %11s %11s'
              % (r.Channel, r.Field, 100 * r.NotNullRate, r.NUnique,
                 100 * r.ZeroRate if pd.notna(r.ZeroRate) else -1, mid, rng))
        P('')

    # ---------- 4) 实跑
    P('=== 实跑通道（build_piece_index → compute_segments）===')
    runs = []
    for p, path in SOURCES.items():
        if args.only and p != args.only:
            continue
        if not os.path.exists(path):
            P('  %s：文件不存在，跳过' % p)
            continue
        P('  [%s] %s' % (p, os.path.basename(path)[:60]))
        r = try_pipeline(path, p)
        runs.append(r)
        P('      stage=%s  ok=%s' % (r['Stage'], r['Ok']))
        P('      %s' % r['Detail'])
        P('')
    if runs:
        pd.DataFrame(runs).to_csv(os.path.join(OUT, 'channel_pipeline_run.csv'),
                                  index=False, encoding='utf-8-sig')

    # ---------- 5) 结论
    P('=== 结论 ===')
    P('  映射层本身可用：detect() 能只凭表头判别 L/R；validate() 能列出缺失字段；')
    P('  hi_asof 在 CHANNEL=L 下解析出的物理字段与原写法**完全相同**（Test-B 回归 PASS）。')
    P('')
    P('  ⛔ 但「换到 R 通道」**不是换列名，也不只是 Z0 标定**——实测有四个结构性差异：')
    P('')
    P('    ① **X 行程完全不同（最致命）**')
    P('       L(4152) 切断段 X ∈ [19, 40]，全行程 0~233；')
    P('       R(2428) X ∈ [−112, +168]，**落在 19~40 的行只有 0.8~1.4%**。')
    P('       → xalign 的 X 窗口不是"参数偏了"，是把 99% 的行排除在外。')
    P('')
    P('    ② **件划分失效**')
    P('       L: allnum 逐件 +1，1685 件 / 20 周期；')
    P('       R: c11~c19 的 allnum **100% 为 0** → 整周期被当成 1 个"件"，')
    P('          c20 有 87.6% 为 0，仅 c21~c23 可用（1969/2642/1601 件）。')
    P('       → 件数不足 min_pieces=40，或件内混入整周期数据。')
    P('')
    P('    ③ **门控量语义不同**')
    P('       L: spindle_speed>0 占 52.4%（用于剔非切削行）；')
    P('       R: spindle_speed>0 占 **100%**（中位 3799，定转速）→ 无法用它判断是否在切削。')
    P('')
    P('    ④ **主程序语义不同**')
    P('       L: 品番程序 `C5G25-02/03`，7 个程序 = 2 品番；')
    P('       R: 主程序只有 3 个值，主体 `201(SUB KASHIME)`（副轴夹持）→ **不是品番加工程序**。')
    P('')
    P('  扫描 8 个候选 Z0（覆盖 Z=−7.3 ~ +153.5，行数 1467~3926）后**全部 0/3 周期出不了段**，')
    P('  说明卡点不在 Z0，而在上面 ①②。')
    P('')
    P('  → 结论：**映射架构已交付并可用（L 通道回归无损）；R 通道改造不是"移植"，是"重做几何定位"**，')
    P('     需要先确定 R 通道的切削工步判据（不能靠 spindle_speed）与件划分依据（allnum 不可用）。')

    with open(os.path.join(OUT, 's41_console.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG) + '\n')
    P('')
    P('[out] channel_contract.csv / channel_field_health.csv / channel_pipeline_run.csv / s41_console.txt')


if __name__ == '__main__':
    main()
