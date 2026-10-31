# -*- coding: utf-8 -*-
"""R-6 基线冻结表（任务书 §4-P0-C03 + §5.3 十三项锁定）

一行 = 一个冻结格：(模型 × 口径 Tag × 截断件数 N)。共 4 模型 × 3 口径 × 4 截断 = 48 格。

为什么按"格"而不是按"模型"冻结：
  训练池是**逐格变化**的（两层留一：外层移除测试刀 k，内层再为每个训练刀留一），
  按模型只冻结一条会把"AsOfCount=80 时训练池只剩 2~3 把"这种关键信息抹掉。
  —— 正是任务书 §5.3 说的"避免模型各用各的分母，最后只比 MAE 表格"。

十三项锁定的落位：
  DatasetVersion / CodeCommit / TrainPhysicalLifeIds / TestPhysicalLifeIds / AsOfTime(件数口径)
  ExposureDefinition / TargetDefinition / LabelEvidenceLevel / ExclusionReason
  MetricCodeVersion / ModelConfig / PredictionValue / ActualValue

冻结校验（不只看，要能对上）：
  本脚本从 **R-5 长表**（P0-C02_逐刀逐时点预测长表.csv）按 (口径, 截断, 模型) 重算 n 与 MAE，
  与 s53 的汇总表 R04_pred_split.csv 逐格对账；三方可对：长表 ↔ 冻结表 ↔ 54-00。

版本号规则（写死，防"移动目标"）：
  BaselineVersionId = BLK-<模型>-<口径>-v<n>；n 从 1 起。
  模型代码、HI 管道、数据集定义、截断档位任一发生变化 → **新增 v(n+1) 行，旧行永不覆盖**。
"""
import csv
import os
import subprocess
import time
from collections import defaultdict

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
SPLIT = os.path.join(BASE, '附件_证据', '54_R04_分级输出', 'R04_pred_split.csv')
DETAIL = os.path.join(BASE, '附件_证据', '54_R04_分级输出', 'R04_pred_split_detail.csv')
LEDGER = os.path.join(BASE, 'P0_数据与代码移交', 'P0-C02_逐刀逐时点预测长表.csv')
DUAL = os.path.join(BASE, 'P0_数据与代码移交', 'P0-D05_双维度证据等级.csv')
OUT = os.path.join(BASE, 'P0_数据与代码移交', 'P0-C03_基线冻结.csv')

MODELS = ['M0A', 'M0B', 'M1Old', 'M1New']
CUTS = [20, 40, 60, 80]

# 模型定义与代码位置（bt_core.py 为准，行号取自本仓库当前版本）
MODEL_DEF = {
    'M0A': ('历史同类寿命中位数 − 已用量（同程序，最低复杂度统计参照）',
            'bt_core.py:45 m0a_k=median(life[S])；:91 M0A=m0a_k-N'),
    'M0B': ('条件生存：只用训练池中"寿命>N"的刀算剩余中位（不看 HI）',
            'bt_core.py:46-47 cond_k/m0b_k；:91 M0B=m0b_k'),
    'M1Old': ('HI 修正·旧口径：life/M0A−1 ~ rise 过原点最小二乘，乘性外推',
              'bt_core.py:59-67 b 拟合与 pred_old'),
    'M1New': ('HI 修正·新口径：log((L−N)/M0B) ~ 标准化 z(rise)，乘性因子限幅 + 绝对值兜底',
              'bt_core.py:69-87 g 拟合、factor 限幅、pred_new 兜底'),
}
POOL_RULE = ('外层：S={同程序 且 ≠测试刀k}；内层：训练刀 o 的基线只用 S\\{o}（两层留一）；'
             '|S|<2 或可拟合刀数 <min_fit=2 → 本格不出预测，记入"算不出"')
EXPOSURE = ('件 = L_allnum 跨度（1 件 = 一次 allnum 递增）；寿命占比 progress=piece_idx/Life；'
            '退化信号 rise = HI_comb 自该刀首件起的相对上升（HI_comb 由 hi_asof/s20 口径产出）')
TARGET = ('ActualRUL = Life − AsOfCount（单位：件）；口径 A 用 aligned 条数、口径 C2 用参与件数，'
          '两者在 N03/s76 长表中分列，不互换')
METRIC_VER = ('s53_r04_grade_split.py:190-230（MAE/MedAE/Bias/Over/Cover 定义与分母口径）；'
              'Over=预测高于真值计数；Cover=真值落入 [PI_lo,PI_hi] 的比例（仅 M0B/M1 有区间）')
CONFIG = ('min_fit=2；g 拟合后 clamp ±0.5；z_k 截断 Z_PRED_MAX=3.0；'
          '乘性因子 factor∈[0.5,2.0]；绝对值兜底 pred≤3×M0B 且 ≥0（bt_core.py:20-28,77-85）')
FREEZE = 'v1 冻结于 2026-10-31；代码/数据/截断档位任一变化 → 新增 v(n+1)，本行不覆盖不删除'


def git_last(repo, rel):
    try:
        r = subprocess.run(['git', '-C', repo, 'log', '--format=%h', '-1', '--', rel],
                           capture_output=True, text=True, timeout=30)
        return (r.stdout or '').strip() or 'unknown'
    except Exception:
        return 'unknown'


def main():
    agg = pd.read_csv(SPLIT, encoding='utf-8-sig')
    det = pd.read_csv(DETAIL, encoding='utf-8-sig')
    led = pd.read_csv(LEDGER, encoding='utf-8-sig')
    dual = pd.read_csv(DUAL, encoding='utf-8-sig')

    src_pred = git_last(BASE, ':/20261027/附件_证据/54_R04_分级输出/R04_pred_split_detail.csv')
    src_hi = git_last(BASE, ':/20261027/24_程序归属修正/HI_merged_28.csv')
    now = time.strftime('%Y-%m-%d %H:%M:%S')

    # 标签证据等级：本格测试刀里"可进正式真值评分=YES"的有几把
    can = {int(r.顺序号Cycle): (r.可进正式真值评分 == 'YES') for r in dual.itertuples()}
    gradeB = {int(r.顺序号Cycle): r.维度B_原因可信度 for r in dual.itertuples()}
    gradeA = {int(r.顺序号Cycle): r.维度A_件数一致性 for r in dual.itertuples()}

    ref = {(r.Tag, int(r.CutoffN), r.Model): r for r in agg.itertuples()}

    rows, mism = [], 0
    for m in MODELS:
        for tag in sorted({t for t, _, _ in ref}):
            for N in CUTS:
                key = (tag, N, m)
                if key not in ref:
                    continue
                a = ref[key]
                # 从 R-5 长表回算该格
                sel = led[(led.口径Tag == tag) & (led.截断件数CutoffN == N) & (led.模型 == m)]
                errs = pd.to_numeric(sel['绝对误差'], errors='coerce').dropna()
                n_own, mae_own = len(errs), (errs.mean() if len(errs) else float('nan'))
                if int(a.n) != n_own or abs(float(a.MAE) - mae_own) > 0.02:
                    mism += 1
                    print('   ★ 对不上 %s：长表 n=%d MAE=%.2f  vs  54-00 n=%d MAE=%.2f'
                          % (key, n_own, mae_own, int(a.n), float(a.MAE)))
                # 测试刀集合与该格的训练池示例
                dsel = det[(det.Tag == tag) & (det.CutoffN == N)]
                dsel = dsel[[c for c in dsel.columns if c == m or c in ('Tag', 'CutoffN', 'Cycle', 'TrainIds', 'TrainN', 'Program')]]
                cyc = [int(c) for c in dsel['Cycle']]
                pools = sorted({str(t) for t in dsel['TrainIds']})
                nok = sum(1 for c in cyc if can.get(c))
                lvls = sorted({str(gradeB.get(c, '')) for c in cyc})
                as_ = sorted({str(gradeA.get(c, '')) for c in cyc})
                rows.append({
                    'BaselineVersionId': 'BLK-%s-%s-v1' % (m, tag),
                    '模型': m, '模型定义': MODEL_DEF[m][0], '代码位置': MODEL_DEF[m][1],
                    '口径Tag': tag, 'AsOfCount截断件数': N,
                    'AsOfTime口径': '按件数截断（progress=piece_idx/Life），非墙钟时刻',
                    'DatasetVersion': 'HI_merged_28(life_cycle 25~60) + P01_evidence_all60 + 台账 v3',
                    '预测来源Commit': src_pred, '输入数据Commit': src_hi,
                    '训练池规则': POOL_RULE,
                    '训练池实例(该格出现过)': ' ｜ '.join(pools[:3]) + (' …' if len(pools) > 3 else ''),
                    '测试刃口集': ','.join('c%d' % c for c in cyc),
                    '测试刀数_本表': n_own, '测试刀数_5400': int(a.n),
                    '其中可进正式真值评分': nok,
                    'ExposureDefinition': EXPOSURE, 'TargetDefinition': TARGET,
                    'LabelEvidenceLevel': 'B档=%s ｜ A档=%s ｜ 全批最高 E2（无独立物证列）' % ('/'.join(lvls), '/'.join(as_)),
                    'ExclusionReason': ('|S|<2 或可拟合<min_fit=2 → 不出预测；'
                                        '左截断/右删失/覆盖<0.95/件数差>8 的刀在严格口径已排除'),
                    'ModelConfig': CONFIG, 'MetricCodeVersion': METRIC_VER,
                    'n': int(a.n), 'MAE': round(float(a.MAE), 3),
                    'MedAE': a.MedAE, 'Bias': a.Bias, 'Over高估数': a.Over,
                    'Cover': a.Cover if pd.notna(a.Cover) else '无区间',
                    'PredictionValue位置': 'P0-C02_逐刀逐时点预测长表.csv 列「预测值」',
                    'ActualValue位置': '同上 列「真实剩余ActualRUL」（算不出时为 Unknown，见「失败原因」列）',
                    '冻结声明': FREEZE, '生成时间': now,
                })

    COLS = list(rows[0].keys())
    with open(OUT, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print('\n写出', OUT, len(rows), '行 ×', len(COLS), '列')
    print('三方对账（长表 ↔ 冻结表 ↔ 54-00）：一致 %d 格 / 不一致 %d 格' % (len(rows) - mism, mism))
    print('口径 × 模型 × 截断 组合数：%d（模型 %d × 口径 %d × 截断 %d）'
          % (len(rows), len(MODELS), len({r['口径Tag'] for r in rows}), len(CUTS)))
    # 各口径测试刀数
    print('\n各口径在 AsOfCount=20 的测试刀数与可评分刀数：')
    for tag in sorted({r['口径Tag'] for r in rows}):
        s = [r for r in rows if r['口径Tag'] == tag and r['AsOfCount截断件数'] == 20]
        if s:
            print('  %-14s 模型 %d 个，测试刀 %d 把，其中可进正式真值评分 %d 把'
                  % (tag, len(s), s[0]['测试刀数_本表'], s[0]['其中可进正式真值评分']))


if __name__ == '__main__':
    main()
