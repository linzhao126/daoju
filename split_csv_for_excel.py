#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把超大 CSV 拆成 Excel 能装下的小文件（解决"超过 Excel 最大行"问题）。

三种模式：
  split  按行数切片，每片 <= 100万行 -> part_001.xlsx / part_002.xlsx ...
  bydate 按日期列(默认 _time)每天一个 xlsx -> 2026-08-17.xlsx ...
  sample 只导出前 N 行做快速查看 -> sample.xlsx

用法示例：
  python split_csv_for_excel.py influxdb_FanucNormalData_20260824_094536.csv
  python split_csv_for_excel.py 大文件.csv --mode bydate
  python split_csv_for_excel.py 大文件.csv --mode sample --sample 3000
"""
import pandas as pd
import argparse, os, sys

XLSX_LIMIT = 1_048_576  # .xlsx 单行上限

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", help="要拆分的 CSV 路径")
    ap.add_argument("--mode", choices=["split","bydate","sample"], default="split")
    ap.add_argument("--rows", type=int, default=1_000_000, help="split 模式每片行数(默认100万)")
    ap.add_argument("--datecol", default="_time", help="bydate 模式的日期列名")
    ap.add_argument("--sample", type=int, default=2000, help="sample 模式导出行数")
    ap.add_argument("--outdir", default="excel_parts", help="输出目录")
    args = ap.parse_args()

    if not os.path.exists(args.csv):
        sys.exit(f"找不到文件: {args.csv}")
    os.makedirs(args.outdir, exist_ok=True)
    base = os.path.splitext(os.path.basename(args.csv))[0]

    if args.mode == "sample":
        df = pd.read_csv(args.csv, encoding="utf-8-sig", nrows=args.sample)
        out = os.path.join(args.outdir, f"{base}_sample{args.sample}.xlsx")
        df.to_excel(out, index=False)
        print(f"已导出前 {len(df)} 行 -> {out}")

    elif args.mode == "bydate":
        # 整文件读入，按日期分组写出（适合时间序列数据）
        print("读取整文件(按日期拆分)...")
        df = pd.read_csv(args.csv, encoding="utf-8-sig")
        if args.datecol not in df.columns:
            sys.exit(f"找不到日期列 {args.datecol}，可用列: {list(df.columns)[:10]}...")
        dstr = df[args.datecol].astype(str).str[:10]
        for day, grp in df.groupby(dstr):
            out = os.path.join(args.outdir, f"{day}.xlsx")
            grp.to_excel(out, index=False)
            print(f"  {day}: {len(grp)} 行 -> {out}")
        print(f"按日期拆分完成，共 {dstr.nunique()} 个文件。")

    else:  # split
        n = sum(1 for _ in open(args.csv, encoding="utf-8-sig", errors="ignore")) - 1
        parts = (n // args.rows) + (1 if n % args.rows else 0)
        print(f"总行数 {n}，每片 {args.rows} 行 -> 将生成 {parts} 个 xlsx 文件")
        if n > XLSX_LIMIT:
            print(f"  ⚠️ 原文件超 Excel 上限({XLSX_LIMIT})，拆完后每个 part 都在上限内。")
        reader = pd.read_csv(args.csv, encoding="utf-8-sig", chunksize=args.rows)
        for i, chunk in enumerate(reader, 1):
            out = os.path.join(args.outdir, f"{base}_part{i:03d}.xlsx")
            chunk.to_excel(out, index=False)
            print(f"  part {i:03d}: {len(chunk)} 行 -> {out}")
        print("拆分完成。")

if __name__ == "__main__":
    main()
