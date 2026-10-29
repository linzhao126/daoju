# -*- coding: utf-8 -*-
"""test_e_switch_truth.py —— Test-E：换刀检测不得读取 life_cycle

审核 P0-03 / R03 的要求
--------------------
「把 `life_cycle` 整列置空/打乱，换刀检测的结果必须完全不变。」

原病灶（循环论证）
--------------
`s30_switch_truth.py` 原版：

```python
cross = B[B.cross_cycle]        # 跨周期断点 ← cross_cycle 由 life_cycle 变化定义
innr  = B[~B.cross_cycle]
tp = (cross.gap_s > thr).sum()  # ← TP 直接来自标签
fp = (innr.gap_s > thr).sum()
```

报告里写的是"可部署口径：只看 gap，不看标签"，但代码里 **TP/FP 恰恰是按标签分的**。
于是 `AUC=0.862` 是标签自己解释自己 —— 标签定义的正类，其 gap 当然更大。

本轮的修正
----------
1. 候选 = `gap > 阈值`（纯数据）
2. TP/FP = 台账 `EndTime ± TOL_MIN` 是否命中（**外部真值，不是 life_cycle**）
3. 真值筛选改成"台账窗内是否有数据行"，不再用 `have_cyc = set(d.life_cycle)`
4. 顺带修 `E_sorted` 排序后 `truth.cycle.values[k]` 未同步的错配 bug

本测试
------
连跑两次 `main()`：一次正常，一次 `blank_cycle=True`（life_cycle 整列置 0）。
比较所有指标，要求**逐项完全相等**。

用法
----
    python test_e_switch_truth.py
输出：同目录 test_e_switch_truth.csv + 控制台
"""
import os
import sys
import shutil
import tempfile
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, os.path.abspath(os.path.join(BASE, '..', '09_repro_scripts')))

import s30_switch_truth as S   # noqa: E402


def flatten(m):
    """把 METRICS 摊平成可比对的 点值 + 曲线"""
    flat = dict(n_truth=m['n_truth'], n_breaks=m['n_breaks'],
                n_pos=m['n_pos'], n_neg=m['n_neg'],
                auc_gap=m['auc_gap'], auc_dload=m['auc_dload'])
    for c in m['curve']:
        t = c['t']
        flat['tp@%.2fh' % t] = c['tp']
        flat['fp@%.2fh' % t] = c['fp']
        flat['prec@%.2fh' % t] = c['prec']
        flat['rec@%.2fh' % t] = c['rec']
    return flat


def main():
    print('=' * 88)
    print('Test-E  换刀检测不得读取 life_cycle')
    print('判据：把 life_cycle 整列置 0，所有指标必须完全不变')
    print('=' * 88)

    tmp = tempfile.mkdtemp(prefix='test_e_')
    d1 = os.path.join(tmp, 'normal')
    d2 = os.path.join(tmp, 'blank_cycle')
    try:
        print('\n[1/2] 正常跑（life_cycle 原样）...')
        m1 = S.main(blank_cycle=False, out_dir=d1)
        print('\n[2/2] 置零跑（life_cycle 全为 0）...')
        m2 = S.main(blank_cycle=True, out_dir=d2)

        f1, f2 = flatten(m1), flatten(m2)
        keys = sorted(f1)
        rows = []
        for k in keys:
            a, b = f1[k], f2[k]
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                same = (abs(float(a) - float(b)) < 1e-12) or \
                       (np.isnan(float(a)) and np.isnan(float(b)))
            else:
                same = (a == b)
            rows.append(dict(Metric=k, Normal=a, BlankCycle=b,
                             Delta=(float(b) - float(a)) if isinstance(a, (int, float)) else None,
                             Result='PASS' if same else 'FAIL'))
        T = pd.DataFrame(rows)
        T.to_csv(os.path.join(HERE, 'test_e_switch_truth.csv'),
                 index=False, encoding='utf-8-sig')

        print('')
        print('%-22s %14s %14s %s' % ('指标', '正常', '置零', '判定'))
        print('-' * 68)
        for _, r in T.iterrows():
            va = ('%.4f' % r.Normal) if isinstance(r.Normal, float) else str(r.Normal)
            vb = ('%.4f' % r.BlankCycle) if isinstance(r.BlankCycle, float) else str(r.BlankCycle)
            print('%-22s %14s %14s %s' % (r.Metric, va, vb, r.Result))

        n_pass = int((T.Result == 'PASS').sum())
        print('')
        print('=' * 88)
        print('汇总：%d / %d PASS' % (n_pass, len(T)))
        print('  关键指标：AUC_gap  %.6f  vs  %.6f' % (f1['auc_gap'], f2['auc_gap']))
        print('            AUC_dload %.6f  vs  %.6f' % (f1['auc_dload'], f2['auc_dload']))
        print('            真值 %d 条 / 断点 %d 个'
              % (f1['n_truth'], f1['n_breaks']))
        if n_pass == len(T):
            print('')
            print('结论：PASS —— 指标完全不依赖 life_cycle，可部署口径成立')
        else:
            print('')
            print('结论：**FAIL** —— 仍有指标偷偷读了 life_cycle')
        print('=' * 88)
        return 0 if n_pass == len(T) else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
