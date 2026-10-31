# -*- coding: utf-8 -*-
"""
刀具寿命标注工具
----------------
输入：
  1) 机床采集 CSV（influxdb 导出，单通道 L_ 或双通道 L_/R_ 均可）
  2) 刀具寿命记录表 CSV（列：刀塔,刀号,顺序号,StartTime,EndTime,刀具型号）
输出：
  在原数据基础上追加寿命标签列，另存为 <原名>_life_labeled.csv

新增标签列：
  life_cycle          生命周期序号(第几次从新刀到报废)
  tool_model          刀具型号
  life_used_pct_time  已用寿命% (按墙钟时间线性)
  life_remain_pct_time剩余寿命%
  rul_sec / rul_hour  剩余寿命(秒 / 小时)
  elapsed_sec         本周期内已过秒数
  life_used_pct_cut   已用寿命% (按实际切削时间累计，物理意义更强)
  cycle_cover_ratio   该周期在本文件中被覆盖的比例(1.0=完整覆盖)
  in_life_range       是否落在某个生命周期内(换刀间隙为 False)

用法：
  python label_tool_life.py <数据文件.csv> [--records tool_life_records.csv]
                            [--tool T1111] [--cutting-state 3] [--selftest]
"""
import argparse, os, re, sys
import pandas as pd, numpy as np
from datetime import datetime

pd.set_option('display.width', 240)
TIME_FMT = '%Y/%m/%d %H:%M'      # 保留作参考；解析已改用下面的宽松函数

# 台账 xlsx 里的时间写法不统一（已实测到 4 种），严格 strptime 碰到一条坏值就整批抛错。
# 这正是当年只导出前 38 行、丢掉 39~60 的根因（见《P0_修复留痕/R-0_台账修复报告.md》）。
_FULLWIDTH = {'：': ':', '／': '/', '－': '-', '　': ' '}
_TIME_FMTS = ('%Y/%m/%d %H:%M:%S', '%Y/%m/%d %H:%M', '%Y-%m-%d %H:%M:%S',
              '%Y-%m-%d %H:%M', '%Y/%m/%d/%H:%M', '%Y-%m-%d/%H:%M', '%Y-%m-%d', '%Y/%m/%d')


def parse_ledger_time(v):
    """宽松解析台账时间：全角→半角、多格式尝试、日与时粘连兜底。
    返回 pd.NaT 表示解析不了（由调用方汇总后**报错指名**，不默默丢行）。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return pd.NaT
    if isinstance(v, (pd.Timestamp, datetime)):
        return pd.Timestamp(v)
    s = re.sub(r'\s+', ' ', str(v).strip())
    for a, b in _FULLWIDTH.items():
        s = s.replace(a, b)
    if not s or s.lower() in ('none', 'nan'):
        return pd.NaT
    for fmt in _TIME_FMTS:
        try:
            return pd.Timestamp(datetime.strptime(s, fmt))
        except ValueError:
            pass
    # 兜底 1：日与时粘连，如 `2026/9/416:05` 实为 9-04 16:05（不是“9 月 41 日”）
    m = re.match(r'^(\d{4})[/-](\d{1,2})[/-](\d{3,4})[:](\d{2})$', s)
    if m:
        y, mo, dh, mi = (m.group(1), m.group(2), m.group(3), m.group(4))
        day, hh = (int(dh[0]), int(dh[1:])) if len(dh) == 3 else (int(dh[:2]), int(dh[2:]))
        try:
            return pd.Timestamp(datetime(int(y), int(mo), day, hh, int(mi)))
        except ValueError:
            return pd.NaT
    # 兜底 2：分隔符混用，如 `2026-10-26/9:20`
    m = re.match(r'^(\d{4})[/-](\d{1,2})[/-](\d{1,2})[ /](\d{1,2}):(\d{2})', s)
    if m:
        try:
            return pd.Timestamp(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                                         int(m.group(4)), int(m.group(5))))
        except ValueError:
            return pd.NaT
    return pd.NaT


def pick(df, names):
    """返回 df 中第一个存在的列名"""
    for n in names:
        if n in df.columns:
            return n
    return None


def read_records_any(path):
    """台账表可读 CSV 也可读 xlsx（xlsx 取第一个 sheet）。"""
    if path.lower().endswith(('.xlsx', '.xlsm')):
        return pd.read_excel(path, sheet_name=0)
    return pd.read_csv(path, encoding='utf-8-sig')


def load_records(path):
    rec = read_records_any(path)
    rec.columns = [str(c).strip() for c in rec.columns]
    if '刀号' not in rec.columns or '顺序号' not in rec.columns:
        raise SystemExit('台账表缺必需列（需 刀号/顺序号/StartTime/EndTime）：%s → 实有列 %s'
                         % (path, list(rec.columns)))
    # 实测到 ` T1111`（带前导空格）：不归一会被下面的等值过滤默默漏掉整条
    rec['刀号'] = rec['刀号'].astype(str).str.upper().str.replace(r'\s+', '', regex=True).str.strip()
    rec['_start'] = rec['StartTime'].map(parse_ledger_time)
    rec['_end'] = rec['EndTime'].map(parse_ledger_time)
    # 开始时解析失败 = 这条周期无法定位 → **指名报错**，不静默丢行（静默丢行就是当年 38 行的成因）
    bad = rec[rec['_start'].isna()]
    if len(bad):
        raise SystemExit('台账 %s 有 %d 条 StartTime 无法解析（顺序号 %s）；'
                         'EndTime 为空属正常（末条开式区间），不视为错误。'
                         % (os.path.basename(path), len(bad), bad['顺序号'].tolist()))
    return rec


def detect_channel(df):
    """返回 (tool_col, state_col, load_col, channel_name)"""
    if 'L_tool_number' in df.columns:
        return ('L_tool_number', pick(df, ['L_state']), pick(df, ['L_spindle_load']), 'L')
    if 'R_tool_number' in df.columns:
        return ('R_tool_number', pick(df, ['R_state']), pick(df, ['R_spindle_load']), 'R')
    return (None, None, None, None)


# ---------------- 核心标注 ----------------
def label_life(df, rec, tool_no, cutting_state=3):
    df = df.copy()
    df['_t'] = pd.to_datetime(df['_time'], errors='coerce')
    df = df.sort_values('_t').reset_index(drop=True)

    tool_col, state_col, load_col, ch = detect_channel(df)
    if tool_col is None:
        raise SystemExit("错误：数据中找不到 L_tool_number 或 R_tool_number 列")

    # 新增标签列(按类型分别初始化，避免字符串赋给 float 列报错)
    num_cols = ['life_used_pct_time', 'life_remain_pct_time', 'rul_sec', 'rul_hour',
                'elapsed_sec', 'life_used_pct_cut', 'cycle_cover_ratio']
    for c in num_cols:
        df[c] = np.nan
    df['life_cycle'] = pd.array([pd.NA] * len(df), dtype='Int64')
    df['tool_model'] = pd.Series([None] * len(df), dtype='object')
    df['in_life_range'] = False
    df['change_reason'] = pd.Series([None] * len(df), dtype='object')
    df['real_failure'] = pd.Series([None] * len(df), dtype='object')

    tool_no = str(tool_no).upper().strip()
    # 规范化记录表(若尚未解析时间列则就地解析，保证被调用方直接传 DataFrame 也能用)
    sub = rec.copy()
    if '_start' not in sub.columns:
        sub['_start'] = sub['StartTime'].map(parse_ledger_time)
    if '_end' not in sub.columns:
        sub['_end'] = sub['EndTime'].map(parse_ledger_time)
    sub['刀号'] = sub['刀号'].astype(str).str.upper().str.replace(r'\s+', '', regex=True).str.strip()
    sub = sub[sub['刀号'] == tool_no].sort_values('顺序号')
    if len(sub) == 0:
        print("!! 寿命记录表中找不到刀具 %s" % tool_no)
        return df, tool_col, ch

    is_tool = df[tool_col].astype(str).str.upper().str.strip() == tool_no
    tool_idx = df.index[is_tool]
    print("文件中 %s 的行数: %d / %d" % (tool_no, len(tool_idx), len(df)))

    # 切削判定(用于切削时间口径)
    if state_col is not None and load_col is not None:
        cutting = (df[state_col] == cutting_state) & (df[load_col].fillna(0) > 0)
    elif state_col is not None:
        cutting = (df[state_col] == cutting_state)
    else:
        cutting = pd.Series(False, index=df.index)

    dt = df['_t'].diff().dt.total_seconds().fillna(0)

    tmax = df['_t'].max()
    rows_report = []
    for _, r in sub.iterrows():
        cid, st, en, model = int(r['顺序号']), r['_start'], r['_end'], r['刀具型号']
        reason = r.get('换刀原因') if '换刀原因' in sub.columns else None
        fail = r.get('是否真实失效') if '是否真实失效' in sub.columns else None
        open_ended = pd.isna(en)            # 结束时间为空 = 刀具仍在用(开放式区间)
        en_eff = en if not open_ended else tmax
        in_cycle = is_tool & (df['_t'] >= st) & (df['_t'] <= en_eff)
        n = int(in_cycle.sum())
        dur = (en_eff - st).total_seconds()

        if n == 0:
            rows_report.append((cid, st, en_eff, 0, 0.0, False, open_ended))
            continue

        idx = df.index[in_cycle]
        tsub = df.loc[idx, '_t']
        cover = (tsub.max() - tsub.min()).total_seconds() / dur if dur > 0 else 0.0
        cover = min(cover, 1.0)
        full = cover >= 0.95

        # 墙钟线性口径
        elapsed = (tsub - st).dt.total_seconds()
        df.loc[idx, 'life_cycle'] = cid
        df.loc[idx, 'tool_model'] = model
        df.loc[idx, 'change_reason'] = reason
        df.loc[idx, 'real_failure'] = fail
        df.loc[idx, 'elapsed_sec'] = elapsed
        df.loc[idx, 'life_used_pct_time'] = (elapsed / dur * 100).clip(0, 100)
        df.loc[idx, 'life_remain_pct_time'] = 100 - (elapsed / dur * 100).clip(0, 100)
        df.loc[idx, 'rul_sec'] = (en_eff - tsub).dt.total_seconds()
        df.loc[idx, 'rul_hour'] = df.loc[idx, 'rul_sec'] / 3600.0
        df.loc[idx, 'cycle_cover_ratio'] = cover
        df.loc[idx, 'in_life_range'] = True

        # 切削时间口径(仅周期被完整覆盖时才有意义)
        if full:
            cut_dt = (cutting.loc[idx].astype(float) * dt.loc[idx])
            cum = cut_dt.cumsum()
            total = cum.iloc[-1]
            if total > 0:
                df.loc[idx, 'life_used_pct_cut'] = (cum / total * 100).clip(0, 100)

        rows_report.append((cid, st, en_eff, n, cover, full, open_ended))

    print("\n=== 各生命周期标注结果 ===")
    print("%4s %-18s %-18s %9s %8s %s" % ("序号", "开始", "结束", "命中行数", "覆盖率", "完整"))
    for cid, st, en, n, cover, full, open_ended in rows_report:
        tag = "进行中(无结束)" if open_ended else ("是" if full else "否")
        print("%4d %-18s %-18s %9d %7.1f%% %s"
              % (cid, str(st)[:16], str(en)[:16], n, cover * 100, tag))

    unlabeled = is_tool & (~df['in_life_range'])
    print("\n%s 落在所有周期之外(换刀间隙/未记录时段)的行数: %d"
          % (tool_no, int(unlabeled.sum())))
    return df, tool_col, ch


# ---------------- 自检 ----------------
def selftest():
    print("=== 自检：用合成数据验证标注逻辑 ===")
    rng = pd.date_range("2026-08-27 10:25:00", "2026-08-27 21:45:00", freq='30s')
    n = len(rng)
    syn = pd.DataFrame({
        '_time': rng.strftime('%Y-%m-%d %H:%M:%S.%f').str[:-3],
        'L_tool_number': ['T1111'] * n,
        'L_state': [3] * n,
        'L_spindle_load': [50] * n,
        'L_allnum': range(1000, 1000 + n),
    })
    rec = pd.DataFrame({
        '刀塔': ['T11'], '刀号': ['T1111'], '顺序号': [7],
        'StartTime': ['2026/8/27 10:25'], 'EndTime': ['2026/8/27 21:45'],
        '刀具型号': ['B1340'],
    })
    out, tc, ch = label_life(syn, rec, 'T1111', cutting_state=3)
    print("\n合成数据 n=%d，通道=%s，刀具列=%s" % (len(out), ch, tc))
    print("\n前3行标签：")
    print(out[['_time', 'life_cycle', 'life_used_pct_time', 'life_remain_pct_time',
               'rul_hour', 'life_used_pct_cut']].head(3).to_string(index=False))
    print("\n末3行标签：")
    print(out[['_time', 'life_cycle', 'life_used_pct_time', 'life_remain_pct_time',
               'rul_hour', 'life_used_pct_cut']].tail(3).to_string(index=False))
    ok = (out['life_used_pct_time'].iloc[0] < 1 and
          out['life_used_pct_time'].iloc[-1] > 99 and
          out['rul_sec'].iloc[-1] <= 30)
    print("\n自检结果:", "通过 (首行约0%，末行约100%，RUL 递减到 0)" if ok else "未通过")
    return ok


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("datafile", nargs='?', help="采集数据 CSV")
    ap.add_argument("--records", default=None,
                    help="台账表（CSV 或 xlsx）；不传则按优先级自动取"
                         "手工提供文件/tool_life_records_v3.csv → 三厂换刀记录.xlsx → tool_life_records.csv")
    ap.add_argument("--tool", default=None, help="要标注的刀号，默认取记录表第一把刀")
    ap.add_argument("--cutting-state", type=int, default=3, help="表示切削的 L_state 值，默认 3")
    ap.add_argument("--selftest", action="store_true", help="用合成数据自检标注逻辑")
    a = ap.parse_args()

    if a.selftest or not a.datafile:
        selftest()
        return

    WS = os.path.dirname(os.path.abspath(__file__))
    data_p = a.datafile if os.path.isabs(a.datafile) else os.path.join(WS, a.datafile)

    if a.records:
        rec_p = a.records if os.path.isabs(a.records) else os.path.join(WS, a.records)
    else:
        # 优先级：修复后的 60 条 v3 → xlsx 原件 → 仓库旧副本（38 行，会丢 22 个周期）
        cands = []
        for wsd in (WS, os.path.abspath(os.path.join(WS, '..'))):
            cands += [
                os.path.join(wsd, '手工提供文件', 'tool_life_records_v3.csv'),
                os.path.join(wsd, '手工提供文件', '三厂换刀记录.xlsx'),
                os.path.join(wsd, '15_台账真值检验', 'input', '三厂换刀记录.xlsx'),
                os.path.join(wsd, '20261027', '15_台账真值检验', 'input', '三厂换刀记录.xlsx'),
                os.path.join(wsd, 'tool_life_records.csv'),
            ]
        rec_p = next((p for p in cands if os.path.exists(p)), None)
        if rec_p is None:
            raise SystemExit('找不到台账表：试过\n  ' + '\n  '.join(cands))
        if os.path.basename(rec_p) == 'tool_life_records.csv':
            print('!! 警告：用的是仓库旧副本 %s（实测只有 38 行，会丢 22 个周期）。\n'
                  '   请优先放 手工提供文件/tool_life_records_v3.csv（60 条）或用 --records 指定。' % rec_p)

    print("数据文件:", data_p)
    print("寿命记录表:", rec_p)
    df = pd.read_csv(data_p, encoding="utf-8-sig", low_memory=False)
    rec = load_records(rec_p)
    print("\n寿命记录表共 %d 条，刀具: %s" % (len(rec), rec['刀号'].unique().tolist()))
    print("时间跨度: %s → %s" % (df['_time'].iloc[0], df['_time'].iloc[-1]))

    tool = a.tool or str(rec['刀号'].iloc[0])
    out, tc, ch = label_life(df, rec, tool, a.cutting_state)

    base = os.path.splitext(os.path.basename(data_p))[0]
    out_p = os.path.join(WS, "%s_%s_life_labeled.csv" % (base, tool))
    out.to_csv(out_p, index=False, encoding="utf-8-sig")
    print("\n已保存: %s" % out_p)

    # 标注统计
    lab = out[out['in_life_range'] == True]
    print("\n=== 标注汇总 ===")
    print("总行数: %d ；已标注寿命的行数: %d" % (len(out), len(lab)))
    if len(lab):
        print("覆盖生命周期: %s" % sorted(lab['life_cycle'].dropna().unique().tolist()))
        print("life_used_pct_time 范围: %.2f ~ %.2f"
              % (lab['life_used_pct_time'].min(), lab['life_used_pct_time'].max()))
        if lab['life_used_pct_cut'].notna().any():
            print("life_used_pct_cut  范围: %.2f ~ %.2f"
                  % (lab['life_used_pct_cut'].min(), lab['life_used_pct_cut'].max()))
        print("rul_hour 范围: %.2f ~ %.2f"
              % (lab['rul_hour'].min(), lab['rul_hour'].max()))


if __name__ == "__main__":
    main()
