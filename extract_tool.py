# -*- coding: utf-8 -*-
"""
extract_tool.py —— 从全机床原始 CSV 中抽取指定刀具的数据行
============================================================
只做一件事：按刀具号过滤行并另存，不打标、不算特征。

用法（PowerShell）:
  cd c:\abc\workBoddyHome刀具
  $env:PYTHONUTF8 = "1"
  python extract_tool.py --file "20260914_4152\influxdb_FanucNormalData_20260914_081959.csv" --tool T1111
  python extract_tool.py --file a.csv b.csv --tool T1111 T0202 --out-dir out_extract

说明:
  * 刀具号列自动探测（列名含"刀号/刀具号/tool"），也可 --tool-col 手动指定
  * 刀具号写 T1111 或 1111 均可（比较时忽略 T 前缀、大小写、空格）
  * 分块读取，大文件不会撑爆内存
  * 命中 0 行时自动打印该列实际取值，便于发现写错刀具号
"""
import os
import re
import sys
import time
import argparse
import pandas as pd

CHUNK = 200_000
CAND_COLS = ('刀具号', '刀号', 'tool', 'tool_no', 'toolno', 'tool_number', 't_code')
ENC_OUT = 'utf-8-sig'

def norm(s):
    return str(s).strip().upper()

def strip_t(s):
    return re.sub(r'^T+', '', norm(s))

def find_tool_col(columns):
    low = [c.lower() for c in columns]
    for cand in CAND_COLS:
        if cand.lower() in low:
            return columns[low.index(cand.lower())]
    for i, c in enumerate(low):
        if 'tool' in c or ('刀' in columns[i] and '号' in columns[i]):
            return columns[i]
    return None

def extract_one(path, tools, tool_col, out_dir):
    t0 = time.time()
    head = pd.read_csv(path, nrows=0, encoding='utf-8-sig')
    head.columns = [c.replace('\ufeff', '') for c in head.columns]
    tc = tool_col or find_tool_col(list(head.columns))
    if tc is None:
        print('[abort] %s\n  找不到刀具号列，请用 --tool-col 指定。前30列: %s'
              % (path, list(head.columns)[:30]))
        return
    targets = {t: strip_t(t) for t in tools}
    parts = {t: [] for t in tools}
    n_total, n_hit = 0, {t: 0 for t in tools}
    for ch in pd.read_csv(path, chunksize=CHUNK, encoding='utf-8-sig'):
        ch.columns = [c.replace('\ufeff', '') for c in ch.columns]
        n_total += len(ch)
        v = ch[tc].astype(str).map(strip_t)
        for t, tv in targets.items():
            m = v == tv
            if m.any():
                parts[t].append(ch[m])
                n_hit[t] += int(m.sum())
    print('\n[文件] %s' % path)
    print('  刀具号列 = %s | 总行数 = %d | 耗时 %.1fs' % (tc, n_total, time.time() - t0))
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    for t in tools:
        if not parts[t]:
            print('  [miss] %-8s 命中 0 行' % t)
            sample = pd.read_csv(path, usecols=[tc], nrows=CHUNK * 3, encoding='utf-8-sig')[tc]
            uniq = sorted(set(norm(x) for x in sample.dropna()))[:20]
            print('         该列实际取值(前20): %s' % uniq)
            continue
        df = pd.concat(parts[t], ignore_index=True)
        dst = os.path.join(out_dir, '%s__%s.csv' % (stem, norm(t)))
        df.to_csv(dst, index=False, encoding=ENC_OUT)
        print('  [ok]   %-8s 命中 %6d 行 -> %s' % (t, n_hit[t], dst))

def main():
    ap = argparse.ArgumentParser(description='按刀具号抽取原始数据行（只抽取，不打标/不算特征）')
    ap.add_argument('--file', nargs='+', required=True, help='输入 CSV（可多个）')
    ap.add_argument('--tool', nargs='+', required=True, help='刀具号（可多个，如 T1111 或 1111）')
    ap.add_argument('--tool-col', default=None, help='手动指定刀具号列名（默认自动探测）')
    ap.add_argument('--out-dir', default='out_extract')
    a = ap.parse_args()
    for p in a.file:
        if not os.path.isfile(p):
            print('[abort] 文件不存在: %s' % p)
            sys.exit(1)
    for p in a.file:
        extract_one(p, [norm(t) for t in a.tool], a.tool_col, a.out_dir)

if __name__ == '__main__':
    main()