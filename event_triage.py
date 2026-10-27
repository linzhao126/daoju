# -*- coding: utf-8 -*-
"""
对 selftest_alarm 事件表里的 3 个正常周期小事件（c6/c7/c14）做定性分析。
事件不是用泄漏特征解释，而是看：
  1) 时间上下文: 事件前是否有数据断层(停机/开机)
  2) 行间隔模式: 是否高频突发写入(数据伪影嫌疑)
  3) 解释列(不入模, 仅用于分析): L_state / L_spindle_speed / 程序号 等
  4) Top 偏差特征: 事件行 vs 本周期正常行 的 |median差|/IQR
"""
import sys
import json
import joblib
import numpy as np
import pandas as pd

BASE = r"C:/abc/workBoddyHome刀具"
sys.path.insert(0, BASE)
from alarm_online import load_features, LIB_META  # noqa: E402
from ts_split import SRC  # noqa: E402

EXPL = ["L_state", "L_main_program", "L_sub_program", "L_spindle_speed",
        "L_spindle_load", "L_spindle_override", "L_feed_override",
        "L_X_servo_load", "L_Y_servo_load", "L_Z_servo_load",
        "L_tool_offset", "L_wear_x", "L_wear_z"]


def main():
    meta = json.load(open(LIB_META, encoding="utf-8"))
    evs = json.load(open(BASE + "/selftest_alarm_alarm_events.json",
                         encoding="utf-8"))["events"]
    iso = joblib.load(BASE + "/alarm_library.joblib")
    raw, XF, times, cyc = load_features(SRC, meta)
    s = -iso.score_samples(XF)
    times = pd.Series(times).reset_index(drop=True)

    L = ["=" * 78, "正常周期小事件定性分析（c6/c7/c14）", "=" * 78, ""]
    for i, e in enumerate(evs):
        r0, r1 = e["row_start"], e["row_end"]
        cid = (e.get("life_cycle") or [None])[0]
        L.append("【事件#%d】%s ~ %s | %d 行 | peak=%.4f | cycle=%s" %
                 (i + 1, e["start"][:19], e["end"][:19], e["n_rows"],
                  e["peak_score"], cid))
        gap1 = (times.iloc[r0] - times.iloc[r0 - 1]).total_seconds()
        gap2 = (times.iloc[min(r1 + 1, len(times) - 1)] - times.iloc[r1]).total_seconds()
        L.append("  时间上下文: 事件前 gap=%.0fs (%.1f min) | 事件后 gap=%.0fs%s" %
                 (gap1, gap1 / 60.0, gap2,
                  "  <- 断层后开机段" if gap1 > 60 else ""))
        dts = (times.iloc[r0:r1 + 1].diff().dt.total_seconds()).dropna()
        L.append("  行间隔: 中位=%.3fs p90=%.3fs%s" %
                 (dts.median(), dts.quantile(0.9),
                  "  <- 高频突发写入" if dts.median() < 0.2 else ""))
        m_cyc = (cyc.values == cid) & (s < meta["thr_warn"])
        nidx = np.where(m_cyc)[0]
        med = XF.iloc[nidx].median()
        q1 = XF.iloc[nidx].quantile(0.25)
        q3 = XF.iloc[nidx].quantile(0.75)
        iqr = (q3 - q1).replace(0, np.nan)
        dev = ((XF.iloc[r0:r1 + 1].median() - med).abs() / iqr).sort_values(ascending=False)
        L.append("  Top10 偏差特征 (事件行中位 vs 本周期正常行中位, 单位=IQR):")
        for f, v in dev.head(10).items():
            L.append("    %-38s %6.2f  (正常中位=%.3f, 事件行=%.3f)" %
                     (f, v, med[f], XF.iloc[r0:r1 + 1][f].median()))
        L.append("  解释列 (事件行 vs 本周期正常行):")
        for c in EXPL:
            if c not in raw.columns:
                continue
            ev_v = raw[c].iloc[r0:r1 + 1]
            nm_v = raw[c].iloc[nidx]
            ev_m = ev_v.mode(dropna=True)
            nm_m = nm_v.mode(dropna=True)
            ev_v_num = pd.to_numeric(ev_v, errors="coerce")
            nm_v_num = pd.to_numeric(nm_v, errors="coerce")
            if ev_v_num.notna().sum() and nm_v_num.notna().sum():
                L.append("    %-22s 事件行中位=%-10.3f 正常行中位=%-10.3f" %
                         (c, ev_v_num.median(), nm_v_num.median()))
            elif len(ev_m) and len(nm_m):
                L.append("    %-22s 事件行众数=%-12s 正常行众数=%-12s" %
                         (c, str(ev_m.iloc[0])[:12], str(nm_m.iloc[0])[:12]))
        L.append("")

    open(BASE + "/alarm_events_定性分析.txt", "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L))
    print("\n[saved] alarm_events_定性分析.txt")


if __name__ == "__main__":
    main()
