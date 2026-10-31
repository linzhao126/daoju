# -*- coding: utf-8 -*-
"""s01_build_audit.py — P0-02 4152/T1111 生命周期、程序、标签逐刀审计

只读。输入：
  RAW   = influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv
  LEDGER= tool_life_records.csv
  HI    = out_T1111/xalign_HI.csv
输出（全部落在 03_原始数据与标签/）：
  life_audit_4152.csv / life_program_segments.csv / source_log_mapping.csv / label_conflicts.md

设计要点（对齐任务书 P0-02）
  1) 一条 life_cycle = 一个物理刃口（用户 2026-10-27 确认：不翻刃，换上即新刀）
     → EdgeId 恒等于 PhysicalLifeId，不做刃口拆分
  2) 「已换刀」≠「物理失效」：NormLabel 双标签（磨损/破损 + 是否仅达设定寿命）
  3) 台账与数据编号体系不同 → 只做时间×件数×allnum 三重匹配，不合并主键
  4) 不猜缺口：件间空档以「本周期件间间隔中位数」为基准，区分
       ShiftGap(>6h，交班/过夜，通常是停机) 与 IdleGap(30min~6h，待机) 与 Gap(>30min)
     无法区分「停机」与「采集失败」— 需要机床心跳，库中没有，明确标 UNKNOWN
"""
import os, re, hashlib
import numpy as np
import pandas as pd

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import wsroot as _wsroot          # G2：不再数 '..'，按工作区特征向上定位
ROOT = _wsroot.find(_os.path.dirname(_os.path.abspath(__file__)))
RAW = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv')
LEDGER = os.path.join(ROOT, 'tool_life_records.csv')
HI = os.path.join(ROOT, 'out_T1111', 'xalign_HI.csv')
OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '03_原始数据与标签'))

DEV, CH, TOOL = '4152', 'L', 'T1111'
GAP_IDLE = 1800      # 30min：超过此值记一次「空档」
GAP_SHIFT = 21600    # 6h  ：超过此值视为交班/过夜


def sha16(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()[:16]


def prog_no(s):
    return str(s).split('(')[0].strip()


cols = ['_time', 'life_cycle', 'L_main_program', 'L_allnum', 'L_abso_z',
        'L_spindle_load', 'tool_model', 'change_reason', 'cycle_cover_ratio']
d = pd.read_csv(RAW, usecols=cols, encoding='utf-8-sig')
d['_t'] = pd.to_datetime(d['_time'], format='%Y-%m-%d %H:%M:%S.%f', errors='coerce')
d = d.sort_values(['life_cycle', '_t']).reset_index(drop=True)
T_MIN, T_MAX = d['_t'].min(), d['_t'].max()
print('[raw] rows=%d cycles=%d  %s ~ %s' % (len(d), d['life_cycle'].nunique(), T_MIN, T_MAX))

hi = pd.read_csv(HI, encoding='utf-8-sig')
hi_n = hi.groupby('life_cycle').size().to_dict()
hi_prog = hi.groupby('life_cycle')['prog'].first().to_dict()

rows, segs = [], []
for c, g in d.groupby('life_cycle'):
    pid = '%s-%s-%s-c%d' % (DEV, CH, TOOL, c)
    t0, t1 = g['_t'].min(), g['_t'].max()
    an = pd.to_numeric(g['L_allnum'], errors='coerce').dropna().astype(int)
    an0, an1 = int(an.min()), int(an.max())
    piec = int(an.nunique())
    span = an1 - an0 + 1

    # ---- 件内/件间时间结构：以「件」为单位，不看行
    gp = g.groupby('L_allnum')['_t'].agg(['min', 'max']).sort_values('min')
    inter = gp['min'].diff().dt.total_seconds().dropna()
    med_gap = float(inter.median()) if len(inter) else np.nan
    gaps = inter[inter > GAP_IDLE]
    n_shifts = int((gaps > GAP_SHIFT).sum())
    n_idle = int(((gaps > GAP_IDLE) & (gaps <= GAP_SHIFT)).sum())
    max_gap = float(inter.max()) if len(inter) else np.nan

    prog_vc = g['L_main_program'].dropna().value_counts()
    prog_set = ';'.join('%s(%d)' % (prog_no(k), v) for k, v in prog_vc.items())
    first_prog = prog_no(g['L_main_program'].dropna().iloc[0])
    reason = g['change_reason'].dropna().iloc[0] if g['change_reason'].notna().any() else ''
    model = str(g['tool_model'].dropna().iloc[0]).strip() if g['tool_model'].notna().any() else ''
    cover = float(pd.to_numeric(g['cycle_cover_ratio'], errors='coerce').median()) \
        if g['cycle_cover_ratio'].notna().any() else np.nan
    minor = float(1 - prog_vc.iloc[0] / prog_vc.sum()) if len(prog_vc) > 1 else 0.0

    # ---- 规范标签
    has_break = ('破损' in str(reason)) or ('崩' in str(reason))
    has_wear = ('磨损' in str(reason)) and not has_break
    lab = 'CHIPPING_FAILURE' if has_break else ('PHYSICAL_WEAR_FAILURE' if has_wear else 'UNKNOWN')
    planned = bool(re.search(r'刀[寿寿]\s*\d+', str(reason)) or '设定寿命' in str(reason))

    # ---- 截断 / 删失
    left_trunc = bool(t0 <= T_MIN + pd.Timedelta(minutes=5)) or piec <= 5
    right_cens = bool(t1 >= T_MAX - pd.Timedelta(hours=1))
    interval_cens = bool(span <= 2 and len(g) > 1000)

    # ---- 质量分级
    if interval_cens or piec < 5:
        grade = 'D'
    elif left_trunc or minor > 0.05 or (span - piec) / max(span, 1) > 0.10:
        grade = 'C'
    elif n_idle > 0 or n_shifts > 0:
        grade = 'B'
    else:
        grade = 'A'

    rows.append(dict(
        PhysicalLifeId=pid, LifeSourceId='life_cycle=%d' % c,
        DeviceId=DEV, ChannelPrefix=CH, ToolNo=TOOL, InsertModel=model,
        EdgeId=pid, ChangeReasonRaw=reason,
        StartTime=str(t0), EndTime=str(t1),
        StartAllNum=an0, EndAllNum=an1, AllNumSpan=span,
        AllNumContiguous='YES' if span == piec else 'NO(%d missing)' % (span - piec),
        TimeSource='FOCAS _time', AllNumSource='L_allnum',
        StartConfidence='HIGH' if not left_trunc else 'LOW(左截断)',
        EndConfidence='LOW(右删失)' if right_cens else 'HIGH',
        ProgramSet=prog_set, FirstRowProgram=first_prog,
        AlignedProgForM0=hi_prog.get(c, ''), AlignedPieceCount=int(hi_n.get(c, 0)),
        AlignedCoverage=round(int(hi_n.get(c, 0)) / piec, 4) if piec else '',
        MinorProgramShare=round(minor, 4),
        ProgramSwitchRows=int(len(g) - prog_vc.iloc[0]) if len(prog_vc) > 1 else 0,
        ObservedPieceCount=piec, ObservedRows=int(len(g)),
        MedianPieceGapSec=round(med_gap, 1) if med_gap == med_gap else '',
        MaxPieceGapSec=round(max_gap, 1) if max_gap == max_gap else '',
        IdleGapCount=n_idle, ShiftGapCount=n_shifts,
        GapAmbiguity='无法区分停机/采集失败(无机床心跳)',
        HasTipBreakage='YES' if has_break else 'NO',
        HasQualityAnomaly='UNKNOWN(原因文字无尺寸/粗糙度数值)',
        IsPlannedLifeOnly='YES' if planned else 'NO',
        SameEdgeContinued='NO(用户确认不翻刃)',
        NormLabel=lab,
        NormLabelSecondary='PLANNED_LIFE_LIMIT' if planned else '',
        LabelEvidenceLevel='E2(仅人工文字)' if (has_break or has_wear) else 'E4(未知)',
        LeftTruncated=left_trunc, RightCensored=right_cens, IntervalCensored=interval_cens,
        CycleCoverRatio=round(cover, 4) if cover == cover else '',
        DataQualityGrade=grade,
        SourceFile=os.path.basename(RAW), SourceHash=sha16(RAW)))

    g2 = g.sort_values('_t').reset_index(drop=True)
    pn = g2['L_main_program'].ffill()
    brk = (pn != pn.shift()).cumsum()
    for si, (_, s) in enumerate(g2.groupby(brk), 1):
        a = pd.to_numeric(s['L_allnum'], errors='coerce').dropna().astype(int)
        segs.append(dict(
            PhysicalLifeId=pid, SegIdx=si, Program=prog_no(pn.loc[s.index[0]]),
            StartTime=str(s['_t'].min()), EndTime=str(s['_t'].max()),
            StartAllNum=int(a.min()) if len(a) else '', EndAllNum=int(a.max()) if len(a) else '',
            Rows=len(s), PieceCount=int(a.nunique()) if len(a) else 0,
            RowShare=round(len(s) / len(g2), 4)))

audit = pd.DataFrame(rows).sort_values('StartTime')
segs = pd.DataFrame(segs)

# ---------------------------------------------------------------- 台账解析与匹配
led = pd.read_csv(LEDGER, encoding='utf-8-sig')
led.columns = [c.strip() for c in led.columns]


def parse_t(s):
    s = str(s).strip().replace('：', ':').replace('/', '-')
    # 台账 3 类脏格式：
    #   `2026/9/18/16:26`  日期与时间之间误用斜杠 → 2026-9-18 16:26
    #   `2026/9/416:05`    空格丢失           → 2026-9-4 16:05
    s = re.sub(r'^(\d{4})-(\d{1,2})-(\d{1,2})-(\d{1,2}:\d{2})', r'\1-\2-\3 \4', s)
    s = re.sub(r'^(\d{4})-(\d{1,2})-(\d)(\d{2}:\d{2})', r'\1-\2-\3 \4', s)
    for f in ('%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S'):
        try:
            return pd.to_datetime(s, format=f)
        except Exception:
            pass
    return pd.NaT


led['t0'] = led['StartTime'].map(parse_t)
led['t1'] = led['EndTime'].map(parse_t)
led['DaoShou'] = led['换刀原因'].astype(str).str.extract(r'刀[寿寿]\s*(\d+)')[0]

# 时间窗 → 原始数据里 distinct allnum 的件数（独立复算，用于核对 13/13）
ans = d[['_t', 'L_allnum']].dropna()

map_rows = []
for _, r in led.iterrows():
    hit, pw = [], ''
    if pd.notna(r['t0']):
        t1e = r['t1'] if pd.notna(r['t1']) else r['t0'] + pd.Timedelta(days=1)
        sub = ans[(ans['_t'] >= r['t0']) & (ans['_t'] <= t1e)]
        pw = int(sub['L_allnum'].nunique())
        for _, a in audit.iterrows():
            s0, s1 = pd.Timestamp(a.StartTime), pd.Timestamp(a.EndTime)
            if min(t1e, s1) - max(r['t0'], s0) > pd.Timedelta(0):
                hit.append(a.PhysicalLifeId)
    map_rows.append(dict(
        LedgerSeq=int(r['顺序号']), LedgerStart=str(r['StartTime']), LedgerEnd=str(r['EndTime']),
        LedgerT0=str(r['t0']), LedgerT1=str(r['t1']), TimeParseOK=bool(pd.notna(r['t0'])),
        ToolModel=str(r['刀具型号']).strip(), ReasonRaw=str(r['换刀原因']).strip(),
        DaoShou=r['DaoShou'] if pd.notna(r['DaoShou']) else '',
        PiecesInLedgerWindow=pw,
        MatchedLifeIds=';'.join(hit), MatchCount=len(hit),
        MatchQuality='EXACT_1TO1' if len(hit) == 1 else ('NONE' if not hit else 'MULTI')))
mp = pd.DataFrame(map_rows)

# 逐条核对：台账窗内件数 vs 审计周期件数
chk = []
for _, r in mp.iterrows():
    if r.MatchQuality == 'EXACT_1TO1':
        a = audit[audit.PhysicalLifeId == r.MatchedLifeIds].iloc[0]
        chk.append(dict(LedgerSeq=r.LedgerSeq, PhysicalLifeId=r.MatchedLifeIds,
                        LedgerWindowPieces=r.PiecesInLedgerWindow,
                        CycleObservedPieces=a.ObservedPieceCount,
                        Equal='YES' if int(r.PiecesInLedgerWindow) == int(a.ObservedPieceCount) else 'NO'))
chk = pd.DataFrame(chk)

os.makedirs(OUT, exist_ok=True)
audit.to_csv(os.path.join(OUT, 'life_audit_4152.csv'), index=False, encoding='utf-8-sig')
segs.to_csv(os.path.join(OUT, 'life_program_segments.csv'), index=False, encoding='utf-8-sig')
mp.to_csv(os.path.join(OUT, 'source_log_mapping.csv'), index=False, encoding='utf-8-sig')
chk.to_csv(os.path.join(OUT, 'ledger_piece_check.csv'), index=False, encoding='utf-8-sig')

print('[out] life_audit_4152.csv        %d × %d' % audit.shape)
print('[out] life_program_segments.csv  %d × %d' % segs.shape)
print('[out] source_log_mapping.csv     %d × %d' % mp.shape)
print('[out] ledger_piece_check.csv     %d × %d' % chk.shape)
print('\n--- 质量分级 ---\n' + audit['DataQualityGrade'].value_counts().to_string())
print('--- 规范标签 ---\n' + audit['NormLabel'].value_counts().to_string())
print('--- 台账匹配 ---\n' + mp['MatchQuality'].value_counts().to_string())
print('--- 台账 vs 周期 件数核对 ---')
print(chk.to_string(index=False) if len(chk) else '(无)')
print('--- 多程序周期 ---')
print(audit[audit.MinorProgramShare > 0][['PhysicalLifeId', 'ProgramSet', 'MinorProgramShare',
                                         'AlignedProgForM0']].to_string(index=False))
print('--- 空档结构 ---')
print(audit[['PhysicalLifeId', 'ObservedPieceCount', 'AllNumContiguous', 'MedianPieceGapSec',
             'MaxPieceGapSec', 'IdleGapCount', 'ShiftGapCount']].to_string(index=False))
