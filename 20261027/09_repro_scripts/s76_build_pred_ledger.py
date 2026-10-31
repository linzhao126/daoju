# -*- coding: utf-8 -*-
"""R-5 逐刀逐时点预测长表（任务书 §4-P0-C02 第 4 条）

任务书原话：
  "提交逐刀逐时点输出，包括训练池ID、测试刃口ID、截至该时点的输入、实际终止原因、
   预测值、误差、失败原因、样本等级、实验Commit和时间。"

一行 = 一个 (口径, 模型, 截断件数 N, 测试刃口) 的四元组合，共 12+ 栏覆盖上述十项要求。

**关键设计决定：不重造模型。**
预测值全部复用 `s53_r04_grade_split.py` 已交付的 `R04_pred_split_detail.csv`（142 行 OK）
与 `R04_pred_missing_reason.csv`（42 行算不出），本脚本只做三件事：
  1) 把宽表 M0A/M0B/M1Old/M1New 拆长（一行一个模型，便于逐行核对与重新算 MAE）；
  2) 从 `HI_merged_28.csv` 回捞"截至第 N 件时模型看得到的输入"（progress、HI_comb 当前值、
     HI_comb 累计均值），让审核方能逐行验证"有没有用未来数据"；
  3) 挂上 R-8 的双维度样本等级（A 档件数一致性 / B 档原因可信度）与实际终止类型。

输出：P0_数据与代码移交/P0-C02_逐刀逐时点预测长表.csv
"""
import csv
import os
import subprocess
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
WORK = os.path.abspath(os.path.join(BASE, '..', '..'))
DETAIL = os.path.join(BASE, '附件_证据', '54_R04_分级输出', 'R04_pred_split_detail.csv')
MISSING = os.path.join(BASE, '附件_证据', '54_R04_分级输出', 'R04_pred_missing_reason.csv')
HI = os.path.join(BASE, '24_程序归属修正', 'HI_merged_28.csv')
P01 = os.path.join(BASE, '34_P0-01收尾', 'P01_evidence_all60.csv')
DUAL = os.path.join(BASE, 'P0_数据与代码移交', 'P0-D05_双维度证据等级.csv')
OUT = os.path.join(BASE, 'P0_数据与代码移交', 'P0-C02_逐刀逐时点预测长表.csv')

MODELS = ['M0A', 'M0B', 'M1Old', 'M1New']
MODEL_DESC = {
    'M0A': '历史同类寿命中位数−已用量（同程序，最低复杂度统计参照）',
    'M0B': '条件生存：只用截至当前已存活刀龄的剩余寿命分布',
    'M1Old': '1 号位 HI 修正（旧程序归属口径）',
    'M1New': '1 号位 HI 修正（新程序归属口径）',
}

COLS = ['口径Tag', '模型', '模型定义', '截断件数CutoffN', '测试刃口ID', '台账顺序号', '程序',
        '训练池ID', '训练刀数',
        '输入_已用件数', '输入_已用寿命占比', '输入_HI_comb当前件', '输入_HI_comb累计均值',
        '完整寿命Life', '真实剩余ActualRUL', '预测值', '绝对误差', '区间下沿', '区间上沿', '落入区间',
        '实际终止类型', '终止原因可信度B档', '件数一致性A档', '可进正式真值评分',
        '状态', '失败原因',
        '预测来源Commit', '输入数据Commit', '装配Commit', 'DatasetVersion', '生成时间']


def git_sha(path):
    try:
        r = subprocess.run(['git', '-C', path, 'rev-parse', '--short', 'HEAD'],
                           capture_output=True, text=True, timeout=20)
        return (r.stdout or '').strip() or 'unknown'
    except Exception:
        return 'unknown'


def git_last_touch(repo, relpath):
    """某产物最后一次被提交的短 sha——用于说清“预测值是哪个代码状态跑出来的”。"""
    try:
        r = subprocess.run(['git', '-C', repo, 'log', '--format=%h', '-1', '--', relpath],
                           capture_output=True, text=True, timeout=30)
        return (r.stdout or '').strip() or 'unknown'
    except Exception:
        return 'unknown'


def main():
    det = pd.read_csv(DETAIL, encoding='utf-8-sig')
    mis = pd.read_csv(MISSING, encoding='utf-8-sig')
    hi = pd.read_csv(HI, encoding='utf-8-sig')
    p01 = pd.read_csv(P01, encoding='utf-8-sig')
    dual = pd.read_csv(DUAL, encoding='utf-8-sig')

    commit = git_sha(BASE)
    src_pred = git_last_touch(BASE, ':/20261027/附件_证据/54_R04_分级输出/R04_pred_split_detail.csv')
    src_hi = git_last_touch(BASE, ':/20261027/24_程序归属修正/HI_merged_28.csv')
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    dataset = 'HI_merged_28(life_cycle 25~60, n=%d件行) + P01_evidence_all60' % len(hi)
    print('detail %d 行 / missing %d 行 / HI %d 行 / 装配commit=%s  预测来源=%s  输入来源=%s'
          % (len(det), len(mis), len(hi), commit, src_pred, src_hi))

    # —— 索引：台账顺序号 → 终止类型 & 双维度等级 ——
    form = {int(r.Cycle): r.Form for r in p01.itertuples()}
    dd = {int(r.顺序号Cycle): (r.维度A_件数一致性, r.维度B_原因可信度, r.可进正式真值评分)
          for r in dual.itertuples()}
    END = {'破损': '崩刃/破损（台账文字，未独立核实）', '磨损': '正常磨损达工艺极限（台账文字，未独立核实）'}

    # —— 输入摘要：某刀第 N 件时的 progress / HI_comb ——
    hi_by = {c: g for c, g in hi.groupby('life_cycle')}

    def input_at(cyc, n):
        g = hi_by.get(cyc)
        if g is None or g.empty:
            return ('', '', '', '')
        g = g.sort_values('piece_idx')
        upto = g[g['piece_idx'] <= n]
        if upto.empty:
            return ('', '', '', '')
        cur = upto.iloc[-1]
        prog = cur.get('progress')
        hc_at = cur.get('HI_comb')
        hc_mean = upto['HI_comb'].mean() if upto['HI_comb'].notna().any() else np.nan
        return (int(upto['piece_idx'].max()),
                round(float(prog), 4) if pd.notna(prog) else '',
                round(float(hc_at), 4) if pd.notna(hc_at) else '',
                round(float(hc_mean), 4) if pd.notna(hc_mean) else '')

    rows = []
    # —— 有预测的行：宽表拆长 ——
    for r in det.itertuples():
        cyc = int(r.Cycle)
        a_grade, b_grade, can = dd.get(cyc, ('A_NOCOMP', 'E3_UNKNOWN', 'NO'))
        used, prog, hc_at, hc_mean = input_at(cyc, int(r.CutoffN))
        for m in MODELS:
            pred = getattr(r, m)
            abserr = getattr(r, 'Abs' + m)
            rows.append({
                '口径Tag': r.Tag, '模型': m, '模型定义': MODEL_DESC[m],
                '截断件数CutoffN': int(r.CutoffN),
                '测试刃口ID': '4152-L-T1111-c%d' % cyc, '台账顺序号': cyc,
                '程序': int(r.Program) if pd.notna(r.Program) else '',
                '训练池ID': r.TrainIds, '训练刀数': int(r.TrainN),
                '输入_已用件数': used, '输入_已用寿命占比': prog,
                '输入_HI_comb当前件': hc_at, '输入_HI_comb累计均值': hc_mean,
                '完整寿命Life': int(r.Life) if pd.notna(r.Life) else '',
                '真实剩余ActualRUL': int(r.ActualRUL) if pd.notna(r.ActualRUL) else '',
                '预测值': round(float(pred), 4) if pd.notna(pred) else '',
                '绝对误差': round(float(abserr), 4) if pd.notna(abserr) else '',
                '区间下沿': r.PI_lo if pd.notna(r.PI_lo) else '',
                '区间上沿': r.PI_hi if pd.notna(r.PI_hi) else '',
                '落入区间': ('是' if int(r.InPI) == 1 else '否') if pd.notna(r.InPI) else '无区间',
                '实际终止类型': END.get(form.get(cyc, ''), '未知（台账未填原因）'),
                '终止原因可信度B档': b_grade, '件数一致性A档': a_grade,
                '可进正式真值评分': can,
                '状态': 'OK', '失败原因': '',
                '预测来源Commit': src_pred, '输入数据Commit': src_hi,
                '装配Commit': commit, 'DatasetVersion': dataset, '生成时间': now,
            })

    # —— 算不出的行：一行一个（口径, 截断, 刀），模型列记"未产出" ——
    for r in mis.itertuples():
        cyc = int(r.Cycle)
        a_grade, b_grade, can = dd.get(cyc, ('A_NOCOMP', 'E3_UNKNOWN', 'NO'))
        used, prog, hc_at, hc_mean = input_at(cyc, int(r.CutoffN))
        rows.append({
            '口径Tag': r.Tag, '模型': '（四个模型均未产出）', '模型定义': '同程序且寿命>N 的训练刀不足 min_fit=2',
            '截断件数CutoffN': int(r.CutoffN),
            '测试刃口ID': '4152-L-T1111-c%d' % cyc, '台账顺序号': cyc, '程序': '',
            '训练池ID': '', '训练刀数': 0,
            '输入_已用件数': used, '输入_已用寿命占比': prog,
            '输入_HI_comb当前件': hc_at, '输入_HI_comb累计均值': hc_mean,
            '完整寿命Life': '', '真实剩余ActualRUL': '',
            '预测值': '', '绝对误差': '', '区间下沿': '', '区间上沿': '', '落入区间': '',
            '实际终止类型': END.get(form.get(cyc, ''), '未知（台账未填原因）'),
            '终止原因可信度B档': b_grade, '件数一致性A档': a_grade,
            '可进正式真值评分': can,
            '状态': '算不出', '失败原因': r.Reason,
            '预测来源Commit': src_pred, '输入数据Commit': src_hi,
            '装配Commit': commit, 'DatasetVersion': dataset, '生成时间': now,
        })

    with open(OUT, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, '') for c in COLS})
    print('\n写出', OUT, len(rows), '行', len(COLS), '列')

    # —— 自检 ——
    ok = [r for r in rows if r['状态'] == 'OK']
    bad = [r for r in rows if r['状态'] != 'OK']
    withinput = sum(1 for r in rows if r['输入_已用件数'] != '')
    print('\n=== 自检 ===')
    print('有预测 %d 行 / 算不出 %d 行 / 合计 %d' % (len(ok), len(bad), len(rows)))
    print('已填"截至该时点输入"的行：%d / %d（缺的应为该刀未进 HI 管道）' % (withinput, len(rows)))
    print('测试刃口数：%d   截断档位：%s' % (len({r['台账顺序号'] for r in rows}),
          sorted({r['截断件数CutoffN'] for r in rows})))
    print('口径：%s' % sorted({r['口径Tag'] for r in rows}))
    # 逐格 MAE 复算：按 (口径, 截断, 模型) 重算并与 54-00 汇总表逐格对账
    # （自证这张表能被独立重算，不只是拄数字）
    agg = pd.read_csv(os.path.join(BASE, '附件_证据', '54_R04_分级输出', 'R04_pred_split.csv'),
                      encoding='utf-8-sig')
    ref = {(r.Tag, int(r.CutoffN), r.Model): (int(r.n), float(r.MAE)) for r in agg.itertuples()}
    mine = {}
    for r in ok:
        if r['绝对误差'] == '':
            continue
        k = (r['口径Tag'], r['截断件数CutoffN'], r['模型'])
        mine.setdefault(k, []).append(float(r['绝对误差']))
    diff = same = miss_ref = 0
    for k, v in sorted(mine.items()):
        if k not in ref:
            miss_ref += 1
            continue
        n_ref, mae_ref = ref[k]
        mae_own = sum(v) / len(v)
        if n_ref == len(v) and abs(mae_ref - mae_own) < 0.02:
            same += 1
        else:
            diff += 1
            print('   ★ 不一致 %s 本表 n=%d MAE=%.2f  vs  54-00 n=%d MAE=%.2f'
                  % (k, len(v), mae_own, n_ref, mae_ref))
    print('\nMAE 逐格对账：一致 %d / 不一致 %d / 54-00 无此格 %d（共 %d 格）'
          % (same, diff, miss_ref, len(mine)))
    # 泄漏自查：输入用的件数必须 == 截断件数（不能看了未来）
    leak = [r for r in rows if r['输入_已用件数'] != '' and int(r['输入_已用件数']) > r['截断件数CutoffN']]
    print('\n输入超过截断点（潜在未来泄漏）的行：%d' % len(leak))


if __name__ == '__main__':
    main()
