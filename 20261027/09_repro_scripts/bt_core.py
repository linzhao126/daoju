# -*- coding: utf-8 -*-
"""bt_core.py — 嵌套（两层留一）预测核心，供回测与单元测试共用同一实现

为什么单独抽出来
  审核要求"Test-A 训练隔离"必须能直接证明不泄漏。若测试脚本自己复制一份公式，
  就只能证明"复制的那份没泄漏"，不能证明回测用的那份没泄漏。
  所以回测(s21)与测试(test_a)都调这里，只有一份实现。

泄漏的原始病灶（v1 s02）
  M0[k] 一次性对全集算好 → 训练刀 o 的基线 M0[o] 里含测试刀 k 的真实寿命
  → 斜率 b 被 life(k) 污染。

本实现的隔离规则
  外层：测试刀 k 先从池中移除（S 不含 k）
  内层：训练刀 o 的基线只用 S\\{o} 计算，不含 o 自己，更不含 k
  ⇒ 任何 life(k) 的改动，都不可能改变 FitB / FitG
"""
import numpy as np


def nested_predict(life, prog_of, k, N, RISE, min_fit=2):
    """返回单个测试刀 k 在截断点 N 的四个预测 + 拟合参数

    life  : {cycle: 完整寿命件数}
    prog_of: {cycle: 程序号}
    k     : 测试刀
    N     : 截断件数
    RISE  : {cycle: 截断点可得的相对上升}（没活到 N 的刀为 NaN）
    """
    S = [o for o in life if prog_of[o] == prog_of[k] and o != k]
    if len(S) < 2:
        return None

    # ---------- 外层：测试基线只用训练池
    m0a_k = float(np.median([life[o] for o in S]))
    cond_k = [life[o] - N for o in S if life[o] > N]
    m0b_k = float(np.median(cond_k)) if cond_k else float(max(1.0, m0a_k - N))

    # ---------- 内层：训练目标用留一基线（不含自己）
    m0a_loo, m0b_loo = {}, {}
    for o in S:
        others = [p for p in S if p != o]
        m0a_loo[o] = float(np.median([life[p] for p in others]))
        cc = [life[p] - N for p in others if life[p] > N]
        m0b_loo[o] = float(np.median(cc)) if cc else float(max(1.0, m0a_loo[o] - N))

    r_k = RISE.get(k, np.nan)

    # ---------- M1-old：life/M0A - 1 ~ rise，过原点最小二乘
    xs = np.array([RISE.get(o, np.nan) for o in S], float)
    ys = np.array([life[o] / m0a_loo[o] - 1.0 for o in S], float)
    ok = np.isfinite(xs) & np.isfinite(ys)
    if ok.sum() >= min_fit and np.isfinite(r_k):
        b = float((xs[ok] * ys[ok]).sum() / max((xs[ok] ** 2).sum(), 1e-12))
        pred_old = (1.0 + b * r_k) * m0a_k - N
    else:
        b, pred_old = np.nan, np.nan

    # ---------- M1-new：log((L-N)/M0B) ~ z(rise)，乘性修正
    xv = np.array([RISE.get(o, np.nan) for o in S], float)
    yv = np.array([np.log(max(life[o] - N, 1e-9) / m0b_loo[o]) if life[o] > N else np.nan
                   for o in S], float)
    ok2 = np.isfinite(xv) & np.isfinite(yv)
    if ok2.sum() >= min_fit and np.isfinite(r_k):
        sd = max(float(xv[ok2].std(ddof=1)), 1e-9)
        z = (xv[ok2] - xv[ok2].mean()) / sd
        g = float(np.clip((z * yv[ok2]).sum() / max((z * z).sum(), 1e-12), -0.5, 0.5))
        pred_new = float(m0b_k * np.exp(g * (r_k - xv[ok2].mean()) / sd))
    else:
        g, pred_new = np.nan, np.nan

    q = np.percentile(cond_k, [10, 90]) if len(cond_k) >= 2 else [np.nan, np.nan]
    return dict(k=k, N=N, S=S, m0a_k=m0a_k, m0b_k=m0b_k,
                M0A=m0a_k - N, M0B=m0b_k,
                M1Old=pred_old, M1New=pred_new,
                FitB=b, FitG=g, PI_lo=q[0], PI_hi=q[1], n_fit=int(ok.sum()), n_fit2=int(ok2.sum()))
