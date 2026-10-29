# -*- coding: utf-8 -*-
"""test_d_chipping_cutoff.py —— Test-D：崩刃检测的时间截断因果性

审核 P0-02 / R02 的要求
--------------------
「删掉第 N 件之后的所有数据，前 N 件的检测结果必须逐位不变。」

原病灶
----
`s25_chipping_piece_phase.py`（原 `s25_chipping_row.py`）第 87 行原为 `nh = max(TPL, int(len(score)*HEALTH_FRAC))`，
参考段（定阈值 mu/sd 的"健康段"）长度由**最终周期长度**决定 →
删掉后半段数据，阈值就变了，前 N 件的判定也跟着变。这是偷看未来。

改法
----
`REF_FIXED = 30`：参考段固定取前 30 件，与周期总长无关。
并让 `cycle_score(c, N)` 支持按**件序号**截断（不是按"前 N 个有效段"）。

判定标准
--------
对每个测试周期 c 与每个截断点 N（N ≥ REF_FIXED）：
  |Δscore_i| = 0 for i < N      （逐位）
  |Δthr| = 0
  nh_full == nh_trunc
  events_trunc ⊆ events_full 的前缀集合
全部为 0 才 PASS。

用法
----
    python test_d_chipping_cutoff.py
输出：同目录 test_d_chipping_cutoff.csv + 控制台
"""
import os
import sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, os.path.abspath(os.path.join(BASE, '..', '09_repro_scripts')))

import s25_chipping_piece_phase as S   # noqa: E402

CUTS = (40, 60, 80)
TEST_CYCLES = (30, 33, 38, 41)   # 覆盖长(197)/中(107~109)/短(47) 各档


def main():
    print('=' * 84)
    print('Test-D  崩刃检测的时间截断因果性')
    print('判据：删掉第 N 件之后的数据，前 N 件的结果必须逐位不变')
    print('=' * 84)
    print('REF_FIXED = %d（参考段固定件数，不随周期总长变）' % S.REF_FIXED)
    print('')

    rows = []
    for c in TEST_CYCLES:
        if c not in S.idx:
            print('  c%-4d 不在可算周期列表里，跳过' % c)
            continue
        full = S.cycle_score(c)
        if full is None:
            print('  c%-4d 无数据，跳过' % c)
            continue
        n_full = len(full['score'])
        if n_full <= max(CUTS):
            print('  c%-4d 只有 %d 件，不足以测 N=%s' % (c, n_full, CUTS))
            continue
        print('c%-4d 全周期 %d 件  nh=%d  thr=%.4f'
              % (c, n_full, full['nh'], full['thr']))

        for N in CUTS:
            if N > n_full:
                continue
            tr = S.cycle_score(c, N)
            if tr is None:
                print('    N=%-3d 截断后样本不足，跳过' % N)
                continue

            # ---- 逐位比较 score
            a = np.asarray(full['score'][:N], float)
            b = np.asarray(tr['score'], float)
            if len(b) != N:
                print('    N=%-3d ⚠ 截断后长度 %d ≠ %d' % (N, len(b), N))
            m = min(len(a), len(b))
            fin = np.isfinite(a[:m]) & np.isfinite(b[:m])
            dmax_score = float(np.max(np.abs(a[:m][fin] - b[:m][fin]))) if fin.any() else 0.0
            nan_mismatch = int((np.isnan(a[:m]) != np.isnan(b[:m])).sum())

            # ---- 阈值与参考段
            d_thr = abs(float(full['thr']) - float(tr['thr']))
            same_nh = (int(full['nh']) == int(tr['nh']))

            # ---- 事件：截断后的事件必须落在全周期事件集里（且位置 ≤ N）
            def ev_set(evs):
                """s25 的 events 是 dict(StartPiece/EndPiece)，取出起始件号做集合比较"""
                out = set()
                for e in evs:
                    if isinstance(e, dict):
                        out.add(int(e.get('StartPiece', e.get('start', 0))))
                    elif np.isscalar(e):
                        out.add(int(e))
                    else:
                        out.add(int(np.asarray(e).ravel()[0]))
                return out

            ev_full = ev_set(full['events'])
            ev_tr = ev_set(tr['events'])
            ev_extra = len(ev_tr - ev_full)

            ok = (dmax_score < 1e-12) and (d_thr < 1e-12) and same_nh and \
                 (nan_mismatch == 0) and (ev_extra == 0)

            rows.append(dict(Cycle=c, N=N, Pieces_full=n_full, Pieces_trunc=len(b),
                             NH_full=full['nh'], NH_trunc=tr['nh'],
                             dScoreMax=dmax_score, dThr=d_thr,
                             NanMismatch=nan_mismatch,
                             Events_full=len(ev_full), Events_trunc=len(ev_tr),
                             EventsNotInFull=ev_extra,
                             Result='PASS' if ok else 'FAIL'))
            print('    N=%-3d %s  |Δscore|max=%.3e  |Δthr|=%.3e  nh %d/%d  事件 %d/%d 多余%d'
                  % (N, 'PASS' if ok else '**FAIL**', dmax_score, d_thr,
                     full['nh'], tr['nh'], len(ev_full), len(ev_tr), ev_extra))
        print('')

    if not rows:
        print('没有可测的周期'); return 1

    T = pd.DataFrame(rows)
    T.to_csv(os.path.join(HERE, 'test_d_chipping_cutoff.csv'),
             index=False, encoding='utf-8-sig')

    n_pass = int((T.Result == 'PASS').sum())
    print('=' * 84)
    print('汇总：%d / %d PASS' % (n_pass, len(T)))
    print('  最大 |Δscore|  = %.3e' % T.dScoreMax.max())
    print('  最大 |Δthr|    = %.3e' % T.dThr.max())
    print('  nh 一致        = %s' % ('是' if (T.NH_full == T.NH_trunc).all() else '否'))
    print('  事件越界合计   = %d' % T.EventsNotInFull.sum())
    if n_pass == len(T):
        print('')
        print('结论：PASS —— 删掉第 N 件之后的数据，前 N 件的检测结果逐位不变')
    else:
        print('')
        print('结论：**FAIL** —— 仍有依赖未来数据的地方')
    print('=' * 84)
    return 0 if n_pass == len(T) else 1


if __name__ == '__main__':
    sys.exit(main())
