# -*- coding: utf-8 -*-
# 同工况对齐趋势检验 + 磨损补偿列(L_wear_x/z, L_tool_offset)周期内行为检验
# 背景:混合工况的整周期趋势检验 0/13 通道达标;这里把 (life_cycle, 主程序, 子程序) 对齐后再算,
# 看同一工况下是否存在随寿命进度的一致性漂移(混合计算会稀释这种信号)。
import pandas as pd
import numpy as np

SRC = "merged_csv_20260901_160345_T2525_life_labeled_rollingfeat.csv"
COLS = ["_time", "life_cycle", "life_used_pct_time", "L_main_program", "L_sub_program",
        "L_circle_time", "L_spindle_load", "L_X_servo_load", "L_Y_servo_load",
        "L_Z_servo_load", "L_B_servo_load", "L_wear_x", "L_wear_z", "L_tool_offset",
        "L_state", "real_failure", "L_spindle_speed"]

df = pd.read_csv(SRC, usecols=COLS, low_memory=False)
df["_t"] = pd.to_datetime(df["_time"], errors="coerce")
df = df.sort_values("_t").reset_index(drop=True)

print("=" * 78)
print("第一部分:磨损补偿列在单个寿命周期内是否变化(若是=人工记录的磨损曲线)")
print("=" * 78)
for col in ["L_wear_x", "L_wear_z"]:
    print("\n-- %s --" % col)
    print("  cycle  nunique    min      max     range   与寿命进度相关r   变化方向")
    for cyc, g in df.groupby("life_cycle"):
        v = pd.to_numeric(g[col], errors="coerce")
        nu = v.nunique()
        rng = v.max() - v.min()
        y = g["life_used_pct_time"].astype("float64")
        m = v.notna() & y.notna() & (v.std() > 0)
        r = v[m].corr(y[m]) if m.sum() > 50 else np.nan
        # 单调性: 相邻不同值的符号
        dv = v.dropna().diff().dropna()
        dv = dv[dv != 0]
        direction = ("升" if (dv > 0).mean() > 0.8 else ("降" if (dv < 0).mean() > 0.8 else "杂")) if len(dv) > 0 else "-"
        print("  %5s  %7d %8.3f %8.3f %8.3f   %s   %s" % (
            str(cyc), nu, v.min(), v.max(), rng,
            ("%.3f" % r) if np.isfinite(r) else "  n/a", direction))

print()
print("注: L_tool_offset 是 JSON 字符串列(含 x_wear/z_wear), 与 L_wear_x/z 同源, 不重复检验")

print()
print("=" * 78)
print("第二部分:同工况对齐趋势检验 (按 life_cycle x 主程序 x 子程序 分组)")
print("=" * 78)
chans = ["L_spindle_load", "L_X_servo_load", "L_Y_servo_load", "L_Z_servo_load",
         "L_B_servo_load", "L_circle_time", "L_spindle_speed"]

d = df[df["life_used_pct_time"].notna()].copy()
print("有效行(有寿命进度标签): %d / %d" % (len(d), len(df)))
print("真实失效周期行数: %d, 周期: %s" % (
    (d["real_failure"] == "是").sum(),
    sorted(d.loc[d["real_failure"] == "是", "life_cycle"].unique().tolist())))

MIN_ROWS = 200  # 组内最少行数
res = {c: [] for c in chans}
grp_keys = ["life_cycle", "L_main_program", "L_sub_program"]
n_grp = 0
for _, g in d.groupby(grp_keys):
    if len(g) < MIN_ROWS:
        continue
    y = g["life_used_pct_time"].astype("float64")
    if y.nunique() < 5:
        continue
    n_grp += 1
    for c in chans:
        v = g[c].astype("float64")
        m = v.notna()
        if m.sum() < MIN_ROWS or v[m].std() == 0:
            continue
        r = v[m].corr(y[m], method="spearman")
        if np.isfinite(r):
            res[c].append(r)

print("参与检验的同工况组数(>= %d 行): %d" % (MIN_ROWS, n_grp))
print()
print("%-18s %6s %10s %10s %10s %s" % ("通道", "组数", "r中位", "|r|中位", "正相关占比", "判读"))
for c in chans:
    arr = np.array(res[c])
    if len(arr) == 0:
        print("%-18s %6d   (无有效组)" % (c, 0))
        continue
    med = np.median(arr)
    amed = np.median(np.abs(arr))
    pos = (arr > 0).mean()
    verdict = ""
    if amed >= 0.3 and (pos >= 0.8 or pos <= 0.2):
        verdict = "<== 达标! 同工况下有趋势"
    elif amed >= 0.2:
        verdict = "有弱迹象"
    else:
        verdict = "无信号"
    print("%-18s %6d %10.3f %10.3f %9.0f%%  %s" % (c, len(arr), med, amed, 100 * pos, verdict))

# 对照: 不分组的混合基线
print()
print("对照(混合全部工况, 整周期 spearman):")
for c in chans:
    rs = []
    for cyc, g in d.groupby("life_cycle"):
        v = g[c].astype("float64")
        y = g["life_used_pct_time"].astype("float64")
        m = v.notna()
        if m.sum() > 200 and v[m].std() > 0:
            rs.append(v[m].corr(y[m], method="spearman"))
    arr = np.array(rs)
    print("  %-18s 周期数 %2d  r中位 %7.3f  |r|中位 %7.3f  正相关占比 %3.0f%%" % (
        c, len(arr), np.median(arr), np.median(np.abs(arr)), 100 * (arr > 0).mean()))

print()
print("=" * 78)
print("第三部分:单件循环时间 L_circle_time 的同工件对齐(磨损往往让加工变慢)")
print("=" * 78)
# circle_time 是每件的标量,组内中位数随寿命进度的漂移
d2 = d[d["L_circle_time"].notna() & (d["L_circle_time"] > 0)]
rs = []
for _, g in d2.groupby(grp_keys):
    if len(g) < MIN_ROWS:
        continue
    y = g["life_used_pct_time"].astype("float64")
    v = g["L_circle_time"].astype("float64")
    if y.nunique() >= 5 and v.std() > 0:
        r = v.corr(y, method="spearman")
        if np.isfinite(r):
            rs.append(r)
arr = np.array(rs)
if len(arr):
    print("  同工况组数 %d, r中位 %.3f, |r|中位 %.3f, 正相关(变慢)占比 %.0f%%" % (
        len(arr), np.median(arr), np.median(np.abs(arr)), 100 * (arr > 0).mean()))
    if np.median(np.abs(arr)) >= 0.3 and (arr > 0).mean() >= 0.8:
        print("  <== 加工变慢趋势成立, 可作为特征")
    else:
        print("  无一致变慢趋势")
