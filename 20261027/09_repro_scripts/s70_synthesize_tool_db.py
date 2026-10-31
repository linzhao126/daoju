# -*- coding: utf-8 -*-
"""s70_synthesize_tool_db.py —— 从 3.3 GB Influx CSV + life_audit + xlsx 灌一份
SYNTHESIZED 影子服务数据库（供审核方独立复算用），落到 `手工提供文件/tool.db.synthesized`。

设计边界（不能假装的东西，写死）：
  1. raw_rows     ← CSV 全量灌（过滤 deviceName=4152 + L_tool_number=T1111）
  2. raw_rows.recv_ts  ← 加列但全填 NULL（历史到达时刻不可回填）
  3. pieces       ← 按 allnum 从 raw_rows 聚合（load_med/n_points/ts/curve）
  4. history      ← 从 life_audit_4152.csv 灌（prog / life / src='SYNTHESIZED' / valid=1）
  5. tool_events  ← **保持空**（xlsx 60 行是车间文字，不是服务运行时判出来的事件；
                     另出 `手工提供文件/ledger_changes.csv` 供审核方按需读）
  6. kv           ← 灌一行 `__SYNTH_NOTE__` 说明；其余运行时锁存（grant_*/sealed_upto）保持空

用法：
    python -X utf8 09_repro_scripts/s70_synthesize_tool_db.py            # 全量
    python -X utf8 09_repro_scripts/s70_synthesize_tool_db.py --sample 500000
"""
import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time

csv.field_size_limit(10 * 1024 * 1024)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))                     # daoju_repo/20261027
WORK = os.path.abspath(os.path.join(REPO, '..', '..'))               # c:/abc/workBoddyHome刀具
CSV_IN = os.path.join(WORK, '手工提供文件', 'influxdb_4152_FanucNormalData_20261027_200034.csv')
XLSX_IN = os.path.join(WORK, '手工提供文件', '三厂换刀记录.xlsx')
LIFE_AUDIT = os.path.join(REPO, '03_原始数据与标签', 'life_audit_4152.csv')
DB_OUT = os.path.join(WORK, '手工提供文件', 'tool.db.synthesized')
CSV_OUT_LEDGER = os.path.join(WORK, '手工提供文件', 'ledger_changes.csv')

DEVICE = '4152'
TOOL_NO = 'T1111'
BATCH = 10000


def parse_prog(v):
    """'20(C5G25-03-BAR331)' → 20 (int). 拿不到就 None."""
    if v is None:
        return None
    m = re.match(r'^\s*(\d+)', str(v))
    return int(m.group(1)) if m else None


def build_schema(cur):
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS raw_rows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        allnum INTEGER,
        prog INTEGER,
        x_pos REAL,
        z_pos REAL,
        spindle_load REAL,
        spindle_speed REAL,
        feedrate REAL,
        state INTEGER,
        modal TEXT,
        recv_ts TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_raw_allnum ON raw_rows(allnum);
    CREATE INDEX IF NOT EXISTS idx_raw_ts ON raw_rows(ts);

    CREATE TABLE IF NOT EXISTS pieces (
        allnum INTEGER PRIMARY KEY,
        prog INTEGER,
        load_med REAL,
        n_points INTEGER,
        ts TEXT,
        curve TEXT
    );

    CREATE TABLE IF NOT EXISTS tool_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, event TEXT, prog INTEGER, allnum INTEGER,
        pieces INTEGER, state TEXT, reason TEXT, limit_at INTEGER
    );

    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        prog INTEGER, life REAL, src TEXT, valid INTEGER
    );

    CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
    """)


def load_raw_rows(cur, sample_limit=None):
    """从 3.3 GB CSV 流式读，过滤 deviceName=4152 + L_tool_number=T1111。"""
    t0 = time.time()
    n_read = n_kept = 0
    buf = []
    with open(CSV_IN, encoding='utf-8-sig', newline='') as f:
        rd = csv.DictReader(f)
        for row in rd:
            n_read += 1
            if row.get('deviceName') != DEVICE:
                continue
            if row.get('L_tool_number') != TOOL_NO:
                continue
            try:
                allnum = int(row['L_allnum'])
            except (ValueError, KeyError):
                continue
            buf.append((
                row.get('_time'), allnum, parse_prog(row.get('L_main_program')),
                _f(row.get('L_abso_x')), _f(row.get('L_abso_z')),
                _f(row.get('L_spindle_load')), _f(row.get('L_spindle_speed')),
                _f(row.get('L_feed_override')),
                _i(row.get('L_state')), (row.get('L_modal') or '').strip()[:16],
            ))
            if len(buf) >= BATCH:
                cur.executemany(
                    'INSERT INTO raw_rows(ts,allnum,prog,x_pos,z_pos,spindle_load,spindle_speed,feedrate,'
                    'state,modal,recv_ts) VALUES (?,?,?,?,?,?,?,?,?,?,NULL)', buf)
                n_kept += len(buf)
                buf = []
                if n_kept % 100000 == 0:
                    cur.connection.commit()
                    print('  kept=%d  read=%d  elapsed=%.0fs' % (n_kept, n_read, time.time() - t0), flush=True)
            if sample_limit and n_kept >= sample_limit:
                break
    if buf:
        cur.executemany(
            'INSERT INTO raw_rows(ts,allnum,prog,x_pos,z_pos,spindle_load,spindle_speed,feedrate,'
            'state,modal,recv_ts) VALUES (?,?,?,?,?,?,?,?,?,?,NULL)', buf)
        n_kept += len(buf)
    cur.connection.commit()
    print('[raw_rows] read=%d kept=%d  %.1fs' % (n_read, n_kept, time.time() - t0))
    return n_kept


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def build_pieces(cur):
    """按 allnum 聚合：load_med / n_points / ts=min / curve=spindle_load 序列 JSON。"""
    t0 = time.time()
    cur.execute('DROP TABLE IF EXISTS pieces')
    cur.execute("""CREATE TABLE pieces (
        allnum INTEGER PRIMARY KEY, prog INTEGER, load_med REAL,
        n_points INTEGER, ts TEXT, curve TEXT)""")
    cur.execute("""INSERT INTO pieces (allnum, prog, load_med, n_points, ts, curve)
        SELECT allnum, MIN(prog),
               AVG(spindle_load),
               COUNT(*),
               MIN(ts),
               NULL
        FROM raw_rows GROUP BY allnum""")
    # curve 单独填（SQLite 无原生 array_agg；分批拉）
    cur.execute('SELECT allnum FROM pieces')
    alln = [r[0] for r in cur.fetchall()]
    print('[pieces] %d 件聚合完成  %.1fs' % (len(alln), time.time() - t0))


def load_history(cur):
    """从 life_audit_4152.csv 灌 prog / 寿命件数。"""
    if not os.path.exists(LIFE_AUDIT):
        print('[history] 缺 life_audit_4152.csv，跳过')
        return
    rows = list(csv.DictReader(open(LIFE_AUDIT, encoding='utf-8-sig')))
    buf = []
    for r in rows:
        try:
            prog = int(r.get('AlignedProgForM0') or '0')
            life = float(r.get('AllNumSpan') or 0)
        except (ValueError, TypeError):
            continue
        if not prog or not life:
            continue
        buf.append((prog, life, 'SYNTHESIZED_FROM_LIFE_AUDIT', 1))
    cur.executemany('INSERT INTO history(prog,life,src,valid) VALUES (?,?,?,?)', buf)
    cur.connection.commit()
    print('[history] 灌 %d 条（prog / AllNumSpan 件数）' % len(buf))


def dump_ledger_changes():
    """xlsx 60 条 → 独立 CSV，不入 tool_events。"""
    import openpyxl
    wb = openpyxl.load_workbook(XLSX_IN, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    hdr = ['主副轴', '刀塔', '刀号', '顺序号', 'StartTime', 'EndTime',
           '刀具型号', '换刀原因', '是否真实失效', '备注']
    with open(CSV_OUT_LEDGER, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(hdr)
        for r in rows[1:]:
            w.writerow([str(x) if x is not None else '' for x in r])
    print('[ledger_changes.csv] 导出 %d 条（原 xlsx sheet=%s，未入 tool_events 表）'
          % (len(rows) - 1, '水性 4152'))


def write_note(cur):
    cur.execute('INSERT OR REPLACE INTO kv(k,v) VALUES (?,?)', ('__SYNTH_NOTE__', json.dumps({
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'source_influx_csv': os.path.basename(CSV_IN),
        'source_ledger_xlsx': os.path.basename(XLSX_IN),
        'source_life_audit': os.path.basename(LIFE_AUDIT),
        'filter': 'deviceName==4152 AND L_tool_number==T1111',
        'filled': ['raw_rows（recv_ts 全 NULL）', 'pieces（load_med/n_points/ts；curve 未填）',
                   'history（prog/AllNumSpan 件数，src=SYNTHESIZED）',
                   'kv.__SYNTH_NOTE__'],
        'left_empty_or_null': [
            'raw_rows.recv_ts 值（历史到达时刻不可回填）',
            'tool_events 全表（车间换刀 ≠ 服务运行时判出来的事件；见同目录 ledger_changes.csv）',
            'kv 运行时锁存 grant_*/sealed_upto（服务从未跑过这段时间）',
            'pieces.curve（每条 30+ 点浮点数组；如需请另跑 s70b）'
        ],
        'usage': '审核方可用 `server.py --db tool.db.synthesized --source influx` 走影子服务；'
                 '可复算 raw_rows/pieces/history；**不得**据此得"服务运行时行为"或"生产 P50/P95 延迟"结论',
        'not_equal_to': '本文件不是"生产 tool.db 一致快照"；真实运行时快照见仓库 '
                        '03_原始数据与标签/tool_db_snapshot/tool.db.readonly_copy（同 SHA=3fd701389c45，24h 副本）',
    }, ensure_ascii=False)))
    cur.connection.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', type=int, default=None,
                    help='raw_rows 只灌前 N 行（快速自测用；不加=全量）')
    args = ap.parse_args()

    if os.path.exists(DB_OUT):
        os.remove(DB_OUT)
    con = sqlite3.connect(DB_OUT)
    cur = con.cursor()
    print('=== s70 SYNTHESIZED tool.db 灌库开始 ===')
    print('输入 CSV:', CSV_IN, '大小 %.2f GB' % (os.path.getsize(CSV_IN) / 1024**3))
    build_schema(cur)
    load_raw_rows(cur, sample_limit=args.sample)
    build_pieces(cur)
    load_history(cur)
    write_note(cur)
    cur.execute('VACUUM')
    con.close()
    dump_ledger_changes()
    sz = os.path.getsize(DB_OUT) / 1024 / 1024
    print('=== DONE → %s   (%.1f MB)   附 %s ===' % (DB_OUT, sz, CSV_OUT_LEDGER))


if __name__ == '__main__':
    main()
