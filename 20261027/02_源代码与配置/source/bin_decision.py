#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
2 档换刀决策 —— 唯一入口脚本
================================================================
规则来源: 同目录《二档换刀决策方案.md》(2026-10-16)
数据基础: out_T1111/xalign_HI.csv (相位对齐后的逐件 HI, 由 t1111_pipeline.py xalign 产出)

本脚本只做"决策层":
    - 输入: 上游已算好的逐件 HI 表 (不重算 xalign, 不碰特征工程)
    - 输出: 每把刀的换刀件号 / 各策略的成本对比 / 参数稳健性

三个子命令:
    eval   离线回测: 留一刀口径, 对比现状/固定件数/纯计数/纯信号/两级报警 的成本
    plan   执行表  : 输出每把刀的 基准点/硬上限/信号触发点/最终决策
    scan   稳健性  : 参数网格 + 嵌套交叉验证, 检查参数是否过拟合

用法示例:
    python bin_decision.py eval
    python bin_decision.py plan --base 0.4 --extend 0.9 --tau 0.01 --rho 10
    python bin_decision.py scan
"""

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- 默认参数
HERE = Path(__file__).resolve().parent
DEFAULT_CSV = HERE.parent / "out_T1111" / "xalign_HI.csv"
DEFAULT_OUT = HERE / "out"

DEF = dict(
    csv=str(DEFAULT_CSV),
    col="HI_L_spindle_load",   # 指标列 (相位对齐后的主轴负载比)
    base=0.40,                 # 基准点: base * M0 件时触发检查
    extend=0.90,               # 硬上限: 判健康则延长到 extend * M0 件
    tau=0.01,                  # 信号阈值: HI 相对新刀基线上升 >= tau 判"正常磨损"
    K=20,                      # 新刀基线窗口: 该周期前 K 件的中位
    W=10,                      # 信号平滑窗口: 滑动 W 件的中位
    rho=10.0,                  # 一次失效的损失折合几把刀
    min_cycles=3,              # 该程序至少要有几把刀才参与(否则无法留一刀)
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------- 数据层
def load_hi(csv_path, col, min_cycles):
    """读逐件 HI 表, 过滤掉周期数不足的程序 (无法算留一刀 M0)"""
    df = pd.read_csv(csv_path)
    need = {"life_cycle", "prog", "piece_idx", col}
    miss = need - set(df.columns)
    if miss:
        raise SystemExit("[错误] CSV 缺少列: %s\n可用列: %s" % (miss, list(df.columns)))
    df = df.sort_values(["prog", "life_cycle", "piece_idx"]).reset_index(drop=True)
    cnt = df.groupby("prog").life_cycle.nunique()
    keep = cnt[cnt >= min_cycles].index.tolist()
    drop = sorted(set(df.prog.unique()) - set(keep))
    if drop:
        print("[提示] 程序 %s 周期数 < %d, 已排除 (无法留一刀算 M0)" % (drop, min_cycles))
    return df[df.prog.isin(keep)].copy()


def build_knives(df, col, K, W):
    """把逐件序列汇总成'一把刀'一条记录, 并算出信号触发件号

    返回字段:
        prog, cyc, n(寿命件数), M0(同程序其余刀寿命中位), ratio(n/M0),
        sig_k(信号首次达 tau 的件号, 1-based; 从未达阈则 NaN)
    """
    life = df.groupby(["prog", "life_cycle"]).size().rename("n").reset_index()
    rows = []
    for (prog, cyc), g in df.groupby(["prog", "life_cycle"]):
        g = g.sort_values("piece_idx")
        y = g[col].values.astype(float)
        n = len(y)
        if n < max(K, W) + 5:
            continue
        others = life[(life.prog == prog) & (life.life_cycle != cyc)].n.values
        M0 = float(np.median(others)) if len(others) else float(n)
        base = np.nanmedian(y[:K])
        rel = y / base - 1.0 if base != 0 else np.zeros(n)
        wmed = pd.Series(rel).rolling(W, min_periods=W).median().values
        rows.append(dict(prog=int(prog), cyc=int(cyc), n=int(n), M0=M0,
                         ratio=n / M0, rel=rel, wmed=wmed, start=int(max(K, W))))
    return pd.DataFrame(rows)


def sig_trigger(rec, tau):
    """信号首次达到 tau 的件号 (1-based); 从未达阈返回 NaN

    起点 = max(K, W): 前 K 件被用作新刀基线, 前 W-1 件滑动窗口中位为 NaN,
    这段区间的数据不能拿来判"是否已上升", 否则会自我指涉
    """
    w = rec["wmed"]
    start = int(rec["start"])
    idx = np.where((np.arange(len(w)) >= start) & (w >= tau))[0]
    if len(idx) == 0:
        return np.nan
    return float(idx[0] + 1)


# ---------------------------------------------------------------- 决策层
def decision_rows(I, base, extend, tau):
    """两级报警: 基准点触发 -> 信号已超阈则延长到硬上限, 否则立即换"""
    ks, dec, base_k, hard_k, sig_k = [], [], [], [], []
    for _, r in I.iterrows():
        b = int(round(r.M0 * base))
        h = int(round(r.M0 * extend))
        s = sig_trigger(r, tau)
        # 判"健康" = 在基准点之前信号就已经正常上升 -> 走正常磨损路径 -> 给延长许可
        healthy = (s == s) and (s <= b)
        k = h if healthy else b
        ks.append(float(k))
        dec.append("延长" if healthy else "基准换")
        base_k.append(b); hard_k.append(h); sig_k.append(s)
    out = I[["prog", "cyc", "n", "M0", "ratio"]].copy()
    out["基准点"] = base_k
    out["硬上限"] = hard_k
    out["信号@"] = sig_k
    out["决策"] = dec
    out["换刀件号"] = ks
    return out


def cost_of(ks, ns, rho):
    """全局口径成本 = (刀数 + 失效数*rho) / 实际加工总件数

    注意: 必须用全局口径, 单刀平均会因 Jensen 不等式偏大 (10-16 踩过)
    """
    ks = np.asarray(ks, dtype=float)
    ns = np.asarray(ns, dtype=float)
    fail = (ks >= ns) | np.isnan(ks)          # 换刀件号 >= 寿命 => 报废前没换 => 失效
    used = np.where(fail, ns, np.minimum(ks, ns))
    cost = (len(ns) + fail.sum() * rho) / used.sum()
    return cost, int(fail.sum()), used.sum()


# ---------------------------------------------------------------- 策略集合
def strategies(I, base, extend, tau):
    """返回 [(策略名, 换刀件号数组)] —— 覆盖方案文档里的全部对照路线"""
    n = len(I)
    ns = I.n.values
    out = [("现状: 用到失效才换", np.full(n, np.inf))]
    for N in (50, 70, 100):
        out.append(("固定 %d 件换刀" % N, np.full(n, float(N))))
    for f in (0.4, 0.5, 0.7, 0.8):
        out.append(("纯计数 %d%%M0" % round(f * 100), (I.M0 * f).values))
    for t in (0.01, 0.02, 0.03):
        sig = np.array([sig_trigger(r, t) for _, r in I.iterrows()], dtype=float)
        sig = np.where(np.isnan(sig), np.inf, sig)
        out.append(("纯信号 tau=%d%%" % round(t * 100), sig))
    D = decision_rows(I, base, extend, tau)
    out.append(("两级 %.0f%%->%.0f%% tau=%.0f%% (推荐)"
                % (base * 100, extend * 100, tau * 100), D["换刀件号"].values))
    return out


def ks_two(I, base, extend, tau):
    return decision_rows(I, base, extend, tau)["换刀件号"].values


# ---------------------------------------------------------------- 子命令
def cmd_eval(a):
    df = load_hi(a.csv, a.col, a.min_cycles)
    I = build_knives(df, a.col, a.K, a.W)
    rhos = [float(x) for x in a.rho.split(",")]
    print("=" * 108)
    print("2 档换刀决策 · 离线回测 (留一口口径)")
    print("数据: %s   指标: %s   参与刀数: %d   总件数: %d" % (a.csv, a.col, len(I), I.n.sum()))
    print("参数: base=%.2f extend=%.2f tau=%.3f K=%d W=%d" % (a.base, a.extend, a.tau, a.K, a.W))
    print("=" * 108)
    print("%-34s" % "策略" + " ".join("%-11s" % ("rho=%g" % r) for r in rhos)
          + "  失效   加工件数")
    lines = []
    for nm, ks in strategies(I, a.base, a.extend, a.tau):
        row = [cost_of(ks, I.n.values, r)[0] for r in rhos]
        _, nf, nu = cost_of(ks, I.n.values, rhos[0])
        s = "%-34s" % nm + " ".join("%-11.4f" % v for v in row) + "  %2d/%d  %7.0f" % (nf, len(I), nu)
        print(s); lines.append(s)
    print()
    print("--- 各 rho 下的最优策略 ---")
    best = []
    for r in rhos:
        sc = sorted([(cost_of(ks, I.n.values, r)[0], nm) for nm, ks in strategies(I, a.base, a.extend, a.tau)])
        best.append("rho=%-4g 最优: %-34s %.4f   次优: %-28s %.4f   最差: %-22s %.4f"
                    % (r, sc[0][1], sc[0][0], sc[1][1], sc[1][0], sc[-1][1], sc[-1][0]))
        print(best[-1])
    os.makedirs(a.outdir, exist_ok=True)
    p = Path(a.outdir) / "eval_成本对比.txt"
    p.write_text("\n".join([
        "2 档换刀决策 · 离线回测 (留一口口径)",
        "数据: %s  指标: %s  刀数: %d  总件数: %d" % (a.csv, a.col, len(I), I.n.sum()),
        "参数: base=%.2f extend=%.2f tau=%.3f K=%d W=%d" % (a.base, a.extend, a.tau, a.K, a.W),
        "", "%-34s" % "策略" + " ".join("%-11s" % ("rho=%g" % r) for r in rhos) + "  失效   加工件数",
    ] + lines + [""] + best), encoding="utf-8")
    print("\n[已写出] %s" % p)


def cmd_plan(a):
    rho = float(str(a.rho).split(",")[0])   # plan 只画一条成本, 取第一个 rho
    df = load_hi(a.csv, a.col, a.min_cycles)
    I = build_knives(df, a.col, a.K, a.W)
    D = decision_rows(I, a.base, a.extend, a.tau)
    ks = D["换刀件号"].values
    res = []
    for i, r in D.iterrows():
        if ks[i] >= r.n:
            res.append("**失效** (报废前未换)")
        else:
            res.append("第 %d 件换, 浪费 %d%%" % (int(ks[i]), round((r.n - ks[i]) / r.n * 100)))
    D["结果"] = res
    D["短刀"] = np.where(D.ratio < 0.7, "是", "")
    cost, nf, nu = cost_of(ks, D.n.values, rho)
    base_cost = (len(D) + len(D) * rho) / D.n.sum()
    print("=" * 112)
    print("2 档换刀决策 · 执行表   参数: base=%.2f extend=%.2f tau=%.3f K=%d W=%d rho=%g"
          % (a.base, a.extend, a.tau, a.K, a.W, rho))
    print("=" * 112)
    show = D[["prog", "cyc", "n", "M0", "ratio", "基准点", "硬上限", "信号@", "决策", "结果", "短刀"]]
    print(show.to_string(index=False, float_format=lambda v: "%.3f" % v))
    print()
    print("合计: 加工 %.0f 件 (全用到失效可加工 %.0f 件) | 失效 %d/%d | 刀具消耗率 %.2fx"
          % (nu, D.n.sum(), nf, len(D), D.n.sum() / nu))
    print("rho=%g 成本 = %.4f   vs 现状(用到失效) %.4f   -> 改善 %.2f 倍"
          % (rho, cost, base_cost, base_cost / cost))
    print()
    print("风险结构: 判'延长'的刀中短刀 %d 把 (假阴性, 必须为 0 才安全); 判'基准换'的刀中短刀 %d 把"
          % (int((D[D.决策 == "延长"].短刀 == "是").sum()),
             int((D[D.决策 == "基准换"].短刀 == "是").sum())))
    os.makedirs(a.outdir, exist_ok=True)
    p = Path(a.outdir) / "plan_逐刀决策.csv"
    D.to_csv(p, index=False, encoding="utf-8-sig")
    print("\n[已写出] %s" % p)


def cmd_scan(a):
    df = load_hi(a.csv, a.col, a.min_cycles)
    I = build_knives(df, a.col, a.K, a.W)
    rhos = [float(x) for x in a.rho.split(",")]
    grid = []
    for f in (0.4, 0.5, 0.6, 0.7, 0.8):
        grid.append(("计数", f, None, None))
    for t in (0.01, 0.015, 0.02, 0.03):
        grid.append(("信号", None, None, t))
    for f in (0.4, 0.5, 0.6, 0.7):
        for H in (0.7, 0.8, 0.9, 1.0):
            for t in (0.01, 0.015, 0.02):
                grid.append(("两级", f, H, t))

    def ks_for(kind, f, H, t, sub):
        if kind == "计数":
            return (sub.M0 * f).values
        if kind == "信号":
            s = np.array([sig_trigger(r, t) for _, r in sub.iterrows()], dtype=float)
            return np.where(np.isnan(s), np.inf, s)
        return ks_two(sub, f, H, t)

    print("=" * 100)
    print("嵌套交叉验证: 每次留一把刀, 用其余刀在 %d 组参数里选最优, 再在留下的那把上评估" % len(grid))
    print("=" * 100)
    rows = []
    from collections import Counter
    N = len(I)
    for r in rhos:
        chosen, costs = [], []
        for j in range(N):
            tr = I.drop(I.index[j]); te = I.iloc[[j]]
            best = None
            for kind, f, H, t in grid:
                c = cost_of(ks_for(kind, f, H, t, tr), tr.n.values, r)[0]
                if best is None or c < best[0]:
                    best = (c, kind, f, H, t)
            _, kind, f, H, t = best
            chosen.append((kind, f, H, t))
            costs.append(cost_of(ks_for(kind, f, H, t, te), te.n.values, r)[0])
        top = Counter(chosen).most_common(1)[0]
        rows.append((r, np.mean(costs), "%s(f=%s,H=%s,tau=%s)" % top[0], top[1], N))
        print("rho=%-4g 嵌套CV成本=%.4f   16 次中被选中最多的参数: %s x%d 次"
              % (r, np.mean(costs), "%s(f=%s,H=%s,tau=%s)" % top[0], top[1]))
    print()
    print("解读: 若多次留一刀都选中同一组参数, 说明参数稳健(非过拟合);")
    print("      若每次选的都不一样, 说明参数是在这 16 把刀上碰运气选出来的。")
    os.makedirs(a.outdir, exist_ok=True)
    p = Path(a.outdir) / "scan_参数稳健性.csv"
    pd.DataFrame(rows, columns=["rho", "嵌套CV成本", "最常选中的参数", "选中次数", "留一刀次数"]) \
        .to_csv(p, index=False, encoding="utf-8-sig")
    print("\n[已写出] %s" % p)


def cmd_online(a):
    """在线判定: 指定一把刀, 逐件回放, 在基准点给出"立即换 / 可延长"的建议

    这是车间每天真正会用的模式 —— 给它一把正在跑的刀, 它告诉你现在该不该换。
    """
    df = load_hi(a.csv, a.col, a.min_cycles)
    I = build_knives(df, a.col, a.K, a.W)
    hit = I[(I.prog == a.prog) & (I.cyc == a.cyc)]
    if len(hit) == 0:
        avail = ["%s/c%s" % (r.prog, r.cyc) for _, r in I.iterrows()]
        raise SystemExit("[错误] 找不到 程序%s 周期%s\n可用: %s" % (a.prog, a.cyc, avail))
    r = hit.iloc[0]
    rel = np.asarray(r["rel"], dtype=float)
    wmed = np.asarray(r["wmed"], dtype=float)
    n = int(r.n)
    b = int(round(r.M0 * a.base))
    h = int(round(r.M0 * a.extend))
    s = sig_trigger(r, a.tau)
    print("=" * 96)
    print("在线判定 · 程序%s 周期%s   寿命=%d 件   M0(同程序其余刀中位)=%.0f 件" % (a.prog, a.cyc, n, r.M0))
    print("参数: 基准点=%d 件 (%.0f%%M0)   硬上限=%d 件 (%.0f%%M0)   信号阈值 tau=%.1f%%"
          % (b, a.base * 100, h, a.extend * 100, a.tau * 100))
    print("=" * 96)
    print("%6s %10s %10s   %s" % ("件号", "HI相对基线", "滑动中位", "状态"))
    step = max(1, b // 12)
    for i in range(0, min(b, n), step):
        st = ""
        if i >= a.K:
            if s == s and i + 1 >= s:
                st = "<= 信号已达阈 (第 %d 件)" % int(s)
        print("%6d %9.2f%% %9.2f%%   %s" % (i + 1, rel[i] * 100, wmed[i] * 100, st))
    print("-" * 96)
    healthy = (s == s) and (s <= b)
    if healthy:
        print("判定: 正常磨损路径 (信号在第 %d 件已达 %.1f%%, 早于基准点 %d 件)" % (int(s), a.tau * 100, b))
        print("建议: 放行延长, 到 第 %d 件 (%.0f%%M0) 再换" % (h, a.extend * 100))
    else:
        why = "信号从未达到 %.1f%%" % (a.tau * 100) if s != s else \
              "信号到第 %d 件才达阈, 晚于基准点 %d 件" % (int(s), b)
        print("判定: 异常路径 (%s)" % why)
        print("建议: 立即换刀, 不要延长 (该刀历史上多为短刀)")
    print()
    print("实际结果(回看): 这把刀最终活了 %d 件; 按建议会在第 %d 件换 -> %s"
          % (n, h if healthy else b,
             "**失效**" if (h if healthy else b) >= n else "成功避免失效"))
    os.makedirs(a.outdir, exist_ok=True)
    p = Path(a.outdir) / ("online_程序%s_周期%s.txt" % (a.prog, a.cyc))
    p.write_text("程序%s 周期%s  寿命=%d  M0=%.0f  基准=%d  上限=%d  信号@=%s  判定=%s\n"
                 % (a.prog, a.cyc, n, r.M0, b, h,
                    "从未" if s != s else int(s), "延长" if healthy else "立即换"),
                 encoding="utf-8")
    print("\n[已写出] %s" % p)


# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="2 档换刀决策 (两级报警) —— 唯一入口")
    ap.add_argument("cmd", choices=["eval", "plan", "scan", "online"])
    ap.add_argument("--csv", default=DEF["csv"], help="逐件 HI 表 (默认 ../out_T1111/xalign_HI.csv)")
    ap.add_argument("--col", default=DEF["col"], help="指标列")
    ap.add_argument("--base", type=float, default=DEF["base"], help="基准点 = base*M0")
    ap.add_argument("--extend", type=float, default=DEF["extend"], help="硬上限 = extend*M0")
    ap.add_argument("--tau", type=float, default=DEF["tau"], help="信号阈值 (相对新刀基线升幅)")
    ap.add_argument("--K", type=int, default=DEF["K"], help="新刀基线窗口件数")
    ap.add_argument("--W", type=int, default=DEF["W"], help="信号平滑窗口件数")
    ap.add_argument("--rho", default="2,5,10,20,50", help="失效代价(可逗号分隔多个)")
    ap.add_argument("--min-cycles", type=int, default=DEF["min_cycles"], help="程序最少刀数")
    ap.add_argument("--outdir", default=str(DEFAULT_OUT), help="产出目录")
    ap.add_argument("--prog", type=int, default=None, help="online: 程序号")
    ap.add_argument("--cyc", type=int, default=None, help="online: 周期号")
    a = ap.parse_args()
    if not os.path.exists(a.csv):
        raise SystemExit("[错误] 找不到数据文件: %s" % a.csv)
    if a.cmd == "online" and (a.prog is None or a.cyc is None):
        raise SystemExit("[错误] online 必须给 --prog 和 --cyc, 例如: --prog 14 --cyc 27")
    {"eval": cmd_eval, "plan": cmd_plan, "scan": cmd_scan, "online": cmd_online}[a.cmd](a)


if __name__ == "__main__":
    main()
