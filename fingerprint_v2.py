# -*- coding: utf-8 -*-
"""
工况指纹报警 v2：包络完备后的复检（最终判定实验）
================================================================
上一轮教训（fingerprint_alarm.py）：用 c3~8 当包络时，"陌生正常周期"
（c10/c14/c15）会系统性误报（超阈 24~36%），cycle13 的 22.1% 因此无法
解读 —— 包络不完备，判别无从谈起。

本轮修正该缺陷：包络覆盖【全部正常周期 c3~12,14】，并用行级 70/30
留出获得公平基线：

  fit      = 正常库 70% 行（覆盖所有周期的工况指纹）
  baseline = 正常库其余 30% 行  -> 超阈率 ≈ 1%（按 p99 定义自洽）
  test     = cycle13（刀柄坏，1877 行）+ cycle15（开放式，164 行）

与上一轮的本质区别：基线与被测对象来自**同一批工况**（所有周期都有
fit 行与留出行），"认周期"效应被消除，剩下的差异只能来自行本身的异常。

【判定（只有两种结局）】
  1) cycle13 超阈率显著高于基线（>=3 倍且 >=5%）-> 报警器可判别刀具异常
  2) cycle13 超阈率 ≈ 基线 -> 刀柄坏在这些传感器上无可分异象，
     出路 C 也无法检测刀具异常，项目在现有数据上的建模空间完全关闭。
"""
import os
import sys
import json
import time
import numpy as np
import pandas as pd

BASE = r"C:/abc/workBoddyHome刀具"
sys.path.insert(0, BASE)

from fingerprint_alarm import build_all  # noqa: E402
from sklearn.ensemble import IsolationForest  # noqa: E402

OUT_TXT = os.path.join(BASE, "工况指纹报警v2_包络完备复检.txt")
OUT_JSON = os.path.join(BASE, "fingerprint_v2_metrics.json")
OUT_BAR = os.path.join(BASE, "fingerprint_v2_exceed_bar.png")
OUT_TS = os.path.join(BASE, "fingerprint_v2_cycle13_ts.png")

NORM_CYCLES = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14]


def main():
    L = []
    A = L.append
    A("=" * 78)
    A("工况指纹报警 v2 —— 包络完备（全部正常周期）后的 cycle13 判定复检")
    A("=" * 78)
    A("生成时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    A("")

    X, cyc, rul, dfm_pos, info = build_all()
    cyc_v = cyc.values.astype("float64")
    m_norm = np.isin(cyc_v, NORM_CYCLES)
    m13 = cyc_v == 13
    m15 = cyc_v == 15

    A("【0. 数据】")
    A("  全量 %d 行 | 正常库 %d 行（周期 %s）| cycle13 %d 行 | cycle15 %d 行" %
      (len(X), int(m_norm.sum()), NORM_CYCLES, int(m13.sum()), int(m15.sum())))
    A("")

    rng = np.random.RandomState(42)
    idx_norm = np.where(m_norm)[0]
    perm = rng.permutation(len(idx_norm))
    n_fit = int(0.7 * len(idx_norm))
    fit_rows = idx_norm[perm[:n_fit]]
    hold_rows = idx_norm[perm[n_fit:]]
    A("【1. 行级 70/30 拆分（正常库内部）】")
    A("  fit %d 行 / holdout %d 行 —— 每个周期的工况在两侧都有覆盖" % (len(fit_rows), len(hold_rows)))
    chk = pd.Series(cyc_v[hold_rows]).value_counts().sort_index()
    A("  holdout 各周期行数: %s" % ", ".join("c%d=%d" % (int(k), v) for k, v in chk.items()))
    A("")

    fill = X.iloc[fit_rows].median()
    XF = X.fillna(fill).fillna(0.0)
    t0 = time.time()
    iso = IsolationForest(n_estimators=300, max_samples=8192, random_state=42, n_jobs=6)
    iso.fit(XF.iloc[fit_rows])
    iso_score = -iso.score_samples(XF)
    A("【2. 评分（IsolationForest 300 树，仅 iso；MAD 距离上轮无效应，本轮省略）】")
    A("  fit+score 用时 %.0fs" % (time.time() - t0))
    A("")

    thr99 = float(np.quantile(iso_score[hold_rows], 0.99))
    thr999 = float(np.quantile(iso_score[hold_rows], 0.999))
    A("【3. 基线校准（holdout 正常行）】")
    A("  score p50=%.4f p90=%.4f p99=%.4f p99.9=%.4f" % (
        np.quantile(iso_score[hold_rows], 0.5), np.quantile(iso_score[hold_rows], 0.9),
        thr99, thr999))
    hold_ex = float((iso_score[hold_rows] > thr99).mean())
    A("  holdout 超阈率（应≈1%%）: %.2f%%" % (100 * hold_ex))
    A("")

    A("【4. 各周期超阈率（正常周期只用其 holdout 行 —— 公平口径）】")
    A("  %-8s %-10s %-12s %-12s" % ("周期", "n(评估行)", "超阈%", "中位分"))
    rows = []
    for c in NORM_CYCLES:
        m = (cyc_v == c) & np.isin(np.arange(len(cyc_v)), hold_rows)
        si = iso_score[m]
        rows.append(dict(cycle=c, n=int(m.sum()), ex=float((si > thr99).mean()),
                         med=float(np.median(si))))
        A("  %-8d %-10d %-12.2f %-12.4f" %
          (c, rows[-1]["n"], 100 * rows[-1]["ex"], rows[-1]["med"]))
    for tag, m in (("13", m13), ("15", m15)):
        si = iso_score[m]
        rows.append(dict(cycle=int(tag), n=int(m.sum()), ex=float((si > thr99).mean()),
                         med=float(np.median(si))))
        A("  %-8s %-10d %-12.2f %-12.4f   <- 全行评估" %
          (tag, rows[-1]["n"], 100 * rows[-1]["ex"], rows[-1]["med"]))
    r13 = next(r for r in rows if r["cycle"] == 13)
    r15 = next(r for r in rows if r["cycle"] == 15)
    A("")

    try:
        from scipy.stats import mannwhitneyu
        u = mannwhitneyu(iso_score[m13], iso_score[hold_rows], alternative="two-sided")
        A("【5. 显著性与效应量（cycle13 vs holdout 正常行）】")
        A("  MWU p=%.3e | 中位分比 = %.2f 倍 | 超阈率比 = %.1f 倍" %
          (u.pvalue, r13["med"] / float(np.median(iso_score[hold_rows])),
           r13["ex"] / hold_ex))
        A("  （大样本 p 必显著，判定只看效应量与特异性）")
        p13 = float(u.pvalue)
    except Exception as e:
        p13 = None
        A("  (scipy 跳过: %s)" % e)
    A("")

    A("【6. 最终判定】")
    ratio = r13["ex"] / hold_ex
    worst_norm = max((r for r in rows if r["cycle"] in NORM_CYCLES), key=lambda r: r["ex"])
    A("  基线超阈率 %.2f%% | cycle13 %.2f%%（%.1f 倍）| cycle15 %.2f%% | 正常周期最大 c%d=%.2f%%" %
      (100 * hold_ex, 100 * r13["ex"], ratio, 100 * r15["ex"],
       worst_norm["cycle"], 100 * worst_norm["ex"]))
    if ratio >= 3 and r13["ex"] >= 0.05:
        A("  -> 判别成立：包络完备后 cycle13 仍显著高于全部正常周期。")
        A("     报警器可检测刀具异常：监控 iso_score 超 holdout-p99（%.4f）触发检查。" % thr99)
        A("     部署形态 = 全正常工况指纹库 + 本评分，换刀后需增量更新指纹库。")
        verdict = "alarm_ok"
    else:
        A("  -> 判别不成立：包络完备后 cycle13（%.2f%%）与正常周期（最大 c%d=%.2f%%）" %
          (100 * r13["ex"], worst_norm["cycle"], 100 * worst_norm["ex"]))
        A("     无法区分，且接近基线 %.2f%% —— 刀柄坏在 316 维传感器特征上" % (100 * hold_ex))
        A("     没有可分的异象。出路 C 检测刀具异常的能力被最终排除；")
        A("     它只能当「工况迁移检测器」（上一轮结论）。")
        verdict = "alarm_no"
    A("")

    res = dict(thr99=thr99, thr999=thr999, hold_ex=hold_ex, cycles=rows,
               c13=dict(ex=r13["ex"], med=r13["med"], ratio=ratio, p=p13),
               c15=dict(ex=r15["ex"], med=r15["med"]), verdict=verdict)
    open(OUT_TXT, "w", encoding="utf-8").write("\n".join(L))
    json.dump(res, open(OUT_JSON, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=float)

    plots(iso_score, cyc_v, rul.values, info["rec_h"], hold_rows, m13, thr99)
    print("\n".join(L))
    print("\n[saved] %s" % OUT_TXT)


def plots(iso_score, cyc_v, rul, rec_h, hold_rows, m13, thr99):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        NORM_CYCLES = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14]
        labs, vals, cols = [], [], []
        hold_set = np.zeros(len(cyc_v), dtype=bool)
        hold_set[hold_rows] = True
        for c in NORM_CYCLES:
            m = (cyc_v == c) & hold_set
            labs.append(str(c)); vals.append(100 * (iso_score[m] > thr99).mean())
            cols.append("#378ADD")
        for tag, m in (("13", cyc_v == 13), ("15", cyc_v == 15)):
            labs.append(tag); vals.append(100 * (iso_score[m] > thr99).mean())
            cols.append("#A32D2D" if tag == "13" else "#BA7517")
        fig, ax = plt.subplots(figsize=(13, 5))
        ax.bar(range(len(labs)), vals, color=cols, alpha=0.85)
        ax.axhline(1.0, color="#5F5E5A", ls="--", lw=1.2, label="baseline 1% (p99 definition)")
        for i, v in enumerate(vals):
            ax.text(i, v + 0.15, "%.1f" % v, ha="center", fontsize=9)
        ax.set_xticks(range(len(labs)))
        ax.set_xticklabels(labs)
        ax.set_xlabel("life cycle   (13 = broken holder, 15 = open-ended)")
        ax.set_ylabel("exceed rate over p99 (%)")
        ax.set_title("Envelope-complete retest: exceed rate per cycle\n"
                     "(normal cycles scored on holdout rows only)")
        ax.legend(fontsize=9)
        ax.grid(alpha=0.25, axis="y")
        fig.tight_layout()
        fig.savefig(OUT_BAR, dpi=110)
        plt.close(fig)

        rec_row = np.array([rec_h.get(int(c), np.nan) if np.isfinite(c) else np.nan
                            for c in cyc_v])
        epct = 100.0 * (1.0 - rul / rec_row)
        norm_m = np.isin(cyc_v, NORM_CYCLES) & hold_set
        b = np.clip(np.round(epct / 2) * 2, 0, 100)
        dfb = pd.DataFrame({"b": b[norm_m], "s": iso_score[norm_m]}).dropna()
        g = dfb.groupby("b")["s"]
        fig, ax = plt.subplots(figsize=(13, 5))
        ax.fill_between(g.median().index, g.quantile(0.25).values, g.quantile(0.75).values,
                        color="#378ADD", alpha=0.25, label="normal holdout p25-p75")
        ax.plot(g.median().index, g.median().values, color="#185FA5", lw=1.5,
                label="normal median")
        d13 = pd.DataFrame({"b": b[m13], "s": iso_score[m13]}).dropna()
        g13 = d13.groupby("b")["s"]
        ax.plot(g13.median().index, g13.median().values, color="#A32D2D", lw=2,
                label="cycle13 median (broken holder)")
        ax.axhline(thr99, color="#A32D2D", ls="--", lw=1, label="alarm line p99")
        ax.set_xlabel("elapsed % of cycle life")
        ax.set_ylabel("iso anomaly score")
        ax.set_title("cycle13 vs normal envelope (envelope complete, fair baseline)")
        ax.legend(fontsize=9)
        ax.grid(alpha=0.25)
        fig.tight_layout()
        fig.savefig(OUT_TS, dpi=110)
        plt.close(fig)
    except Exception as e:
        print("[warn] plot failed: %s" % e)


if __name__ == "__main__":
    main()
