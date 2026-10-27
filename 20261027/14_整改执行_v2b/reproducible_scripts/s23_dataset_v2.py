# -*- coding: utf-8 -*-
"""s23_dataset_v2.py — FIX-03：物理刃口 × 程序 Segment 数据集（Version A 严格 / Version B 分层）

按审核 P0-03 的要求
  · 外层 Group = 物理刃口（用户已确认不翻刃 → life_cycle 1:1）
  · 内部 Segment = 主程序连续片段
  · 不许"把首行程序当整周期程序"；也不许"按程序计数漏掉另一程序的切削暴露"
  · 混合周期不剔除即弃，而是要**分层**；严格版另给一套

Version A（严格）：MinorProgramShare == 0 且 DQ ∈ {A,B} 且 台账边界闭合
Version B（分层）：全部 16 个 LOCO 周期，附 Segment 视图

字段补齐（审核点名三项）
  ToolInvocationIndex      —— 现有数据无法得到（L_sub_program 切削时为空、刀号恒 T1111）
                              → 填 UNKNOWN，另给 EffectiveCutSegmentCount 作替代并注明
  EffectiveCutSegmentCount —— 该刃口下"对齐成功"的件数（等价于有效切削段数）
  EquivalentCutExposure    —— Σ(X 扫掠跨度)，单位 mm；跨程序累加时不归零

输出（14_整改执行_v2b/dataset/）
  life_audit_v3.csv / life_segments_v3.csv / sample_versions.csv / exclusion_log.csv
  exposure_check.csv
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import hi_asof as HA

HERE = os.path.dirname(__file__)
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))
RAW = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv')
AUD = os.path.abspath(os.path.join(HERE, '..', '03_原始数据与标签', 'life_audit_4152.csv'))
SEG = os.path.abspath(os.path.join(HERE, '..', '03_原始数据与标签', 'life_program_segments.csv'))
HI_CSV = os.path.join(ROOT, 'out_T1111', 'xalign_HI.csv')
OUT = os.path.abspath(os.path.join(HERE, '..', '14_整改执行_v2b', 'dataset'))
os.makedirs(OUT, exist_ok=True)

aud = pd.read_csv(AUD, encoding='utf-8-sig')
seg = pd.read_csv(SEG, encoding='utf-8-sig')
hi = pd.read_csv(HI_CSV, encoding='utf-8-sig')
aligned = hi.groupby('life_cycle').size().to_dict()
aligned_prog = hi.groupby('life_cycle')['prog'].first().to_dict()

# ---------------- 等效切除暴露：逐周期 Σ(x_hi − x_lo)
aud['LifeCycle'] = aud.PhysicalLifeId.str.extract(r'-c(\d+)$')[0].astype(int)
idx = HA.build_piece_index(RAW, cycles=sorted(aud.LifeCycle.tolist()))
exp_rows = []
for c in sorted(idx):
    pieces, rows = idx[c]['pieces'], idx[c]['rows']
    z0, segs = HA.compute_segments(pieces, rows)
    if not segs:
        exp_rows.append(dict(LifeCycle=c, SweepPieces=0, MeanSweep_mm=np.nan, Exposure_mm=np.nan))
        continue
    spans = np.array([s['r']['x_hi'] - s['r']['x_lo'] for s in segs], float)
    exp_rows.append(dict(LifeCycle=c, SweepPieces=len(spans),
                         MeanSweep_mm=round(float(np.mean(spans)), 3),
                         MedianSweep_mm=round(float(np.median(spans)), 3),
                         Exposure_mm=round(float(np.sum(spans)), 1)))
E = pd.DataFrame(exp_rows)
E.to_csv(os.path.join(OUT, 'exposure_check.csv'), index=False, encoding='utf-8-sig')
print('[exposure] %d 周期；平均扫掠跨度 %.2f mm'
      % (len(E), E.MeanSweep_mm.mean()))

# ---------------- 逐刃口表 v3
A = aud.copy()
A = A.merge(E, on='LifeCycle', how='left')
A['EffectiveCutSegmentCount'] = A.LifeCycle.map(aligned).fillna(0).astype(int)
A['ToolInvocationIndex'] = 'UNKNOWN(无NC调用数据:L_sub_program切削时为空,刀号恒T1111)'
A['AlignedProg'] = A.LifeCycle.map(aligned_prog)

# 边界证据等级
def boundary(r):
    if r.DataQualityGrade == 'D':
        return 'DATA_EDGE_OR_FROZEN'
    if r.PhysicalLifeId.endswith('c38'):
        return 'LEDGER_OPEN(台账EndTime为空)'
    if r.LeftTruncated:
        return 'DATA_EDGE(数据起点)'
    return 'LEDGER_EXACT'

A['BoundaryEvidence'] = A.apply(boundary, axis=1)

# ---------------- 版本划分
def reason_exclude(r):
    if r.DataQualityGrade == 'D':
        return 'DQ=D(不可用周期)'
    if r.MinorProgramShare > 0:
        return 'PROGRAM_MIXED(%.1f%%)' % (100 * r.MinorProgramShare)
    if r.BoundaryEvidence.startswith('LEDGER_OPEN'):
        return 'LEDGER_OPEN(边界未闭合)'
    if r.LeftTruncated:
        return 'LEFT_TRUNCATED(数据起点)'
    return ''

A['ExcludeReason_A'] = A.apply(reason_exclude, axis=1)
A['InVersionA'] = (A.ExcludeReason_A == '').astype(int)
# Version B：v1/v2 回测用的 16 个周期（同程序 >=3 且件数 >=25）
ncyc = A[A.LifeCycle.isin(aligned)].groupby('AlignedProg').LifeCycle.nunique()
A['InVersionB'] = A.apply(lambda r: int(r.LifeCycle in aligned and
                                        ncyc.get(r.AlignedProg, 0) >= 3 and aligned[r.LifeCycle] >= 25), axis=1)

out_cols = ['PhysicalLifeId', 'LifeCycle', 'AlignedProg', 'MinorProgramShare', 'DataQualityGrade',
            'NormLabel', 'ChangeReasonRaw', 'ObservedPieceCount', 'AlignedPieceCount',
            'EffectiveCutSegmentCount', 'ToolInvocationIndex', 'MeanSweep_mm', 'Exposure_mm',
            'BoundaryEvidence', 'LeftTruncated', 'RightCensored', 'IntervalCensored',
            'ExcludeReason_A', 'InVersionA', 'InVersionB']
out_cols = [c for c in out_cols if c in A.columns]
A[out_cols].to_csv(os.path.join(OUT, 'life_audit_v3.csv'), index=False, encoding='utf-8-sig')

# ---------------- Segment 视图（补暴露量）
S = seg.copy()
S['LifeCycle'] = S.PhysicalLifeId.str.extract(r'-c(\d+)$')[0].astype(int)
S['PieceShare'] = (S.PieceCount / S.groupby('PhysicalLifeId').PieceCount.transform('sum')).round(4)
S.to_csv(os.path.join(OUT, 'life_segments_v3.csv'), index=False, encoding='utf-8-sig')

# ---------------- 样本清单与淘汰日志
verA = A[A.InVersionA == 1].PhysicalLifeId.tolist()
verB = A[A.InVersionB == 1].PhysicalLifeId.tolist()
pd.DataFrame([
    dict(Version='A_strict', N=len(verA), Cycles=','.join(verA)),
    dict(Version='B_layered', N=len(verB), Cycles=','.join(verB)),
]).to_csv(os.path.join(OUT, 'sample_versions.csv'), index=False, encoding='utf-8-sig')

EX = A[A.ExcludeReason_A != ''][['PhysicalLifeId', 'AlignedProg', 'MinorProgramShare',
                                 'DataQualityGrade', 'BoundaryEvidence', 'ExcludeReason_A']]
EX.to_csv(os.path.join(OUT, 'exclusion_log.csv'), index=False, encoding='utf-8-sig')

print('\n=== 版本划分 ===')
print('Version A（严格）%d 把：%s' % (len(verA), ','.join(x[-4:] for x in verA)))
print('Version B（分层）%d 把：%s' % (len(verB), ','.join(x[-4:] for x in verB)))
print('\n=== 淘汰日志（Version A）===')
print(EX.to_string(index=False))
print('\n=== 每把刀的暴露量 ===')
print(A[A.InVersionB == 1][['PhysicalLifeId', 'AlignedProg', 'AlignedPieceCount',
                            'EffectiveCutSegmentCount', 'MeanSweep_mm', 'Exposure_mm']]
      .to_string(index=False))
