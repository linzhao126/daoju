# -*- coding: utf-8 -*-
"""
刀具故障确认器 —— 在线打分原型（fit / score 两模式）
================================================================
依据 2026-09-03 三轮实验的最终结论：
  * 寿命预测不可行（无渐进退化信号）；
  * 「刀具已坏还在运转」的突变型异常可被 IsolationForest 检出
    （cycle13 坏柄段 100% 命中，连续约 1 小时）；
  * 正确报警形态 = 连续 K 行超阈 -> 事件报警，不是累计超阈率。

本脚本把结论封装为可复用工具：

  fit 模式（建指纹库，换刀/新工况后重跑）:
    python alarm_online.py fit
      - 用全部正常周期（c3~12,14）fit IsolationForest
      - 阈值沿用 v2 holdout 独立校准: WARN=p99(0.4997) CRIT=p99.9(0.5723)
      - 存 alarm_library.joblib + alarm_library_meta.json

  score 模式（对新 CSV 打分，要求已含滚动特征列）:
    python alarm_online.py score --csv <path> [--out-prefix xxx]
      - 特征管线与训练一致: 泄漏黑名单 -> relatize_position -> 库中位数填充
      - 输出 <prefix>_alarm_scored.csv（逐行 iso_score/over/level）
            <prefix>_alarm_events.json（事件表: 起止/持续/峰值/级别/所在周期）
      - 事件判定: 时间断层>10min 分段；每段开头 3min 瞬态屏蔽；
        连续 >=10 行超阈 WARN 线 -> 事件，峰值>CRIT 线则升级 CRIT
"""
import os
import sys
import json
import time
import argparse
import numpy as np
import pandas as pd

BASE = r"C:/abc/workBoddyHome刀具"
sys.path.insert(0, BASE)

from ts_split import SRC  # noqa: E402  默认自测数据源
from engineering_norm import relatize_position  # noqa: E402
from sklearn.ensemble import IsolationForest  # noqa: E402

BL_PATH = os.path.join(BASE, "leakage_blacklist.json")
LIB_JOB = os.path.join(BASE, "alarm_library.joblib")
LIB_META = os.path.join(BASE, "alarm_library_meta.json")

NORM_CYCLES = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14]
THR_WARN = 0.4997   # v2 holdout p99（独立校准，勿用 fit 内分位替代）
THR_CRIT = 0.5723   # v2 holdout p99.9
K_ROWS = 10         # 连续超阈行数阈值 -> 事件
GAP_MIN = 10        # 时间断层(分钟) -> 分段
WARMUP_MIN = 3      # 每段开头瞬态屏蔽(分钟)


# ---------------------------------------------------------------- fit
def do_fit():
    import joblib
    from fingerprint_alarm import build_all
    X, cyc, rul, dfm_pos, info = build_all()
    cyc_v = cyc.values.astype("float64")
    m_norm = np.isin(cyc_v, NORM_CYCLES)
    fill = X[m_norm].median(numeric_only=True)
    XF = X.fillna(fill).fillna(0.0)
    t0 = time.time()
    iso = IsolationForest(n_estimators=300, max_samples=8192,
                          random_state=42, n_jobs=6)
    iso.fit(XF[m_norm])
    print("fit %d rows x %d cols, %.0fs" % (int(m_norm.sum()), X.shape[1], time.time() - t0))
    meta = dict(
        feature_cols=list(X.columns),
        fill={k: float(v) for k, v in fill.items()},
        thr_warn=THR_WARN, thr_crit=THR_CRIT,
        k_rows=K_ROWS, gap_min=GAP_MIN, warmup_min=WARMUP_MIN,
        n_fit=int(m_norm.sum()), fit_cycles=NORM_CYCLES,
        thr_source="v2 holdout (36,346 rows) p99/p99.9, independent calibration",
        created=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    joblib.dump(iso, LIB_JOB)
    json.dump(meta, open(LIB_META, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("[saved] %s" % LIB_JOB)
    print("[saved] %s" % LIB_META)


# ---------------------------------------------------------------- 特征
def load_features(path, meta):
    df = pd.read_csv(path, low_memory=False)
    df.columns = [c.replace("\ufeff", "") for c in df.columns]
    cols = meta["feature_cols"]
    base = [c for c in cols if not c.endswith("_relpos")]
    missing = [c for c in base if c not in df.columns]
    if missing:
        raise SystemExit(
            "[abort] 缺少 %d 个滚动特征列（请先跑 标注+滚动特征 管线），示例: %s%s"
            % (len(missing), missing[:5], " ..." if len(missing) > 5 else ""))
    X = pd.DataFrame(index=df.index)
    for c in base:
        X[c] = pd.to_numeric(df[c], errors="coerce")
    X = X.astype("float32")
    relatize_position(X)
    X = X.copy()
    X = X.reindex(columns=cols)
    gone = [c for c in cols if c not in X.columns or X[c].isna().all()]
    if gone:
        raise SystemExit("[abort] 工程列生成失败 %d 个: %s" % (len(gone), gone[:5]))
    fill = pd.Series(meta["fill"], dtype="float32")
    XF = X.fillna(fill).fillna(0.0)
    times = None
    if "_time" in df.columns:
        times = pd.to_datetime(df["_time"], errors="coerce")
    cyc_col = None
    if "life_cycle" in df.columns:
        cyc_col = pd.to_numeric(df["life_cycle"], errors="coerce")
    return df, XF, times, cyc_col


# ---------------------------------------------------------------- 事件
def detect_events(score, times, cyc_col, meta, idle=None):
    k = meta.get("k_rows", K_ROWS)
    gap = pd.Timedelta(minutes=meta.get("gap_min", GAP_MIN))
    warm = pd.Timedelta(minutes=meta.get("warmup_min", WARMUP_MIN))
    tw, tc = meta["thr_warn"], meta["thr_crit"]
    n = len(score)
    if times is None:
        times = pd.Series(pd.date_range("2000-01-01", periods=n, freq="s"))
    else:
        times = pd.Series(times).reset_index(drop=True)
    d = pd.DataFrame({"s": np.asarray(score, dtype="float64"), "t": times})
    d["over"] = d.s > tw
    d["lvl"] = np.where(d.s > tc, "CRIT", np.where(d.over, "WARN", "ok"))
    d["seg"] = (d.t.diff() > gap).cumsum()
    seg_start = d.groupby("seg").t.transform("min")
    seg_len = d.groupby("seg").t.transform("max") - seg_start
    warm_eff = (seg_len * 0.3).clip(upper=warm)  # 比例屏蔽：段长的30%，上限3min。
    # 教训：固定3min warmup 会把"比它还短的突发段"整段吞掉（cycle13 的
    # 坏柄段只有 2min15s、407 行，全被屏蔽导致漏报）。
    d["eff"] = d.over & ~((d.t - seg_start) <= warm_eff)
    if idle is not None:
        idle = np.asarray(pd.Series(idle).reset_index(drop=True), dtype=bool)
        d["eff"] = d.eff & ~idle      # 停转/非加工行不参与事件
        d.loc[idle, "lvl"] = "idle"
    d["run"] = (d.eff != d.eff.shift()).cumsum()
    events = []
    cc = None if cyc_col is None else pd.Series(cyc_col).reset_index(drop=True)
    for (seg, run), g in d[d.eff].groupby(["seg", "run"]):
        if len(g) < k:
            continue
        e = dict(
            start=str(g.t.iloc[0]), end=str(g.t.iloc[-1]),
            n_rows=int(len(g)), dur_sec=float((g.t.iloc[-1] - g.t.iloc[0]).total_seconds()),
            peak_score=round(float(g.s.max()), 4),
            mean_score=round(float(g.s.mean()), 4),
            level="CRIT" if (g.s > tc).any() else "WARN",
            row_start=int(g.index[0]), row_end=int(g.index[-1]),
        )
        if cc is not None:
            vals = cc.iloc[g.index].dropna()
            e["life_cycle"] = sorted(set(int(v) for v in vals)) if len(vals) else None
        events.append(e)
    return d, events


# ---------------------------------------------------------------- score
def do_score(csv_path, out_prefix, write_full=True):
    import joblib
    meta = json.load(open(LIB_META, encoding="utf-8"))
    iso = joblib.load(LIB_JOB)
    print("[load] library n_fit=%d cols=%d thr=%.4f/%.4f" %
          (meta["n_fit"], len(meta["feature_cols"]), meta["thr_warn"], meta["thr_crit"]))
    raw, XF, times, cyc_col = load_features(csv_path, meta)
    score = -iso.score_samples(XF)
    idle = None
    if "L_spindle_speed" in raw.columns:
        sp = pd.to_numeric(raw["L_spindle_speed"], errors="coerce")
        idle = ~(sp > 0)   # 停转/缺失(非加工) -> 旁路，不参与事件
        idle = idle.fillna(True)
    d, events = detect_events(score, times, cyc_col, meta, idle=idle)

    out_csv = out_prefix + "_alarm_scored.csv"
    if write_full:
        res = raw.copy()
        res["iso_score"] = np.round(score, 5)
        res["alarm_level"] = d.lvl.values
        res.to_csv(out_csv, index=False, encoding="utf-8-sig")
    else:
        m = d.lvl.values != "ok"
        res = raw[m].copy()
        res["iso_score"] = np.round(score[m], 5)
        res["alarm_level"] = d.lvl.values[m]
        res.to_csv(out_csv, index=False, encoding="utf-8-sig")

    out_ev = out_prefix + "_alarm_events.json"
    json.dump(dict(source=os.path.basename(csv_path), n_rows=int(len(score)),
                   thr_warn=meta["thr_warn"], thr_crit=meta["thr_crit"],
                   k_rows=meta.get("k_rows", K_ROWS), n_events=len(events),
                   events=events),
              open(out_ev, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("\n[scored] %s -> %s (%d rows)" % (os.path.basename(csv_path), out_csv, len(res)))
    print("[events] %d 个（连续>=%d 行超阈）-> %s" % (len(events), meta.get("k_rows", K_ROWS), out_ev))
    if events:
        ev = pd.DataFrame(events)
        print("\n  %-4s %-20s %-20s %-7s %-8s %-9s %-6s %s" %
              ("#", "start", "end", "rows", "dur(min)", "peak", "level", "cycle"))
        for i, e in enumerate(events):
            print("  %-4d %-20s %-20s %-7d %-8.1f %-9.4f %-6s %s" %
                  (i + 1, e["start"][:19], e["end"][:19], e["n_rows"],
                   e["dur_sec"] / 60.0, e["peak_score"], e["level"],
                   e.get("life_cycle")))
        if "life_cycle" in ev.columns:
            by = ev.explode("life_cycle").groupby("life_cycle").size()
            print("\n  按周期事件数: %s" % ", ".join("c%s=%d" % (k_, v) for k_, v in by.items()))
    over_pct = 100 * float(pd.Series(d.lvl.values).isin(["WARN", "CRIT"]).mean())
    print("\n  超阈(WARN线)行占比: %.2f%%" % over_pct)
    print("[saved] %s" % out_ev)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["fit", "score"])
    ap.add_argument("--csv", default=SRC, help="score 模式的输入 CSV（默认自测=训练源）")
    ap.add_argument("--out-prefix", default=None)
    ap.add_argument("--no-full", action="store_true", help="只写超阈行，减小输出体积")
    a = ap.parse_args()
    if a.mode == "fit":
        do_fit()
    else:
        prefix = a.out_prefix or os.path.splitext(a.csv)[0]
        do_score(a.csv, prefix, write_full=not a.no_full)


if __name__ == "__main__":
    main()
