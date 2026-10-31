# -*- coding: utf-8 -*-
"""B4 采集质量体检（任务书 §3-P0-D01 第 4 条）

要回答的问题：4152 的信号测不出退化，到底是**信号本身没信息**，还是**采集环节把信息丢了**？
不先把这个分清，任何"FOCAS 无增量"的结论都归因不明。

任务书要求交付三样，本脚本各出一张表：
  1. 每个通道的**原始采样间隔分布**（实测，不是固件设计值 15 s）
  2. **丢失 / 重复 / 乱序** 统计（含 allnum 跳号与回退）
  3. **跨班停机段**清单（>6 h 空档，用于区分"停机"与"采集失败"）

实现约束：3.3 GB / 约 3300 万行，**全程向量化**（分块 + np.diff + groupby.diff），
不做逐行 Python 循环，也不用"存下所有时间戳去判重"这种 O(n) 内存做法。
"整刀序列"与"全通道序列"分开统计：换刀瞬间必然出现长空档，混在一起会把正常换刀误判成采集故障。
"""
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))                  # daoju_repo/20261027
WORK = os.path.abspath(os.path.join(REPO, '..', '..'))
SRC = os.path.join(WORK, '手工提供文件', 'influxdb_4152_FanucNormalData_20261027_200034.csv')
OUTD = os.path.join(REPO, 'P0_数据与代码移交', 'B4_采集质量')

COLS = ['_time', 'L_allnum', 'L_tool_number', 'L_state', 'L_main_program']
GAPS = [(2, '>2 s'), (5, '>5 s'), (10, '>10 s'), (16, '>16 s'),
        (30, '>30 s'), (60, '>1 min'), (300, '>5 min'),
        (3600, '>1 h'), (21600, '>6 h'), (86400, '>24 h')]
# 为什么加了 2/5/10/16 s 这几档：行是**成批写入**的（实测行间隔中位 0.02 s、P95 0.98 s），
# 所以“行间隔”不等于“采集轮询周期”：burst 内间隔极短，burst 之间才是轮询周期。
# 只有 ≥ 秒级的这几档能定位轮询周期（例如 >16 s 的段数应近似于采集次数）。
NS = 1e9


def pct(a):
    if len(a) == 0:
        return {}
    q = np.percentile(a, [1, 5, 25, 50, 75, 95, 99])
    return dict(n=len(a), min_s=round(float(a.min()), 2), p01_s=round(float(q[0]), 2),
                p05_s=round(float(q[1]), 2), p25_s=round(float(q[2]), 2),
                median_s=round(float(q[3]), 2), p75_s=round(float(q[4]), 2),
                p95_s=round(float(q[5]), 2), p99_s=round(float(q[6]), 2),
                max_s=round(float(a.max()), 2), mean_s=round(float(a.mean()), 2))


def main():
    os.makedirs(OUTD, exist_ok=True)
    if not os.path.exists(SRC):
        raise SystemExit('缺 %s' % SRC)

    dt_chunks, tool_dt, gap_rec = [], {}, []
    n_rows = n_bad = n_dup = n_dis = 0
    prev_t = None                     # 上一块最后一行的时间（int64 ns）
    prev_tool_t = {}                  # 刀号 → 上一行时间
    an_track = {}                     # 刀号 → {'重复','跳号','回退','缺号数','prev'}
    state_cnt, prog_cnt = {}, {}

    for ci, ch in enumerate(pd.read_csv(SRC, usecols=COLS, chunksize=1_000_000,
                                        encoding='utf-8-sig', low_memory=False)):
        n_rows += len(ch)
        # ⚠ 必须显式转到 ns：_time 格式丕一（混有带/不带小数秒），pandas 可能推成
        #    datetime64[us]/[s]；那种情况下 .astype('int64') 拿到的不是纳秒，
        #    再除 1e9 就会把中位间隔算成 0.0 s（上一版实际跳的坑）。
        tt = pd.to_datetime(ch['_time'], errors='coerce')
        try:
            tt = tt.dt.as_unit('ns')
        except Exception:
            tt = tt.astype('datetime64[ns]')
        t = tt.astype('int64')
        ok = t.notna()
        n_bad += int((~ok).sum())
        ch = ch[ok].reset_index(drop=True)
        t = t[ok].to_numpy()
        if len(t) == 0:
            continue

        # —— 全通道相邻间隔（首行接上一块末行）——
        d = np.empty(len(t), dtype=np.float64)
        d[0] = (t[0] - prev_t) / NS if prev_t is not None else np.nan
        d[1:] = np.diff(t) / NS
        n_dup += int(np.nansum(d == 0))
        n_dis += int(np.nansum(d < 0))
        good = d[(d > 0)]
        dt_chunks.append(good.astype(np.float32))

        # —— 长空档明细（>60 s）——
        gi = np.flatnonzero(np.nan_to_num(d, nan=0) > 1.0)   # >1 s 即视为 burst 边界
        for k in gi:
            start = t[k - 1] if k > 0 else (prev_t if prev_t is not None else t[k])
            gap_rec.append((start, t[k], float(d[k])))

        # —— 分刀间隔（同刀相邻两行，跨块接续）——
        tool = ch['L_tool_number'].astype(str)
        tmp = pd.DataFrame({'tool': tool.to_numpy(), 't': t})
        for tl, g in tmp.groupby('tool', sort=False):
            arr = g['t'].to_numpy()
            dd = np.empty(len(arr), dtype=np.float64)
            dd[0] = (arr[0] - prev_tool_t[tl]) / NS if tl in prev_tool_t else np.nan
            dd[1:] = np.diff(arr) / NS
            v = dd[dd > 0]
            if len(v):
                tool_dt.setdefault(tl, []).append(v.astype(np.float32))
            prev_tool_t[tl] = int(arr[-1])

        # —— allnum 序列异常（重复/跳号/回退/缺号数），向量化 + 跨块接续 ——
        an = pd.to_numeric(ch['L_allnum'], errors='coerce').to_numpy()
        for tl, g in pd.DataFrame({'tool': tool.to_numpy(), 'a': an}).groupby('tool', sort=False):
            a = g['a'].to_numpy()
            a = a[~np.isnan(a)].astype(np.int64)
            if len(a) == 0:
                continue
            rec = an_track.setdefault(tl, {'重复': 0, '跳号': 0, '回退': 0, '缺号数': 0,
                                          'prev': None, '首': None, '末': None})
            if rec['首'] is None:
                rec['首'] = int(a[0])
            rec['末'] = int(a[-1])
            seq = a
            if rec['prev'] is not None:                # 把上一块末尾值接在前面
                seq = np.concatenate(([rec['prev']], a))
            if len(seq) >= 2:
                df = np.diff(seq)
                rec['重复'] += int((df == 0).sum())
                rec['回退'] += int((df < 0).sum())
                up = df[df > 1]
                rec['跳号'] += int(len(up))
                rec['缺号数'] += int((up - 1).sum())
            rec['prev'] = int(a[-1])

        for k, v in ch['L_state'].value_counts().items():
            try:
                state_cnt[int(k)] = state_cnt.get(int(k), 0) + int(v)
            except (ValueError, TypeError):
                pass
        for k, v in pd.to_numeric(ch['L_main_program'], errors='coerce').value_counts().items():
            try:
                prog_cnt[int(k)] = prog_cnt.get(int(k), 0) + int(v)
            except (ValueError, TypeError):
                pass

        prev_t = int(t[-1])
        print('  块 %d：累计 %d 行 / 间隔样本 %d / 空档 %d' %
              (ci + 1, n_rows, sum(len(x) for x in dt_chunks), len(gap_rec)), flush=True)

    # —— 1. 间隔分布 ——
    all_d = np.concatenate(dt_chunks) if dt_chunks else np.array([], dtype=np.float32)
    rows = [dict(范围='全通道（含换刀与停机）', **pct(all_d))]
    for tl, parts in sorted(tool_dt.items()):
        arr = np.concatenate(parts)
        if len(arr) < 200:
            continue
        rows.append(dict(范围='刀号 %s（同刀相邻两行）' % tl, **pct(arr)))
    c1 = ['范围', 'n', 'min_s', 'p01_s', 'p05_s', 'p25_s', 'median_s', 'p75_s',
          'p95_s', 'p99_s', 'max_s', 'mean_s']
    pd.DataFrame(rows).reindex(columns=c1).to_csv(
        os.path.join(OUTD, 'B4-1_采样间隔分布.csv'), index=False, encoding='utf-8-sig')

    # —— 2. 丢失 / 重复 / 乱序 ——
    a2 = [{'项': '总行数', '值': n_rows},
          {'项': '_time 解析失败行', '值': n_bad},
          {'项': '完全重复时间戳（间隔=0）', '值': n_dup},
          {'项': '时间乱序（后一行早于前一行）', '值': n_dis}]
    for tl, r in sorted(an_track.items()):
        a2.append({'项': 'allnum@%s（首%s 末%s）' % (tl, r['首'], r['末']),
                   '值': '重复%d 跳号%d 回退%d 缺号数%d'
                         % (r['重复'], r['跳号'], r['回退'], r['缺号数'])})
    pd.DataFrame(a2).to_csv(os.path.join(OUTD, 'B4-2_丢失重复乱序.csv'),
                            index=False, encoding='utf-8-sig')

    # —— 3. 长空档 ——
    gr = pd.DataFrame(gap_rec, columns=['start_ns', 'end_ns', 'gap_s'])
    pd.DataFrame([{'阈值': lab, '段数': int((gr['gap_s'] > th).sum())} for th, lab in GAPS]
                 ).to_csv(os.path.join(OUTD, 'B4-3a_空档阈值统计.csv'), index=False, encoding='utf-8-sig')
    if len(gr):
        gr['start'] = pd.to_datetime(gr['start_ns'], unit='ns').dt.strftime('%Y-%m-%d %H:%M:%S')
        gr['end'] = pd.to_datetime(gr['end_ns'], unit='ns').dt.strftime('%Y-%m-%d %H:%M:%S')
        gr['gap_h'] = (gr['gap_s'] / 3600).round(2)
        gr.sort_values('gap_s', ascending=False).head(300)[['start', 'end', 'gap_h']
                                                           ].to_csv(os.path.join(OUTD, 'B4-3b_最长空档TOP300.csv'),
                                                                   index=False, encoding='utf-8-sig')

    # —— 4/5. 状态与程序分布 ——
    pd.DataFrame([{'L_state': k, '行数': v} for k, v in sorted(state_cnt.items())]
                 ).to_csv(os.path.join(OUTD, 'B4-4_L_state分布.csv'), index=False, encoding='utf-8-sig')
    pd.DataFrame([{'主程序': k, '行数': v} for k, v in sorted(prog_cnt.items())]
                 ).to_csv(os.path.join(OUTD, 'B4-5_主程序行数分布.csv'), index=False, encoding='utf-8-sig')

    print('\n=== B4 完成：读 %d 行 ===' % n_rows)
    print('解析失败 %d / 重复时戳 %d / 乱序 %d' % (n_bad, n_dup, n_dis))
    if len(all_d):
        print('全通道间隔：中位 %.1f s ｜ P95 %.1f s ｜ 最大 %.1f s（%.1f h）'
              % (np.median(all_d), np.percentile(all_d, 95), all_d.max(), all_d.max() / 3600))
        print('其中 >6 h 的空档：%d 段（跨班/停机候选）' % int((all_d > 21600).sum()))
    for tl, r in sorted(an_track.items())[:12]:
        print('  allnum@%-8s 重复%-7d 跳号%-6d 回退%-5d 缺号%d'
              % (tl, r['重复'], r['跳号'], r['回退'], r['缺号数']))
    print('输出:', OUTD)


if __name__ == '__main__':
    main()
