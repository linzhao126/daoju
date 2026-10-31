# -*- coding: utf-8 -*-
"""P0-D01 交付物：4152/2496/2428 相关原始、labeled、xalign、tool.db 快照的清单表。
对每个文件给出：体积、SHA256、行数、时间覆盖（首末 _time）、存放位置、是否公开仓库内、衍生关系。
> 500 MB 的大文件只登记体积与生成 SHA/行数的命令，不在此处实算（避免长时阻塞）。
"""
import csv
import hashlib
import os

HERE = os.path.dirname(os.path.abspath(__file__))          # .../20261027/09_repro_scripts
REPO = os.path.abspath(os.path.join(HERE, '..'))           # .../20261027
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))  # c:/abc/workBoddyHome刀具
BIG_MB = 200     # 超过此阈值的文件不在此处实算 SHA/行数（避免长时间阻塞），仅登记体积与生成命令
OUT = os.path.join(REPO, 'P0_数据与代码移交', 'P0-D01_数据清单表.csv')
os.makedirs(os.path.dirname(OUT), exist_ok=True)

# (角色, 位置标签, 相对路径 或 绝对路径)
# 角色：RAW-全量 / RAW-旧批 / LABELED-衍生 / ALIGN产物 / 台账原始 / DB-快照 / 打包-交付件
FILES = [
    ('RAW-全量',       '工作区本地', os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261027_200034.csv')),
    ('RAW-旧批-4152', '工作区本地', os.path.join(ROOT, 'influxdb_FanucNormalData_20260910_085752_4152.csv')),
    ('RAW-旧批-4152', '工作区本地', os.path.join(ROOT, '20260914_4152/influxdb_FanucNormalData_20260924_170215.csv')),
    ('RAW-旧批-4152', '工作区本地', os.path.join(ROOT, '20260914_4152/influxdb_FanucNormalData_20260914_081959.csv')),
    ('RAW-旧批',       '工作区本地', os.path.join(ROOT, 'influxdb_FanucNormalData_20260829_133632.csv')),
    ('RAW-旧批',       '工作区本地', os.path.join(ROOT, 'influxdb_FanucNormalData_20260829_144941.csv')),
    ('RAW-旧批',       '工作区本地', os.path.join(ROOT, 'influxdb_FanucNormalData_20260831_084019.csv')),
    ('RAW-旧批',       '工作区本地', os.path.join(ROOT, 'influxdb_FanucNormalData_20260831_092637.csv')),
    ('RAW-旧批',       '工作区本地', os.path.join(ROOT, 'influxdb_FanucNormalData_20260831_104331.csv')),
    ('RAW-旧批',       '工作区本地', os.path.join(ROOT, 'influxdb_FanucNormalData_20260901_091924.csv')),
    ('RAW-旧批',       '工作区本地', os.path.join(ROOT, 'influxdb_FanucNormalData_20260901_114136.csv')),
    ('LABELED-衍生',   '工作区本地', os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv')),
    ('LABELED-衍生',   '工作区本地', os.path.join(ROOT, 'influxdb_FanucNormalData_20260829_144941_T1111_life_labeled.csv')),
    ('LABELED-衍生',   '工作区本地', os.path.join(ROOT, 'influxdb_FanucNormalData_20260901_114136_T2525_life_labeled.csv')),
    ('LABELED-衍生',   '工作区本地', os.path.join(ROOT, 'influxdb_2428_FanucNormalData_20261014_141920_R_T3434_T3434_life_labeled.csv')),
    ('LABELED-衍生',   '工作区本地', os.path.join(ROOT, 'merged_csv_20260901_160345_T2525_T2525_life_labeled.csv')),
    ('LABELED-衍生',   '工作区本地', os.path.join(ROOT, 'merged_csv_20260901_160345_T2525_life_labeled_rollingfeat.csv')),
    ('MERGED-中间件', '工作区本地', os.path.join(ROOT, 'merged_20260810_113424-notoolLift.csv')),
    ('MERGED-中间件', '工作区本地', os.path.join(ROOT, 'merged_csv_20260816_123219手动标记刀具寿命.csv')),
    ('MERGED-中间件', '工作区本地', os.path.join(ROOT, 'merged_csv_20260901_160345.csv')),
    ('MERGED-中间件', '工作区本地', os.path.join(ROOT, 'merged_csv_20260901_160345_T2525.csv')),
    ('MERGED-中间件', '工作区本地', os.path.join(ROOT, 'merged_csv_20260908_131914.csv')),
    ('RAW-局部导出',   '工作区本地', os.path.join(ROOT, '20261008/influxdb_4152_FanucNormalData_20261007_151102_L_T1111.csv')),
    ('ALIGN产物',       '交付包内', os.path.join(REPO, '22_c48-c60_xalign/labeled_T1111_new.csv')),
    ('ALIGN产物',       '交付包内', os.path.join(REPO, '22_c48-c60_xalign/xalign_HI_new_c49c60.csv')),
    ('ALIGN产物',       '交付包内', os.path.join(REPO, '22_c48-c60_xalign/backup_history/xalign_HI.csv')),
    ('原始抽样-交付', '交付包内', os.path.join(REPO, '03_原始数据与标签/raw_or_traceable/raw_extract_T1111_20cycles.csv.gz')),
    ('DB-快照',         '交付包内', os.path.join(REPO, '03_原始数据与标签/tool_db_snapshot/tool.db.readonly_copy')),
    ('DB-快照',         '交付包内', os.path.join(REPO, '03_原始数据与标签/tool_db_snapshot/raw_rows.csv')),
    ('DB-快照',         '交付包内', os.path.join(REPO, '03_原始数据与标签/tool_db_snapshot/pieces.csv')),
    ('DB-快照',         '交付包内', os.path.join(REPO, '03_原始数据与标签/tool_db_snapshot/tool_events.csv')),
    ('DB-快照',         '交付包内', os.path.join(REPO, '03_原始数据与标签/tool_db_snapshot/history.csv')),
    ('DB-快照',         '交付包内', os.path.join(REPO, '03_原始数据与标签/tool_db_snapshot/kv.csv')),
    ('台账原始',       '工作区本地', os.path.join(ROOT, '20261008/三厂4152换刀时间.xlsx')),
]

def sha256(path, block=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(block), b''):
            h.update(chunk)
    return h.hexdigest()

def line_count(path, block=1 << 20):
    n = 0
    with open(path, 'rb') as f:
        while True:
            b = f.read(block)
            if not b:
                break
            n += b.count(b'\n')
    return n

def time_span(path, header_time_idx=None):
    """读首行 header 找 _time 列索引，返回 (first_row_time, last_row_time)；失败给 ''。"""
    try:
        with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
            first = f.readline().rstrip('\r\n')
        cols = first.split(',')
        idxs = [i for i, c in enumerate(cols) if c.strip().lower() in ('_time', 'time', 'timestamp', 'ts', 'recv_ts')]
        if not idxs:
            return ('', '', '无 _time 列')
        i = idxs[0]
        # first data line
        with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
            f.readline()
            ln = f.readline().rstrip('\r\n')
            t_first = ln.split(',')[i] if ln else ''
        # last data line — tail 256KB
        sz = os.path.getsize(path)
        with open(path, 'rb') as f:
            if sz > 256 * 1024:
                f.seek(sz - 256 * 1024)
                f.readline()  # 丢弃可能截断的一行
            lines = f.read().decode('utf-8', errors='replace').splitlines()
        t_last = lines[-1].split(',')[i] if lines else ''
        return (t_first, t_last, '')
    except Exception as e:
        return ('', '', 'ERR:%s' % type(e).__name__)

def rel_in_repo(p):
    try:
        r = os.path.relpath(p, ROOT).replace('\\', '/')
        return r
    except Exception:
        return p

rows = []
for role, loc, path in FILES:
    if not os.path.exists(path):
        rows.append(dict(角色=role, 位置=loc, 文件=os.path.basename(path),
                         路径=rel_in_repo(path), 存在='否', 说明='文件未在本机找到'))
        continue
    sz = os.path.getsize(path)
    big = sz > BIG_MB * 1024 * 1024
    if big:
        sha = '(未计算，>%dMB)' % BIG_MB
        ln = '(未计算)'
        tf, tl, note = '', '', 'SHA/行数/时间覆盖 用命令：python -X utf8 09_repro_scripts/s67_manifest_big.py "%s"' % path
    elif path.endswith('.gz'):
        sha = sha256(path)
        ln = '(压缩内行数待解gz)'
        tf, tl, note = '', '', 'gz 压缩，时间覆盖需先解'
    elif path.endswith(('.xlsx', '.db', '.db.readonly_copy', '-shm', '-wal')):
        sha = sha256(path)
        ln = '(非行式)'
        tf, tl, note = '', '', '二进制/DB，用 sqlite3 单独导出'
    elif path.endswith('.csv'):
        sha = sha256(path)
        ln = line_count(path)
        tf, tl, note = time_span(path)
    else:
        sha = sha256(path)
        ln = ''
        tf, tl, note = '', '', ''
    rows.append(dict(角色=role, 位置=loc, 文件=os.path.basename(path),
                     路径=rel_in_repo(path), 存在='是', 字节=sz,
                     MB=round(sz / 1024 / 1024, 2), SHA256=sha, 行数=ln,
                     首行_time=tf, 末行_time=tl, 备注=note))

COLS = ['角色', '位置', '文件', '路径', '存在', '字节', 'MB', 'SHA256', '行数',
        '首行_time', '末行_time', '备注']
with open(OUT, 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.DictWriter(f, fieldnames=COLS)
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, '') for c in COLS})

print('DONE →', OUT)
print('%d 行；>=%dMB 的大文件按命令模板待生成' % (len(rows), BIG_MB))
for r in rows:
    if r.get('存在') == '否':
        print('  MISS:', r['文件'])
    elif r.get('MB', 0) and r['MB'] > BIG_MB:
        print('  BIG :', '%10.1f MB' % r['MB'], r['文件'])
