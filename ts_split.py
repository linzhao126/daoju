# -*- coding: utf-8 -*-
"""
T2525 刀具寿命 RUL 回归 —— 时间序列切分模块
================================================================
核心原则
    1. 切分单元 = 一把刀的完整生命周期(life_cycle)，**不是行**。
       同一周期内相邻采样行高度相关，按行切 = 让模型"背答案"。
    2. 特征全部是 trailing 滑窗（只看过去 N 分钟），所以：
       - 训练集(早) 不会看到 测试集(晚) 的数据 -> 天然安全
       - 但 LOCO 场景下，测试周期"之后"的那些周期，其开头 10min 的
         滑窗会回头读到测试周期的数据 -> 必须 purge
    3. 边界 embargo：切分点两侧各丢弃 EMBARGO_MIN 分钟的行。

用法（在建模脚本里直接 import）:
    from ts_split import load_split
    idx = load_split(scheme="block")      # block / loco / walkforward
    train_idx, val_idx, test_idx = idx["block"]["train"], ...

命令行（生成报告 + 索引文件）:
    python ts_split.py
"""

import os
import json
import numpy as np
import pandas as pd

SRC = r"C:/abc/workBoddyHome刀具/merged_csv_20260901_160345_T2525_life_labeled_rollingfeat.csv"
OUT_TXT = r"C:/abc/workBoddyHome刀具/时间序列切分方案.txt"
OUT_JSON = r"C:/abc/workBoddyHome刀具/ts_split_indices.json"

EMBARGO_MIN = 10          # == 最大滑窗窗口(10min)，边界净化宽度
EXCLUDE_OPEN_CYCLE = True  # 剔除"进行中/开放式"周期(标签是占位值)


# ----------------------------------------------------------------------
# 载入（只读需要的列，避免读 208MB 全量）
# ----------------------------------------------------------------------
def load_meta(src=SRC):
    cols = [c.replace("\ufeff", "") for c in pd.read_csv(src, nrows=1).columns]
    need = ["_time", "life_cycle", "rul_hour", "life_used_pct_cut",
            "real_failure", "in_life_range"]
    use = [c for c in need if c in cols]
    df = pd.read_csv(src, usecols=use, low_memory=False)
    df.columns = [c.replace("\ufeff", "") for c in df.columns]
    df["_t"] = pd.to_datetime(df["_time"], errors="coerce")
    df["life_cycle"] = pd.to_numeric(df["life_cycle"], errors="coerce").astype("Int64")
    df = df.sort_values("_t").reset_index(drop=True)
    return df


def cycle_table(df):
    rows = []
    for c, sub in df.groupby("life_cycle", dropna=True):
        rows.append(dict(
            cycle=int(c),
            n=int(len(sub)),
            start=sub["_t"].min(),
            end=sub["_t"].max(),
            hours=round((sub["_t"].max() - sub["_t"].min()).total_seconds() / 3600.0, 2),
            rul_min=float(sub["rul_hour"].min()) if "rul_hour" in sub else np.nan,
            rul_max=float(sub["rul_hour"].max()) if "rul_hour" in sub else np.nan,
            real_failure=(str(sub["real_failure"].iloc[0])
                          if "real_failure" in sub.columns else ""),
        ))
    t = pd.DataFrame(rows).sort_values("cycle").reset_index(drop=True)
    t["cum_n"] = t["n"].cumsum()

    # 记录表给出的周期总寿命 ~= 该周期第一行的 rul_hour（距报废的小时数）
    t["record_h"] = t["rul_max"]
    # 数据实际覆盖的寿命比例
    t["obs_ratio"] = (t["hours"] / t["record_h"]).round(3)
    # 是否观测到寿命终点：绝对 1h 内 或 相对总寿命 8% 内
    # （短周期如 cycle5 总寿命仅 1.3h，用纯相对 5% 阈值会误判，故取混合判据）
    tol = np.maximum(1.0, 0.08 * t["record_h"].fillna(0))
    t["reaches_end"] = t["rul_min"].fillna(9e9) <= tol
    t["usable_as_test"] = t["reaches_end"] & (t["n"] >= 1000)
    return t


# ----------------------------------------------------------------------
# purge：删掉"紧随测试周期之后"的那些训练周期的开头窗口行
# ----------------------------------------------------------------------
def purge_after_test(df, ct, test_cycles, gap_min=EMBARGO_MIN):
    """
    返回被净化的行索引集合。
    对每个测试周期 c：找时间上紧接其后的周期 c2（若 c2 不在测试集），
    删除 c2 开头 gap_min 分钟内的行 —— 这些行的 10min trailing 滑窗
    会回头读到测试周期 c 的数据。
    """
    gap = pd.Timedelta(minutes=gap_min)
    tc = set(test_cycles)
    purge_idx = set()
    ends = dict(zip(ct["cycle"], ct["end"]))
    for c in tc:
        later = [x for x in ct["cycle"] if x not in tc and ends[x] >= ends[c]]
        if not later:
            continue
        c2 = min(later, key=lambda x: ends[x])
        start2 = df.loc[df["life_cycle"] == c2, "_t"].min()
        mask = (df["life_cycle"] == c2) & (df["_t"] <= start2 + gap)
        purge_idx |= set(df.index[mask])
    return purge_idx


def purge_between(df, ct, setA, setB, gap_min=EMBARGO_MIN):
    """
    A 在时间上整体早于 B 时，删掉 B 开头 gap_min 分钟的行（保守净化）。
    """
    gap = pd.Timedelta(minutes=gap_min)
    if not setA or not setB:
        return set()
    endA = df.loc[df["life_cycle"].isin(setA), "_t"].max()
    startB = df.loc[df["life_cycle"].isin(setB), "_t"].min()
    if startB <= endA:
        return set()
    mask = (df["life_cycle"].isin(setB)) & (df["_t"] <= startB + gap)
    return set(df.index[mask])


# ----------------------------------------------------------------------
# 三种切分方案
# ----------------------------------------------------------------------
def split_block(df, ct, cut1, cut2):
    """按周期顺序三段切：train=cycle<cut1, val=cut1..cut2-1, test>=cut2"""
    cyc = list(ct["cycle"])
    tr = [c for c in cyc if c < cut1]
    va = [c for c in cyc if cut1 <= c < cut2]
    te = [c for c in cyc if c >= cut2]
    pi = purge_after_test(df, ct, te)
    pi |= purge_between(df, ct, tr, va)
    pi |= purge_between(df, ct, va, te)
    return _pack(df, tr, va, te, pi)


def split_loco(df, ct):
    """Leave-One-Cycle-Out：每个周期轮流做测试，其余训练。
       返回 dict: cycle -> {'train':..., 'val':..., 'test':...}
       val 取测试周期的前一个周期（若无则取后一个）。"""
    cyc = list(ct["cycle"])
    out = {}
    for i, c in enumerate(cyc):
        te = [c]
        va = [cyc[i - 1]] if i > 0 else [cyc[i + 1]] if len(cyc) > 1 else []
        tr = [x for x in cyc if x not in te and x not in va]
        pi = purge_after_test(df, ct, te)
        pi |= purge_between(df, ct, tr, va)
        pi |= purge_between(df, ct, va, te)
        out[int(c)] = _pack(df, tr, va, te, pi)
    return out


def split_walkforward(df, ct, min_train=3):
    """Walk-forward(expanding)：前 k 个周期训练，第 k+1 个测试。"""
    cyc = list(ct["cycle"])
    out = {}
    for k in range(min_train, len(cyc)):
        tr = cyc[:k]
        te = [cyc[k]]
        va = [cyc[k - 1]]
        tr = [x for x in tr if x not in va]
        pi = purge_after_test(df, ct, te)
        pi |= purge_between(df, ct, tr, va)
        pi |= purge_between(df, ct, va, te)
        out[int(cyc[k])] = _pack(df, tr, va, te, pi)
    return out


def _pack(df, tr_c, va_c, te_c, purge_idx):
    def idx(cycles):
        m = df["life_cycle"].isin(cycles)
        return np.array(sorted(set(df.index[m]) - set(purge_idx)), dtype=np.int64)
    return {
        "train_cycles": [int(x) for x in tr_c],
        "val_cycles": [int(x) for x in va_c],
        "test_cycles": [int(x) for x in te_c],
        "train": idx(tr_c), "val": idx(va_c), "test": idx(te_c),
        "purged_rows": len(purge_idx),
    }


# ----------------------------------------------------------------------
def load_split(scheme="block", src=SRC, **kw):
    """
    对外接口。返回结构：
      scheme='block'       -> {'train': idx, 'val': idx, 'test': idx,
                               'train_cycles': [...], ...}
      scheme='loco'        -> {cycle: {'train': idx, 'val': idx, 'test': idx, ...}}
      scheme='walkforward' -> {cycle: {'train': idx, 'val': idx, 'test': idx, ...}}
    其中 idx 为 np.int64 行索引数组（可直接 df.iloc[idx]）。
    """
    d = json.load(open(OUT_JSON, encoding="utf-8"))
    if scheme not in d["schemes"]:
        raise ValueError("scheme must be one of %s" % list(d["schemes"]))
    raw = d["schemes"][scheme]

    def conv(v):
        if isinstance(v, list) and len(v) and all(isinstance(x, int) for x in v):
            return np.array(v, dtype=np.int64)   # 行索引 -> ndarray
        return v

    def conv_dict(dd):
        return {k: conv(v) for k, v in dd.items()}

    if scheme in ("loco", "walkforward"):
        return {int(k) if k.isdigit() else k: conv_dict(v) for k, v in raw.items()}
    return conv_dict(raw)


# ----------------------------------------------------------------------
def main():
    df = load_meta()
    n_all = len(df)
    ct_all = cycle_table(df)

    # 建模用周期：剔除开放式(进行中) + 非真实失效
    drop_cycles = []
    last = ct_all.iloc[-1]
    if EXCLUDE_OPEN_CYCLE:
        drop_cycles.append(int(last["cycle"]))
    bad_fail = [int(r["cycle"]) for _, r in ct_all.iterrows()
                if str(r["real_failure"]).strip() == "否"]
    drop_cycles += bad_fail
    drop_cycles = sorted(set(drop_cycles))

    dfm = df[~df["life_cycle"].isin(drop_cycles)].reset_index(drop=True)
    ct = cycle_table(dfm)
    cyc = list(ct["cycle"])

    L = []
    A = L.append
    A("=" * 78)
    A("T2525 刀具寿命 RUL 回归 —— 时间序列切分方案")
    A("=" * 78)
    A("")
    A("【0. 为什么不能按行随机切 / 按行顺序切】")
    A("  - 123,192 行是 13 个寿命周期的密集采样，同一周期内相邻行几乎重复。")
    A("  - 若按行切，同一个周期会被切成两半：训练段末尾 与 测试段开头 是同一把刀")
    A("    相隔几秒的采样行，模型只要'记住'这把刀的负载水平就能答对 -> 假高分。")
    A("  - 所以：**切分单元必须是一把刀的完整生命周期(life_cycle)**。")
    A("")
    A("【1. 数据总体】")
    A("  总行数 %d，被排除周期 %s" % (n_all, drop_cycles if drop_cycles else "无"))
    A("  参与建模 %d 行 / %d 个周期" % (len(dfm), len(cyc)))
    A("")
    A("【2. 各周期明细（切分的最小单元）】")
    A("  %-5s %-8s %-8s %-8s %-8s %-7s %-7s %-7s %-6s %s" %
      ("周期", "行数", "数据跨度h", "周期总寿命h", "覆盖%", "rul_min", "rul_max",
       "到终点", "可测", "真实失效"))
    for _, r in ct.iterrows():
        A("  %-5d %-8d %-8.1f %-8.1f %-8.1f %-7.2f %-7.1f %-7s %-6s %s" %
          (r["cycle"], r["n"], r["hours"], r["record_h"], 100 * r["obs_ratio"],
           r["rul_min"], r["rul_max"],
           "是" if r["reaches_end"] else "否",
           "是" if r["usable_as_test"] else "否",
           r["real_failure"]))
    A("")
    half = ct[~ct["reaches_end"]]
    if len(half):
        A("  !! 半截周期警示: %s" % ", ".join(
            "cycle%d(rul最低%.1fh, 只覆盖前%.0f%%)" % (r["cycle"], r["rul_min"], 100 * r["obs_ratio"])
            for _, r in half.iterrows()))
        A("     这些周期的数据从未走到寿命终点 -> 放测试集无法检验'末期预测能力'。")
        A("     放训练集无害(标签仍是真实的), 但会让 train 的 rul 分布整体上移。")
    A("")

    # -------- 方案 A：候选三段切 --------
    A("【3. 方案 A：按周期顺序三段切（推荐主方案）】")
    A("  含义：用'较早换掉的刀'训练，预测'较晚换掉的刀' = 真实上线场景。")
    A("")
    A("  候选切割点枚举（cut1/train|val 边界，cut2/val|test 边界；'可测'=test 中")
    A("  覆盖到寿命终点且 >=1000 行的周期数，为 0 说明该切法测不出末期能力）")
    A("  %-10s %-8s %-8s %-8s %-8s %-8s %-8s %s" %
      ("cut1,cut2", "train周期", "val周期", "test周期", "train%", "val%", "test%", "可测"))
    cands = []
    n_tot = len(dfm)
    usable = set(int(x) for x in ct.loc[ct["usable_as_test"], "cycle"])
    for i in range(1, len(cyc) - 1):
        for j in range(i + 1, len(cyc)):
            c1, c2 = cyc[i], cyc[j]
            tr = [c for c in cyc if c < c1]
            va = [c for c in cyc if c1 <= c < c2]
            te = [c for c in cyc if c >= c2]
            nt = int(ct.loc[ct["cycle"].isin(tr), "n"].sum())
            nv = int(ct.loc[ct["cycle"].isin(va), "n"].sum())
            ne = int(ct.loc[ct["cycle"].isin(te), "n"].sum())
            n_ute = sum(1 for c in te if c in usable)
            cands.append(dict(c1=c1, c2=c2, tr=tr, va=va, te=te,
                              nt=nt, nv=nv, ne=ne, n_ute=n_ute,
                              ptr=nt / n_tot, pva=nv / n_tot, pte=ne / n_tot))
            A("  %-10s %-8d %-8d %-8d %-8.1f %-8.1f %-8.1f %d" %
              ("%d,%d" % (c1, c2), len(tr), len(va), len(te),
               100 * nt / n_tot, 100 * nv / n_tot, 100 * ne / n_tot, n_ute))
    A("")

    # 自动挑最佳切割
    # 第1优先：test 全部由"可测"周期组成(不含半截周期) 且 比例合理
    # 第2优先：test 至少含 1 个"可测"周期 且 比例合理
    def ratio_ok(c):
        return (0.15 <= c["pte"] <= 0.32 and 0.10 <= c["pva"] <= 0.28
                and c["ptr"] >= 0.50)
    strict = [c for c in cands if c["n_ute"] == len(c["te"]) and ratio_ok(c)]
    loose = [c for c in cands if c["n_ute"] >= 1 and ratio_ok(c)]
    anyok = [c for c in cands if c["n_ute"] >= 1]
    for pool in (strict, loose, anyok, cands):
        if pool:
            best = max(pool, key=lambda c: c["ne"])
            break
    A("  筛选口径：优先选 test 全部为'可测'完整周期的切法（避免把半截周期混入")
    A("  测试集导致指标虚高/外推），其次才放宽。")
    A("")
    cut1, cut2 = best["c1"], best["c2"]
    A("  -> 自动推荐 cut=(%d,%d)：train=%s, val=%s, test=%s" %
      (cut1, cut2, best["tr"], best["va"], best["te"]))
    A("     (train %.1f%% / val %.1f%% / test %.1f%%，test 含 %d 个'可测'完整周期)" %
      (100 * best["ptr"], 100 * best["pva"], 100 * best["pte"], best["n_ute"]))
    A("")

    block = split_block(dfm, ct, cut1, cut2)
    A("  推荐切割的实际结果：")
    for part in ["train", "val", "test"]:
        ii = block[part]
        sub = dfm.loc[ii]
        A("    %-6s 周期=%-16s 行数=%-8d 时间 %s ~ %s  rul范围 %.1f ~ %.1f" %
          (part, str(block[part + "_cycles"]), len(ii),
           str(sub["_t"].min())[:19], str(sub["_t"].max())[:19],
           sub["rul_hour"].min(), sub["rul_hour"].max()))
    A("    边界净化丢弃行数: %d (切分点两侧各 %d 分钟)" % (block["purged_rows"], EMBARGO_MIN))
    A("")

    # -------- 3.5 分布对齐 --------
    A("【3.5 目标值(rul_hour) 分布对齐检查 —— 切分成立与否的硬指标】")
    A("  若 test 的 rul 分布落在 train 范围之外，模型就是在做外推，指标必然崩。")
    A("")
    A("  %-8s %-9s %-9s %-9s %-9s %-9s %-9s %-9s" %
      ("集合", "行数", "min", "p25", "中位", "p75", "p90", "max"))
    q = {}
    for part in ["train", "val", "test"]:
        v = dfm.loc[block[part], "rul_hour"].dropna()
        q[part] = (v.min(), v.max())
        A("  %-8s %-9d %-9.2f %-9.2f %-9.2f %-9.2f %-9.2f %-9.2f" %
          (part, len(v), v.min(), v.quantile(.25), v.median(),
           v.quantile(.75), v.quantile(.90), v.max()))
    A("")
    tr_lo, tr_hi = q["train"]
    te_lo, te_hi = q["test"]
    # 容差：train 跨度的 1% 或 0.5h（避免 2 分钟级别的边界差异误报外推）
    tol = max(0.5, 0.01 * (tr_hi - tr_lo))
    if te_lo < tr_lo - tol or te_hi > tr_hi + tol:
        A("  !! 外推风险: test 的 rul 范围 [%.1f, %.1f] 超出 train [%.1f, %.1f]" %
          (te_lo, te_hi, tr_lo, tr_hi))
        A("     超出区间 %.1f%% 的 test 样本处在训练未见过的寿命段。" %
          (100 * ((dfm.loc[block["test"], "rul_hour"] > tr_hi).mean()
                  + (dfm.loc[block["test"], "rul_hour"] < tr_lo).mean())))
    else:
        A("  OK: test 的 rul 范围 [%.1f, %.1f] 完全落在 train [%.1f, %.1f] 之内，" %
          (te_lo, te_hi, tr_lo, tr_hi))
        A("     属于内插预测，指标可信。")
    A("")

    # -------- 方案 B：LOCO --------
    loco = split_loco(dfm, ct)
    A("【4. 方案 B：Leave-One-Cycle-Out (LOCO) 交叉验证 —— 样本少时最稳】")
    A("  含义：每次留 1 个完整周期做测试、其余训练（val 取相邻周期）。")
    A("  用途：拿到'每一把刀'的泛化误差，看模型是不是只在某几把刀上好看。")
    A("")
    A("  %-6s %-9s %-9s %-9s %-9s %s" %
      ("测试周期", "train行", "val行", "test行", "purge", "train周期范围"))
    for c in cyc:
        r = loco[c]
        A("  %-6d %-9d %-9d %-9d %-9d %d~%d" %
          (c, len(r["train"]), len(r["val"]), len(r["test"]), r["purged_rows"],
           min(r["train_cycles"]), max(r["train_cycles"])))
    A("")
    A("  -> 建议：主报告用 LOCO 的'每周期 MAE/R² 均值±标准差'，")
    A("     而不是单一 split 的一个数字 —— 这 %d 个周期差异很大，单点结果不可信。" % len(cyc))
    A("     注意 cycle10/11 是半截周期(rul 未走到 0)，它们的 R² 天然偏低，")
    A("     统计时应单独标注，别让它拉低对模型的整体判断。")
    A("")

    # -------- 方案 C：walk-forward --------
    wf = split_walkforward(dfm, ct, min_train=3)
    A("【5. 方案 C：Walk-Forward (滚动外推) —— 最贴近在线部署】")
    A("  含义：只用'当前时间点之前'的刀训练，预测下一把刀；逐步向前滚。")
    A("")
    A("  %-10s %-10s %-10s %-10s %s" %
      ("折(测试周期)", "train行", "val行", "test行", "train周期"))
    for c, r in wf.items():
        A("  %-10d %-10d %-10d %-10d %s" %
          (c, len(r["train"]), len(r["val"]), len(r["test"]),
           str(r["train_cycles"])))
    A("")
    A("  与方案 A 的区别：A 只有一次外推；C 有 %d 次，能看'随着历史数据累积，" % len(wf))
    A("  误差是否下降'——这条曲线能判断你还需不需要继续攒数据。")
    A("")

    # -------- 目标列建议 --------
    A("=" * 78)
    A("【6. 切分之后的 3 个实操坑（决定指标是否可信）】")
    A("=" * 78)
    A("")
    _a = ct.loc[ct["record_h"].idxmin()]
    _b = ct.loc[ct["record_h"].idxmax()]
    A("  坑1: rul_hour 量纲在不同周期差异巨大（见第2节 rul_max）。")
    A("       最短 cycle%d 总寿命仅 %.1fh，最长 cycle%d 有 %.1fh（相差 %.0f 倍）" %
      (_a["cycle"], _a["record_h"], _b["cycle"], _b["record_h"],
       _b["record_h"] / max(_a["record_h"], 1e-9)))
    A("       -> 同样 MAE=2h，对 cycle%d 是灾难、对 cycle%d 是优秀。" %
      (_a["cycle"], _b["cycle"]))
    A("       建议：主目标改用 剩余寿命百分比 remain_pct = 100 - life_used_pct_cut，")
    A("             或 rul_hour 做 per-cycle 归一化后再评估。")
    A("")
    A("  坑2: 样本不独立 -> 逐行 R² 会虚高。")
    A("       正确做法：按周期分别算 MAE/R²，再对 13 个周期取均值±std；")
    A("       不要只报一个全局行数加权的数字（会被最大周期主导）。")
    A("")
    A("  坑3: 类别/目标泄漏字段没剔干净时，切分做得再对也是白搭。")
    A("       务必先套用 leakage_blacklist.json 的 hard_drop/gray_drop。")
    A("")
    A("=" * 78)
    A("【7. 直接可用的代码】")
    A("=" * 78)
    A("")
    A("  import sys; sys.path.insert(0, r'%s')" % os.path.dirname(os.path.abspath(__file__)))
    A("  from ts_split import load_split")
    A("  import pandas as pd, json")
    A("")
    A("  SRC = r'%s'" % SRC)
    A("  bl  = json.load(open(r'C:/abc/workBoddyHome刀具/leakage_blacklist.json', encoding='utf-8'))")
    A("  df  = pd.read_csv(SRC, low_memory=False)")
    A("  drop= bl['hard_drop'] + bl['gray_drop'] + bl['constant_drop']")
    A("  X   = df[[c for c in df.columns if c not in drop]]")
    A("  y   = df['rul_hour']        # 或 100 - df['life_used_pct_cut']")
    A("")
    A("  sp  = load_split('block')            # 'block' / 'loco' / 'walkforward'")
    A("  Xtr, ytr = X.iloc[sp['train']], y.iloc[sp['train']]")
    A("  Xva, yva = X.iloc[sp['val']],   y.iloc[sp['val']]")
    A("  Xte, yte = X.iloc[sp['test']],  y.iloc[sp['test']]")
    A("")
    A("  # LOCO 时:")
    A("  for cyc, s in load_split('loco').items():")
    A("      ... X.iloc[s['train']] ... X.iloc[s['test']]")
    A("")

    txt = "\n".join(L)
    open(OUT_TXT, "w", encoding="utf-8").write(txt)

    def pack(o):
        if isinstance(o, dict):
            return {str(k): pack(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [pack(v) for v in o]
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return None if np.isnan(o) else float(o)
        if isinstance(o, (pd.Timestamp,)):
            return str(o)
        return o

    json.dump({
        "source_file": SRC,
        "embargo_minutes": EMBARGO_MIN,
        "excluded_cycles": drop_cycles,
        "n_rows_modeling": int(len(dfm)),
        "cycles": pack(ct.to_dict("records")),
        "recommended_block_cut": [int(cut1), int(cut2)],
        "usable_as_test_cycles": sorted(int(x) for x in usable),
        "schemes": {
            "block": pack(block),
            "loco": pack(loco),
            "walkforward": pack(wf),
        },
    }, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False)

    print(txt)
    print("\n[saved] %s" % OUT_TXT)
    print("[saved] %s" % OUT_JSON)


if __name__ == "__main__":
    main()
