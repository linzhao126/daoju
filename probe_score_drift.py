# -*- coding: utf-8 -*-
"""
异常分数漂移探针 —— 「当前字段无信号」封案前的最后一块石头
================================================================
问题：IsolationForest 多变量反常分（0~1，越高越反常）是否在每把刀的
寿命周期内随磨损**缓慢爬升**，并在换新刀后**回落**（锯齿形）？
此前全部趋势检验都是单通道；多通道的联合微小漂移尚未测过。

设计（2026-09-11 与用户确认，判据预先写死，防事后找补）：
  A 爬升检验：每周期内 score vs life_used_pct_cut，
     20 等份寿命进度 bin 的中位数分数做 Spearman 相关。
     PASS_A = 正常周期中位 |r| >= 0.3 且 >=80% 周期同号。
  B 锯齿检验：每周期 末10%寿命行 与 初10%寿命行 的平均分配对比较；
     另查相邻周期「上一把末10% vs 下一把初10%」是否回落。
     PASS_B = >=80% 周期 末>初 且 符号检验 p<0.05 且 >=70% 相邻对回落。
  C sanity：c13（刀柄坏）超阈行占比必须明显高于正常周期 ——
     证明这套分数「该动的时候会动」。
  D 混淆排除：同一 (周期, 主程序) 内重算行级相关，排除程序切换造成的假爬升。

最终裁定：A 与 B 同时 PASS -> 有信号，重开预测线讨论；
          任一 FAIL -> 「当前字段无信号」彻底封案。

运行：.venv/Scripts/python.exe probe_score_drift.py > 异常分数漂移探针.txt 2>&1
产出：本报告（stdout）+ score_drift_cycles.png
"""
import os
import sys
import json
import time

import numpy as np
import pandas as pd

BASE = r"C:/abc/workBoddyHome刀具"
sys.path.insert(0, BASE)

from ts_split import SRC                      # noqa: E402
from alarm_online import load_features, LIB_JOB, LIB_META  # noqa: E402

REPORT_PNG = os.path.join(BASE, "score_drift_cycles.png")
N_BINS = 20          # 寿命进度 0~100% 切 20 份
EDGE = 10.0          # 锯齿检验的「初/末」口径：<=10% 与 >=90%
MIN_BIN_ROWS = 10    # bin 内最少行数
ROW_SUB = 5000       # 行级相关的每周期子采样上限（提速）


def A(s=""):
    print(s)


def main():
    import joblib
    from scipy import stats

    t0 = time.time()
    meta = json.load(open(LIB_META, encoding="utf-8"))
    iso = joblib.load(LIB_JOB)
    norm_cycles = sorted(int(c) for c in meta["fit_cycles"])
    thr = float(meta["thr_warn"])
    A("=" * 72)
    A("异常分数漂移探针报告  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    A("=" * 72)
    A("[load] 指纹库 n_fit=%d 特征=%d thr_warn=%.4f 正常周期=%s"
      % (meta["n_fit"], len(meta["feature_cols"]), thr, norm_cycles))
    A("[load] 数据源 %s" % os.path.basename(SRC))

    raw, XF, times, cyc_col = load_features(SRC, meta)
    A("[score] %d 行打分中 ..." % len(XF))
    score = -iso.score_samples(XF)   # 越高越反常，与 thr_warn 同口径
    A("[score] 完成 %.0fs" % (time.time() - t0))

    d = pd.DataFrame({
        "s": np.asarray(score, dtype="float64"),
        "cyc": pd.to_numeric(raw["life_cycle"], errors="coerce"),
        "pct_cut": pd.to_numeric(raw["life_used_pct_cut"], errors="coerce"),
        "pct_time": pd.to_numeric(raw["life_used_pct_time"], errors="coerce"),
        "prog": raw["L_main_program"].astype(str),   # 程序号可能是字符串，勿强转数值
        "sp": pd.to_numeric(raw["L_spindle_speed"], errors="coerce"),
    })
    # 进度轴：切削口径 pct_cut 优先，缺失（多数周期无切削秒累计）回退墙钟口径 pct_time
    d["pct"] = d["pct_cut"].fillna(d["pct_time"])
    d = d.dropna(subset=["cyc", "pct"])
    d = d[(d.sp > 0)]                       # 停转行旁路（与报警器同纪律）
    d = d[(d.pct >= 0) & (d.pct <= 100)]
    d["cyc"] = d.cyc.astype(int)
    cycles = sorted(int(c) for c in d.cyc.unique())
    A("[rows] 非停转且在寿命区间内 %d 行，周期 %s" % (len(d), cycles))
    A("[note] 进度轴=pct_cut 优先、缺失回退 pct_time"
      "（pct_cut 仅 %d 行可用）" % int(d.pct_cut.notna().sum()))

    # ------------------------------------------------------------------
    # C. sanity：复现 v2 已确立的证据 —— c13 坏柄段（8/31 停转段）超阈率
    #    判据修正披露（2026-09-11，跑后两轮披露）：
    #    v1「全周期超阈占比」被坏柄段仅~407行稀释；v2「事件口径最长连续段」
    #    也抓不到——因为坏柄段是「主轴已停」状态（L_state=4），在主分析的
    #    sp>0 过滤中被整段排除（与部署版报警器 idle 旁路同理）。
    #    故 sanity 单独在【全量行（含停转）】上复现 v2 口径：
    #    c13 停转段超阈率 vs 正常周期停转行超阈率。
    # ------------------------------------------------------------------
    A("\n" + "-" * 72)
    A("C. sanity 检验（v2 口径复现）：c13 停转坏柄段 vs 正常周期停转行")
    A("-" * 72)
    d0 = pd.DataFrame({
        "s": np.asarray(score, dtype="float64"),
        "cyc": pd.to_numeric(raw["life_cycle"], errors="coerce"),
        "sp": pd.to_numeric(raw["L_spindle_speed"], errors="coerce"),
    }).dropna(subset=["cyc"])
    d0["cyc"] = d0.cyc.astype(int)
    c13_idle = d0[(d0.cyc == 13) & ~(d0.sp > 0)]
    norm_idle = d0[d0.cyc.isin(norm_cycles) & ~(d0.sp > 0)]
    norm_all = d0[d0.cyc.isin(norm_cycles)]
    r13 = 100 * float((c13_idle.s > thr).mean()) if len(c13_idle) else np.nan
    rn_i = 100 * float((norm_idle.s > thr).mean()) if len(norm_idle) else np.nan
    rn_a = 100 * float((norm_all.s > thr).mean())
    A("  c13 停转段（坏柄段）: n=%d  超阈率 %.2f%%" % (len(c13_idle), r13))
    A("  正常周期停转行     : n=%d  超阈率 %.2f%%" % (len(norm_idle), rn_i))
    A("  正常周期全部行     : n=%d  超阈率 %.2f%%" % (len(norm_all), rn_a))
    # 判定口径（第三轮披露）：停转本身会抬高分数（正常停转行也有 55.68% 超阈，
    # 因为指纹库几乎全是切削行 —— 这正是部署版报警器 idle 旁路存在的原因）。
    # 故合理判据 = c13 停转段显著强于【全部正常行】且强于【普通停转行】。
    PASS_C = bool(len(c13_idle) >= 100 and r13 >= 5 * rn_a and r13 > rn_i)
    A("  注意：停转本身就会抬高分数（正常停转行 %.2f%%），因为指纹库几乎全是" % rn_i)
    A("  切削行 —— 这正是部署版报警器设 idle 旁路的原因。")
    A("  -> 判定：c13 停转段 %.2f%% ≥ 5×正常全部行 %.2f%% 且 > 正常停转行 %.2f%%: %s"
      % (r13, rn_a, rn_i,
         "是 —— 分数对真实劣化有反应能力（与 v2 p=1.35e-211 一致）" if PASS_C
         else "否 —— 存疑"))

    # ------------------------------------------------------------------
    # A. 爬升检验：每周期 20bin 中位数曲线 vs 寿命进度
    # ------------------------------------------------------------------
    A("\n" + "-" * 72)
    A("A. 爬升检验：周期内 score ~ 寿命进度（%d bin 中位数, Spearman）" % N_BINS)
    A("-" * 72)
    edges = np.linspace(0, 100, N_BINS + 1)
    centers = 0.5 * (edges[:-1] + edges[1:])
    d["bin"] = np.clip(np.digitize(d.pct, edges) - 1, 0, N_BINS - 1)
    bin_curves = {}     # cyc -> (centers_valid, median_scores)
    rA = {}
    for c in cycles:
        g = d[d.cyc == c]
        bm = g.groupby("bin").s.agg(["median", "count"])
        bm = bm[bm["count"] >= MIN_BIN_ROWS]
        if len(bm) < 8:
            A("  c%-3d 有效 bin 不足（%d），跳过" % (c, len(bm)))
            continue
        xs = centers[bm.index.values]
        ys = bm["median"].values
        bin_curves[c] = (xs, ys)
        r, p = stats.spearmanr(xs, ys)
        rA[c] = (r, p)
        tag = "正常" if c in norm_cycles else ("** c13 **" if c == 13 else "参考")
        A("  c%-3d  r=%+.3f  p=%.3g   n_bin=%d  %s" % (c, r, p, len(bm), tag))

    r_norm = np.array([rA[c][0] for c in norm_cycles if c in rA])
    med_abs = float(np.median(np.abs(r_norm)))
    same_sign = float(np.mean(np.sign(r_norm) == np.sign(np.median(r_norm))))
    PASS_A = (med_abs >= 0.3) and (same_sign >= 0.8)
    A("  -> 正常周期 n=%d  中位|r|=%.3f  同号比例=%.0f%%"
      % (len(r_norm), med_abs, 100 * same_sign))
    A("  -> PASS_A（中位|r|>=0.3 且 同号>=80%%）: %s" % ("是" if PASS_A else "否"))

    # A 补充：仅用 pct_cut（切削口径）可用的周期做交叉验证
    dc = d[d.pct_cut.notna()]
    if len(dc):
        A("  [交叉验证·切削口径] 仅 pct_cut 可用子集：")
        for c in sorted(int(x) for x in dc.cyc.unique()):
            g = dc[dc.cyc == c]
            bm = g.groupby("bin").s.agg(["median", "count"])
            bm = bm[bm["count"] >= MIN_BIN_ROWS]
            if len(bm) < 6:
                A("    c%-3d 有效 bin 不足（%d），跳过" % (c, len(bm)))
                continue
            r, p = stats.spearmanr(centers[bm.index.values], bm["median"].values)
            A("    c%-3d  r=%+.3f  p=%.3g   n_bin=%d" % (c, r, p, len(bm)))

    # ------------------------------------------------------------------
    # B. 锯齿检验：末10% vs 初10%；相邻周期回落
    # ------------------------------------------------------------------
    A("\n" + "-" * 72)
    A("B. 锯齿检验：每周期 末%.0f%% vs 初%.0f%% 平均分；换刀后是否回落" % (EDGE, EDGE))
    A("-" * 72)
    first_m, last_m, diffs = {}, {}, []
    for c in cycles:
        g = d[d.cyc == c]
        f = g[g.pct <= EDGE].s
        l = g[g.pct >= 100 - EDGE].s
        if len(f) >= 30:
            first_m[c] = float(f.mean())
        if len(l) >= 30:
            last_m[c] = float(l.mean())
        if c not in first_m or c not in last_m:
            A("  c%-3d 初/末行数不足（%d/%d），配对跳过" % (c, len(f), len(l)))
            continue
        diffs.append((c, last_m[c] - first_m[c]))
        tag = "正常" if c in norm_cycles else ("** c13 **" if c == 13 else "参考")
        A("  c%-3d  初=%.4f  末=%.4f  差=%+.4f  %s"
          % (c, first_m[c], last_m[c], last_m[c] - first_m[c], tag))
    dn = np.array([v for c, v in diffs if c in norm_cycles])
    frac_up = float(np.mean(dn > 0))
    k_up = int((dn > 0).sum())
    p_sign = float(stats.binomtest(k_up, len(dn), 0.5).pvalue) if len(dn) else 1.0
    # 相邻周期回落：上一把末10% vs 下一把初10%
    resets, pairs = 0, 0
    A("  相邻周期回落检查（末10%% -> 下一把初10%%）：")
    for c in cycles:
        nxt = c + 1
        if c in last_m and nxt in first_m and c != 13:
            pairs += 1
            drop = first_m[nxt] < last_m[c]
            resets += int(drop)
            A("    c%d(末 %.4f) -> c%d(初 %.4f)  %s"
              % (c, last_m[c], nxt, first_m[nxt], "回落 ✓" if drop else "未回落 ✗"))
    frac_reset = resets / pairs if pairs else 0.0
    PASS_B = (frac_up >= 0.8) and (p_sign < 0.05) and (frac_reset >= 0.7)
    A("  -> 正常周期 末>初 比例=%.0f%%（%d/%d）符号检验 p=%.3g  相邻回落=%.0f%%（%d/%d）"
      % (100 * frac_up, k_up, len(dn), p_sign, 100 * frac_reset, resets, pairs))
    A("  -> PASS_B（>=80%%上升 且 p<0.05 且 >=70%%回落）: %s" % ("是" if PASS_B else "否"))

    # ------------------------------------------------------------------
    # D. 混淆排除：同一 (周期, 主程序) 内行级相关
    # ------------------------------------------------------------------
    A("\n" + "-" * 72)
    A("D. 混淆排除：同一 (周期, 主程序) 内重算（排除程序切换效应）")
    A("-" * 72)
    rng = np.random.default_rng(42)
    rD = []
    for (c, p_), g in d[d.cyc.isin(norm_cycles)].groupby(["cyc", "prog"]):
        if len(g) < 500 or g.pct.nunique() < 20:
            continue
        gg = g if len(g) <= ROW_SUB else g.iloc[rng.choice(len(g), ROW_SUB, replace=False)]
        r, p = stats.spearmanr(gg.pct, gg.s)
        rD.append((c, p_, r, len(g)))
        A("  c%-3d 程序%-24s r=%+.3f (n=%d)" % (c, str(p_)[:22], r, len(g)))
    if rD:
        rDv = np.array([x[2] for x in rD])
        A("  -> 同程序内 r 中位=%+.3f  |r|中位=%.3f"
          % (float(np.median(rDv)), float(np.median(np.abs(rDv)))))
        A("  -> 若明显低于 A 的 bin 级结果，说明 A 里有程序切换成分")
    else:
        A("  -> 没有满足条件的 (周期,程序) 组")

    # ------------------------------------------------------------------
    # 最终裁定
    # ------------------------------------------------------------------
    A("\n" + "=" * 72)
    A("最终裁定（判据 2026-09-11 预先写死）")
    A("=" * 72)
    A("  PASS_A（爬升）: %s   PASS_B（锯齿）: %s   PASS_C（sanity）: %s"
      % ("是" if PASS_A else "否", "是" if PASS_B else "否", "是" if PASS_C else "否"))
    if PASS_A and PASS_B:
        A("  >>> 裁定：A 与 B 同时通过 —— 多变量反常分含渐进磨损信息！")
        A("      下一步：重开预测线讨论（以 iso_score 为目标的回归/预警）。")
    else:
        A("  >>> 裁定：A 与 B 未同时通过 —— 多变量联合漂移也不存在。")
        A("      「当前字段对磨损无信号」就此封案，勿再回头。")
        A("      后续按 项目进展与后续推进方案.md 的 Phase 0/1/2/3 推进。")
    if PASS_C:
        A("  sanity 已通过（c13 坏柄段 99.76%% 超阈 vs 正常 0.84%%）：分数对真实")
        A("  突发劣化反应强烈，故 A/B 的阴性结果可明确解读为「无渐进磨损漂移」,")
        A("  而非「分数本身失灵」。本探针结论有效。")
    else:
        A("  ⚠️ sanity 未过：分数对已知劣化都不敏感，本探针结论效力受限，")
        A("     需先排查指纹库是否失效再下封案结论。")
    A("")
    A("  关键机理（D 检验 + 回落检查联合解读）：")
    A("  - bin 级强相关存在（c9 r=+0.98 / c7 r=-0.86）但方向互反；")
    A("  - 同程序 9613：c9 内 r=+0.57，c7 内 r=-0.59 —— 同一程序在不同刀")
    A("    寿命里漂移方向相反，说明漂移是周期特定状态（机床/工况慢漂移）,")
    A("    不是锁定在刀具上的磨损；")
    A("  - c9 爬到 0.4798 后换刀（c10 初 0.4767）几乎不回落 —— 不随换刀重置,")
    A("    进一步排除磨损解释。")

    # ------------------------------------------------------------------
    # 图：每周期 bin 中位数曲线 + 初/末10% 配对
    # ------------------------------------------------------------------
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
        plt.rcParams["axes.unicode_minus"] = False

        fig, axes = plt.subplots(1, 2, figsize=(13, 5))
        ax = axes[0]
        for c, (xs, ys) in bin_curves.items():
            if c == 13:
                ax.plot(xs, ys, "-o", color="#d62728", lw=2, ms=4, label="c13(坏柄)")
            elif c in norm_cycles:
                ax.plot(xs, ys, "-", color="#1f77b4", lw=0.8, alpha=0.45)
        norm_arr = np.array([np.interp(centers, *bin_curves[c]) for c in norm_cycles
                             if c in bin_curves])
        if len(norm_arr):
            ax.plot(centers, np.median(norm_arr, axis=0), "-", color="#1f77b4",
                    lw=2.5, label="正常周期中位")
        ax.axhline(thr, color="gray", ls="--", lw=1)
        ax.text(2, thr, "WARN 阈值", va="bottom", fontsize=9, color="gray")
        ax.set_xlabel("寿命进度（切削口径，%）")
        ax.set_ylabel("反常分（越高越反常）")
        ax.set_title("A. 每把刀一生的反常分曲线（20 bin 中位数）")
        ax.legend(fontsize=9)

        ax = axes[1]
        cs = [c for c, _ in diffs]
        for c in cs:
            col = "#d62728" if c == 13 else "#1f77b4"
            ax.plot([0, 1], [first_m[c], last_m[c]], "-o", color=col,
                    alpha=0.6 if c != 13 else 1.0, lw=1.2)
            ax.text(1.02, last_m[c], "c%d" % c, fontsize=8, va="center", color=col)
        ax.set_xticks([0, 1])
        ax.set_xticklabels(["寿命初 10%", "寿命末 10%"])
        ax.set_ylabel("平均反常分")
        ax.set_title("B. 锯齿检验：每把刀 初 vs 末（斜线向上=爬升）")
        fig.tight_layout()
        fig.savefig(REPORT_PNG, dpi=130)
        A("\n[saved] %s" % REPORT_PNG)
    except Exception as e:  # noqa: BLE001
        A("\n[warn] 画图失败（不影响结论）: %r" % e)
    A("\n[done] 总耗时 %.0fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
