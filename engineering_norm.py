# -*- coding: utf-8 -*-
"""
工程归一化工具 (与模型无关的三件事)

  1) relatize_position()  绝对机械坐标 -> 行程内相对位置 [0,1]
       消除对刀点/工件坐标系/机床原点差异。实测 48 列中 47 列跨周期漂移下降。
  2) RobustNorm           中位数/IQR 缩放
       只在 train 上 fit。树模型不需要; 换 LSTM/TCN/SVM/线性/PCA 时必须。
  3) quantile_align()     分位数映射
       跨机床迁移时, 把新机床的读数分布对齐到训练机床的分布。

用法见文件末尾 __main__ 自检。
"""
import numpy as np
import pandas as pd

EPS = 1e-9


# ---------------------------------------------------------------- 1
def relatize_position(X, axes=("x", "y", "z"),
                      windows=("1min", "3min", "5min", "10min"),
                      base_window="10min", stats=("mean", "max", "min", "std")):
    """
    把 L_abso_<轴>_* 系列从绝对机械坐标转成"行程内相对位置"。

        x_rel = (x - L_abso_<轴>_min_<base_window>) / L_abso_<轴>_ptp_<base_window>

    物理含义: 刀具当前处在本次行程的百分之几 —— 与坐标原点无关。
    返回: 新增的列名 list
    """
    added = []
    for ax in axes:
        mn_c = "L_abso_%s_min_%s" % (ax, base_window)
        ptp_c = "L_abso_%s_ptp_%s" % (ax, base_window)
        if mn_c not in X.columns or ptp_c not in X.columns:
            continue
        mn_all = pd.to_numeric(X[mn_c], errors="coerce")
        ptp_all = pd.to_numeric(X[ptp_c], errors="coerce")
        ptp_all = ptp_all.mask(ptp_all <= EPS)

        # 裸的瞬时坐标列 L_abso_x / L_abso_y / L_abso_z
        raw = "L_abso_%s" % ax
        if raw in X.columns:
            new = raw + "_relpos"
            X[new] = (pd.to_numeric(X[raw], errors="coerce") - mn_all) / ptp_all
            added.append(new)

        # 各窗口各统计量
        for w in windows:
            mnc = "L_abso_%s_min_%s" % (ax, w)
            ptpc = "L_abso_%s_ptp_%s" % (ax, w)
            if mnc not in X.columns or ptpc not in X.columns:
                continue
            mn_w = pd.to_numeric(X[mnc], errors="coerce")
            ptp_w = pd.to_numeric(X[ptpc], errors="coerce")
            ptp_w = ptp_w.mask(ptp_w <= EPS)
            for st in stats:
                c = "L_abso_%s_%s_%s" % (ax, st, w)
                if c in X.columns:
                    new = c + "_relpos"
                    X[new] = (pd.to_numeric(X[c], errors="coerce") - mn_w) / ptp_w
                    added.append(new)
    return added


# ---------------------------------------------------------------- 2
class RobustNorm:
    """
    中位数 / IQR 缩放。比 StandardScaler 更抗离群点(你的负载列离群点很多)。

    硬性纪律:
        scaler 只能在 train 上 fit; LOCO 每一折都要重新 fit,
        否则测试周期的均值/方差会泄进 scaler。
    """

    def __init__(self):
        self.med_ = None
        self.iqr_ = None
        self.cols_ = None

    def fit(self, X):
        X = pd.DataFrame(X)
        self.cols_ = list(X.columns)
        self.med_ = X.median(numeric_only=True)
        q1 = X.quantile(0.25, numeric_only=True)
        q3 = X.quantile(0.75, numeric_only=True)
        self.iqr_ = (q3 - q1).replace(0, np.nan)
        return self

    def transform(self, X):
        X = pd.DataFrame(X)[self.cols_]
        return (X - self.med_) / self.iqr_

    def fit_transform(self, X):
        return self.fit(X).transform(X)


# ---------------------------------------------------------------- 3
def quantile_align(x_new, x_ref, n_grid=512):
    """
    分位数映射: 把 x_new 的分布对齐到 x_ref 的分布。

    做法: 算 x_new 中每个值在【自身分布】里的百分位 r,
          再取 x_ref 的第 r 百分位作为对齐后的值。

    x_ref : 训练集(源机床)的该列
    x_new : 待对齐(目标机床/目标周期)的该列
    """
    x_new = pd.to_numeric(pd.Series(x_new), errors="coerce")
    x_ref = pd.to_numeric(pd.Series(x_ref), errors="coerce").dropna().values
    if len(x_ref) == 0:
        return x_new

    out = np.full(len(x_new), np.nan, dtype="float64")
    idx = x_new.notna().values
    if idx.sum() == 0:
        return pd.Series(out, index=x_new.index)

    new_sorted = np.sort(x_new.values[idx])
    pos = np.searchsorted(new_sorted, x_new.values[idx], side="right") / len(new_sorted)
    pos = np.clip(pos, 0.0, 1.0)

    qs = np.linspace(0, 1, n_grid + 1)[1:-1]
    grid = np.quantile(x_ref, qs)
    # 在网格上插值, 避免逐点 searchsorted 太慢
    out[idx] = np.interp(pos, qs, grid)
    return pd.Series(out, index=x_new.index)


def align_columns(df_new, df_ref, cols=None):
    """对一批列批量做分位数对齐(逐列独立)"""
    if cols is None:
        cols = [c for c in df_ref.columns if c in df_new.columns]
    out = pd.DataFrame(index=df_new.index)
    for c in cols:
        out[c] = quantile_align(df_new[c], df_ref[c])
    return out


# ---------------------------------------------------------------- 自检
if __name__ == "__main__":
    np.random.seed(0)
    print("=" * 66)
    print("自检 1: relatize_position —— 坐标原点变了，相对位置不变")
    print("=" * 66)
    n = 1000
    t = np.linspace(0, 10, n)
    # 机床A: 行程 10~60mm；机床B: 同样的运动，但原点偏了 500mm、量程放大 2 倍
    posA = 10 + 25 * (1 + np.sin(t))
    posB = 500 + 2 * posA
    df = pd.DataFrame({
        "L_abso_x": np.r_[posA, posB],
        "L_abso_x_min_10min": np.r_[np.full(n, 10.0), np.full(n, 520.0)],
        "L_abso_x_ptp_10min": np.r_[np.full(n, 50.0), np.full(n, 100.0)],
        "L_abso_y": np.r_[posA, posB],
        "L_abso_y_min_10min": np.r_[np.full(n, 10.0), np.full(n, 520.0)],
        "L_abso_y_ptp_10min": np.r_[np.full(n, 50.0), np.full(n, 100.0)],
    })
    added = relatize_position(df, axes=("x", "y"))
    a = df["L_abso_x_relpos"].values[:n]
    b = df["L_abso_x_relpos"].values[n:]
    print("  新增列: %s" % added)
    print("  机床A 绝对坐标均值 %.1f mm | 机床B %.1f mm  (差 %.0f mm, 模型会当成两种状态)"
          % (df["L_abso_x"].values[:n].mean(), df["L_abso_x"].values[n:].mean(),
             df["L_abso_x"].values[n:].mean() - df["L_abso_x"].values[:n].mean()))
    print("  机床A 相对位置均值 %.4f     | 机床B %.4f      (差 %.2e -> 已经可比)"
          % (np.nanmean(a), np.nanmean(b), abs(np.nanmean(a) - np.nanmean(b))))

    print()
    print("=" * 66)
    print("自检 2: RobustNorm —— 只在 train 上 fit")
    print("=" * 66)
    Xtr = pd.DataFrame({"load": np.random.normal(100, 20, 500),
                        "speed": np.random.normal(2000, 300, 500)})
    Xte = pd.DataFrame({"load": np.random.normal(130, 20, 200),
                        "speed": np.random.normal(2100, 300, 200)})
    rn = RobustNorm().fit(Xtr)
    Ztr, Zte = rn.transform(Xtr), rn.transform(Xte)
    print("  train fit 后: load med=%.1f iqr=%.1f | speed med=%.1f iqr=%.1f"
          % (rn.med_["load"], rn.iqr_["load"], rn.med_["speed"], rn.iqr_["speed"]))
    print("  train 缩放后中位: load=%.4f, speed=%.4f  (应接近 0)"
          % (Ztr["load"].median(), Ztr["speed"].median()))
    print("  test  缩放后中位: load=%.4f, speed=%.4f  (>0 说明 test 整体偏高, 这正是要检测的漂移)"
          % (Zte["load"].median(), Zte["speed"].median()))

    print()
    print("=" * 66)
    print("自检 3: quantile_align —— 两台机床尺子不一样时的对齐")
    print("=" * 66)
    A = np.random.normal(100, 20, 4000)          # 源机床
    B = A[:2000] * 1.30 + 12                     # 目标机床: 同样状态, 读数偏大 30%
    thr = np.quantile(A, 0.5)
    print("  源机床中位数阈值 = %.1f" % thr)
    print("  对齐前: 目标机床走'低'分支的比例 = %.1f%%  (源机床是 50.0%%) -> 崩了"
          % (100 * (B <= thr).mean()))
    Ba = quantile_align(B, A).values
    print("  对齐后: 比例 = %.1f%%                -> 修好了" % (100 * (Ba <= thr).mean()))
    print()
    print("  %-8s %12s %12s %12s" % ("分位", "源机床A", "目标B(原始)", "目标B(对齐)"))
    for q in (0.1, 0.25, 0.5, 0.75, 0.9):
        print("  %-8s %12.1f %12.1f %12.1f"
              % ("%d%%" % (q * 100), np.quantile(A, q), np.quantile(B, q),
                 np.quantile(Ba, q)))
    print()
    print("ALL SELFTEST DONE")
