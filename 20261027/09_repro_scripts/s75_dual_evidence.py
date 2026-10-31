# -*- coding: utf-8 -*-
"""R-8 双维度证据等级（任务书 §5.2）

任务书原话：
  "原 ±3、±8 件容差是**件数一致性维度**，不能单独证明'磨损终点真实''崩刃发生时刻已知'；
   需要同时给**终止原因可信度**。"

所以每条记录给两个独立档位，再做联合矩阵：

  维度 A · 件数一致性（沿用 D-07 卡 C 的容差口径）
    A_GOLD   |Δ| ≤ 3        A_STRICT  3 < |Δ| ≤ 8
    A_LOOSE  |Δ| > 8        A_NOCOMP  无法比（缺台账刀寿 / 缺数据件数 / 无数据）

  维度 B · 终止原因可信度（§5.2 的 E0~E3，判定规则写死、可逐条回溯）
    E0_DIRECT         有独立物证列：VB 值 / 刀片损伤照片 / 量具测量 / 设备报警日志
    E1_CROSSCHECKED   同时满足四条件：
                        (1) 换刀原因明确（含"破损/崩"或"磨损"，非空、非纯"计划寿命到"）
                        (2) 台账窗口与数据边界 EXACT
                        (3) 件数偏差 |Δ| ≤ 8
                        (4) 无删失（Left / Right / Interval 全 false）
    E2_PROXY          有原因文字但不满足 E1 全部条件
    E3_UNKNOWN        原因未填，或该周期无数据可核（NO_DATA_AT_ALL / Coverage=NONE）

  ⚠ 关键诚实点：E0 取决于 xlsx 是否含物证列；E1 取决于四条件能否同时成立。
    本脚本的作用就是把"到底有几条能站上 E1"这个数算出来——它直接决定 C3 严格真值上限。

数据源（全部只读，不新建解释）：
  P01_evidence_all60.csv（LedgerLife / MachinePieceCount / Boundary / Coverage / Form）
  life_audit_4152.csv（LabelEvidenceLevel / DataQualityGrade / 三种删失 / AllNumSpan）
  tool_life_records_v3.csv（是否真实失效 / 换刀原因原文）
  15_台账真值检验/truth_vs_data_pieces.csv（件数第二来源，交叉核对）
  三层对齐表/physical_tool_lives.csv（R-4 产出，用于交叉核对 InDataWindow）
"""
import csv
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
WORK = os.path.abspath(os.path.join(BASE, '..', '..'))
P01 = os.path.join(BASE, '34_P0-01收尾', 'P01_evidence_all60.csv')
AUD = os.path.join(BASE, '03_原始数据与标签', 'life_audit_4152.csv')
V3 = os.path.join(WORK, '手工提供文件', 'tool_life_records_v3.csv')
TVP = os.path.join(BASE, '15_台账真值检验', 'truth_vs_data_pieces.csv')
LIVES = os.path.join(BASE, 'P0_数据与代码移交', '三层对齐表', 'physical_tool_lives.csv')
OUT = os.path.join(BASE, 'P0_数据与代码移交', 'P0-D05_双维度证据等级.csv')
MATRIX = os.path.join(BASE, 'P0_数据与代码移交', 'P0-D05_证据矩阵汇总.csv')

# xlsx 里可用于 E0 的"独立物证"列名（当前原件没有，列出来是为了规则可复核）
E0_COLS = ['VB值', '刀片损伤照片', '量具测量', '粗糙度', '设备报警日志', '工单号', '是否更换物理刀片']


def rd(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def num(v):
    try:
        x = float(v)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def iv(v):
    x = num(v)
    return int(x) if x is not None else None


def grade_A(ledger, mach):
    """维度 A：件数一致性（D-07 卡 C：±3 黄金 / ±8 严格）"""
    if ledger is None or mach is None or mach <= 0:
        return 'A_NOCOMP', None
    d = mach - ledger
    if abs(d) <= 3:
        return 'A_GOLD', d
    if abs(d) <= 8:
        return 'A_STRICT', d
    return 'A_LOOSE', d


def grade_B(reason, form, boundary, cov, d, cens, has_e0):
    """维度 B：终止原因可信度。返回 (档位, 逐条判定说明)"""
    r = (reason or '').strip()
    plan_only = bool(re.fullmatch(r'刀具(设定)?寿命已到|计划(换)?刀|寿命到', r))
    explicit = (('破损' in r or '崩' in r) or ('磨损' in r)) and not plan_only

    if has_e0:
        return 'E0_DIRECT', '存在独立物证列'
    # 无数据可核 → 直接 E3（原因文字再明确也无法交叉验证）
    if cov == 'NONE' or boundary == 'NO_DATA_AT_ALL':
        return 'E3_UNKNOWN', '无数据可核（窗口在采集范围外）'
    if not r or form == '未标':
        return 'E3_UNKNOWN', '台账未填换刀原因'
    conds = {
        '(1)原因明确': explicit,
        '(2)边界EXACT': boundary == 'EXACT',
        '(3)件数|Δ|≤8': (d is not None and abs(d) <= 8),
        '(4)无删失': not cens,
    }
    ok = sum(1 for v in conds.values() if v)
    if ok == 4:
        return 'E1_CROSSCHECKED', '四条件全满足：' + ' '.join(conds)
    miss = ' '.join(k for k, v in conds.items() if not v)
    if explicit:
        return 'E2_PROXY', '原因仅人工文字，未满足 ' + miss
    return 'E2_PROXY', '原因含磨损/破损以外表述（如纯计划寿命），未满足 ' + miss


def main():
    p01 = {iv(r['Cycle']): r for r in rd(P01) if iv(r.get('Cycle'))}
    aud = {}
    for r in rd(AUD):
        m = re.search(r'c(\d+)$', r.get('PhysicalLifeId') or '')
        if m:
            aud[int(m.group(1))] = r
    led = {iv(r['顺序号']): r for r in rd(V3) if iv(r.get('顺序号'))}
    tvp = {iv(r['cycle']): r for r in rd(TVP) if iv(r.get('cycle'))}
    lv = {iv(r['LedgerSeqNo']): r for r in rd(LIVES) if iv(r.get('LedgerSeqNo'))}
    print('数据源：P01=%d life_audit=%d v3=%d truth_vs_data=%d 三层刃口=%d'
          % (len(p01), len(aud), len(led), len(tvp), len(lv)))

    v3cols = list(led.values())[0].keys() if led else []
    has_e0_global = any(c in v3cols for c in E0_COLS)
    print('xlsx 是否含 E0 级物证列（%s）：%s' % ('/'.join(E0_COLS), has_e0_global))

    rows = []
    for cyc in sorted(set(p01) | set(led)):
        P, A, L, T = p01.get(cyc, {}), aud.get(cyc, {}), led.get(cyc, {}), tvp.get(cyc, {})
        ledger = num(P.get('LedgerLife'))
        mach = num(P.get('MachinePieceCount'))
        align = num(P.get('Aligned'))
        boundary = (P.get('Boundary') or '').strip()
        cov = (P.get('Coverage') or '').strip()
        reason = (L.get('换刀原因') or '').strip()
        form = (P.get('Form') or '').strip()
        cens = [k for k, v in (('Left', A.get('LeftTruncated')),
                                ('Right', A.get('RightCensored')),
                                ('Interval', A.get('IntervalCensored'))) if str(v) == 'True']
        a_grade, d = grade_A(ledger, mach)
        b_grade, why = grade_B(reason, form, boundary, cov, d, cens, has_e0_global)
        # 件数第二来源交叉核对（truth_vs_data）
        # ⚠ 该表基于**旧批 labeled**（仅 c16/25~42/45），对新批周期会给出 0−台账＝大负数，
        #    那是“此来源无数据”，不是件数矛盾 → 只有 数据有=YES 时才采信。
        d2 = num(T.get('差'))
        has2 = (T.get('数据有') or '').strip().upper() == 'YES'
        rows.append({
            '顺序号Cycle': cyc,
            'PhysicalToolLifeId': '4152-L-T1111-c%d' % cyc,
            '台账刀寿': ledger if ledger is not None else '',
            '数据参与件': mach if mach is not None else '',
            'HI对齐件': align if align is not None else '',
            '件数偏差': d if d is not None else '',
            '第二来源偏差(truth_vs_data旧批)': (d2 if has2 else '此来源无数据') if (d2 is not None or has2) else '',
            '维度A_件数一致性': a_grade,
            '维度B_原因可信度': b_grade,
            '换刀原因(台账原文)': reason or '（空）',
            '标准化终止类型': {'破损': 'CHIPPING', '磨损': 'WEAR'}.get(form, 'UNKNOWN'),
            '边界核验': boundary or 'NO_DATA_AT_ALL',
            'Coverage': cov or 'NONE',
            '删失': ','.join(cens) or 'None',
            'B档判定说明': why,
            '可进正式真值评分': 'YES' if b_grade in ('E0_DIRECT', 'E1_CROSSCHECKED')
                              and a_grade in ('A_GOLD', 'A_STRICT') else 'NO',
        })

    def dump(path, rs, cols):
        with open(path, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in rs:
                w.writerow(r)
        print('写出', path, len(rs), '行')

    dump(OUT, rows, list(rows[0].keys()))

    # —— 联合矩阵 A × B ——
    AS = ['A_GOLD', 'A_STRICT', 'A_LOOSE', 'A_NOCOMP']
    BS = ['E0_DIRECT', 'E1_CROSSCHECKED', 'E2_PROXY', 'E3_UNKNOWN']
    grid = {(a, b): 0 for a in AS for b in BS}
    for r in rows:
        grid[(r['维度A_件数一致性'], r['维度B_原因可信度'])] += 1
    mrows = [{'维度A\\维度B': a, **{b: grid[(a, b)] for b in BS},
              '行合计': sum(grid[(a, b)] for b in BS)} for a in AS]
    mrows.append({'维度A\\维度B': '列合计', **{b: sum(grid[(a, b)] for a in AS) for b in BS},
                  '行合计': len(rows)})
    dump(MATRIX, mrows, ['维度A\\维度B'] + BS + ['行合计'])

    both = [r for r in rows if r['可进正式真值评分'] == 'YES']
    e1 = [r for r in rows if r['维度B_原因可信度'] in ('E0_DIRECT', 'E1_CROSSCHECKED')]
    print('\n=== 关键读数 ===')
    print('B 档达 E0/E1（原因可交叉核实）：%d 条 → %s' % (len(e1), [r['顺序号Cycle'] for r in e1]))
    print('A、B 双达（可进正式 C3 真值评分）：%d 条 → %s'
          % (len(both), [r['顺序号Cycle'] for r in both]))
    print('仅 E2（人工文字）：%d 条；E3（无原因/无数据）：%d 条'
          % (sum(1 for r in rows if r['维度B_原因可信度'] == 'E2_PROXY'),
             sum(1 for r in rows if r['维度B_原因可信度'] == 'E3_UNKNOWN')))
    print('\n说明：E0 恒为 0 的原因是 xlsx 无物证列；E1 上限由四条件决定，'
          '其中"(1)原因明确"与"(3)件数|Δ|≤8"是最常见的卡点。')


if __name__ == '__main__':
    main()
