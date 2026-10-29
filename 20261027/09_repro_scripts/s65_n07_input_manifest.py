# -*- coding: utf-8 -*-
"""s65_n07_input_manifest.py —— N07：完整输入清单 + SHA + 干净-clone 可复现性标注

对应审核：Commit 0338a5d §4 **P1-01**、§5 **N07**。
  审核指出：A 段依赖"仓库根的旧 labeled / xalign_HI / 台账"，不属于干净 clone 无外部依赖；
  B 段 3.3 GB 原始文件缺 → 不得写"全部复现"。
本脚本把**每个关键输入**的：是否在仓库内、是否存在、SHA256、大小、获取途径、
  干净 clone 能否复现 —— 摊成一张可核对的清单（不谎报"全部可复现"）。
"""
import os
import sys
import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))          # .../20261027
REPO = os.path.abspath(os.path.join(BASE, '..'))         # daoju_repo（clone 根）
ROOT = os.path.abspath(os.path.join(BASE, '..', '..'))   # 原工作区（含旧 labeled / 台账 xlsx）
OUT = os.path.join(BASE, 'N07_可复现性')
os.makedirs(OUT, exist_ok=True)


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 16), b''):
            h.update(b)
    return h.hexdigest()


# 候选路径：先查仓库内(BASE/REPO)，再查原工作区(ROOT)。
CANDIDATES = [
    ('22_c48-c60_xalign/labeled_T1111_new.csv', 'C', 'xalign 新批 labeled'),
    ('24_程序归属修正/HI_merged_28.csv', 'A', 'xalign_HI（A 段核心输入）'),
    ('34_P0-01收尾/P01_evidence_all60.csv', 'A', '逐把证据台账对齐表'),
    ('03_原始数据与标签/life_audit_4152.csv', 'A', '逐刃口 as_of 真值审计'),
    ('03_原始数据与标签/tool_db_snapshot/tool.db.readonly_copy', 'A', '只读库快照（N06 影子用）'),
    ('附件_证据/52_R02_延长分支审计/r02_grant_audit.csv', 'A', 'R02 放行审计（N04 用）'),
    # 仓库外的历史/原始输入（审核点名）
    ('influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv', 'A*', '仓库根旧 labeled（ROOT，非仓库内）'),
    ('influxdb_4152_FanucNormalData_20261027_200034.csv', 'B', '3.3 GB 未过滤原始（缺）'),
    ('三厂4152换刀时间.xlsx', 'ext', '换刀台账（商业敏感，需单独复核公开性 P1-04）'),
]

rows = []
for rel, seg, note in CANDIDATES:
    loc = None
    for base in (BASE, REPO, ROOT):
        p = os.path.join(base, rel.replace('/', os.sep))
        if os.path.exists(p):
            loc = p
            break
    in_repo = bool(loc and (loc.startswith(BASE) or loc.startswith(REPO)))
    if loc:
        rows.append(dict(输入=rel, 段=seg, 说明=note, 存在='YES',
                         位置=('仓库内' if in_repo else '仓库外(本地ROOT)'),
                         干净clone可复现=('YES' if in_repo else 'NO—需自备'),
                         字节=os.path.getsize(loc), SHA256_12=sha(loc)[:12]))
    else:
        rows.append(dict(输入=rel, 段=seg, 说明=note, 存在='MISSING',
                         位置='—', 干净clone可复现='NO—缺文件',
                         字节='', SHA256_12=''))

# 依赖版本：读 requirements.txt 的 pin 行
req = os.path.join(REPO, 'requirements.txt')
if not os.path.exists(req):
    req = os.path.join(ROOT, 'requirements.txt')
pins = []
if os.path.exists(req):
    for ln in open(req, encoding='utf-8'):
        ln = ln.strip()
        if ln and not ln.startswith('#'):
            pins.append(ln)

with open(os.path.join(OUT, 'N07_输入清单_SHA.csv'), 'w', encoding='utf-8-sig') as f:
    cols = ['输入', '段', '说明', '存在', '位置', '干净clone可复现', '字节', 'SHA256_12']
    f.write(','.join(cols) + '\n')
    for r in rows:
        f.write(','.join(str(r[c]) for c in cols) + '\n')

print('输入清单 →', os.path.join(OUT, 'N07_输入清单_SHA.csv'))
for r in rows:
    print('  [%s] %-60s %s %s' % (r['存在'], r['输入'][:58], r['位置'], r['干净clone可复现']))
print('\n依赖 pin（requirements.txt=%s）：' % (os.path.basename(req) if os.path.exists(req) else '缺'))
for p in pins:
    print('   ', p)

with open(os.path.join(OUT, 'N07_依赖版本.txt'), 'w', encoding='utf-8') as f:
    f.write('requirements 来源：%s\n' % req)
    f.write('\n'.join(pins) if pins else '（未找到 requirements.txt）')
print('DONE →', OUT)
