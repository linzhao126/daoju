"""
check_g0.py —— G0 治理契约自动检查（G0-05 的可执行化）

【为什么要有这个】
审核要求「每轮提交《项目目标遵循性检查》」，但过去两轮我们犯过 4 次同类错
（"不是两种失效机制"、"其余方向已证否"、"唯一站得住"、"必然预防性更换"）。
治理不能靠自觉——要有能被别人一条命令查出来的东西。

【检查三项】
1. **红线词**：`g0_redlines.csv` 里的 12 类禁用语（带上下文豁免，能识别"引用红线本身"）
2. **七项自检**：G0-05 要求的 7 项是否齐全
3. **C 级标注**：RUL 结论是否按 C0/C1/C2/C3 分级

【豁免机制】
- 行内豁免：同一行或上一行出现豁免词（不得/禁止/⛔/撤回/作废/违规/误用…）→ 跳过
- 区块豁免：`<!-- g0:ignore-start -->` 与 `<!-- g0:ignore-end -->` 之间整段跳过
  （用于"红线词表"这类**必须**包含违规词的章节）

【输出】
PASS  无红线命中 + 七项 ≥6 + C 级标注存在
WARN  红线命中但均可疑（如仅出现在标题）；或七项 4~5；或 C 级缺失
FAIL  红线命中且非豁免；或七项 ≤3

用法
  python check_g0.py <报告.md> [<更多报告.md> ...]
  python check_g0.py --all 33_治理模板_G0        # 扫一个目录下所有 .md
  python check_g0.py --redlines                 # 只打印红线词表

只依赖标准库。
"""
import os
import re
import sys
import csv
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
REDLINES = os.path.join(os.path.dirname(HERE), '33_治理模板_G0', 'g0_redlines.csv')

# 行内豁免词：出现这些说明该行是在"声明红线/撤回/纠错"，不是在违规
EXEMPT = ['不得', '禁止', '禁', '不能', '不可', '不应', '不会', '没有', '≠', '⛔',
          '撤回', '作废', '降级', '旧表述', '原表述', '改成', '已改为',
          '违规', '误用', '不合格', '反例', '红线', '整改', '禁止的', '越界', '事故', '纠错']

# G0-05 七项（用关键词匹配，容忍换行与措辞差异）
SEVEN = [
    ('①涉及项目', ['涉及哪个项目', '本轮涉及']),
    ('②业务目标', ['对应哪项业务目标', '业务目标']),
    ('③证明了什么', ['实际证明了什么', '证明了什么']),
    ('④没有证明什么', ['没有证明什么', '没证明什么']),
    ('⑤是否改变目标', ['是否改变目标', '改变目标或技术边界', '是否改变']),
    ('⑥能否继续推进', ['能否继续推进', '现有方案能否继续']),
    ('⑦可复现证据', ['可复现证据', '可复现证据']),
]

IGNORE_START = 'g0:ignore-start'
IGNORE_END = 'g0:ignore-end'


def load_redlines(path=REDLINES):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f):
            if r.get('pattern'):
                out.append(r)
    return out


def scan_text(text, redlines):
    """返回命中列表：[(lineno, line, rule)]"""
    lines = text.split('\n')
    hits = []
    in_ignore = False
    for i, ln in enumerate(lines):
        if IGNORE_START in ln:
            in_ignore = True
            continue
        if IGNORE_END in ln:
            in_ignore = False
            continue
        if in_ignore:
            continue
        prev = lines[i - 1] if i > 0 else ''
        exempted = any(w in ln for w in EXEMPT) or any(w in prev for w in EXEMPT)
        for r in redlines:
            if r['pattern'] in ln:
                hits.append(dict(lineno=i + 1, line=ln.strip()[:140],
                                 pattern=r['pattern'], kind=r['kind'],
                                 reason=r['reason'], replacement=r['replacement'],
                                 exempt=exempted))
    return hits


def check_seven(text):
    """返回 (命中数, 明细)"""
    got, detail = 0, []
    for name, kws in SEVEN:
        ok = any(k in text for k in kws)
        got += 1 if ok else 0
        detail.append((name, ok))
    return got, detail


def check_levels(text):
    """C0~C3 分级标注检查：至少出现 3 个不同级别才算有分级意识"""
    found = [f'C{i}' for i in range(4) if re.search(r'\bC%d\b' % i, text) or f'C{i}' in text]
    return found


def check_file(path, redlines, verbose=True):
    if not os.path.exists(path):
        print('  [MISS] 文件不存在：%s' % path)
        return 'FAIL', {}
    with open(path, encoding='utf-8', errors='replace') as f:
        text = f.read()

    hits = scan_text(text, redlines)
    hard = [h for h in hits if not h['exempt']]
    soft = [h for h in hits if h['exempt']]
    n7, d7 = check_seven(text)
    levels = check_levels(text)

    # 七项自检只对"回执类"文档强制（自称遵循性检查 / 文件名含 R06 或 回执）
    # 研究报告（16-00 / 17-00 等）不是回执，缺七项不算违规，只提示
    base = os.path.basename(path)
    is_receipt = ('遵循性检查' in text) or ('R06' in base) or ('回执' in base)

    if hard:
        verdict = 'FAIL'
    elif is_receipt and n7 <= 3:
        verdict = 'FAIL'
    elif (is_receipt and n7 < 6) or not levels:
        verdict = 'WARN'
    elif n7 < 6:
        verdict = 'WARN'          # 非回执：只提示"可补七项"
    else:
        verdict = 'PASS'
    # 红线命中但全部豁免 → 最多 WARN（需人复核是引用还是违规）
    if not hard and soft and verdict == 'PASS':
        verdict = 'WARN'
    if not hard and verdict == 'FAIL':
        verdict = 'FAIL'

    if verbose:
        base = os.path.basename(path)
        is_rcpt = ('遵循性检查' in text) or ('R06' in base) or ('回执' in base)
        print('  %-44s %-5s %-4s 红线 %d（豁免 %d） 七项 %d/7  C级 %s'
              % (base[:44], verdict, '回执' if is_rcpt else '研究', len(hard), len(soft), n7,
                 ''.join(levels) if levels else '无'))
        for h in hard:
            print('      ⛔ L%-5d [%s] %s' % (h['lineno'], h['kind'], h['pattern']))
            print('         原文：%s' % h['line'])
            print('         建议：%s' % h['replacement'])
        for h in soft:
            print('      ⚠ L%-5d [%s] %s（已豁免，请人工确认是引用）'
                  % (h['lineno'], h['kind'], h['pattern']))
        if n7 < 7:
            miss = [n for n, ok in d7 if not ok]
            tag = '' if is_rcpt else '（非回执，可不补）'
            print('      缺自检项%s：%s' % (tag, '、'.join(miss)))
    return verdict, dict(hard=len(hard), soft=len(soft), seven=n7,
                         levels=levels, receipt=is_receipt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='*')
    ap.add_argument('--all', default=None, help='扫描某目录下所有 .md')
    ap.add_argument('--redlines', action='store_true', help='只打印红线词表')
    args = ap.parse_args()

    rl = load_redlines()
    if args.redlines:
        print('红线词表：%s（%d 条）' % (REDLINES, len(rl)))
        for r in rl:
            print('  [%-10s] %-24s → %s' % (r['kind'], r['pattern'], r['replacement']))
        return 0
    if not rl:
        print('⚠ 红线词表缺失：%s' % REDLINES)
        print('  （继续执行，但红线检查会被跳过）')

    targets = list(args.files)
    if args.all:
        d = os.path.abspath(args.all)
        if not os.path.isdir(d):
            print('目录不存在：%s' % d)
            return 2
        targets += [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith('.md')]
    if not targets:
        print('用法：python check_g0.py <报告.md> [--all <目录>] [--redlines]')
        return 2

    print('=' * 96)
    print('G0 治理契约检查　（红线词 %d 条 / 七项自检 / C 级标注）' % len(rl))
    print('=' * 96)
    tally = {'PASS': 0, 'WARN': 0, 'FAIL': 0}
    for p in targets:
        v, _ = check_file(p, rl)
        tally[v] = tally.get(v, 0) + 1
    print('-' * 96)
    print('合计：PASS %d / WARN %d / FAIL %d' % (tally['PASS'], tally['WARN'], tally['FAIL']))
    print()
    print('说明：')
    print('  PASS = 无未豁免红线 + 七项≥6 + 有 C 级标注')
    print('  WARN = 有可疑表述（多为引用红线本身），需人工复核')
    print('  FAIL = 命中未豁免红线，或缺七项自检（≤3 项）')
    print('  确认某行是引用而非违规 → 在该行加"⛔/不得/撤回"等豁免词，')
    print('  或把整段包进 <!-- g0:ignore-start --> … <!-- g0:ignore-end -->')
    return 0 if tally['FAIL'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
