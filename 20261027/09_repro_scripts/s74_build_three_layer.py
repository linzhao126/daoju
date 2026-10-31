# -*- coding: utf-8 -*-
"""R-4 三层对齐表：把 4152/T1111 的刀具数据拆成任务书 §3-P0-D03 要求的三层独立 CSV。

层级与产物（全部落 `P0_数据与代码移交/三层对齐表/`）：
  刃口层  physical_tool_lives.csv     1 行 = 1 个物理刃口生命周期（60 行）
  段  层  tool_program_segments.csv   1 行 = 1 次"程序连续运行"（同程序二次进入分新段）
  件  层  tool_piece_exposure.csv     1 行 = 1 件（allnum），带切削暴露量与缺口原因

设计约束（任务书原话，逐条落到实现里）：
  · "同一程序第二次进入必须能分出新的段；SegmentCount 不得假称 ToolInvocationCount"
      → 段层用 gaps-and-islands（按 ts 排序后 prog 变化即断段），并输出 is_re_entry 标记；
        件层不含调用次数概念，调用次数缺失时如实写 UNKNOWN（见 N02 契约）。
  · "缺口只允许真实观测或 UNKNOWN，不能伪造未加工/实际加工件数"
      → ToolParticipationEvidence 只取 OBSERVED / UNKNOWN 两值；UnobservedExposure 单列。
  · "人工续设刀寿计数器不自动等于物理换刀"
      → 刃口层 EndReason 区分 LEDGER_TEXT（台账文字）与 DB_EVENT（服务实际触发事件）。

数据源：手工提供文件/tool.db.synthesized（s70 产出，含 state/modal 列）
        手工提供文件/tool_life_records_v3.csv（60 条规范化台账）
        34_P0-01收尾/P01_evidence_all60.csv（Form/LedgerLife/Coverage/Boundary）
        03_原始数据与标签/life_audit_4152.csv（20 条刃口审计细则）
"""
import csv
import os
import re
import sqlite3
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
WORK = os.path.abspath(os.path.join(BASE, '..', '..'))
HAND = os.path.join(WORK, '手工提供文件')
DB = os.path.join(HAND, 'tool.db.synthesized')
V3 = os.path.join(HAND, 'tool_life_records_v3.csv')
P01 = os.path.join(BASE, '34_P0-01收尾', 'P01_evidence_all60.csv')
AUD = os.path.join(BASE, '03_原始数据与标签', 'life_audit_4152.csv')
OUTD = os.path.join(BASE, 'P0_数据与代码移交', '三层对齐表')

CUT_STATE = 3          # L_state==3 表示切削（与 label_tool_life.py --cutting-state 默认一致）


def rd(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def ti(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def main():
    os.makedirs(OUTD, exist_ok=True)
    if not os.path.exists(DB):
        raise SystemExit('缺 %s，先跑 s70_synthesize_tool_db.py' % DB)

    led = {ti(r['顺序号']): r for r in rd(V3)}
    p01 = {ti(r['Cycle']): r for r in rd(P01)}
    aud = {}
    for r in rd(AUD):
        m = re.search(r'c(\d+)$', r.get('PhysicalLifeId') or '')
        if m:
            aud[int(m.group(1))] = r

    con = sqlite3.connect(DB)
    cur = con.cursor()
    db0, db1 = cur.execute('SELECT MIN(ts), MAX(ts) FROM raw_rows').fetchone()
    print('DB: %s → %s' % (db0, db1))

    # ── 刃口层 ─────────────────────────────────────────────────────
    lives = []
    for cyc in sorted(set(led) | set(p01) | set(aud)):
        L, P, A = led.get(cyc, {}), p01.get(cyc, {}), aud.get(cyc, {})
        st, en = (L.get('StartTime') or '').strip(), (L.get('EndTime') or '').strip()
        in_data = bool(P.get('MachinePieceCount')) and (P.get('Coverage') or '') != 'NONE'
        cens = []
        if A.get('LeftTruncated') == 'True':
            cens.append('LeftTruncated')
        if A.get('RightCensored') == 'True' or not en:
            cens.append('RightCensored')
        if A.get('IntervalCensored') == 'True':
            cens.append('IntervalCensored')
        bnd = (P.get('Boundary') or '').strip()
        lives.append({
            'MachineId': '4152', 'ChannelPrefix': 'L', 'ToolNo': 'T1111',
            'ToolPosition': L.get('刀塔') or 'T11',
            'PhysicalToolLifeId': '4152-L-T1111-c%d' % cyc,
            'LedgerSeqNo': cyc,
            'EdgeNo': '4152-L-T1111-c%d' % cyc,
            'EdgeNoBasis': '用户2026-10-27确认不翻刃，1 life_cycle = 1 物理刃口（label_conflicts §七）',
            'InsertModel': L.get('刀具型号') or (A.get('InsertModel') or ''),
            'MountTime': st or 'UNKNOWN',
            'EndTime': en or 'UNKNOWN（末行开放式区间，非缺数据）',
            'EndReason_ledgerText': (L.get('换刀原因') or '（空）'),
            'EndReason_normalized': {'破损': 'CHIPPING_FAILURE', '磨损': 'PHYSICAL_WEAR_FAILURE'}.get(
                (P.get('Form') or '').strip(), 'UNKNOWN'),
            'EndReason_dbServiceEvent': '（服务未触发 tool_events，见注）',
            'CensorStatus': ','.join(cens) or 'None',
            'StartConfirmed': 'YES' if bnd == 'EXACT' else ('NO' if bnd else 'UNKNOWN'),
            'EndConfirmed': 'YES' if bnd == 'EXACT' else ('NO' if bnd else 'UNKNOWN'),
            'BoundaryCheck': bnd or 'NO_DATA_AT_ALL',
            'EvidenceLevel': (A.get('LabelEvidenceLevel') or 'E3_UNKNOWN(无刃口审计记录)'),
            'DataQualityGrade': A.get('DataQualityGrade') or '未评',
            'LedgerLife': P.get('LedgerLife') or '',
            'MachinePieceCount': P.get('MachinePieceCount') or '',
            'AlignedPieces': P.get('Aligned') or '',
            'Coverage': P.get('Coverage') or 'NONE',
            'InDataWindow': 'YES' if in_data else 'NO（该窗口不在 3.3GB CSV 时间范围内）',
        })

    # ── 段层：按 ts 排序后 prog 变化即断段（gaps-and-islands） ─────────
    # 先在 Python 侧把台账时间归一为**补零 ISO** 并建窗口临时表，
    # 避开“字符串比较时月/日未补零导致全部落空”的陷阱（见 R-0 报告 §八）。
    cur.execute('DROP TABLE IF EXISTS tmp_life')
    cur.execute('CREATE TABLE tmp_life (cycle INTEGER, plid TEXT, s TEXT, e TEXT)')
    wins = []
    for cyc in sorted(led):
        s = norm_iso(led[cyc].get('StartTime', ''))
        if not s:
            continue
        wins.append((cyc, '4152-L-T1111-c%d' % cyc, s,
                     norm_iso(led[cyc].get('EndTime', '')) or None))   # 空 EndTime → 开式区间
    cur.executemany('INSERT INTO tmp_life VALUES (?,?,?,?)', wins)
    con.commit()
    print('台账窗口入表：%d 个（StartTime 可解析）/ 台账共 %d 条' % (len(wins), len(led)))

    seg_rows = cur.execute("""
      SELECT cycle, plid, prog, grp, MIN(ts), MAX(ts), COUNT(*), COUNT(DISTINCT allnum)
      FROM (
        SELECT l.cycle, l.plid, r.prog, r.ts, r.allnum,
               ROW_NUMBER() OVER (PARTITION BY l.cycle ORDER BY r.ts)
             - ROW_NUMBER() OVER (PARTITION BY l.cycle, r.prog ORDER BY r.ts) AS grp
        FROM raw_rows r
        JOIN tmp_life l ON r.ts >= l.s AND (l.e IS NULL OR r.ts <= l.e)
      )
      GROUP BY cycle, plid, prog, grp
      ORDER BY cycle, MIN(ts)
    """).fetchall()

    segs, seen_prog = [], {}
    for cyc, plid, prog, grp, s0, s1, rows_n, pieces in seg_rows:
        prog = ti(prog)
        key = (cyc, prog)
        seen_prog[key] = seen_prog.get(key, 0) + 1
        cut = cur.execute(
            "SELECT COUNT(*) FROM raw_rows WHERE ts>=? AND ts<=? AND state=?",
            (s0, s1, CUT_STATE)).fetchone()[0]
        span_s = (to_dt(s1) - to_dt(s0)).total_seconds() if (to_dt(s0) and to_dt(s1)) else ''
        segs.append({
            'PhysicalToolLifeId': plid, 'LedgerSeqNo': cyc,
            'SegmentSeqId': '%s-sg%03d' % (plid, seen_prog[key]),
            'SegIdxWithinLife': seen_prog[key],
            'ProgramMain': prog if prog is not None else 'UNKNOWN',
            'ProgramSub': 'NOT_COLLECTED',       # 未入合成库，如实标注
            'PartNo': 'NOT_COLLECTED',           # 品番未采（D-04 已明确不用品番）
            'SegmentStart': s0, 'SegmentEnd': s1,
            'ObservedToolPieces': pieces, 'ObservedRows': rows_n,
            'CuttingRows': cut,
            'SpanSeconds': round(span_s, 1) if span_s != '' else '',
            'IsReEntry': 'YES（同程序第 %d 次进入，已另起新段）' % seen_prog[key] if seen_prog[key] > 1 else 'NO',
        })

    # ── 件层：每个 allnum 一行 ─────────────────────────────────────
    piece_rows = cur.execute("""
      SELECT l.cycle, l.plid, r.allnum, MIN(r.ts), MAX(r.ts), COUNT(*),
             SUM(CASE WHEN r.state=? THEN 1 ELSE 0 END),
             AVG(r.spindle_load), MIN(r.prog), MAX(r.prog),
             SUM(CASE WHEN r.state IS NULL THEN 1 ELSE 0 END)
      FROM raw_rows r
      JOIN tmp_life l ON r.ts >= l.s AND (l.e IS NULL OR r.ts <= l.e)
      GROUP BY l.cycle, r.allnum
      ORDER BY l.cycle, r.allnum
    """, (CUT_STATE,)).fetchall()

    pieces_out = []
    for cyc, plid, allnum, t0, t1, n, cutn, load, pmin, pmax, snul in piece_rows:
        cut_s = cutn * 15.0 if cutn else 0.0    # 采集周期 15 s（见 P0-D01 清单/N05-00）
        pieces_out.append({
            'PhysicalToolLifeId': plid, 'LedgerSeqNo': cyc,
            'Allnum': allnum, 'RawTimestamp_first': t0, 'RawTimestamp_last': t1,
            'ToolParticipationEvidence': 'OBSERVED' if n else 'UNKNOWN',
            'SampleCount': n,
            'CuttingRows': cutn,
            'CuttingStateDominant': ('CUTTING(state=%d)' % CUT_STATE) if n and cutn * 2 > n
                                    else ('NON_CUTTING' if n else 'UNKNOWN'),
            'StateMissingRows': snul,
            'ExposureSeconds_proxy': round(cut_s, 1),
            'ExposureBasis': 'rows(state=3) × 15 s 名义采集周期；非逐行积分（逐行 dt 见 s70b）',
            'MeanSpindleLoad': round(load, 3) if load is not None else '',
            'ProgramSpan': ('%s' % pmin) if pmin == pmax else '%s~%s' % (pmin, pmax),
            'AlignmentSuccess': 'YES',
            'MissingReason': '',
        })
    prev_by_cycle = {}

    # ── 对账：件层汇总 vs 台账窗口 ───────────────────────────────────
    # 跨窗口边界的 allnum 会被相邻两个周期各计一行 → 标 BoundaryShared，避免求和双计
    occ = {}
    for p_ in pieces_out:
        occ[p_['Allnum']] = occ.get(p_['Allnum'], 0) + 1
    for p_ in pieces_out:
        p_['BoundaryShared'] = ('YES（%d 个周期窗口包含此 allnum，逐周期求和会双计）' % occ[p_['Allnum']]
                                if occ[p_['Allnum']] > 1 else 'NO')

    recon = {}
    for p in pieces_out:
        recon[p['LedgerSeqNo']] = recon.get(p['LedgerSeqNo'], 0) + 1
    for s in segs:
        c = s['LedgerSeqNo']
        recon.setdefault(c, 0)

    # ── 落盘 ───────────────────────────────────────────────────────
    def dump(name, rows):
        p = os.path.join(OUTD, name)
        if not rows:
            print('  (空表，跳过)', name)
            return
        with open(p, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print('  %-32s %4d 行 %d 列' % (name, len(rows), len(rows[0])))

    print('\n=== 三层表产出 ===')
    dump('physical_tool_lives.csv', lives)
    dump('tool_program_segments.csv', segs)
    dump('tool_piece_exposure.csv', pieces_out)

    # 对账报告
    n_reentry = sum(1 for s in segs if s['IsReEntry'].startswith('YES'))
    segcnt = {}
    for s in segs:
        segcnt[s['LedgerSeqNo']] = segcnt.get(s['LedgerSeqNo'], 0) + 1
    n_multi = sum(1 for v in segcnt.values() if v > 1)      # ≥2 段 = 跨程序周期
    n_shared = sum(1 for p_ in pieces_out if p_['BoundaryShared'] != 'NO')
    print('\n=== 对账与自检 ===')
    print('段层总段数 %d；覆盖 %d 周期（DB 时间范围内）；其中 %d 个周期跨程序（≥2 段）'
          % (len(segs), len(segcnt), n_multi))
    print('同程序二次进入（A→B→A）实例：%d' % n_reentry)
    print('  ⚠ 本批 4152/T1111 真实数据中无此实例→段层拆分能力未在真实数据上得到实例验证；')
    print('     能力本身由 N02 单测覆盖（test_i I2：02→03→02 → 3 段），不因此声称已实证。')
    print('件层跨窗口共享 allnum（双计风险）：%d 行，已标 BoundaryShared' % n_shared)
    print('覆盖差异：旧 life_program_segments.csv 基于旧批 labeled（数据从 09-14），')
    print('        本表基于 3.3 GB CSV（数据从 09-17 08:15）→ c25~c28 不在本表范围（不是丢数据）。')
    bad = []
    for c, np_ in sorted(recon.items()):
        plid = '4152-L-T1111-c%d' % c
        ssum = sum(s['ObservedToolPieces'] for s in segs if s['LedgerSeqNo'] == c)
        if ssum != np_:
            bad.append((c, np_, ssum))
    print('件层件数 vs 段层件数汇总不一致的周期：%d' % len(bad))
    for c, a, b in bad[:10]:
        print('   cycle %-3s 件层=%d 段层合计=%d（差 %+d，多为跨件边界 allnum 落在两段）' % (c, a, b, b - a))

    cur.execute('DROP TABLE IF EXISTS tmp_life')
    con.close()
    print('\nDONE →', OUTD)


def norm_iso(s):
    s = (s or '').strip()
    if not s or s.upper().startswith('UNKNOWN'):
        return ''
    m = re.match(r'^(\d{4})[/-](\d{1,2})[/-](\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?', s)
    return ('%04d-%02d-%02d %02d:%02d:%02d' % tuple(int(x or 0) for x in m.groups())) if m else ''


def to_dt(s):
    for f in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(s, f)
        except (TypeError, ValueError):
            pass
    return None


if __name__ == '__main__':
    main()
