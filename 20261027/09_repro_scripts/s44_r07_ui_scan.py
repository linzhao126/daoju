# -*- coding: utf-8 -*-
"""s44_r07_ui_scan.py —— R07：UI 整改证据分级（已实施给 diff+回归，未实施标 DESIGN_ONLY）

审核 R07 原文
  「UI 整改证据：仅建议标 DESIGN_ONLY；已实施给 commit 差异与回归」
  「不得用许可延寿 / 预测安全措辞」

本脚本做三件事，全部可机检：
  A. 禁用措辞扫描：线上 + 交付副本 + 改动前快照，逐文件逐行给命中
     —— 带否定语境识别（"不构成延长许可"不算违规）
  B. 逐条状态表：FIX-08 的 15 个条目 → IMPLEMENTED / DESIGN_ONLY，
     IMPLEMENTED 的必须能被 grep 证明（PRESENT 找得到 / ABSENT 找不到）
  C. 交付一致性：02_源代码与配置/source/ 与线上文件的 SHA256 是否一致

输出（45_R07_UI证据/）
  ui_phrase_scan.csv     A，含 before / after 两列状态
  ui_status.csv          B
  s44_console.txt

判定口径
  - IMPLEMENTED 条目若 grep 证明失败 → 整脚本 exit code 1（不许口头说做了）
  - 任一 HARD 级禁用措辞在 after 版本仍存在 → exit code 1
"""
import os
import re
import sys
import hashlib

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)                                   # .../20261027
ROOT = os.path.abspath(os.path.join(BASE, '..', '..', '..'))
OUT = os.path.join(BASE, '45_R07_UI证据')
SRC = os.path.join(BASE, '02_源代码与配置', 'source')
LIVE = os.path.join(ROOT, '2ndPrediction', 'tool_log')
BEFORE = os.path.join(OUT, 'index.html.before_R07')
os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------- A. 禁用措辞
# 级 别：HARD = 审核明令禁止，必须 0 命中；SOFT = 需人工看上下文
PHRASES = [
    ('HARD', '延长许可', '「已获延长许可」= 把信号当成放行依据'),
    ('HARD', '可用到', '「可用到 N 件」= 承诺使用寿命上限'),
    ('HARD', '已许可', '同上'),
    ('HARD', '延长寿命', '同「延寿」'),
    ('HARD', '比新刀', '冷启动下没有"新刀"真值，此比较无依据'),
    ('HARD', '必须换', '把提示写成命令，超出"只提示不许可"定位'),
    ('HARD', '就换', '『第 N 件就换』同上'),
    ('HARD', '保证', '任何"保证"都不成立（0/16 覆盖不足以宣称）'),
    ('HARD', '必然', '把假说写成事实'),
    ('HARD', '已验证安全', '安全结论未经验证'),
    ('SOFT', '安全', '需看上下文（"不安全生产"vs"已验证安全"）'),
    ('SOFT', '预测', '需看是否暗示"预测未来崩刃"'),
]
NEG = ('不构成', '不是', '非', '无', '不能', '未')


def in_comment(ln, j):
    """粗略判断命中位置是否在代码注释里（# 行注释 / // 行注释）

    存在的意义：审核禁的是**页面可见措辞**。"延长许可"是本项目对"跨过基准点后放行"
    这一步的内部叫法，写在变量名和注释里不算违规；写进 return 给前端的字符串里才算。
    升为 HARD 的证据必须是"能被用户读到"的那一处，所以这里把注释单独降级。
    """
    if ln.lstrip().startswith('#'):
        return True
    before = ln[:j]
    k = before.rfind('//')
    return k >= 0 and before.count('"') % 2 == 0


def scan_text(path, text):
    """返回命中列表；否定语境 / 注释分别标记，只有"非注释 + 非否定"才算 HARD"""
    hits = []
    lines = text.split('\n')
    for i, ln in enumerate(lines, 1):
        for sev, ph, why in PHRASES:
            start = 0
            while True:
                j = ln.find(ph, start)
                if j < 0:
                    break
                ctx = ln[max(0, j - 8):j]
                negated = any(n in ctx for n in NEG)
                com = in_comment(ln, j)
                hits.append(dict(File=os.path.basename(path), Line=i, Phrase=ph,
                                 Severity=('COMMENT' if (com and sev == 'HARD') else sev),
                                 Negated=('Y' if negated else 'N'),
                                 Why=why, Context=ln.strip()[:110]))
                start = j + len(ph)
    return hits


def read(p):
    if not os.path.exists(p):
        return None
    with open(p, 'rb') as f:
        return f.read().decode('utf-8', 'replace')


def sha(p):
    if not os.path.exists(p):
        return '(missing)'
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


TARGETS = [
    ('live', os.path.join(LIVE, 'index.html')),
    ('live', os.path.join(LIVE, 'server.py')),
    ('pkg', os.path.join(SRC, 'index.html')),
    ('pkg', os.path.join(SRC, 'server.py')),
    ('before', BEFORE),
]
rows = []
for tag, p in TARGETS:
    t = read(p)
    if t is None:
        print('[skip] 缺 %s' % p)
        continue
    for h in scan_text(p, t):
        rows.append(dict(Tag=tag, **h))
S = pd.DataFrame(rows)
S.to_csv(os.path.join(OUT, 'ui_phrase_scan.csv'), index=False, encoding='utf-8-sig')

hard_after = S[(S.Severity == 'HARD') & (S.Negated == 'N') & (S.Tag != 'before')]
soft_after = S[(S.Severity == 'SOFT') & (S.Tag != 'before')]
before_hard = S[(S.Severity == 'HARD') & (S.Negated == 'N') & (S.Tag == 'before')]
print('=' * 96)
print('[A] 禁用措辞扫描')
print('=' * 96)
print('  改动前快照 HARD 命中：%d 处' % len(before_hard))
for _, r in before_hard.iterrows():
    print('     L%-4d [%s] %s' % (r.Line, r.Phrase, r.Context))
print('  改动后（线上 + 交付副本）HARD 命中：%d 处' % len(hard_after))
for _, r in hard_after.iterrows():
    print('     %s L%-4d [%s] %s' % (r.Tag, r.Line, r.Phrase, r.Context))
print('  否定语境（"不构成延长许可"这类，不计入 HARD）：%d 处'
      % len(S[(S.Severity == 'HARD') & (S.Negated == 'Y')]))
print('  代码注释内（内部术语，不计入 HARD）：%d 处' % len(S[S.Severity == 'COMMENT']))
print('  SOFT 待人工判断：%d 处 → 见 ui_phrase_scan.csv' % len(soft_after))

# ---------------------------------------------------------------- B. 逐条状态
TXT_LIVE = read(os.path.join(LIVE, 'index.html')) or ''
TXT_SRV = read(os.path.join(LIVE, 'server.py')) or ''


def absent(pats, txt):
    return all(p not in txt for p in pats)


def present(pats, txt):
    return all(p in txt for p in pats)


ITEMS = [
    # (ID, 出处, 条目, 声明状态, 检查方式, 目标文本, 证据说明)
    ('R07-1', 'FIX-08 §一-1', '禁「已获延长许可 → 可用到 N 件」', 'IMPLEMENTED', 'ABSENT',
     ['已获延长许可', '可用到 '], '改为"信号未见异常上升 · 仅供参考（参考线 N 件，不构成延长许可）"'),
    ('R07-2', 'FIX-08 §一-2', '禁「（比新刀 ±x%）」', 'IMPLEMENTED', 'ABSENT',
     ['比新刀'], '改为"相对本观测段参考基线"'),
    ('R07-3', 'FIX-08 §一-3', 'history 加 src 列，页面显示"在线 N / 离线标定 M"', 'DESIGN_ONLY', 'NONE',
     [], '需 ALTER TABLE + 迁移顺序控制（踩过 ALTER 必须在 UNIQUE 迁移之后），本轮未动库'),
    ('R07-4', 'FIX-08 §二-1', '新增 MONITOR_ONLY 状态（不显示任何该换/可延字样）', 'DESIGN_ONLY', 'NONE',
     [], '需改状态机与前端渲染分支，属判定语义变更'),
    ('R07-5', 'FIX-08 §二-2', '新增 INSUFFICIENT_DATA 状态（水位线停滞 >24h 全页不给数字）', 'DESIGN_ONLY', 'NONE',
     [], '同上；实测库停在 10-22 而当天 10-27，页面仍显示旧值 → 这个缺口真实存在'),
    ('R07-6', 'FIX-08 §三-1', 'TXT.bad「已过中位寿命，必须换」→「已过同程序中位寿命 · 需复核」', 'IMPLEMENTED', 'PRESENT',
     ['已过同程序中位寿命 · 需复核'], '同时消掉 HARD 词「必须换」'),
    ('R07-7', 'FIX-08 §三-2', '信号文案改「相对本观测段参考基线」', 'IMPLEMENTED', 'PRESENT',
     ['相对本观测段参考基线'], '冷启动下不再出现"新刀"二字'),
    ('R07-8', 'FIX-08 §三-3', '「可用到 N 件」→「不构成延长许可」', 'IMPLEMENTED', 'PRESENT',
     ['不构成延长许可'], '保留参考线数字但删除许可语义'),
    ('R07-9', 'FIX-08 §三-4', '「第 N 件就换」→「建议在第 N 件复核」', 'IMPLEMENTED', 'PRESENT',
     ['建议在第 '], '同时消掉 HARD 词「就换」'),
    ('R07-10', 'FIX-08 §三-5', '「已封口 N 件」→「全库已封口 N 件」', 'IMPLEMENTED', 'ABSENT',
     ['>已封口 '], '两处（模拟器分支 + 实时分支）都改；避免把"全库件数"误读成"本把刀件数"'),
    ('R07-11', 'FIX-08 §三-6', '经验值来源写明（非在线统计）', 'IMPLEMENTED', 'PRESENT',
     ['非在线统计'], '原文只说"经验值 100 件"，没说这 100 是怎么来的'),
    ('R07-12', 'FIX-08 §四-1', '颜色与文字同源（状态机改 used ≥ (limit or h)）', 'DESIGN_ONLY', 'NONE',
     [], '会移动橙/红触发点，属判定语义变更，须先批'),
    ('R07-13', 'FIX-08 §四-2', 'history.src 的 ALTER 与 UNIQUE 迁移顺序', 'DESIGN_ONLY', 'NONE',
     [], '与 R07-3 同一件事，依赖库迁移'),
    ('R07-14', 'FIX-08 §五-2', '不自动停机 / 不自动换刀（全文无 FOCAS 写接口）', 'IMPLEMENTED', 'ABSENT',
     ['focas', 'fwlib', 'pmc', 'writeMemory', 'tool_offset', 'socket'],
     'server.py 全文 6 个写通道关键词 0 命中 → 技术上不可能下发指令'),
    ('R07-15', 'FIX-08 §五-4', '页面写明「寿命 = 距下次人工换刀，不是物理磨损失效」', 'DESIGN_ONLY', 'NONE',
     [], '需加固定说明条，与本轮文案改动一起做更省事，但会动布局'),
]

st = []
for iid, ref, item, status, check, pats, note in ITEMS:
    txt = TXT_SRV if iid == 'R07-14' else TXT_LIVE
    if check == 'ABSENT':
        ok = absent(pats, txt)
    elif check == 'PRESENT':
        ok = present(pats, txt)
    else:
        ok = None
    st.append(dict(ID=iid, Ref=ref, Item=item, Status=status, Check=check,
                   Verified=('PASS' if ok else ('FAIL' if ok is False else 'N/A')),
                   Patterns=';'.join(pats), Evidence=note))
ST = pd.DataFrame(st)
ST.to_csv(os.path.join(OUT, 'ui_status.csv'), index=False, encoding='utf-8-sig')

print()
print('=' * 96)
print('[B] FIX-08 逐条状态')
print('=' * 96)
print(ST[['ID', 'Ref', 'Status', 'Verified', 'Item']].to_string(index=False))
n_imp = int((ST.Status == 'IMPLEMENTED').sum())
n_des = int((ST.Status == 'DESIGN_ONLY').sum())
fail = ST[(ST.Status == 'IMPLEMENTED') & (ST.Verified != 'PASS')]
print('\n  IMPLEMENTED %d 条 / DESIGN_ONLY %d 条；IMPLEMENTED 但机检失败 %d 条' % (n_imp, n_des, len(fail)))

# ---------------------------------------------------------------- C. 交付一致性
print()
print('=' * 96)
print('[C] 交付副本 vs 线上文件 SHA256')
print('=' * 96)
cons = []
for fn in ('index.html', 'server.py'):
    a, b = sha(os.path.join(LIVE, fn)), sha(os.path.join(SRC, fn))
    same = (a == b)
    cons.append(dict(File=fn, Live=a, Package=b, Same=('Y' if same else 'N')))
    print('  %-12s live %s  pkg %s  %s' % (fn, a[:16], b[:16], '一致' if same else '★不一致'))
C = pd.DataFrame(cons)
C.to_csv(os.path.join(OUT, 'ui_package_consistency.csv'), index=False, encoding='utf-8-sig')

# ---------------------------------------------------------------- 退出码
rc = 0
if len(hard_after):
    print('\n[FAIL] 改动后仍有 HARD 禁用措辞 %d 处' % len(hard_after))
    rc = 1
if len(fail):
    print('\n[FAIL] 声明 IMPLEMENTED 但机检未通过：%s' % ','.join(fail.ID))
    rc = 1
if (C.Same == 'N').any():
    print('\n[FAIL] 交付副本与线上不一致：%s' % ','.join(C[C.Same == 'N'].File))
    rc = 1
print('\nexit=%d  →  ui_phrase_scan.csv / ui_status.csv / ui_package_consistency.csv' % rc)
sys.exit(rc)
