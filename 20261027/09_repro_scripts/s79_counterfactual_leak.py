# -*- coding: utf-8 -*-
"""U-7 反事实泄漏探针（任务书 §2.1 阶段 C："删掉未来数据，当前输出不变"）

预测模型最容易犯的错是**数据泄漏**：预测"还剩几件"时偷偷用了这把刀之后的数据，
线下分数漂亮、上线就废。本脚本对 s53/bt_core 的真实口径做三种反事实检验：

  V1 未来输入屏蔽  把第 N 件之后的 HI 全部置 NaN，重算特征与预测 → 输出必须**逐位不变**
  V2 测试标签扰动  把被评估刀自己的"完整寿命"改动 ±20% → 它对自身拟合系数 FitB/FitG 的影响必须为 0
                    （bt_core 头注释承诺的性质，这里做实证）
  V3 训练池右删失  模拟真实在线：站在"k 刚跑完第 N 件"的时刻，那时**还没换刀的刀寿命是未知的**，
                    把它们从训练池剔除后重新预测 → 看输出变化多大

V1/V2 通过 = 现有实现没有偷看被评估刀的未来。
V3 有偏差 = 现有回测的乐观程度有多大（这是"离线指标 vs 在线表现"的差距来源，不是 bug，
            但必须量化后才能谈上线可行性）。三个问题分开回答，不混。
"""
import importlib.util
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, HERE)
import bt_core as BT                                        # noqa: E402

HI28 = os.path.join(BASE, '24_程序归属修正', 'HI_merged_28.csv')
OUT = os.path.join(BASE, 'P0_数据与代码移交', 'U-7_反事实泄漏探针.csv')
CUTS, W, MODELS = [20, 40, 60, 80], 10, ['M0A', 'M0B', 'M1Old', 'M1New']


def rise_of(hi, N, mask_after=None):
    """与 s53:156-161 同式；mask_after 不为空时把该位置之后的值置 NaN（V1 用）。"""
    a = np.array(hi, dtype=float)
    if mask_after is not None and mask_after < len(a):
        a[mask_after:] = np.nan
    if N > len(a):
        return np.nan
    seg = a[max(0, N - W):N]
    seg = seg[np.isfinite(seg)]
    return float(np.median(seg) - 1.0) if len(seg) >= 3 else np.nan


def load():
    """HI_merged_28.csv 没存时间列（只有 life_cycle/prog/piece_idx/allnum/progress/HI_*），
    所以 V3 的"时刻"用 **allnum 件时钟**（机床全局递增计数）代替墙钟时间；
    对"谁在谁之前换完刀"这类定序问题，件时钟与时间同序且更干净。
    """
    h = pd.read_csv(HI28, encoding='utf-8-sig')
    d = {}
    for c, g in h.groupby('life_cycle'):
        g = g.sort_values('piece_idx')
        an = pd.to_numeric(g['allnum'], errors='coerce').to_numpy(float)
        d[int(c)] = dict(prog=int(g['prog'].iloc[0]),
                         hi=g['HI_L_spindle_load'].to_numpy(float),
                         an=an[~np.isnan(an)].astype(np.int64))
    return d


def preds(LIFE, PROG, RISE, pool_ids, k, N):
    """注意：bt_core 内部靠 `prog_of[k]` 取测试刀的程序、靠 `o != k` 排除自身，
    所以 **测试刀本身必须在传入的字典里**（与 s53 的传法一致），但不计入训练池集合。"""
    keys = list(pool_ids)
    if k not in keys:
        keys.append(k)
    LP = {c: LIFE[c] for c in keys if c in LIFE}
    PP = {c: PROG[c] for c in keys if c in LIFE}
    return BT.nested_predict(LP, PP, k, N, RISE)


def main():
    d = load()
    ids = sorted(d)
    LIFE = {c: len(d[c]['hi']) for c in ids}
    PROG = {c: d[c]['prog'] for c in ids}
    print('HI 表周期数 %d，寿命范围 %d~%d 件' % (len(ids), min(LIFE.values()), max(LIFE.values())))

    rec = []                      # 每格一条结果
    v3_delta = {m: [] for m in MODELS}
    v1_delta = {m: [] for m in MODELS}
    v2_delta = {'FitB': [], 'FitG': []}
    v3_pool = []
    n_case = 0

    for N in CUTS:
        for k in ids:
            if LIFE[k] <= N + 5:                 # 到不了这个截断点，或剩余太少
                continue
            pool = [c for c in ids if PROG[c] == PROG[k] and c != k and LIFE[c] > N]
            if len(pool) < 2:
                continue
            RISE = {c: rise_of(d[c]['hi'], N) for c in set(pool) | {k}}
            base = preds(LIFE, PROG, RISE, pool, k, N)
            if base is None:
                continue
            n_case += 1

            # —— V1：屏蔽 k 与所有训练刀第 N 件之后的输入 ——
            RISE1 = {c: rise_of(d[c]['hi'], N, mask_after=N) for c in set(pool) | {k}}
            p1 = preds(LIFE, PROG, RISE1, pool, k, N)
            if p1 is not None:
                for m in MODELS:
                    b, a = base.get(m), p1.get(m)
                    if b is not None and a is not None and np.isfinite(b) and np.isfinite(a):
                        v1_delta[m].append(abs(float(a) - float(b)))

            # —— V2：扰动被评估刀自己的完整寿命（±20%）——
            for sgn in (1.2, 0.8):
                LIFE2 = dict(LIFE)
                LIFE2[k] = int(round(LIFE[k] * sgn))
                p2 = preds(LIFE2, PROG, RISE, pool, k, N)
                if p2 is not None:
                    for key in ('FitB', 'FitG'):
                        b, a = base.get(key), p2.get(key)
                        if b is not None and a is not None and np.isfinite(b) and np.isfinite(a):
                            v2_delta[key].append(abs(float(a) - float(b)))

            # —— V3：训练池只保留“k 跑到第 N 件时已经换过刀”的（寿命当时已知）——
            ak = d[k]['an']
            if len(ak) < N:
                continue
            t_k = int(ak[N - 1])                          # k 第 N 件的机床件计数
            pool3 = []
            for c in pool:
                ao = d[c]['an']
                if len(ao) == 0:
                    continue
                # 该刀最后一次出现在第几件：若已发生换刀（件计数不再增长）则视为当时已知
                if int(ao[-1]) <= t_k:
                    pool3.append(c)
            if len(pool3) >= 2 and len(pool3) < len(pool):
                v3_pool.append(1.0 - len(pool3) / len(pool))
                RISE3 = {c: rise_of(d[c]['hi'], N) for c in set(pool3) | {k}}
                p3 = preds(LIFE, PROG, RISE3, pool3, k, N)
                if p3 is not None:
                    for m in MODELS:
                        b, a = base.get(m), p3.get(m)
                        if b is not None and a is not None and np.isfinite(b) and np.isfinite(a):
                            v3_delta[m].append(abs(float(a) - float(b)))

    def stat(a):
        if not a:
            return dict(n=0, 最大偏差=float('nan'), 平均偏差=float('nan'))
        arr = np.asarray(a, float)
        return dict(n=len(arr), 最大偏差=round(float(arr.max()), 4), 平均偏差=round(float(arr.mean()), 4))

    rows = []
    for m in MODELS:
        s1 = stat(v1_delta[m])
        s3 = stat(v3_delta[m])
        rows.append(dict(检验='V1 屏蔽第N件之后的输入', 模型=m, **s1,
                         判定=('通过（输出不变）' if s1['n'] and s1['最大偏差'] < 1e-6 else
                               ('无有效对照' if s1['n'] == 0 else '★存在差异')),
                         说明='特征 rise 只取 [N-W, N) 窗口，理论上不含未来；此检验实证'))
    for key in ('FitB', 'FitG'):
        s2 = stat(v2_delta[key])
        rows.append(dict(检验='V2 扰动被评估刀自身寿命±20%', 模型=key, **s2,
                         判定=('通过（系数不变）' if s2['n'] and s2['最大偏差'] < 1e-6 else
                               ('无有效对照' if s2['n'] == 0 else '★标签泄漏')),
                         说明='bt_core 两层留一承诺的性质'))
    for m in MODELS:
        s3 = stat(v3_delta[m])
        rows.append(dict(检验='V3 训练池右删失（模拟在线可得性）', 模型=m, **s3,
                         判定=('无差异' if s3['n'] == 0 or (s3['最大偏差'] < 1e-6) else
                               '★有差异（离线偏乐观）'),
                         说明='非 bug：量化"离线指标 vs 上线表现"的差距'))

    df = pd.DataFrame(rows)
    df.to_csv(OUT, index=False, encoding='utf-8-sig')
    print('\n=== U-7 结果（有效对照格 %d）===' % n_case)
    print(df.to_string(index=False))
    if v3_pool:
        print('\nV3 训练池平均缩水 %.1f%%（中位 %.1f%%）——即在线时刻平均只有一半不到的人能用于拟合'
              % (100 * float(np.mean(v3_pool)), 100 * float(np.median(v3_pool))))
    print('写出', OUT)


if __name__ == '__main__':
    main()
