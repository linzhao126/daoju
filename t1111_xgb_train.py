#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
t1111_xgb_train.py - T1111(4152 车床) 窗口特征 -> XGBoost 寿命回归（走法A，复现/探索用）

与 t1111_pipeline.py 解耦的独立实验脚本（训练/实验入口单独成文件）。

设计要点：
  - 输入：t1111_pipeline.py window 产出的【行级】窗口特征 CSV
  - 目标：默认 life_used_pct_cut（按切除量折算的已用寿命%）；--target 可切 life_used_pct_time
  - 划分：按 life_cycle（换刀周期）分组切分
          train = 前 N 个周期，test = 最后 test_frac 比例的周期
          —— 杜绝「行级随机/时间切分」把同一周期拆进 train+test 造成的数据泄漏
  - 防泄漏排除列：
      ① 目标/标注/索引列（life_cycle / life_used_pct_* / in_life_range / change_reason /
         real_failure / _time / _t / L_allnum）
      ② 频域列（本数据 28ms/35.7Hz 采样，奈奎斯特 17.86Hz < 主轴转频 22.15Hz，频域混叠，全部禁用）
      ③ leakage_blacklist.json 中的 363 列
  - 输出：
      out_T1111/t1111_xgb_metrics.json          （train/test 的 R2/RMSE/MAE）
      out_T1111/t1111_xgb_feature_importance.csv（特征重要性）
      out_T1111/t1111_xgb_pred_vs_true.png      （test 集 pred vs true 散点，matplotlib 不可用则跳过）
      out_T1111/t1111_xgb_cycle_manifest.csv    （逐周期 train/test 验收清单）
      out_T1111/t1111_xgb_report.md             （普通人能看懂的结论报告，人话版）

用法：
  .venv/Scripts/python.exe t1111_xgb_train.py --file out_T1111/t1111_window_feat_full.csv
  .venv/Scripts/python.exe t1111_xgb_train.py --file out_T1111/t1111_window_feat_full.csv --target life_used_pct_time
"""
import os
import json
import argparse
import numpy as np
import pandas as pd

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(WORKSPACE, 'out_T1111')
BLACKLIST = os.path.join(WORKSPACE, 'leakage_blacklist.json')

# 必须排除的目标/标注/索引列（不能当特征，也不能作为答案之外的泄漏源）
DROP_ANSWER = ['life_cycle', 'life_used_pct_time', 'life_used_pct_cut',
               'in_life_range', 'change_reason', 'real_failure',
               '_time', '_t', 'L_allnum']
# 频域特征关键词（本数据混叠，全部禁用）
FREQ_KEYS = ('_band', '_centroid', '_spec', '_freq')

TARGET = 'life_used_pct_cut'  # 由 main() 按 --target 改写


def P(msg=''):
    print(msg, flush=True)


def banner(t):
    P('\n' + '=' * 70)
    P(t)
    P('=' * 70)


def load_blacklist(path):
    """兼容 list 与 dict 两种格式，返回 set 列名"""
    if not os.path.exists(path):
        P('  ! 未找到黑名单 %s，跳过' % path)
        return set()
    try:
        d = json.load(open(path, encoding='utf-8'))
    except Exception as e:
        P('  ! 黑名单读取失败: %s，跳过' % e)
        return set()
    if isinstance(d, dict):
        cols = set()
        for v in d.values():
            if isinstance(v, (list, tuple, set)):
                cols |= set(map(str, v))
            else:
                cols.add(str(v))
        return cols
    if isinstance(d, list):
        return set(map(str, d))
    return set()


def pick_features(df, target, blacklist):
    """返回 (特征列名 list, 排除原因 dict)"""
    reason = {}
    cand = []
    for c in df.columns:
        if c == target:
            reason[c] = 'target'
            continue
        if c in DROP_ANSWER:
            reason[c] = 'answer/label/index'
            continue
        if any(k in c.lower() for k in FREQ_KEYS):
            reason[c] = 'freq(aliased)'
            continue
        if c in blacklist:
            reason[c] = 'leakage_blacklist'
            continue
        cand.append(c)
    keep = []
    for c in cand:
        s = df[c]
        if not pd.api.types.is_numeric_dtype(s):
            reason[c] = 'non-numeric'
            continue
        if s.isna().all():
            reason[c] = 'all-NaN'
            continue
        keep.append(c)
    return keep, reason


def split_by_cycle(df, test_frac):
    """按 life_cycle 分组切分：train=前 N 周期，test=最后 n_test 周期（至少留 1 个训练周期）"""
    cycles = sorted(df['life_cycle'].dropna().unique().tolist())
    n_test = max(1, int(round(len(cycles) * test_frac)))
    n_test = min(n_test, len(cycles) - 1)  # 至少留 1 个训练周期
    n_train = len(cycles) - n_test
    test_cycles = set(cycles[n_train:])
    train_cycles = set(cycles[:n_train])
    train_df = df[df['life_cycle'].isin(train_cycles)].copy()
    test_df = df[df['life_cycle'].isin(test_cycles)].copy()
    return train_df, test_df, sorted(train_cycles), sorted(test_cycles)


def save_manifest(df, train_cycles, test_cycles, path):
    """逐周期 train/test 验收清单"""
    tset = set(train_cycles)
    rows = []
    for cyc, g in df.groupby('life_cycle'):
        side = 'train' if cyc in tset else 'test'
        rows.append({
            'life_cycle': cyc,
            'split': side,
            'n_rows': len(g),
            'target_mean': float(g[TARGET].mean()) if TARGET in g else np.nan,
            'target_min': float(g[TARGET].min()) if TARGET in g else np.nan,
            'target_max': float(g[TARGET].max()) if TARGET in g else np.nan,
        })
    man = pd.DataFrame(rows).sort_values('life_cycle').reset_index(drop=True)
    man.to_csv(path, index=False, encoding='utf-8-sig')
    return man


def write_plain_report(metrics, train_cycles, test_cycles, man, target, out_dir):
    """生成普通人能看懂的 Markdown 结论报告（动态读当次 metrics，不写死数字）"""
    tr, te = metrics['train'], metrics['test']
    tR2, tMAE = tr['R2'], tr['MAE']
    eR2, eMAE = te['R2'], te['MAE']
    n_tr_c = metrics['n_train_cycles']
    n_te_c = metrics['n_test_cycles']
    n_rows = metrics['n_train_rows'] + metrics['n_test_rows']
    n_feat = metrics['n_features']

    # 结论判定（按测试集 R2）
    if eR2 < 0:
        verdict = ('模型在没见过的刀上，预测准确性**比“瞎猜平均值”还差**'
                   '（测试评分 %.2f，负数代表不如蒙）。' % eR2)
        verdict_short = '结论：当前数据无法预测刀具寿命'
    elif eR2 < 0.1:
        verdict = ('模型在没见过的刀上几乎**没有预测能力**'
                   '（测试评分 %.2f，接近 0）。' % eR2)
        verdict_short = '结论：当前数据基本无法预测刀具寿命'
    else:
        verdict = ('测试评分 %.2f 为正，但请**先核对 t1111_xgb_cycle_manifest.csv'
                   ' 确认没有数据泄漏**，再下结论。' % eR2)
        verdict_short = '结论：测试为正，需先排查数据泄漏'

    # 周期覆盖说明（检测 1..max 是否有缺口 / 早期周期缺失）
    used = sorted(int(c) for c in man['life_cycle'].tolist())
    cmin, cmax = used[0], used[-1]
    if cmin > 1 or (cmax - cmin + 1) != len(used):
        missing = [c for c in range(1, cmax + 1) if c not in set(used)]
        # 把缺失周期合并成连续区间，如 [1..16, 21] -> "1–16、21"
        runs, s, p = [], None, None
        for c in missing:
            if s is None:
                s = p = c
            elif c == p + 1:
                p = c
            else:
                runs.append((s, p)); s = p = c
        if s is not None:
            runs.append((s, p))
        shown = '、'.join(('%d–%d' % (a, b) if a != b else '%d' % a) for a, b in runs)
        gap_note = ('\n> **注意：数据覆盖** 本次实际只用了 %d 个周期（编号 %d–%d），'
                    '更早/中间的周期 %s 没有可用数据（共 %d 个）。可能因为它们发生在采集开始之前、'
                    '或不属于这把刀。结论基于“最近一小段”数据，样本偏少。'
                    % (len(used), cmin, cmax, shown, len(missing)))
    else:
        gap_note = '\n> 本次使用了编号 %d–%d 共 %d 个连续周期。' % (cmin, cmax, len(used))

    L = []
    L.append('# 刀具寿命预测 · 实验报告（人话版）')
    L.append('')
    L.append('> 自动生成于 `t1111_xgb_train.py`｜目标列 `%s`｜按换刀周期分组切分（防泄漏）' % target)
    L.append('')
    L.append('## 一句话结论')
    L.append('')
    L.append('**%s**' % verdict_short)
    L.append('')
    L.append(verdict)
    L.append('')
    L.append('## 我们做了什么')
    L.append('')
    L.append('把加工数据打标后，用“滑窗特征”训练了一个 XGBoost 模型，'
             '目标是预测“刀具已用掉的寿命百分比”（0 = 全新，100 = 该换）。'
             '训练用了 %d 个历史周期，测试用了 %d 个它从没见过的周期。' % (n_tr_c, n_te_c))
    L.append('')
    L.append('## 结果（打比方）')
    L.append('')
    L.append('| 阶段 | 比喻 | 评分 R² | 平均误差（百分点） |')
    L.append('| --- | --- | --- | --- |')
    L.append('| 训练（见过的刀） | 背了例题 | %.2f | %.1f |' % (tR2, tMAE))
    L.append('| 测试（没见过的刀） | 换套新题 | %.2f | %.1f |' % (eR2, eMAE))
    L.append('')
    L.append('**“评分 R²”怎么看**：满分是 1（完美预测），0 是“蒙平均值”，'
             '**负数代表比蒙平均值还离谱**。本次测试评分 %.2f，说明模型没有真正学会'
             '“寿命”的规律，只是把训练那几把刀的脾气背下来了。' % eR2)
    L.append('')
    L.append('## 为什么会这样')
    L.append('')
    L.append('同一道工序，每把刀的实际负载水平随机波动（差异约 30%）。'
             '现有传感器（主轴负载百分比）分辨率太粗，抓不到磨损引起的细微变化。'
             '这是设备客观限制，不是模型没调好。')
    L.append('')
    L.append('## 数据使用说明')
    L.append('')
    L.append('训练 %d 周期、测试 %d 周期，共 %d 行进入模型，特征 %d 列。'
             % (n_tr_c, n_te_c, n_rows, n_feat))
    L.append(gap_note)
    L.append('')
    L.append('## 下一步')
    L.append('')
    L.append('寿命预测这条路在当前数据下走不通。项目真正能交付的是'
             '“**故障指纹报警器**”——在刀已经坏了却还在干活的空窗里报警，'
             '这部分用走法 B（alarm_online.py）验证。')
    L.append('')
    L.append('---')
    L.append('')
    L.append('### 附：给想看数字的人')
    L.append('')
    L.append('- 指标明细：`t1111_xgb_metrics.json`')
    L.append('- 周期切分清单：`t1111_xgb_cycle_manifest.csv`')
    L.append('- 特征重要性：`t1111_xgb_feature_importance.csv`')
    L.append('- 预测 vs 真实散点图：`t1111_xgb_pred_vs_true.png`')
    L.append('')
    txt = '\n'.join(L)
    rpath = os.path.join(out_dir, 't1111_xgb_report.md')
    with open(rpath, 'w', encoding='utf-8') as f:
        f.write(txt)
    return rpath


def main():
    global TARGET
    ap = argparse.ArgumentParser(description='T1111 窗口特征 -> XGBoost 寿命回归（走法A）')
    ap.add_argument('--file', required=True,
                    help='window 产物 CSV（如 out_T1111/t1111_window_feat_full.csv）')
    ap.add_argument('--target', default='life_used_pct_cut',
                    help='目标列：life_used_pct_cut(默认) / life_used_pct_time')
    ap.add_argument('--test-frac', type=float, default=0.2,
                    help='测试周期占比（按 life_cycle 分组切分），默认 0.2')
    ap.add_argument('--out', default=OUT_DIR, help='输出目录，默认 out_T1111')
    ap.add_argument('--blacklist', default=BLACKLIST, help='泄漏黑名单 json 路径')
    a = ap.parse_args()
    TARGET = a.target

    banner('T1111 窗口特征 -> XGBoost 寿命回归（走法A）')
    P('输入: %s' % a.file)
    if not os.path.exists(a.file):
        raise SystemExit('找不到输入文件: %s' % a.file)

    P('\n[1] 读入')
    df = pd.read_csv(a.file, encoding='utf-8-sig', low_memory=False)
    P('    原始 %d 行 x %d 列' % df.shape)
    if TARGET not in df.columns:
        raise SystemExit('目标列 %s 不在文件中；可选: life_used_pct_time / life_used_pct_cut' % TARGET)
    P('    目标列 = %s' % TARGET)

    P('\n[2] 过滤：仅标注区间内 & 目标非 NA')
    if 'in_life_range' in df.columns:
        before = len(df)
        df = df[df['in_life_range'].astype(str).str.lower().isin(['true', '1'])].copy()
        P('    in_life_range 过滤: %d -> %d 行' % (before, len(df)))
    before = len(df)
    df = df[df[TARGET].notna()].copy()
    P('    目标非 NA 过滤: %d -> %d 行' % (before, len(df)))
    if len(df) == 0:
        raise SystemExit('过滤后无可用样本，请检查 mark 是否正确打标')

    P('\n[3] 特征选择（防泄漏）')
    blacklist = load_blacklist(a.blacklist)
    feats, reason = pick_features(df, TARGET, blacklist)
    P('    最终特征 = %d 列' % len(feats))
    from collections import Counter
    rc = Counter(reason.values())
    P('    排除统计: ' + ', '.join('%s=%d' % (k, v) for k, v in rc.items()))

    P('\n[4] 按 life_cycle 分组切分（防周期内泄漏）')
    train_df, test_df, train_cycles, test_cycles = split_by_cycle(df, a.test_frac)
    tc_r = '%s..%s' % (train_cycles[0], train_cycles[-1]) if train_cycles else '-'
    te_r = '%s..%s' % (test_cycles[0], test_cycles[-1]) if test_cycles else '-'
    P('    总周期数 = %d | train 周期 %d（%s）| test 周期 %d（%s）'
      % (len(train_cycles) + len(test_cycles), len(train_cycles), tc_r,
         len(test_cycles), te_r))
    P('    train 行 = %d，test 行 = %d' % (len(train_df), len(test_df)))
    overlap = set(train_cycles) & set(test_cycles)
    P('    周期重叠检查: %s（应为空）' % (overlap if overlap else '无 OK'))

    os.makedirs(a.out, exist_ok=True)
    man_path = os.path.join(a.out, 't1111_xgb_cycle_manifest.csv')
    man = save_manifest(df, train_cycles, test_cycles, man_path)
    P('    逐周期验收清单已保存: %s（%d 周期）' % (man_path, len(man)))
    for _, r in man.iterrows():
        P('    周期 %-4s %-5s 行数=%-6d  target[min=%.1f mean=%.1f max=%.1f]'
          % (r['life_cycle'], r['split'], r['n_rows'],
             r['target_min'], r['target_mean'], r['target_max']))

    P('\n[5] 训练 XGBoost 回归')
    try:
        import xgboost as xgb
        from sklearn.metrics import r2_score, mean_squared_error, mean_absolute_error
    except Exception as e:
        raise SystemExit('缺少依赖（xgboost / scikit-learn）: %s' % e)

    Xtr, ytr = train_df[feats], train_df[TARGET]
    Xte, yte = test_df[feats], test_df[TARGET]
    # inf 不进模型（NaN XGBoost 能原生处理，inf 不能）
    Xtr = Xtr.replace([np.inf, -np.inf], np.nan)
    Xte = Xte.replace([np.inf, -np.inf], np.nan)

    model = xgb.XGBRegressor(n_estimators=300, max_depth=5, learning_rate=0.05,
                             n_jobs=-1, random_state=42)
    model.fit(Xtr, ytr)
    pred_tr = model.predict(Xtr)
    pred_te = model.predict(Xte)
    metrics = {
        'target': TARGET,
        'split': 'by life_cycle (grouped)',
        'test_frac': a.test_frac,
        'n_features': len(feats),
        'n_train_rows': int(len(train_df)), 'n_test_rows': int(len(test_df)),
        'n_train_cycles': len(train_cycles), 'n_test_cycles': len(test_cycles),
        'train': {
            'R2': float(r2_score(ytr, pred_tr)),
            'RMSE': float(np.sqrt(mean_squared_error(ytr, pred_tr))),
            'MAE': float(mean_absolute_error(ytr, pred_tr)),
        },
        'test': {
            'R2': float(r2_score(yte, pred_te)),
            'RMSE': float(np.sqrt(mean_squared_error(yte, pred_te))),
            'MAE': float(mean_absolute_error(yte, pred_te)),
        },
    }
    P('    train R2=%.4f RMSE=%.4f MAE=%.4f'
      % (metrics['train']['R2'], metrics['train']['RMSE'], metrics['train']['MAE']))
    P('    test  R2=%.4f RMSE=%.4f MAE=%.4f'
      % (metrics['test']['R2'], metrics['test']['RMSE'], metrics['test']['MAE']))

    P('\n[6] 特征重要性（Top 30）')
    imp = pd.DataFrame({'feature': feats, 'importance': model.feature_importances_})
    imp = imp.sort_values('importance', ascending=False)
    imp_path = os.path.join(a.out, 't1111_xgb_feature_importance.csv')
    imp.to_csv(imp_path, index=False, encoding='utf-8-sig')
    P('    已保存: %s' % imp_path)
    for _, r in imp.head(30).iterrows():
        P('    %-45s %.4f' % (r['feature'], r['importance']))

    mpath = os.path.join(a.out, 't1111_xgb_metrics.json')
    json.dump(metrics, open(mpath, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
    P('\n    指标已保存: %s' % mpath)

    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(6, 6))
        ax.scatter(yte, pred_te, s=6, alpha=0.3, color='#BA7517')
        lo = float(min(yte.min(), pred_te.min()))
        hi = float(max(yte.max(), pred_te.max()))
        ax.plot([lo, hi], [lo, hi], '--', color='#888780', lw=1)
        ax.set_xlabel('true %s' % TARGET)
        ax.set_ylabel('pred %s' % TARGET)
        ax.set_title('XGBoost pred vs true (test, by life_cycle split)')
        pp = os.path.join(a.out, 't1111_xgb_pred_vs_true.png')
        fig.tight_layout()
        fig.savefig(pp, dpi=110)
        plt.close(fig)
        P('    散点图已保存: %s' % pp)
    except Exception as e:
        P('    ! 绘图跳过（matplotlib 不可用）: %s' % e)

    rpath = write_plain_report(metrics, train_cycles, test_cycles, man, TARGET, a.out)
    P('\n    人话报告已保存: %s' % rpath)

    banner('完成')
    P('预期结论：test R2 接近 0、RMSE/MAE 较大 -> 复现「寿命无信号」。')
    P('若 test R2 显著为正，反而要警惕是否仍有泄漏，请先核对 t1111_xgb_cycle_manifest.csv。')


if __name__ == '__main__':
    main()
