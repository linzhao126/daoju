# -*- coding: utf-8 -*-
"""s08_report.py — 生成 rul_casebook.md（逐刀叙事）与 metrics_digest.md（数字摘要）

只读上游 CSV，不重算模型，保证报告与 CSV 完全一致。
"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
OUT4 = os.path.abspath(os.path.join(HERE, '..', '04_RUL复现实验'))
OUT3 = os.path.abspath(os.path.join(HERE, '..', '03_原始数据与标签'))

B = pd.read_csv(os.path.join(OUT4, 'predictions_loco_baseline.csv'), encoding='utf-8-sig')
C = pd.read_csv(os.path.join(OUT4, 'predictions_loco_conditional.csv'), encoding='utf-8-sig')
MB = pd.read_csv(os.path.join(OUT4, 'metrics_baseline.csv'), encoding='utf-8-sig')
MC = pd.read_csv(os.path.join(OUT4, 'metrics_conditional.csv'), encoding='utf-8-sig')
MP = pd.read_csv(os.path.join(OUT4, 'metrics_paired.csv'), encoding='utf-8-sig')
SS = pd.read_csv(os.path.join(OUT4, 'metrics_samesample.csv'), encoding='utf-8-sig')
AUD = pd.read_csv(os.path.join(OUT3, 'life_audit_4152.csv'), encoding='utf-8-sig')


def to_md(df):
    """不依赖 tabulate 的 markdown 表"""
    cols = [str(c) for c in df.columns]
    out = ['| ' + ' | '.join(cols) + ' |', '|' + '---|' * len(cols)]
    for _, r in df.iterrows():
        out.append('| ' + ' | '.join('' if pd.isna(v) else str(v) for v in r.tolist()) + ' |')
    return '\n'.join(out)


lives = sorted(B.PhysicalLifeId.unique())
meta = {r.PhysicalLifeId: r for r in AUD.itertuples()}


def one_life(pid, life, prog, label, dq):
    b = B[B.PhysicalLifeId == pid].sort_values('CutoffN')
    c = C[C.PhysicalLifeId == pid].sort_values('CutoffN')
    m0 = float(b.M0_A_PredTotal.iloc[0])
    ratio = life / m0
    if ratio < 0.7:
        diag = '**短刀**（寿命/M0 = %.2f）：纯计数在任何截断点都高估剩余量，这正是失效风险的来源' % ratio
    elif ratio > 1.2:
        diag = '**长刀**（寿命/M0 = %.2f）：纯计数系统性低估，策略会提前扔掉大量可用寿命' % ratio
    else:
        diag = '接近中位（寿命/M0 = %.2f）：纯计数本身就准，任何模型都难超过它' % ratio
    lines = []
    lines.append('### %s\n' % pid)
    lines.append('- 程序 %s ｜ 对齐寿命 **%d 件** ｜ M0(同程序其余刀中位) **%.1f** ｜ 寿命/M0 = %.2f'
                 % (prog, life, m0, ratio))
    lines.append('- 标签 `%s` ｜ 数据质量 `%s` ｜ 台账原因：%s'
                 % (label, dq, getattr(meta.get(pid), 'ChangeReasonRaw', '') or '(无台账)'))
    lines.append('- %s\n' % diag)
    lines.append('| 截断N | M0-A RUL | M0-B RUL | M1-旧 RUL | 真值 | 误差 M0-A | 误差 M0-B | 误差 M1-旧 |')
    lines.append('|---|---|---|---|---|---|---|---|')
    for i in range(len(b)):
        r = b.iloc[i]
        cc = c[c.CutoffN == r.CutoffN].iloc[0] if len(c[c.CutoffN == r.CutoffN]) else None
        lines.append('| %d | %.1f | %s | %.1f | %d | %+.1f | %s | %+.1f |' % (
            r.CutoffN, r.M0_A_PredRUL,
            ('%.1f' % cc.M0B_PredRUL) if cc is not None else '—',
            r.M1_Old_PredRUL, r.ActualRemainingToReplacement,
            r.Err_M0_A,
            ('%+.1f' % (cc.M0B_PredRUL - r.ActualRemainingToReplacement)) if cc is not None else '—',
            r.Err_M1_Old))
    lines.append('')
    return '\n'.join(lines)


doc = ['# RUL 逐刀案例册（LOCO，固定截断）\n',
       '> 数值直接来自 `predictions_loco_baseline.csv` 与 `predictions_loco_conditional.csv`，未手抄。',
       '> **标签口径 = 距下一次人工换刀**；不是物理磨损失效。所有预测均为**事后回放**，不是前瞻日志。\n',
       '> 截断方式：固定第 20/40/60/80 件，与真实寿命无关；不满足 life>N 的刀在该截断点不参与（故样本数随 N 下降）。\n']
for pid in lives:
    r = B[B.PhysicalLifeId == pid].iloc[0]
    doc.append(one_life(pid, int(r.ActualReplacementLife), r.Program, r.LifeLabelType, r.DataQuality))

doc.append('---\n')
doc.append('## 汇总：为什么 M0-A 的误差长这样\n')
doc.append(to_md(MB))
doc.append('')
doc.append('## 汇总：条件生存基线与 M1-new\n')
doc.append(to_md(MC))
doc.append('')
doc.append('## 配对差与区间（bootstrap 重采样生命周期）\n')
doc.append(to_md(MP))
doc.append('')
doc.append('## 同样本对比（关键：M1-new 只在部分刀上有定义）\n')
doc.append(to_md(SS))
doc.append('')
open(os.path.join(OUT4, 'rul_casebook.md'), 'w', encoding='utf-8').write('\n'.join(doc))
print('[out] rul_casebook.md  %d 把刀' % len(lives))

# ---------------- 摘要（给 00 回执引用）
dig = ['# 数字摘要（供回执引用，全部由脚本从 CSV 生成）\n']
dig.append('## P0-03 复现原负结果\n')
dig.append(to_md(MB))
dig.append('\n## P0-04 条件生存 + M1-new\n')
dig.append(to_md(MC))
dig.append('\n## 配对差 95% 区间\n')
dig.append(to_md(MP))
dig.append('\n## 同样本对比\n')
dig.append(to_md(SS))
open(os.path.join(OUT4, 'metrics_digest.md'), 'w', encoding='utf-8').write('\n'.join(dig))
print('[out] metrics_digest.md')
