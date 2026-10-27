# -*- coding: utf-8 -*-
"""真正的 ground truth：在原始大文件上数 T1111 「连续出现段」的个数（刀号跳变）"""
import pandas as pd, numpy as np

RAW = "C:/abc/workBoddyHome刀具/20260914_4152/influxdb_FanucNormalData_20260914_081959.csv"
TOOL = "T1111"
prev = None
segs = 0
rows_in = 0
lens = []
cur = 0
tdur = []
t0 = t1 = None
n = 0
for ch in pd.read_csv(RAW, usecols=["_time", "L_tool_number"], encoding="utf-8-sig",
                      low_memory=False, chunksize=300000):
    n += len(ch)
    v = ch["L_tool_number"].astype(str).str.upper().str.strip().values
    tt = ch["_time"].values
    for i in range(len(v)):
        is_t = (v[i] == TOOL)
        if is_t:
            rows_in += 1
            if prev is not True:
                segs += 1
                if cur:
                    lens.append(cur)
                cur = 0
                t0 = tt[i]
            cur += 1
            t1 = tt[i]
        prev = is_t
if cur:
    lens.append(cur)

out = []
out.append("原始总行数 = %d" % n)
out.append("T1111 行数 = %d" % rows_in)
out.append("【刀号跳变】T1111 连续段数 = %d" % segs)
out.append("段长(行数): median=%s  mean=%.1f  min=%s  max=%s"
           % (np.median(lens), np.mean(lens), min(lens), max(lens)))
out.append("短段(<5行) 个数 = %d" % sum(1 for x in lens if x < 5))
out.append("段数(剔除<5行) = %d" % sum(1 for x in lens if x >= 5))
open("C:/abc/workBoddyHome刀具/out_T1111/_probe_tmp.txt", "w", encoding="utf-8").write("\n".join(out))
print("\n".join(out))
