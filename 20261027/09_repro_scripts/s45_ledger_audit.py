# -*- coding: utf-8 -*-
"""s45_ledger_audit.py —— P1-07：台账入公开仓库的字段级审计 + 解析路径自检

审核 P1-07 原文
  「公开仓库含 三厂换刀记录.xlsx：确认敏感信息 → 确认后移出或加控制」
2026-10-29 0 号位书面答复：**可以进公开仓库**（见 44-00 §1）。

本脚本不判断"要不要公开"（那是 0 号位的决定），只做三件可核查的事
  A. 字段级清单：两个 Sheet 的每一列 → 非空数/唯一值/样例/敏感级别/是否含个人标识
  B. 个人标识扫描：正则命中即 FAIL（姓名/电话/身份证/邮箱/工号/住址）
  C. 解析路径自检：确认 7 个读台账的脚本都指向**包内副本**，且副本 SHA256 与
     仓库根原件、MANIFEST 记录三者一致 —— 这条是回应"包搬走后能不能跑"

输出（44_P1-07台账入库/）
  ledger_fields.csv          字段级清单（A）
  ledger_pii_scan.txt        个人标识扫描（B）
  ledger_path_check.csv      7 个脚本的解析路径（C）
  s45_console.txt
"""
import os
import re
import hashlib
import io

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)                                   # .../20261027
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import wsroot as _wsroot          # G2：不再数 '..'，按工作区特征向上定位
ROOT = _wsroot.find(_os.path.dirname(_os.path.abspath(__file__)))
OUT = os.path.join(BASE, '44_P1-07台账入库')
os.makedirs(OUT, exist_ok=True)

PKG_XLS = os.path.join(BASE, '15_台账真值检验', 'input', '三厂换刀记录.xlsx')
ROOT_XLS = os.path.join(ROOT, '三厂换刀记录.xlsx')

# ---------------------------------------------------------------- A. 字段清单
# 敏感级别判定（口径写死，不看数据看语义；改动需同时改 44-00 §2）
HIGH_KW = ('刀号', '刀具型号', '换刀原因', '是否真实失效', '备注', '正常寿命件数',
           '每班加工件数', '工序', '历史崩刃频率', '崩刃', '换刀判断方式',
           '寿命是否稳定', '工艺是否稳定')
MID_KW = ('StartTime', 'EndTime', '顺序号', '主/副轴', '主副轴', '刀塔', '设备号',
          '产品变化情况', '刀具名称', '是否容易记录换刀原因')
# 个人标识正则（中文场景）
PII_PAT = [
    # v2 修正：原规则 ^[\u4e00-\u9fa5]{2,4}(?=,|$) 会把 CSV 每行首列"副轴"当成姓名（误报 50 处）
    ('姓名', re.compile(r'(姓名|操作人|填写人|负责人|签名|值班)[:：,、\s]*[\u4e00-\u9fa5]{2,4}')),
    ('手机', re.compile(r'1[3-9]\d{9}')),
    ('固话', re.compile(r'0\d{2,3}-\d{7,8}')),
    ('身份证', re.compile(r'\d{17}[\dXx]')),
    ('邮箱', re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+')),
    ('工号', re.compile(r'(?i)\b(emp|工号)[:：]?\s*\d{3,}')),
    ('住址', re.compile(r'(省|市|区|县|镇|村|路|街)\d*号')),
]


def level_of(col):
    c = str(col)
    if c in HIGH_KW or any(k in c for k in HIGH_KW):
        return '高'
    if c in MID_KW or any(k in c for k in MID_KW):
        return '中'
    return '低'


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def clean(v):
    """样例值：去换行、截断，避免把整段备注写进清单"""
    s = '' if pd.isna(v) else str(v).replace('\n', ' ').replace('\r', ' ').strip()
    return s[:40]


rows = []
dump_text = []
for sheet in pd.ExcelFile(PKG_XLS).sheet_names:
    d = pd.read_excel(PKG_XLS, sheet_name=sheet)
    dump_text.append('===== Sheet: %s  shape=%s' % (sheet, d.shape))
    for col in d.columns:
        s = d[col]
        nn = int(s.notna().sum())
        nuniq = int(s.nunique(dropna=True))
        sample = [clean(v) for v in s.dropna().head(3).tolist()]
        rows.append(dict(Sheet=sheet, Column=str(col), DType=str(s.dtype),
                         NonNull=nn, Blank=len(s) - nn, NUnique=nuniq,
                         Sample=' | '.join(sample), Sensitivity=level_of(col),
                         IsEmptyColumn='Y' if nn == 0 else 'N'))
    # PII 用的全文本
    dump_text.append(d.to_csv(index=False))
F = pd.DataFrame(rows)
F.to_csv(os.path.join(OUT, 'ledger_fields.csv'), index=False, encoding='utf-8-sig')
print('[A] ledger_fields.csv %d 列（高 %d / 中 %d / 低 %d；空列 %d）' % (
    len(F), (F.Sensitivity == '高').sum(), (F.Sensitivity == '中').sum(),
    (F.Sensitivity == '低').sum(), (F.IsEmptyColumn == 'Y').sum()))

# ---------------------------------------------------------------- B. PII 扫描
full = '\n'.join(dump_text)
hits = []
for name, pat in PII_PAT:
    for m in pat.finditer(full):
        hits.append((name, m.group(0)[:30]))
pii_lines = ['个人标识扫描（正则见 s45 顶部 PII_PAT）',
             '扫描范围：两个 Sheet 的全部单元格文本，共 %d 字符' % len(full), '']
if hits:
    pii_lines.append('结论：命中 %d 处 —— 禁止公开，需脱敏' % len(hits))
    for n, v in hits[:50]:
        pii_lines.append('  [%s] %s' % (n, v))
else:
    pii_lines.append('结论：7 类个人标识全部 0 命中 —— 无姓名/电话/身份证/邮箱/工号/住址')
    pii_lines.append('（"住址"规则会匹配含"市/区/路+门牌"的串，产线地址也算命中，需人工复核上下文）')
io.open(os.path.join(OUT, 'ledger_pii_scan.txt'), 'w', encoding='utf-8').write('\n'.join(pii_lines) + '\n')
print('[B] PII 命中 %d 处 → ledger_pii_scan.txt' % len(hits))

# ---------------------------------------------------------------- C. 解析路径自检
SCRIPTS = ['s30_switch_truth.py', 's31_form_stratified_rul.py', 's32_label_audit.py',
           's33_build_labeled_new.py', 's34_data_equiv.py', 's40_gap_classify.py',
           's42_p01_evidence.py']
NEEDLE = "15_台账真值检验', 'input'"
pc = []
for fn in SCRIPTS:
    p = os.path.join(HERE, fn)
    src = io.open(p, encoding='utf-8').read()
    pkg_first = NEEDLE in src
    hard = "'恢复'" in src or '"恢复"' in src
    pc.append(dict(Script=fn, ReadsPackageCopy=('Y' if pkg_first else 'N'),
                   HardcodedOldDir=('Y' if hard else 'N'),
                   Note='包内副本优先' if pkg_first else '仍读仓库根'))
PC = pd.DataFrame(pc)
PC.to_csv(os.path.join(OUT, 'ledger_path_check.csv'), index=False, encoding='utf-8-sig')
print('[C] 7 脚本包内优先 %d/7；残留硬编码旧目录名 %d/7' % (
    (PC.ReadsPackageCopy == 'Y').sum(), (PC.HardcodedOldDir == 'Y').sum()))

# SHA 三方一致
s_pkg = sha(PKG_XLS)
s_root = sha(ROOT_XLS) if os.path.exists(ROOT_XLS) else '(missing)'
man = os.path.join(BASE, '01_交付清单与版本指纹', 'MANIFEST_v2.csv')
s_man = '-'
if os.path.exists(man):
    m = pd.read_csv(man, encoding='utf-8-sig')
    r = m[m.Path == '15_台账真值检验/input/三厂换刀记录.xlsx']
    s_man = r.SHA256.iloc[0] if len(r) else '(not listed)'
lines = ['台账 SHA256 三方对账', '',
         '包内副本    %s  %d B' % (s_pkg, os.path.getsize(PKG_XLS)),
         '仓库根原件  %s' % s_root,
         'MANIFEST_v2 %s' % s_man, '',
         '一致：%s' % ('是' if s_pkg == s_root == s_man else '否 —— 需重跑 s26 重建清单')]
io.open(os.path.join(OUT, 'ledger_sha256.txt'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
print('\n'.join(lines))
