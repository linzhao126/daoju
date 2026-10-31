# -*- coding: utf-8 -*-
"""R-3 生成《台账逐条缺陷清单》P0-D02_台账逐条缺陷清单.csv

按《TOOL-RUL-CHIP-0-REQ-20261029》§3-P0-D02 要求的 12 字段逐条填，
数据全部来自仓库既有产物做 join（不新建解释、不编造）：

  Cycle 主干        34_P0-01收尾/P01_evidence_all60.csv   （60 周期：Form/LedgerLife/Coverage/
                                                             MachinePieceCount/Aligned/Boundary/d0/d1）
  刃口层细则        03_原始数据与标签/life_audit_4152.csv  （20 周期：StartConfidence/
                                                             EndConfidence/AllNumContiguous/
                                                             GapAmbiguity/LabelEvidenceLevel/
                                                             DataQualityGrade/Left/Right/IntervalCensored）
  原因原文          手工提供文件/tool_life_records_v3.csv   （60 条 xlsx 规范化台账）
  件数对照          15_台账真值检验/truth_vs_data_pieces.csv
  副本不一致（G1）  P0_修复留痕/G1_形态分层影响面.csv       （13 改词 / 22 缺行）

争议状态按显式规则拼装，每条给出可核对的触发条件。
"""
import csv
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))          # daoju_repo/20261027
WORK = os.path.abspath(os.path.join(BASE, '..', '..'))    # c:/abc/workBoddyHome刀具
P01 = os.path.join(BASE, '34_P0-01收尾', 'P01_evidence_all60.csv')
AUD = os.path.join(BASE, '03_原始数据与标签', 'life_audit_4152.csv')
V3 = os.path.join(WORK, '手工提供文件', 'tool_life_records_v3.csv')
TVP = os.path.join(BASE, '15_台账真值检验', 'truth_vs_data_pieces.csv')
G1 = os.path.join(WORK, 'P0_修复留痕', 'G1_形态分层影响面.csv')
OUT = os.path.join(BASE, 'P0_数据与代码移交', 'P0-D02_台账逐条缺陷清单.csv')

FORM2END = {'破损': '崩刃/破损（台账文字，未独立核实）',
            '磨损': '正常磨损达工艺极限（台账文字，未独立核实）',
            '未标': '未知（台账未填原因）'}

COLS = ['PhysicalToolLifeId', '候选ID', '顺序号Cycle', '起点证据', '终点证据',
        '原始换刀原因(xlsx)', '标准化终止类型', '终止原因可信度', '台账刀寿',
        'FOCAS参与件', 'HI对齐件', '件数偏差', '时间偏差d0_min', '时间偏差d1_min',
        '缺口与证据来源', '删失状态', '数据质量级', '争议状态', '需车间回答的具体问题']


def rd(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def main():
    p01 = {to_int(r['Cycle']): r for r in rd(P01) if to_int(r.get('Cycle'))}
    aud = {}
    for r in rd(AUD):
        m = re.search(r'c(\d+)$', r.get('PhysicalLifeId') or '')
        if m:
            aud[int(m.group(1))] = r
    v3 = {to_int(r['顺序号']): r for r in rd(V3) if to_int(r.get('顺序号'))}
    tvp = {to_int(r['cycle']): r for r in rd(TVP) if to_int(r.get('cycle'))}
    g1 = {to_int(r['顺序号']): r for r in rd(G1) if to_int(r.get('顺序号'))}
    print('数据源条数：P01=%d  life_audit=%d  v3=%d  truth_vs_data=%d  G1=%d'
          % (len(p01), len(aud), len(v3), len(tvp), len(g1)))

    rows = []
    for cyc in sorted(set(p01) | set(v3) | set(aud)):
        P = p01.get(cyc, {})
        A = aud.get(cyc, {})
        V = v3.get(cyc, {})
        T = tvp.get(cyc, {})
        G = g1.get(cyc, {})

        form = (P.get('Form') or '').strip()
        reason = (V.get('换刀原因') or '').strip()
        ledger = to_int(P.get('LedgerLife'))
        mach = to_int(P.get('MachinePieceCount'))
        align = to_int(P.get('Aligned'))

        # —— 争议状态：显式规则，每条可回溯 ——
        flags = []
        if A.get('LeftTruncated') == 'True':
            flags.append('左截断(起点不可靠)')
        if A.get('RightCensored') == 'True':
            flags.append('右删失(未到终点)')
        if A.get('IntervalCensored') == 'True':
            flags.append('区间删失(计数器冻结/异常密度)')
        if A.get('AllNumContiguous') not in (None, '', 'YES'):
            flags.append('allnum不连续(%s)' % A.get('AllNumContiguous'))
        bnd = (P.get('Boundary') or '').strip()
        cov = (P.get('Coverage') or '').strip()
        if bnd and bnd != 'EXACT':          # EXACT = 台账号与数据窗口完全对齐，无争议
            flags.append('边界:%s' % bnd)
        if cov == 'NONE' or bnd == 'NO_DATA_AT_ALL':
            flags.append('采集期外/无数据可算')
        if form == '未标':
            flags.append('台账未填原因')
        if ledger is not None and mach and abs(mach - ledger) > 8:
            flags.append('件数差%+d 超±8容差' % (mach - ledger))
        if not A and align is None:
            flags.append('无刃口审计记录(未进 HI 管道)')
        if G.get('B_仓库CSV原文', '') == '' and G.get('A_xlsx原文', ''):
            flags.append('仓库38行副本缺此行')
        elif G.get('Form是否分歧') == '同' and G.get('A_xlsx原文') and G.get('B_仓库CSV原文') \
                and G['A_xlsx原文'] != G['B_仓库CSV原文']:
            flags.append('仓库副本措辞与原件不同(分类不变)')

        # —— 需车间回答的问题：只在有具体疑点时生成 ——
        ask = []
        if '左截断(起点不可靠)' in flags:
            ask.append('此刀上刀时刻是否早于 %s？更早的换刀记录在哪？' % (A.get('StartTime') or '?')[:16])
        if '件数差' in ' '.join(flags):
            ask.append('台账刀寿 %s 与数据实际 %s 件差在哪：单位不同？跳机续用？漏记？' % (ledger, mach))
        if form == '未标':
            ask.append('此条换刀原因未填，属正常磨损/崩刃/计划换刀/其他哪一类？')
        if 'allnum不连续' in ' '.join(flags):
            ask.append('缺失的 allnum 是采集漏，还是当时确实没加工？')
        if '右删失(未到终点)' in flags:
            ask.append('导出截止后此刀是否仍在使用？下次换刀时刻是多少？')
        if '仓库38行副本缺此行' in flags:
            ask.append('（内部核对，非车间）仓库台账副本缺此行，以 xlsx 为准即可，请确认无需追查')

        rows.append({
            'PhysicalToolLifeId': A.get('PhysicalLifeId') or ('4152-L-T1111-c%d' % cyc),
            '候选ID': A.get('LifeSourceId') or ('life_cycle=%d' % cyc),
            '顺序号Cycle': cyc,
            '起点证据': A.get('StartConfidence') or ('P01 d0=%s min' % P.get('d0') if P.get('d0') else '未记录'),
            '终点证据': A.get('EndConfidence') or ('P01 d1=%s min' % P.get('d1') if P.get('d1') else '未记录'),
            '原始换刀原因(xlsx)': reason or '（空）',
            '标准化终止类型': FORM2END.get(form, '未知'),
            '终止原因可信度': A.get('LabelEvidenceLevel') or 'E3_UNKNOWN(无刃口审计记录)',
            '台账刀寿': ledger if ledger is not None else '',
            'FOCAS参与件': mach if mach is not None else '',
            'HI对齐件': align if align is not None else '',
            '件数偏差': (mach - ledger) if (mach is not None and ledger is not None) else '',
            '时间偏差d0_min': P.get('d0') or '',
            '时间偏差d1_min': P.get('d1') or '',
            '缺口与证据来源': A.get('GapAmbiguity') or (P.get('Coverage') and 'Coverage=%s' % P.get('Coverage')) or '未记录',
            '删失状态': '；'.join([k for k, v in (('Left', A.get('LeftTruncated')),
                                                 ('Right', A.get('RightCensored')),
                                                 ('Interval', A.get('IntervalCensored')))
                                   if str(v) == 'True']) or '无',
            '数据质量级': A.get('DataQualityGrade') or '未评(无刃口审计记录)',
            '争议状态': '；'.join(flags) or '无已知争议',
            '需车间回答的具体问题': ' ／ '.join(ask) or '—',
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # —— 控制台汇总 ——
    n = len(rows)
    clean = sum(1 for r in rows if r['争议状态'] == '无已知争议')
    print('\n=== P0-D02 台账逐条缺陷清单：%d 行 ===' % n)
    bucket = {}
    for r in rows:
        for fl in (r['争议状态'] or '').split('；'):
            key = re.sub(r'\(.*?\)|:.*$|超±8容差|缺此行|措辞与原件不同.*', '', fl).strip() or fl
            bucket[key] = bucket.get(key, 0) + 1
    for k, v in sorted(bucket.items(), key=lambda kv: -kv[1]):
        if k:
            print('  %-28s %d' % (k, v))
    print('\n无已知争议：%d / %d' % (clean, n))
    print('DONE →', OUT)


if __name__ == '__main__':
    main()
