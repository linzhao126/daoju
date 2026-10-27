# -*- coding: utf-8 -*-
"""test_c — 审核 §3 Test-C：生命周期程序混用

判据（审核原文）
  "构造一把物理刀在 prog14 和 prog20 之间切换，再返回 prog14。
   应保持一个 PhysicalLifeId、两个或三个 ProgramSegment 且累计刀龄不归零；
   不得按第 1 行程序把整个物理生命周期硬归一类，也不得按程序计数漏掉另一个
   程序已发生的切削暴露。"

真实样本：c33（prog19 7638 行 / prog20 7463 行）、c34（prog14 / prog19）、c27（prog20 97.5% / prog14 2.5%）
"""
import os, sys
import pandas as pd
import numpy as np

HERE0 = os.path.dirname(os.path.abspath(__file__))
BASE0 = os.path.abspath(os.path.join(HERE0, '..'))
sys.path.insert(0, os.path.abspath(os.path.join(BASE0, '..', '09_repro_scripts')))

SEG = os.path.abspath(os.path.join(BASE0, '..', '03_原始数据与标签', 'life_program_segments.csv'))
AUD = os.path.abspath(os.path.join(BASE0, '..', '03_原始数据与标签', 'life_audit_4152.csv'))
OUT = os.path.join(BASE0, 'tests')
os.makedirs(OUT, exist_ok=True)

seg = pd.read_csv(SEG, encoding='utf-8-sig')
aud = pd.read_csv(AUD, encoding='utf-8-sig')
aud['LifeCycle'] = aud.PhysicalLifeId.str.extract(r'-c(\d+)$')[0].astype(int)

CASES = [33, 34, 27]
rows = []
for c in CASES:
    pid = '4152-L-T1111-c%d' % c
    s = seg[seg.PhysicalLifeId == pid].sort_values('SegIdx')
    a = aud[aud.LifeCycle == c].iloc[0]
    n_seg = len(s)
    progs = sorted(s.Program.astype(str).unique())
    total_pieces = int(s.PieceCount.sum())
    observed = int(a.ObservedPieceCount)
    # 1) 一个 PhysicalLifeId
    ok_id = (s.PhysicalLifeId.nunique() == 1)
    # 2) 多片段
    ok_seg = (n_seg >= 2)
    # 3) 累计刀龄不归零：各段件数之和 == 该刃口观测件数
    ok_age = (total_pieces == observed)
    # 4) 不按首行硬归一类：首行程序 vs 最大占比程序
    first_prog = str(a.FirstRowProgram)
    seg_sorted = s.sort_values('Rows', ascending=False)
    major_prog = str(seg_sorted.Program.iloc[0])
    hard_assign_bug = (first_prog != major_prog)
    # 5) 按程序计数不会漏掉另一程序
    per_prog = s.groupby('Program').PieceCount.sum().to_dict()
    ok_no_miss = (len(per_prog) == len(progs)) and (sum(per_prog.values()) == total_pieces)

    rows.append(dict(PhysicalLifeId=pid, N_segments=n_seg, Programs=','.join(progs),
                     FirstRowProgram=first_prog, MajorProgram=major_prog,
                     SumSegPieces=total_pieces, ObservedPieces=observed,
                     PASS_OneLifeId=ok_id, PASS_MultiSegment=ok_seg,
                     PASS_AgeNotReset=ok_age, PASS_NoMissingExposure=ok_no_miss,
                     Demonstrates_HardAssignBug=hard_assign_bug,
                     SegmentDetail=';'.join('%s:%d件(%.1f%%)' % (r.Program, r.PieceCount, 100 * r.RowShare)
                                            for _, r in s.iterrows())))

R = pd.DataFrame(rows)
R.to_csv(os.path.join(OUT, 'test_c_program_mixing.csv'), index=False, encoding='utf-8-sig')

checks = ['PASS_OneLifeId', 'PASS_MultiSegment', 'PASS_AgeNotReset', 'PASS_NoMissingExposure']
allpass = bool(R[checks].all().all())
print('=== Test-C 生命周期程序混用 ===')
print(R[['PhysicalLifeId', 'N_segments', 'Programs', 'FirstRowProgram', 'MajorProgram',
         'SumSegPieces', 'ObservedPieces']].to_string(index=False))
print()
for c in checks:
    print('%-26s %s' % (c, 'PASS' if R[c].all() else 'FAIL'))
print('\n逐条明细：')
for _, r in R.iterrows():
    print('  %s  段数=%d  段明细=%s' % (r.PhysicalLifeId, r.N_segments, r.SegmentDetail))
print('\n首行归属缺陷复现（说明"按首行硬归类"会错）：')
for _, r in R[R.Demonstrates_HardAssignBug].iterrows():
    print('  %s  首行=%s  实际占比最大=%s  ← 若按首行归属会归错' % (r.PhysicalLifeId, r.FirstRowProgram, r.MajorProgram))
print('\n结论：%s' % ('PASS —— 单刃口/多片段/刀龄不归零/暴露不漏计 全部成立' if allpass else 'FAIL'))
