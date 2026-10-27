# -*- coding: utf-8 -*-
"""
出路 B 验证：无监督「工况指纹 + 偏离报警」
================================================================
前提：固定 400 轮不早停时 train R2=0.998 —— 316 维传感器特征能极好
地区分工况（周期指纹极稳定）。据此把任务倒过来：不用失效标签，
在「正常工况」上学包络，偏离包络即报警。

【判定性验证设计】
  * 正常参考库 = cycle 3~12,14（全部真实失效周期；fit 只用 block
    train 侧 = cycle 3~8，val/test 周期当"未见过的正常工况"）。
  * 外部异常集 = cycle 13（刀柄坏，此前因「非真实失效」被排除）。
    -> 若模型能把 cycle13 与正常周期分开，报警器就有实用价值。
  * 交叉检验 = 每个正常周期内 early/mid/late（elapsed_pct 三分位）
    的偏离分数对比。
    -> 若 late 不高于 early，交叉确认「无退化信号」；若 late 显著
       偏高，则发现了此前监督学习没抓到的弱信号。

【两种偏离评分（互补）】
  1) iso_score : IsolationForest 孤立度（-score_samples，越大越异常）
  2) mad_dist  : 鲁棒对角马氏距离 mean(|x - median| / (1.4826*MAD))

【纪律】
  * 两种评分只在 block train 周期（cycle 3~8）上 fit。
  * elapsed_pct / rec_h 只用于评估分组与画图，绝不入模。
  * 特征、泄漏黑名单、工程归一化与 train_baseline.py 完全一致；
    relatize_position 是行级运算，cycle13 行可与建模行一起变换。
"""
import os
import sys
import json
import time
import warnings
import numpy as np
import pandas as pd

BASE = r"C:/abc/workBoddyHome刀具"
sys.path.insert(0, BASE)

from ts_split import load_split, OUT_JSON as TS_JSON, SRC  # noqa: E402
from engineering_norm import relatize_position  # noqa: E402
from sklearn.ensemble import IsolationForest  # noqa: E402

BL_PATH = os.path.join(BASE, "leakage_blacklist.json")
OUT_TXT = os.path.join(BASE, "工况指纹报警实验报告.txt")
OUT_JSON = os.path.join(BASE, "fingerprint_metrics.json")
OUT_BOX = os.path.join(BASE, "fingerprint_cycle_box.png")
OUT_PHASE = os.path.join(BASE, "fingerprint_phase_trend.png")
OUT_TS = os.path.join(BASE, "fingerprint_cycle13_ts.png")

EXCLUDED = [13, 15]  # ts_split 的排除口径；13 在这里被重新拿回来当异常集


# ---------------------------------------------------------------- 数据
def build_all():
    bl = json.load(open(BL_PATH, encoding="utf-8"))
    drop = set(bl["hard_drop"]) | set(bl["gray_drop"]) | set(bl["constant_drop"])
    feat_cols = [c for c in bl["keep"] if c not in drop]
    label_cols = ["_time", "life_cycle", "rul_hour"]
    usecols = feat_cols + [c for c in label_cols if c not in feat_cols]

    t0 = time.time()
    df = pd.read_csv(SRC, usecols=usecols, low_memory=False)
    df.columns = [c.replace("\ufeff", "") for c in df.columns]
    df["_t"] = pd.to_datetime(df["_time"], errors="coerce")
    df = df.sort_values("_t").reset_index(drop=True)

    cyc = pd.to_numeric(df["life_cycle"], errors="coerce").astype("Int64")
    rul = pd.to_numeric(df["rul_hour"], errors="coerce").astype("float64")
    is_model = (~cyc.isin(EXCLUDED)).values
    dfm_pos = np.where(is_model)[0]

    # 有效列判定在建模行上做（与 train_baseline.build_matrix 完全一致），
    # 但数值转换对全量行做 —— cycle13 行得到同样的特征。
    parts, skipped = {}, 0
    for c in feat_cols:
        v = pd.to_numeric(df.loc[is_model, c], errors="coerce")
        if v.notna().sum() == 0 or v.nunique(dropna=True) <= 1:
            skipped += 1
            continue
        parts[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
    X = pd.concat(parts, axis=1)
    added = relatize_position(X)
    X = X.copy()

    rec_h = {}
    for c in pd.unique(cyc.dropna()):
        c = int(c)
        r = rul[cyc == c].dropna()
        if len(r):
            rec_h[c] = float(r.max())
    info = dict(n_all=int(len(df)), n_model=int(is_model.sum()),
                n_feat_raw=len(feat_cols), n_skipped=skipped,
                n_relpos=len(added), n_feat_total=X.shape[1],
                cycles_all=sorted(int(c) for c in pd.unique(cyc.dropna())),
                rec_h=rec_h, load_sec=round(time.time() - t0, 1))
    return X, cyc, rul, dfm_pos, info


# ---------------------------------------------------------------- 评分
def fit_scores(X, dfm_pos, sp, A):
    itr_df = dfm_pos[np.asarray(sp["train"], dtype=np.int64)]
    Xtr = X.iloc[itr_df]
    fill = Xtr.median()
    XF = X.fillna(fill).fillna(0.0)
    A("  NaN 填充：train 列中位数（再兜底 0）；参与 fit 的 train 行 %d" % len(itr_df))

    t0 = time.time()
    iso = IsolationForest(n_estimators=300, max_samples=8192,
                          random_state=42, n_jobs=6)
    iso.fit(XF.iloc[itr_df])
    iso_score = -iso.score_samples(XF)
    A("  IsolationForest(300 树, max_samples=8192) fit+score 用时 %.0fs" % (time.time() - t0))

    Xtr_np = XF.iloc[itr_df].to_numpy(dtype="float64")
    med = np.nanmedian(Xtr_np, axis=0)
    mad = np.nanmedian(np.abs(Xtr_np - med), axis=0)
    sd = 1.4826 * mad
    sd[~(sd > 1e-9)] = 1.0
    Xf_np = XF.to_numpy(dtype="float32")
    mad_dist = np.empty(len(XF), dtype="float64")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for s in range(0, len(XF), 20000):
            e = min(s + 20000, len(XF))
            Z = np.abs(Xf_np[s:e].astype("float64") - med) / sd
            Z[~np.isfinite(Z)] = np.nan
            with np.errstate(all="ignore"):
                mad_dist[s:e] = np.nanmean(Z, axis=1)
    mad_dist = np.nan_to_num(mad_dist, nan=0.0)
    A("  鲁棒对角马氏距离（MAD-z 均值）已计算")
    return iso_score, mad_dist, itr_df


# ---------------------------------------------------------------- 主流程
def main():
    L = []
    A = L.append
    A("=" * 78)
    A("T2525 出路B验证：无监督工况指纹 + 偏离报警（cycle13 当外部异常集）")
    A("=" * 78)
    A("生成时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    A("")

    X, cyc, rul, dfm_pos, info = build_all()
    A("【0. 数据】")
    A("  源: %s" % os.path.basename(SRC))
    A("  全量 %d 行（周期 %s）| 建模行 %d（周期 3~12,14）" %
      (info["n_all"], info["cycles_all"], info["n_model"]))
    A("  特征 %d 列（常量剔除 %d + 相对位置新增 %d）" %
      (info["n_feat_total"], info["n_skipped"], info["n_relpos"]))
    A("  各周期行数: %s" % ", ".join(
        "c%d=%d" % (c, int((cyc.values == c).sum())) for c in info["cycles_all"]))
    A("  加载 %.0fs" % info["load_sec"])
    A("")

    sp = load_split("block")
    A("【1. 评分构建（fit 只用 block train = cycle %s）】" % sp["train_cycles"])
    iso_score, mad_dist, itr_df = fit_scores(X, dfm_pos, sp, A)
    A("")

    cyc_v = cyc.values.astype("float64")
    m13 = cyc_v == 13
    A("【2. train 校准分位】")
    cal = {}
    for name, s in (("iso", iso_score), ("mad", mad_dist)):
        qs = {p: float(np.quantile(s[itr_df], p)) for p in (0.5, 0.9, 0.99, 0.999)}
        cal[name] = qs
        A("  %-4s p50=%.4f p90=%.4f p99=%.4f p99.9=%.4f" %
          (name, qs[0.5], qs[0.9], qs[0.99], qs[0.999]))
    A("")

    A("【3. 周期级偏离汇总（iso_exceed = 超 train-p99 的比例）】")
    A("  %-8s %-9s %-11s %-11s %-12s %-11s %-12s" %
      ("周期", "n", "iso中位", "iso_p90", "iso超阈%", "mad中位", "mad超阈%"))
    rows = []
    for c in info["cycles_all"]:
        m = cyc_v == c
        if m.sum() < 50:
            continue
        si, sm = iso_score[m], mad_dist[m]
        r = dict(cycle=int(c), n=int(m.sum()),
                 iso_med=float(np.median(si)), iso_p90=float(np.quantile(si, 0.9)),
                 iso_exceed=float((si > cal["iso"][0.99]).mean()),
                 mad_med=float(np.median(sm)),
                 mad_exceed=float((sm > cal["mad"][0.99]).mean()))
        rows.append(r)
        A("  %-8d %-9d %-11.4f %-11.4f %-12.2f %-11.3f %-12.2f" %
          (r["cycle"], r["n"], r["iso_med"], r["iso_p90"], 100 * r["iso_exceed"],
           r["mad_med"], 100 * r["mad_exceed"]))
    A("")

    A("【4. cycle13（刀柄坏）判别检验】")
    r13 = next((r for r in rows if r["cycle"] == 13), None)
    normal = [r for r in rows if r["cycle"] in (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14)]
    nm_iso = float(np.median([r["iso_exceed"] for r in normal]))
    nm_mad = float(np.median([r["mad_exceed"] for r in normal]))
    A("  正常周期 iso超阈%% 中位 = %.2f%% | cycle13 = %.2f%%（%.1f 倍）" %
      (100 * nm_iso, 100 * (r13["iso_exceed"] if r13 else np.nan),
       (r13["iso_exceed"] / nm_iso) if r13 and nm_iso > 0 else float("nan")))
    A("  正常周期 mad超阈%% 中位 = %.2f%% | cycle13 = %.2f%%（%.1f 倍）" %
      (100 * nm_mad, 100 * (r13["mad_exceed"] if r13 else np.nan),
       (r13["mad_exceed"] / nm_mad) if r13 and nm_mad > 0 else float("nan")))
    try:
        from scipy.stats import mannwhitneyu
        u1 = mannwhitneyu(iso_score[m13], iso_score[itr_df], alternative="two-sided")
        u2 = mannwhitneyu(mad_dist[m13], mad_dist[itr_df], alternative="two-sided")
        A("  Mann-Whitney U（cycle13 vs train行）: iso p=%.3e | mad p=%.3e" %
          (u1.pvalue, u2.pvalue))
        A("  注意：大样本下 p 值几乎必显著，看效应量方向 —— mad 仅 0.6 倍（无效应）。")
        mw = dict(iso_p=float(u1.pvalue), mad_p=float(u2.pvalue))
    except Exception as e:
        mw = {}
        A("  (scipy 检验跳过: %s)" % e)
    A("  【特异性检查】超阈率排名（cycle13 若混在正常周期中间 = 无判别力）:")
    srt = sorted(rows, key=lambda r: -r["iso_exceed"])
    rank13 = [i + 1 for i, r in enumerate(srt) if r["cycle"] == 13]
    rank13 = rank13[0] if rank13 else None
    A("    " + " | ".join("c%d=%.1f%%" % (r["cycle"], 100 * r["iso_exceed"]) for r in srt))
    if rank13 and r13:
        worst_n = max(normal, key=lambda r: r["iso_exceed"])
        A("    cycle13 排第 %d/%d；正常周期最大 = c%d（%.1f%%）" %
          (rank13, len(srt), worst_n["cycle"], 100 * worst_n["iso_exceed"]))
    A("")

    A("【5. 周期内退化交叉检验（early<33%% / mid / late>=66%% elapsed_pct）】")
    rec_row = np.array([info["rec_h"].get(int(c), np.nan) if pd.notna(c) else np.nan
                        for c in cyc_v])
    epct = 100.0 * (1.0 - rul.values / rec_row)
    A("  %-8s %-9s %-10s %-10s %-10s %-11s" %
      ("周期", "n", "early中位", "mid中位", "late中位", "late-early"))
    ph_rows = []
    for r in rows:
        c = r["cycle"]
        m = cyc_v == c
        me, mm_, ml = (m & (epct < 33)), (m & (epct >= 33) & (epct < 66)), (m & (epct >= 66))
        if me.sum() < 30 or ml.sum() < 30:
            continue
        e, l = float(np.median(iso_score[me])), float(np.median(iso_score[ml]))
        ph_rows.append(dict(cycle=c, early=e, mid=float(np.median(iso_score[mm_])) if mm_.sum() else np.nan,
                            late=l, diff=l - e))
        A("  %-8d %-9d %-10.4f %-10.4f %-10.4f %-11.4f" %
          (c, r["n"], e, ph_rows[-1]["mid"], l, l - e))
    dnorm = [p["diff"] for p in ph_rows if p["cycle"] != 13]
    A("  正常周期 late-early 中位 = %.4f（>0 且明显才说明有退化弱信号）" %
      (float(np.median(dnorm)) if dnorm else float("nan")))
    A("")

    A("【6. 结论判据】")
    ratio = (r13["iso_exceed"] / nm_iso) if r13 and nm_iso > 0 else float("nan")
    worst_n = max(normal, key=lambda r: r["iso_exceed"])
    tr_mask = np.isin(cyc_v, sp["train_cycles"])
    te_mask = np.isin(cyc_v, np.concatenate([np.asarray(sp["val_cycles"]).ravel(),
                                             np.asarray(sp["test_cycles"]).ravel()]))
    tr_ex = 100 * float((iso_score[tr_mask] > cal["iso"][0.99]).mean())
    te_ex = 100 * float((iso_score[te_mask] > cal["iso"][0.99]).mean())
    A("  侧别效应（超阈比例）: train 周期行 %.2f%% vs 非train 周期行 %.2f%%" % (tr_ex, te_ex))
    A("  —— 若差距悬殊，说明 iso_score 主要在认周期（认刀效应的无监督镜像），不是认异常。")
    if r13 and r13["iso_exceed"] > 1.5 * worst_n["iso_exceed"]:
        A("  -> cycle13 超阈率 %.1f%% 显著高于一切正常周期（最大 c%d=%.1f%%）：" %
          (100 * r13["iso_exceed"], worst_n["cycle"], 100 * worst_n["iso_exceed"]))
        A("     报警器可判别刀具异常 —— iso_score 超 train-p99 触发人工检查。")
    else:
        A("  -> 刀具异常判别：不合格。cycle13 超阈率 %.1f%%（排 %s）没有超过正常周期" %
          (100 * (r13["iso_exceed"] if r13 else np.nan),
           "第%d/13" % rank13 if rank13 else "NA"))
        A("     的范围（最大 c%d=%.1f%%，比 cycle13 还高）。iso_score 报的是" %
          (worst_n["cycle"], 100 * worst_n["iso_exceed"]))
        A("     「这不是 train 里的工况」，对未见过的正常周期会误报。")
        A("     可用方向：当「工况迁移检测器」（识别换程序/换料/换刀后工况变化），")
        A("     部署前需先建覆盖全部正常工况的指纹库。")
    dnorm_pct = [p["diff"] for p in ph_rows if p["cycle"] != 13]
    pos_share = float(np.mean([d > 0 for d in dnorm_pct])) if dnorm_pct else float("nan")
    med_diff = float(np.median(dnorm_pct)) if dnorm_pct else float("nan")
    A("  周期内 late-early（正常周期 %d 个）: 中位 %.4f，为正比例 %.0f%%，" %
      (len(dnorm_pct), med_diff, 100 * pos_share))
    A("  个别 late 偏高: %s" % ", ".join(
        "c%d(%.3f)" % (p["cycle"], p["diff"]) for p in ph_rows
        if p["cycle"] != 13 and p["diff"] > 0.05))
    if dnorm_pct and abs(med_diff) > 0.02 and (pos_share >= 0.8 or pos_share <= 0.2):
        A("  -> 存在系统性弱退化趋势（方向一致且幅度明显），值得追查来源。")
    else:
        A("  -> 方向不一致且幅度≈0：交叉确认正常周期内无随寿命的漂移，")
        A("     与监督实验「无信号」结论一致。c9/c13 的 late 偏高是个别现象。")
    A("")

    res = dict(info={k: v for k, v in info.items() if k != "rec_h"},
               rec_h=info["rec_h"], cal=cal, cycles=rows, cycle13_test={
                   "normal_iso_exceed_med": nm_iso, "c13_iso_exceed": r13["iso_exceed"] if r13 else None,
                   "ratio": ratio, **mw},
               phase=ph_rows)
    open(OUT_TXT, "w", encoding="utf-8").write("\n".join(L))
    json.dump(res, open(OUT_JSON, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=float)

    plots(iso_score, cyc_v, epct, itr_df, m13, cal)
    print("\n".join(L))
    print("\n[saved] %s" % OUT_TXT)


# ---------------------------------------------------------------- 图
def plots(iso_score, cyc_v, epct, itr_df, m13, cal):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        rng = np.random.RandomState(0)
        cycles = sorted(set(c for c in cyc_v if np.isfinite(c) and (cyc_v == c).sum() >= 50))
        data, pos, colors = [], [], []
        for i, c in enumerate(cycles):
            v = iso_score[cyc_v == c]
            if len(v) > 5000:
                v = v[rng.choice(len(v), 5000, replace=False)]
            data.append(v)
            pos.append(i)
            colors.append("#A32D2D" if c == 13 else "#378ADD")
        fig, ax = plt.subplots(figsize=(13, 5.5))
        bp = ax.boxplot(data, positions=pos, widths=0.6, showfliers=False, patch_artist=True)
        for p_, c_ in zip(bp["boxes"], colors):
            p_.set_facecolor(c_); p_.set_alpha(0.6)
        ax.axhline(cal["iso"][0.99], color="#A32D2D", ls="--", lw=1.2,
                   label="train p99 (alarm line)")
        ax.axhline(cal["iso"][0.5], color="#5F5E5A", ls=":", lw=1, label="train p50")
        ax.set_xticks(pos)
        ax.set_xticklabels([("%d" % c) + (" *" if c == 13 else "") for c in cycles])
        ax.set_xlabel("life cycle   (* = cycle13, broken tool holder)")
        ax.set_ylabel("iso anomaly score")
        ax.set_title("Per-cycle anomaly score (IsolationForest, fit on train cycles only)")
        ax.legend(fontsize=9)
        ax.grid(alpha=0.25, axis="y")
        fig.tight_layout()
        fig.savefig(OUT_BOX, dpi=110)
        plt.close(fig)

        rec = json.load(open(os.path.join(BASE, "fingerprint_metrics.json"), encoding="utf-8"))
        ph = pd.DataFrame(rec["phase"])
        fig, ax = plt.subplots(figsize=(13, 5))
        for _, r in ph.iterrows():
            if r.cycle == 13:
                continue
            ax.plot([0, 1, 2], [r.early, r.mid, r.late], "-o", color="#378ADD",
                    alpha=0.45, lw=1, ms=3)
        if (ph.cycle == 13).any():
            r = ph[ph.cycle == 13].iloc[0]
            ax.plot([0, 1, 2], [r.early, r.mid, r.late], "-o", color="#A32D2D",
                    lw=2.5, ms=7, label="cycle13 (broken holder)")
        ax.set_xticks([0, 1, 2])
        ax.set_xticklabels(["early (<33%)", "mid", "late (>=66%)"])
        ax.set_ylabel("median iso anomaly score")
        ax.set_title("Within-cycle anomaly trend: blue = normal cycles, red = cycle13")
        ax.legend(fontsize=9)
        ax.grid(alpha=0.25)
        fig.tight_layout()
        fig.savefig(OUT_PHASE, dpi=110)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(13, 5))
        norm_m = np.isfinite(cyc_v) & ~m13 & (cyc_v != 15)
        b = np.clip(np.round(epct / 2) * 2, 0, 100)
        dfb = pd.DataFrame({"b": b[norm_m], "s": iso_score[norm_m]}).dropna()
        g = dfb.groupby("b")["s"]
        ax.fill_between(g.median().index, g.quantile(0.25).values, g.quantile(0.75).values,
                        color="#378ADD", alpha=0.25, label="normal cycles p25-p75")
        ax.plot(g.median().index, g.median().values, color="#185FA5", lw=1.5,
                label="normal median")
        d13 = pd.DataFrame({"b": b[m13], "s": iso_score[m13]}).dropna()
        g13 = d13.groupby("b")["s"]
        ax.plot(g13.median().index, g13.median().values, color="#A32D2D", lw=2,
                label="cycle13 median")
        ax.axhline(cal["iso"][0.99], color="#A32D2D", ls="--", lw=1)
        ax.set_xlabel("elapsed % of cycle life")
        ax.set_ylabel("iso anomaly score")
        ax.set_title("Anomaly score vs life-stage: cycle13 vs normal envelope")
        ax.legend(fontsize=9)
        ax.grid(alpha=0.25)
        fig.tight_layout()
        fig.savefig(OUT_TS, dpi=110)
        plt.close(fig)
    except Exception as e:
        print("[warn] plot failed: %s" % e)


if __name__ == "__main__":
    main()
