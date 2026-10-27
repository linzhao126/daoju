#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
按刀具号拆分 influxdb Fanuc CSV 为独立文件。
用法:
  python split_by_tool.py <源csv> [输出目录]

特性:
  - 自动探测刀具号列 (兼容 L_tool_number / R_tool_number / 双刀塔)
  - 分块读取 (默认 300k/块) 避免大文件内存爆
  - 单通道: <源名>_<刀号>.csv
  - 双通道: <源名>_L_<刀号>.csv 与 <源名>_R_<刀号>.csv (分别拆分, 不丢信息)
  - UTF-8-SIG 编码 (Excel 不乱码)
  - 输出 README_拆分说明.txt 汇总
"""
import os, sys, time, pandas as pd

SRC = sys.argv[1] if len(sys.argv) > 1 else None
if not SRC:
    print("用法: python split_by_tool.py <源csv> [输出目录]")
    sys.exit(1)
if not os.path.exists(SRC):
    print("文件不存在:", SRC)
    sys.exit(1)

OUTDIR = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    os.path.dirname(SRC), "split_by_tool_" + os.path.splitext(os.path.basename(SRC))[0])

base = os.path.splitext(os.path.basename(SRC))[0]

# 探测刀具号列
df0 = pd.read_csv(SRC, nrows=3)
tool_cols = [c for c in df0.columns if "tool_number" in c.lower()]
print("文件列数:", df0.shape[1])
print("刀具号列:", tool_cols)
if not tool_cols:
    print("未发现 tool_number 列, 退出")
    sys.exit(1)

all_summary = {}
t0 = time.time()
os.makedirs(OUTDIR, exist_ok=True)   # 必须在写 CSV 前创建目录
print("输出目录:", OUTDIR)

for col in tool_cols:
    prefix = col.split("_")[0]  if "_" in col else "TOOL"   # L / R / TOOL
    subdir = OUTDIR
    counts = {}
    written = set()
    print(f"\n>>> 处理列 {col} (前缀 {prefix})")
    for i, chunk in enumerate(pd.read_csv(SRC, chunksize=300000, low_memory=False)):
        chunk[col] = chunk[col].astype(str).str.strip()
        for tval, sub in chunk.groupby(col):
            if tval in ("", "nan", "None"):
                tval = "UNKNOWN"
            fname = os.path.join(subdir, f"{base}_{prefix}_{tval}.csv")
            if tval not in written:
                sub.to_csv(fname, index=False, mode="w", header=True, encoding="utf-8-sig")
                written.add(tval)
            else:
                sub.to_csv(fname, index=False, mode="a", header=False, encoding="utf-8-sig")
            counts[tval] = counts.get(tval, 0) + len(sub)
        print(f"  chunk {i} 完成, 累计 {time.time()-t0:.1f}s, 已见刀号 {sorted(counts.keys())}")
    all_summary[col] = counts
    print(f"  列 {col} 合计 {sum(counts.values()):,} 行, {len(counts)} 个刀号")

# 写汇总 README
readme = os.path.join(OUTDIR, "README_拆分说明.txt")
os.makedirs(OUTDIR, exist_ok=True)
lines = []
lines.append(f"{base} 按刀具号拆分结果")
lines.append("=" * 60)
lines.append(f"源文件: {SRC}")
lines.append(f"总行数: {sum(sum(v.values()) for v in all_summary.values()):,}")
lines.append(f"拆分脚本: {os.path.abspath(__file__)}")
lines.append(f"输出目录: {OUTDIR}")
lines.append(f"命名规则: <源名>_[L|R]_<刀号>.csv ; UTF-8-SIG 编码")
lines.append("")
for col, counts in all_summary.items():
    prefix = col.split("_")[0] if "_" in col else "TOOL"
    lines.append(f"--- 刀具号列 {col} ({len(counts)} 个) ---")
    for k in sorted(counts):
        lines.append(f"  {prefix}_{k}: {counts[k]:>9,d} 行")
    lines.append("")
lines.append("提示: T0000 一般为待机/空刀位(非真实刀具), 建模时通常排除。")
with open(readme, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print(f"\n汇总已写入: {readme}")
print(f"总耗时 {time.time()-t0:.1f}s")
