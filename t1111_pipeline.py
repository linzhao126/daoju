# -*- coding: utf-8 -*-
r"""
t1111_pipeline.py —— 设备 4152 / L 刀塔 / 刀具 T1111 的【唯一】分析程序
=====================================================================
以后关于这把刀的所有分析、调整、新需求，都只改这个文件。不要再新建脚本。

子命令
------
  scan     扫描工作区：哪些文件含 T1111、时间范围、行数（换数据文件后先跑它）
           加 --file 只查指定文件；不加则扫全部 CSV
  mark     换刀周期打标 + 新列体检（有没有冒出 TCMD/DTRQ/ERR 类列）
           默认读入源数据【全部列】，输出 CSV 不缺源字段；
           加 --cols need 可只读分析所需列以省内存（但输出会缺列）
  feat     构建「调用级特征表」——一次调用一行，是 stats / trend 的输入
  stats    按主程序统计：工艺参数、单位消耗、累计载荷能否解释寿命差异
  trend    退化趋势检验：单调爬升(Spearman) + 末期突变(前20% vs 后20%) + 出图
  window   行级滑窗特征：在打标后的原始行上算 1/3/5/10 分钟窗口的
           均值/最大/最小/标准差/峰峰值/RMS/峭度/斜率/突跳次数/裕度因子/峰值因子
           （--freq 追加频域：频段能量占比 + 谱质心。注意 62.5Hz 采样 → 上限仅 31Hz）
           输出 t1111_window_feat_full.csv：原始全部列（保持原顺序）+ 特征列追加末尾。
           滑窗逐行计算，特征天然与所在行对齐，按行标签写回，不新增任何 ID 列；
           未参与计算的行（停机/非切削）特征留空。合并前后均做 4 项校验
  xalign   相位对齐磨损检验：按「进给轴」把每件切断曲线重采样到同一网格，
           消掉 G96 恒线速下的几何主项，再看各指标是否随刀龄单调上升。
           切断是径向进给 → 对齐 X；车端面/纵车是轴向 → 改对齐 Z。
           输出 xalign_HI.csv（逐件健康指数）+ xalign.txt + 队列曲线图
  alarm    无监督异常事件检测（委托给已验证的通用工具 alarm_online.py）
  archive  把历史上散落的 T1111 脚本归档到 _archive_4152_T1111_脚本/
  all      串跑 mark → feat → stats → trend（日常最常用）

最常用（先看这里）
----------------
本项目实际环境是 Windows + PowerShell，逐行粘贴即可：
  cd c:\abc\workBoddyHome刀具
  $env:PYTHONUTF8 = "1"
  .\.venv\Scripts\python.exe -X utf8 -W ignore t1111_pipeline.py all
  .\.venv\Scripts\python.exe -X utf8 -W ignore t1111_pipeline.py all --file "20260914_4152\influxdb_FanucNormalData_20260914_081959.csv"
  .\.venv\Scripts\python.exe -X utf8 -W ignore t1111_pipeline.py trend

Windows cmd.exe：
  cd /d c:\abc\workBoddyHome刀具
  set PYTHONUTF8=1
  .venv\Scripts\python.exe -X utf8 -W ignore t1111_pipeline.py all

Linux / Git Bash（文件头老写法，等价）：
  PYTHONUTF8=1 ./.venv/Scripts/python.exe -X utf8 -W ignore t1111_pipeline.py all

查看全部参数：
  .\.venv\Scripts\python.exe t1111_pipeline.py -h

调用通用格式
------------
  python t1111_pipeline.py <子命令> [选项]
  <子命令> 必填（不给会报错）；选项顺序随意。

路径规则（重要）
----------------
  * 工作目录 = 本脚本所在目录（c:\abc\workBoddyHome刀具），不是 shell 的当前目录；
  * --file / --labeled 可给相对路径（相对工作目录）或绝对路径；
  * 不给 --file 时，自动挑「最新的 influxdb_*4152*.csv」（按修改时间倒序取第一个）。
    工作区有多个数据文件时容易挑错，日常建议总是显式带上 --file。

每个子命令的调用例子与产物
--------------------------
(1) scan —— 只看不写数据，先摸清哪个文件含 T1111
      python t1111_pipeline.py scan
      python t1111_pipeline.py scan --file 20260914_4152/influxdb_FanucNormalData_20260914_081959.csv
    产物: out_T1111/scan.txt（文件 / T1111行数 / 总行数 / 时间范围）

(2) mark —— 从原始全机床 CSV 抽出 T1111 行并打换刀周期标签（换新数据文件后先跑它）
      python t1111_pipeline.py mark --file 20260914_4152/influxdb_FanucNormalData_20260914_081959.csv
      python t1111_pipeline.py mark --file 20260914_4152/xxx.csv --keep all    # 保留全部刀具
      python t1111_pipeline.py mark --file 20260914_4152/xxx.csv --cols need  # 省内存(输出会缺列)
    产物: out_T1111/<数据文件名>_life_labeled.csv（--keep all 时多一个 _all 后缀）
    新增列: life_cycle / life_used_pct_time / life_used_pct_cut / change_reason / real_failure
    依赖: label_tool_life.py 与 tool_life_records.csv

(3) feat —— 构建「调用级特征表」：一次调用一行（stats/trend 的输入，带缓存）
      python t1111_pipeline.py feat
      python t1111_pipeline.py feat --rebuild      # 强制重算，忽略缓存
    产物: out_T1111/t1111_calls.csv
    注意: 缓存不记录来源数据文件；换了数据文件务必加 --rebuild，否则会静默复用旧结果。

(4) stats —— 按主程序统计：工艺参数、单位消耗、累计载荷能否解释寿命差异
      python t1111_pipeline.py stats
      python t1111_pipeline.py stats --rebuild
    产物: out_T1111/stats.txt

(5) trend —— 退化趋势检验（Spearman 单调爬升 + 末期突变）+ 出图
      python t1111_pipeline.py trend
    产物: out_T1111/trend.txt 与 out_T1111/t1111_趋势图.png

(6) window —— 行级滑窗特征（最重的一步；多窗口时域特征，可选频域）
      python t1111_pipeline.py window
      python t1111_pipeline.py window --file 20260914_4152/xxx.csv
      python t1111_pipeline.py window --labeled out_T1111/xxx_life_labeled.csv
      python t1111_pipeline.py window --ch L_spindle_load,L_X_servo_load   # 只算 2 个通道(省时)
      python t1111_pipeline.py window --freq                              # 追加频域特征(慢)
      python t1111_pipeline.py window --parquet                           # 输出 parquet(更小)
      python t1111_pipeline.py window --cutrows                           # 只输出参与计算的行
    产物: out_T1111/t1111_window_feat_full.csv（--parquet 则为 .parquet）
    输入: 默认复用最新打标文件；用 --file 时自动匹配同名打标文件。

(7) xalign —— 相位对齐磨损检验（按 X 直径对齐切断曲线，消掉几何主项）
      python t1111_pipeline.py xalign
      python t1111_pipeline.py xalign --metrics L_spindle_load,L_X_servo_load
      python t1111_pipeline.py xalign --grid 60 --basek 10 --dropcap
    产物: out_T1111/xalign_HI.csv + xalign.txt + 队列曲线图

(8) alarm —— 本身不干活，只打印正确的调用命令（真正打分的是 alarm_online.py）
      python t1111_pipeline.py alarm
      python t1111_pipeline.py alarm --file 20260914_4152/xxx.csv
    产物: out_T1111/alarm.txt
    提醒: 真正打分去跑 alarm_online.py，其参数是 --csv（不是 --file）：
      .\.venv\Scripts\python.exe -X utf8 -W ignore alarm_online.py fit
      .\.venv\Scripts\python.exe -X utf8 -W ignore alarm_online.py score --csv out_T1111\xxx.csv --out-prefix out_T1111\xxx

(9) archive —— 把散落的旧 T1111 脚本移入 _archive_4152_T1111_脚本/
      python t1111_pipeline.py archive
    产物: out_T1111/archive.txt

(10) all —— 串跑 mark → feat → stats → trend（日常最常用）
      python t1111_pipeline.py all
      python t1111_pipeline.py all --file 20260914_4152/xxx.csv
    产物: 汇总写入 out_T1111/all.txt（all 模式下各子命令 txt 不单独落盘）

选项一览（按用途分组）
----------------------
  数据文件与范围:  --file <路径>         指定数据文件(默认自动取最新)
                   --labeled <路径>      window 专用：指定已打标 CSV
                   --keep tool|all        mark 保留范围，默认 tool=只留 T1111
                   --cols all|need        mark 读入列范围，默认 all(输出不缺列)
  重算与输出:      --rebuild              feat 强制重算调用特征表
                   --ch A,B               window 参与通道，覆盖 WIN_CH
                   --freq                 window 追加频域特征(慢)
                   --parquet              window 输出 parquet
                   --cutrows              window 只输出参与计算的行
  xalign 专项:     --metrics A,B          待检指标，覆盖 XALIGN_METRICS
                   --grid N               X 网格点数
                   --basek N              新刀基线件数
                   --dropcap              剔除主轴转速封顶段(G96 失效区)

推荐执行顺序（换新数据文件时）
------------------------------
  1. scan                → 确认新文件确实含 T1111、有多少行
  2. all --file <新文件> → 打标 + 调用特征 + 统计 + 趋势（先看 out_T1111/trend.txt 的结论）
  3. window --file <新文件> → 需要行级特征做建模/异常检测时才跑（最慢）
  4. xalign（可选）      → 想验证「对齐口径下是否真无信号」时跑
  5. alarm               → 按打印出的命令去跑 alarm_online.py

依赖与常见坑
------------
  * 依赖: scipy(trend/xalign 的 Spearman)、matplotlib(trend 出图)、
          pyarrow(仅 --parquet)、label_tool_life.py + tool_life_records.csv(mark 必需)
  * 中文乱码 → 必须带 -X utf8（或先设 PYTHONUTF8=1）
  * 无参数直接运行会因缺少子命令报错，至少要给一个子命令
  * "PYTHONUTF8=1 ./..." 是 Linux/Git Bash 写法；PowerShell 要用 $env:PYTHONUTF8="1"

输出目录： out_T1111/
"""
import os, sys, shutil, argparse, glob as globmod
import numpy as np
import pandas as pd

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORKSPACE)

# ==================================================================
#  配 置 区 —— 改这里就够了
# ==================================================================
DEVICE   = '4152'
TOWER    = 'L'                 # 刀塔通道：L / R
TOOL     = 'T1111'

RECORDS  = os.path.join(WORKSPACE, 'tool_life_records.csv')
OUT_DIR  = os.path.join(WORKSPACE, 'out_T1111')
ARCHIVE_DIR   = '_archive_4152_T1111_脚本'

# 负载通道（若采集配置改动，改这里）
LOAD_CH = ['L_spindle_load', 'L_X_servo_load', 'L_Y_servo_load', 'L_Z_servo_load',
           'L_A_servo_load', 'L_B_servo_load', 'L_C_servo_load', 'L_E_servo_load']
# 需要读入的列（不存在的会自动忽略）
NEED = ['_time', 'L_tool_number', 'L_spindle_speed', 'L_main_program', 'L_sub_program',
        'L_feed_override', 'L_spindle_override', 'L_allnum', 'L_state'] + LOAD_CH

# 判据
BASE_N         = 5      # 相对倍数基准：本周期前 N 次调用的均值
MIN_ROWS_CALL  = 5      # 一次调用至少几行才计入
TREND_R_MIN    = 0.30   # Spearman |r| 合格线
TREND_SAME_MIN = 0.80   # 方向一致的周期占比下限
HEADTAIL_PCT   = 0.20   # 末期突变：取前/后各 20%
D_MIN          = 0.80   # Cohen's d 大效应线

# ---- 滑窗特征（window 子命令）----
WINDOWS_MIN    = [1, 3, 5, 10]   # 窗口长度（分钟）
WIN_CH         = LOAD_CH         # 参与滑窗的通道；嫌文件太大可改成 ['L_spindle_load','L_X_servo_load']
WIN_CUTTING_ONLY   = True        # 只在切削行（主轴转速>0）上算，排除停机/换刀间隙
SPIKE_K        = 4.0             # 突跳判定：|一阶差分| > K × 本窗口标准差
# ⚠️ min_periods 必须用【绝对点数】，不能用「采样率×窗口×比例」！
# 本数据 T1111 只在自己被调用时才有行：段内 28ms 密集、段间隔几十秒~几小时，
# 1 分钟真实时间里往往只有几十个点。用比例算会得到几百，导致全表 NaN。
WIN_MIN_POINTS = 30              # 窗口内至少几个有效点才输出（约 1 秒的数据）
FREQ_BANDS     = None            # None = 按奈奎斯特频率自动三等分；也可手写 [(0,5),(5,15)]
FREQ_WIN_MIN   = 1               # 频域只对哪个窗口算（FFT 贵，只算 1 分钟窗）
FREQ_STRIDE    = 60              # 频域每隔多少行算一次（其余行留空，省时间）

# ---- 相位对齐（xalign 子命令）：按「进给轴」对齐，消几何主项，捞磨损项 ----
# 原理：切断刀跑 G96 恒线速，同一次切断内负载随 X（直径）剧烈变化（纯几何效应，
# 可达数倍）。粗口径（整件均值 / 按时间对齐）把磨损引起的几个 % 彻底淹掉 →
# 过去的「无信号」是比较口径错误造成的假阴性。把每件的切削段重采样到同一组 X
# 网格，几何项被消掉，剩下才是磨损项。
# 对齐轴 = 该工序刀具实际进给所沿的轴：切断是径向 → X；车端面/纵车是轴向 → Z。
XALIGN_METRICS    = ['L_spindle_load', 'L_X_servo_load', 'L_Z_servo_load',
                     'L_C_servo_load', 'L_A_servo_load', 'L_B_servo_load',
                     'L_E_servo_load', 'L_Y_servo_load', 'L_spindle_speed']
XALIGN_Z_TOL      = 1.0     # 切断 Z 带容差(mm)：|Z-Z0|<=该值才算该工步
XALIGN_GRID_N     = 40      # X 网格点数
XALIGN_BASE_K     = 10      # 前 K 件作「新刀基线」
XALIGN_HEAD_K     = 15      # 首/末各取 K 件对比
XALIGN_MIN_PIECES = 40      # 周期至少多少件才纳入
XALIGN_MIN_POINTS = 8       # 单件在切断段至少几个采样点
XALIGN_DESC_TOL   = 2.50    # 下降段容差(mm)：切断是啄切(切一段退0.3~0.4mm再切)，
                            # 容差要大于啄切退刀量才能把整段扫掠连起来；
                            # 但仍远小于真正退刀(退到X=40, +40mm)，所以拦得住
XALIGN_MIN_SPAN   = 5.0     # 扫掠段 X 跨度下限(mm)：低于此值视为退刀停顿，不算主扫掠
XALIGN_KEEP_FRAC  = 0.70    # 网格点至少被该比例的件覆盖才保留（避免外推噪声）
XALIGN_LOAD_FRAC  = 0.50    # 切削行筛选：负载 >= 该比例 × 下降段内正负载中位数
XALIGN_DROP_CAP   = False   # True=剔除主轴转速被封顶段（G96 失效区：恒速非恒线速）
XALIGN_CAP_FRAC   = 0.98
XALIGN_BINS       = 10      # 队列级：寿命进度分几段看形状
XALIGN_MIN_FILL   = 0.50    # 指标在网格上非空率低于此值则跳过

CHUNKSIZE = 200_000
# 供应商若改了采集配置，新列里可能含这些关键词
KEY_NEW = ['tcmd', 'dtrq', 'torq', 'torque', 'err', 'curr', 'ieff',
           'posf', 'diag', 'vib', 'accel', 'loadraw']
# 归档清单（archive 子命令用；不存在的文件自动跳过）
ARCHIVE_FILES = ['probe_new_4152.py', 'probe_call_count.py', 'scan_t1111_all.py',
                 'trend_by_call.py', 'trend_v2_headtail.py',
                 'stat_t1111_by_program.py', 'mark_20260914.py']
# ==================================================================

_LINES = []
_IN_ALL = False          # all 模式下子命令不单独落盘，只在末尾写 all.txt

def P(s=''):
    print(s)
    _LINES.append(str(s))

def save(name):
    if _IN_ALL and name != 'all.txt':
        return
    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, name)
    with open(p, 'w', encoding='utf-8') as f:
        f.write('\n'.join(_LINES))
    print('\n[输出已保存] %s' % p)

def banner(title):
    P('=' * 78); P(title); P('=' * 78)


# ---------------- 数据定位 ----------------
def find_datafile(path=None):
    """未指定文件时，在工作区（含一层子目录）找 influxdb_*4152*.csv，取最新的"""
    if path:
        p = path if os.path.isabs(path) else os.path.join(WORKSPACE, path)
        if not os.path.exists(p):
            raise SystemExit('找不到数据文件: %s' % p)
        return p
    pats = [os.path.join(WORKSPACE, 'influxdb_*%s*.csv' % DEVICE),
            os.path.join(WORKSPACE, '*', 'influxdb_*%s*.csv' % DEVICE),
            os.path.join(WORKSPACE, 'influxdb_*.csv'),
            os.path.join(WORKSPACE, '*', 'influxdb_*.csv')]
    hits = []
    for pt in pats:
        hits += [f for f in globmod.glob(pt) if os.path.isfile(f)]
    hits = sorted(set(hits))
    if not hits:
        raise SystemExit('工作区找不到 influxdb_*.csv')
    hits.sort(key=lambda f: os.path.getmtime(f), reverse=True)
    return hits[0]


def tool_col_of(cols):
    for c in ['%s_tool_number' % TOWER, 'L_tool_number', 'R_tool_number']:
        if c in cols:
            return c
    return None


def read_data(path, keep='tool', cols='all'):
    """分块读；keep=tool 只留 T1111 行，keep=all 保留全部。返回 (df, meta)
    cols='all'  → 读入源数据【全部列】（默认，保证输出 CSV 不丢任何源字段）
    cols='need' → 只读 NEED 里配置的列（省内存，但输出 CSV 会缺列）"""
    head = list(pd.read_csv(path, nrows=0, encoding='utf-8-sig').columns)
    tc = tool_col_of(head)
    if tc is None:
        raise SystemExit('数据中找不到刀具列（L_tool_number / R_tool_number）')
    if cols == 'need':
        usecols = [c for c in NEED if c in head] + [tc]
    else:
        usecols = list(head)          # 全列：不丢弃源数据任何字段
    usecols = list(dict.fromkeys(usecols))
    if '_time' not in usecols:
        raise SystemExit('数据中没有 _time 列')

    keep_parts, n_total, n_tool = [], 0, 0
    tmin = tmax = None
    for ch in pd.read_csv(path, usecols=usecols, encoding='utf-8-sig',
                          low_memory=False, chunksize=CHUNKSIZE):
        n_total += len(ch)
        ts = pd.to_datetime(ch['_time'], errors='coerce')
        if len(ts):
            lo, hi = ts.min(), ts.max()
            tmin = lo if tmin is None or lo < tmin else tmin
            tmax = hi if tmax is None or hi > tmax else tmax
        m = ch[tc].astype(str).str.upper().str.strip() == TOOL
        n_tool += int(m.sum())
        keep_parts.append(ch[m] if keep == 'tool' else ch)
        print('    读取中… 累计 %d 行' % n_total, end='\r')
    print('')
    df = pd.concat(keep_parts, ignore_index=True) if keep_parts else pd.DataFrame()
    del keep_parts
    df['_t'] = pd.to_datetime(df['_time'], errors='coerce')
    df = df.sort_values('_t').reset_index(drop=True)
    for c in LOAD_CH + ['L_spindle_speed', 'L_feed_override', 'L_spindle_override']:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    meta = dict(path=path, cols=head, tool_col=tc, n_total=n_total,
                n_tool=n_tool, tmin=tmin, tmax=tmax)
    return df, meta


# ---------------- 换刀记录 ----------------
def load_records():
    rec = pd.read_csv(RECORDS, encoding='utf-8-sig')
    rec['_start'] = pd.to_datetime(rec['StartTime'].astype(str).str.strip(),
                                   format='%Y/%m/%d %H:%M', errors='coerce')
    rec['_end'] = pd.to_datetime(rec['EndTime'].astype(str).str.strip(),
                                 format='%Y/%m/%d %H:%M', errors='coerce')
    rec['刀号'] = rec['刀号'].astype(str).str.upper().str.strip()
    sub = rec[rec['刀号'] == TOOL].sort_values('顺序号')
    return sub.reset_index(drop=True)


def cycles_in_range(sub, tmin, tmax):
    """返回与数据时间范围有交集的周期 [(cid, start, end_eff, open_ended)]"""
    out = []
    for _, r in sub.iterrows():
        st = r['_start']
        if pd.isna(st):
            continue
        en = r['_end'] if pd.notna(r['_end']) else pd.Timestamp(tmax)
        if en < pd.Timestamp(tmin) or st > pd.Timestamp(tmax):
            continue                       # 完全落在数据之外
        out.append((int(r['顺序号']), st, min(en, pd.Timestamp(tmax)),
                    pd.isna(r['_end']), r.get('换刀原因') or ''))
    return out


# ---------------- 调用段切分 ----------------
def cut_segments(df, tc):
    is_t = df[tc].astype(str).str.upper().str.strip() == TOOL
    df = df.copy()
    df['seg'] = (is_t & ~is_t.shift(1, fill_value=False)).cumsum().where(is_t)
    return df


def build_call_features(path):
    """读全量 → 切调用段 → 每次调用一行特征。返回 (F, meta)"""
    df, meta = read_data(path, keep='all')      # 必须全量，否则看不到刀号跳变
    df = cut_segments(df, meta['tool_col'])
    sub = load_records()
    BOUND = cycles_in_range(sub, meta['tmin'], meta['tmax'])

    rows = []
    for sid, g in df.dropna(subset=['seg']).groupby('seg'):
        gc = g[g['L_spindle_speed'] > 0] if 'L_spindle_speed' in g.columns else g
        if len(gc) < MIN_ROWS_CALL:
            continue
        gc = gc.sort_values('_t')
        dt = gc['_t'].diff().dt.total_seconds().fillna(0)
        r = {'seg': int(sid), 't0': gc['_t'].min(),
             'dur_s': (gc['_t'].max() - gc['_t'].min()).total_seconds(),
             'n_rows': len(gc)}
        if 'L_spindle_speed' in gc.columns:
            r['sp_speed_mean'] = gc['L_spindle_speed'].mean()
        if 'L_feed_override' in gc.columns:
            r['feed_ovr'] = gc['L_feed_override'].median()
        if 'L_spindle_override' in gc.columns:
            r['sp_ovr'] = gc['L_spindle_override'].median()
        if 'L_main_program' in gc.columns:
            mo = gc['L_main_program'].dropna()
            r['main_program'] = mo.iloc[0] if len(mo) else None
        for c in LOAD_CH:
            if c not in gc.columns:
                continue
            v = pd.to_numeric(gc[c], errors='coerce').fillna(0)
            r[c + '_mean'] = v.mean()
            r[c + '_max'] = v.max()
            r[c + '_std'] = v.std()
            r[c + '_p95'] = v.quantile(0.95)
            r[c + '_int'] = float((v * dt).sum())
        rows.append(r)

    F = pd.DataFrame(rows).sort_values('t0').reset_index(drop=True)
    if len(F) == 0:
        raise SystemExit('未切分出任何调用段，检查刀号与数据是否匹配')
    F['cycle'] = None
    for cid, st, en, _, _ in BOUND:
        m = (F['t0'] >= st) & (F['t0'] <= en)
        F.loc[m, 'cycle'] = cid
    F = F.dropna(subset=['cycle']).copy()
    F['cycle'] = F['cycle'].astype(int)
    F['call_idx'] = F.groupby('cycle').cumcount() + 1
    F['n_call'] = F.groupby('cycle')['call_idx'].transform('max')
    F['prog'] = F['call_idx'] / F['n_call']
    if 'main_program' in F.columns:
        F['prog_no'] = F['main_program'].astype(str).str.extract(r'^(\d+)')
    return F, meta, BOUND


def add_rel(F, feats):
    """相对倍数 = 本次值 / 本周期前 BASE_N 次调用的均值"""
    for c in feats:
        if c not in F.columns:
            continue
        base = F.groupby('cycle')[c].transform(lambda s: s.iloc[:BASE_N].mean())
        F[c + '_rel'] = (F[c] / base.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    return F


# ================= 子命令 =================
def cmd_scan(a):
    banner('T1111 数据扫描   设备 %s / %s 刀塔 / 刀具 %s' % (DEVICE, TOWER, TOOL))
    # --file 优先：只扫指定的那一个文件；不指定才扫全工作区
    if a.file:
        files = [find_datafile(a.file)]
        P('\n（已指定 --file，只检查这 1 个文件；去掉 --file 则扫描工作区全部 CSV）')
    else:
        pats = [os.path.join(WORKSPACE, 'influxdb_*.csv'),
                os.path.join(WORKSPACE, '*', 'influxdb_*.csv')]
        files = sorted(set(f for pt in pats for f in globmod.glob(pt) if os.path.isfile(f)))
    P('\n共找到 %d 个 CSV，逐个检查是否含 %s：' % (len(files), TOOL))
    P('%-52s %10s %10s %s' % ('文件', 'T1111行数', '总行数', '时间范围'))
    P('-' * 118)
    for f in files:
        cols = list(pd.read_csv(f, nrows=0, encoding='utf-8-sig').columns)
        tc = tool_col_of(cols)
        if tc is None:
            continue
        n = nt = 0
        tmin = tmax = None
        try:
            for ch in pd.read_csv(f, usecols=['_time', tc], encoding='utf-8-sig',
                                  low_memory=False, chunksize=CHUNKSIZE):
                n += len(ch)
                m = ch[tc].astype(str).str.upper().str.strip() == TOOL
                nt += int(m.sum())
                ts = pd.to_datetime(ch['_time'], errors='coerce')
                if len(ts):
                    lo, hi = ts.min(), ts.max()
                    tmin = lo if tmin is None or lo < tmin else tmin
                    tmax = hi if tmax is None or hi > tmax else tmax
        except Exception as e:
            P('%-52s   读取失败: %s' % (os.path.basename(f)[:52], e)); continue
        mark = '  <<< 含' if nt > 0 else ''
        P('%-52s %10d %10d %s ~ %s%s' % (
            os.path.relpath(f, WORKSPACE)[:52], nt, n,
            str(tmin)[:16], str(tmax)[:16], mark))
    save('scan.txt')


def cmd_mark(a):
    path = find_datafile(a.file)
    banner('T1111 换刀周期打标')
    P('\n数据文件: %s' % path)
    df0, meta = read_data(path, keep=a.keep, cols=a.cols)
    P('    读入列数 = %d（源数据 %d 列）' % (len(df0.columns), len(meta['cols'])))

    # 新列体检
    P('\n[1] 列名体检（新文件 vs 9-10 旧文件）')
    P('    本文件列数 = %d' % len(meta['cols']))
    old = os.path.join(WORKSPACE, 'influxdb_FanucNormalData_20260910_085752_4152.csv')
    if os.path.exists(old) and os.path.abspath(old) != os.path.abspath(path):
        oc = list(pd.read_csv(old, nrows=0, encoding='utf-8-sig').columns)
        added = [c for c in meta['cols'] if c not in oc]
        removed = [c for c in oc if c not in meta['cols']]
        P('    新增列: %s' % (added if added else '无'))
        P('    缺失列: %s' % (removed if removed else '无'))
    hits = [c for c in meta['cols'] if any(k in c.lower() for k in KEY_NEW)]
    P('\n[2] 关键词扫描（供应商是否改了采集配置）')
    if hits:
        P('    !!! 命中: %s  ← 立刻告知！' % hits)
    else:
        P('    未发现 TCMD / DTRQ / ERR / 电流 / 诊断 类列（采集内容应与旧文件一致）')
    P('\n    时间范围 = %s ~ %s' % (meta['tmin'], meta['tmax']))
    P('    总行数 = %d，其中 %s = %d' % (meta['n_total'], TOOL, meta['n_tool']))

    # 打标
    from label_tool_life import label_life
    rec_all = pd.read_csv(RECORDS, encoding='utf-8-sig')
    rec_all['_start'] = pd.to_datetime(rec_all['StartTime'].astype(str).str.strip(),
                                       format='%Y/%m/%d %H:%M', errors='coerce')
    rec_all['_end'] = pd.to_datetime(rec_all['EndTime'].astype(str).str.strip(),
                                     format='%Y/%m/%d %H:%M', errors='coerce')
    P('\n[3] 打标中…')
    out, tc, chn = label_life(df0, rec_all, TOOL, 3)
    lab = out[out['in_life_range'] == True]
    P('    已标注行数 = %d / %d，覆盖周期 %s' % (
        len(lab), len(out), sorted(lab['life_cycle'].dropna().unique().tolist())))
    os.makedirs(OUT_DIR, exist_ok=True)
    dst = os.path.join(OUT_DIR, '%s_life_labeled%s.csv'
                       % (os.path.splitext(os.path.basename(path))[0],
                          '' if a.keep == 'tool' else '_all'))
    out.to_csv(dst, index=False, encoding='utf-8-sig')
    P('    已保存: %s' % dst)
    save('mark.txt')


def find_labeled(path=None):
    """定位打标文件：默认取 out_T1111/ 下最新的 *_life_labeled*.csv"""
    if path:
        p = path if os.path.isabs(path) else os.path.join(WORKSPACE, path)
        if not os.path.exists(p):
            raise SystemExit('找不到打标文件: %s' % p)
        return p
    hits = [f for pt in (os.path.join(OUT_DIR, '*_life_labeled*.csv'),
                         os.path.join(WORKSPACE, '*_life_labeled*.csv'))
            for f in globmod.glob(pt) if os.path.isfile(f)]
    hits = sorted(set(hits))
    if not hits:
        raise SystemExit('找不到打标文件，请先跑: t1111_pipeline.py mark')
    hits.sort(key=lambda f: os.path.getmtime(f), reverse=True)
    return hits[0]


def _freq_block(vals, fs, bands):
    """返回 [各频段能量占比..., 谱质心(Hz)]"""
    v = np.asarray(vals, dtype=float)
    v = v[~np.isnan(v)]
    if len(v) < 64:
        return [np.nan] * (len(bands) + 1)
    v = v - v.mean()                      # 去直流
    sp = np.abs(np.fft.rfft(v)) ** 2          # numpy 2.x 起必须用 np.fft.rfft
    f = np.fft.rfftfreq(len(v), 1.0 / fs)
    tot = sp.sum()
    if not np.isfinite(tot) or tot <= 0:
        return [np.nan] * (len(bands) + 1)
    out = [float(sp[(f >= lo) & (f < hi)].sum() / tot) for lo, hi in bands]
    out.append(float((f * sp).sum() / tot))
    return out


def cmd_window(a):
    """行级滑窗特征：在打标后的原始行上算 1/3/5/10 分钟窗口的时域(+可选频域)特征"""
    if a.file and not a.labeled:
        src = find_datafile(a.file)
        # 传入的若本身就是打标文件（含 life_cycle 列），直接用，别再拼一次 _life_labeled
        hdr0 = list(pd.read_csv(src, nrows=0, encoding='utf-8-sig').columns)
        if 'life_cycle' in hdr0:
            path = src
            P('(检测到传入的就是已打标文件，直接使用)')
        else:
            # 由原始数据名推出对应打标文件名（避免读到别的文件的打标结果）
            stem = os.path.splitext(os.path.basename(src))[0]
            cand = None
            for suf in ['', '_all']:
                p2 = os.path.join(OUT_DIR, '%s_life_labeled%s.csv' % (stem, suf))
                if os.path.exists(p2):
                    cand = p2
                    break
            if cand is None:
                raise SystemExit(
                    '找不到对应的打标文件: %s_life_labeled.csv\n  请先跑: '
                    't1111_pipeline.py mark --file <同一个数据文件>\n'
                    '  或者直接用 --labeled <已打标文件.csv> 指定' % stem)
            path = cand
    else:
        path = find_labeled(a.labeled)
    banner('T1111 行级滑窗特征')
    P('\n输入: %s' % path)

    head = list(pd.read_csv(path, nrows=0, encoding='utf-8-sig').columns)
    lab = [c for c in ['life_cycle', 'life_used_pct_time', 'life_used_pct_cut',
                       'change_reason', 'real_failure'] if c in head]
    chs = [c for c in (a.ch.split(',') if a.ch else WIN_CH) if c in head]
    if not chs:
        raise SystemExit('打标文件里找不到任何负载通道列，检查 WIN_CH 配置')
    # 全列读入：输出要保留原始数据全部列，不能只读子集
    df = pd.read_csv(path, encoding='utf-8-sig', low_memory=False)
    df['_t'] = pd.to_datetime(df['_time'], errors='coerce')
    df = df.dropna(subset=['_t']).sort_values('_t').reset_index(drop=True)
    # 原始列 + 原始顺序（输出基准）。_t 是内部 datetime 副本，不算原始列
    orig_cols = [c for c in head if c in df.columns and c != '_t']
    raw = df.copy()                       # 原始全量：在数值转换之前复制，保持原始 dtype
    for c in chs:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    P('读入 %d 行 × %d 列（原始全部列，输出时保留），参与通道 %d 个: %s'
      % (len(df), df.shape[1], len(chs), chs))

    if WIN_CUTTING_ONLY and 'L_spindle_speed' in df.columns:
        n0 = len(df)
        # 刻意【不】reset_index：保留原始行标签，特征才能按标签写回对应行
        df = df[pd.to_numeric(df['L_spindle_speed'], errors='coerce').fillna(0) > 0].copy()
        P('切削行过滤: %d -> %d 行（剔除 %.1f%% 的停机/间隙）'
          % (n0, len(df), 100.0 * (1 - len(df) / max(1, n0))))
    if len(df) == 0:
        raise SystemExit('过滤后没有有效行')

    # 采样率（由时间差中位数估计）
    dt_med = df['_t'].diff().dt.total_seconds().median()
    fs = (1.0 / dt_med) if dt_med and dt_med > 0 else 62.5
    nyq = fs / 2.0
    P('采样间隔中位 %.1f ms → 采样率 %.1f Hz → 奈奎斯特 %.2f Hz'
      % (dt_med * 1000, fs, nyq))
    # 混叠检查：主轴转频若超过奈奎斯特，频域结果不可信
    if 'L_spindle_speed' in df.columns:
        sp_med = pd.to_numeric(df['L_spindle_speed'], errors='coerce').median()
        if sp_med and sp_med > 0:
            f_sp = sp_med / 60.0
            P('主轴转速中位 %.0f rpm = %.2f Hz → %s'
              % (sp_med, f_sp,
                 ('⚠️ 超过奈奎斯特 %.2f Hz，频域会混叠失真，慎用' % nyq)
                 if f_sp > nyq else '（在奈奎斯特内）'))
    P('窗口: %s 分钟，最小点数 min_periods = %d' % (WINDOWS_MIN, WIN_MIN_POINTS))

    # 所有新列先攒在 blocks 里，循环外一次性建表（避免逐列 insert 造成碎片化）
    blocks = {}
    tsec = (df['_t'] - df['_t'].iloc[0]).dt.total_seconds()
    T = pd.Series(tsec.values, index=df['_t'])

    qual = []
    for W in WINDOWS_MIN:
        win = '%ds' % int(W * 60)
        mp = WIN_MIN_POINTS          # 绝对点数，别用「采样率×窗口×比例」
        P('  计算 %d 分钟窗口…' % W)
        for c in chs:
            X = pd.Series(df[c].values, index=df['_t'])
            R = X.rolling(win, min_periods=mp)
            cnt = R.count()
            s_mean = R.mean()
            s_std = R.std()
            s_max = R.max()
            s_min = R.min()
            X2 = pd.Series((df[c] ** 2).values, index=df['_t']).rolling(win, min_periods=mp)
            s_rms = np.sqrt(X2.mean())
            p = '%s_%dm' % (c, W)
            blocks[p + '_mean'] = s_mean.values
            blocks[p + '_max'] = s_max.values
            blocks[p + '_min'] = s_min.values
            blocks[p + '_std'] = s_std.values
            blocks[p + '_ptp'] = (s_max - s_min).values
            blocks[p + '_rms'] = s_rms.values
            blocks[p + '_kurt'] = (R.kurt() + 3.0).values       # 转成 Pearson 峭度(正态=3)
            # 最小二乘斜率：单位/分钟
            N = cnt
            Sx = T.rolling(win, min_periods=mp).sum()
            Sy = R.sum()
            Sxy = pd.Series((tsec.values * df[c].values), index=df['_t']).rolling(win, min_periods=mp).sum()
            Sxx = pd.Series((tsec.values ** 2), index=df['_t']).rolling(win, min_periods=mp).sum()
            den = (N * Sxx - Sx ** 2).replace(0, np.nan)
            blocks[p + '_slope'] = (((N * Sxy - Sx * Sy) / den) * 60.0).values
            # 突跳次数
            dabs = X.diff().abs()
            flag = (dabs > SPIKE_K * s_std).fillna(False).astype(float)
            blocks[p + '_spike'] = flag.rolling(win, min_periods=mp).sum().values
            # 裕度因子 / 峰值因子
            msr = pd.Series(np.sqrt(np.abs(df[c].values)), index=df['_t']).rolling(win, min_periods=mp).mean()
            blocks[p + '_margin'] = (s_max / (msr ** 2).replace(0, np.nan)).values
            blocks[p + '_crest'] = (s_max / s_rms.replace(0, np.nan)).values
            blocks[p + '_n'] = cnt.values
        nc = '%s_%dm_mean' % (chs[0], W)
        nn = '%s_%dm_n' % (chs[0], W)
        _v1 = pd.Series(blocks[nc])
        _v2 = pd.Series(blocks[nn])
        qual.append((W, 100.0 * _v1.notna().mean(),
                     float(np.nanmedian(_v2)) if _v2.notna().any() else 0.0))

    if a.freq and FREQ_WIN_MIN in WINDOWS_MIN:
        bands = FREQ_BANDS
        if not bands:                       # 按奈奎斯特自动三等分
            e = nyq / 3.0
            bands = [(0, e), (e, 2 * e), (2 * e, nyq)]
        P('  计算频域（仅 %d 分钟窗，每 %d 行抽样一次）…' % (FREQ_WIN_MIN, FREQ_STRIDE))
        idx = np.arange(0, len(df), max(1, FREQ_STRIDE))
        tvals = df['_t'].values
        for c in chs:
            v = df[c].values
            cols = ['%s_%dm_band%d' % (c, FREQ_WIN_MIN, i) for i in range(len(bands))]
            cols += ['%s_%dm_centroid' % (c, FREQ_WIN_MIN)]
            arr = np.full((len(df), len(cols)), np.nan)   # 预分配，避免 out.at 逐格写
            W = FREQ_WIN_MIN * 60
            for i in idx:
                t1 = df['_t'].iloc[i]
                j0 = np.searchsorted(tvals, np.datetime64(t1 - pd.Timedelta(seconds=W)), 'left')
                if i - j0 < 64:
                    continue
                arr[i, :] = _freq_block(v[j0:i + 1], fs, bands)
            for k, col in enumerate(cols):
                blocks[col] = arr[:, k]
        P('  频段划分(Hz): %s' % [(round(a1, 2), round(b1, 2)) for a1, b1 in bands])

    # ===== 把特征按行标签写回原始表：新增列追加在末尾，不新增任何 ID 列 =====
    # 滑窗是逐行算的：第 i 行的特征只由它之前的 1/3/5/10 分钟窗口得出，
    # 所以特征表与参与计算的行天然一一对应，按行标签写回即可，不需要额外 ID。
    feat = pd.DataFrame(blocks, index=df.index) if blocks else pd.DataFrame(index=df.index)
    new_cols = list(feat.columns)
    if new_cols:
        # 先建一张与原始表同形状的 NaN 表，再把特征按行标签填进去（只填参与计算的行）
        feat_full = pd.DataFrame(np.nan, index=raw.index, columns=new_cols)
        feat_full.loc[df.index, :] = feat.values
    else:
        feat_full = pd.DataFrame(index=raw.index)
    # 一次性拼接（取代逐列 insert，避免 DataFrame 碎片化）
    out = pd.concat([raw.drop(columns=['_t'], errors='ignore'), feat_full], axis=1)
    # 列顺序：原始全部列保持原顺序在前 + 新特征列追加在末尾
    out = out[orig_cols + new_cols]
    P('\n列顺序: 原始 %d 列（保持原顺序） + 新增特征 %d 列 = %d 列'
      % (len(orig_cols), len(new_cols), out.shape[1]))

    # --- 校验 1：行数必须一致，且不能出现重复行 ---
    P('\n[校验]')
    P('  原始行数 %d / 参与计算行 %d / 合并后 %d  → %s'
      % (len(raw), len(df), len(out),
         '一致 ✅' if len(out) == len(raw) else '❌ 不一致，中止'))
    if len(out) != len(raw):
        raise SystemExit('合并后行数与原始数据不一致，已中止（未写出文件）')
    if out.index.duplicated().any():
        raise SystemExit('合并后出现重复行，已中止（未写出文件）')

    # --- 校验 2：特征归属正确性（未参与计算的行必须为空）---
    if new_cols:
        nz = int(out[new_cols[0]].notna().sum())
        P('  特征非空行数 %d / 参与计算行 %d（差 %d 行 = 段首不足 %d 点的 warmup 段，属正常）'
          % (nz, len(df), len(df) - nz, WIN_MIN_POINTS))
        if nz > len(df):
            raise SystemExit('特征非空行数超过参与计算行数，已中止（未写出文件）')
        rest = out.index.difference(df.index)       # 未参与计算的行（停机 / 非切削）
        leak = int(out.loc[rest, new_cols[0]].notna().sum())
        P('  未参与计算行 %d 行中特征非空 %d 行  → %s'
          % (len(rest), leak, '✅ 无越界' if leak == 0 else '❌ 特征越界'))
        if leak:
            raise SystemExit('特征被写到未参与计算的行，已中止（未写出文件）')

    # --- 校验 3：抽查若干行，确认特征值与其所属行匹配 ---
    if new_cols:
        rng = np.random.default_rng(20260915)
        pick = rng.choice(len(df), size=min(5, len(df)), replace=False)
        chk = new_cols[:3]
        P('  抽查 %d 行（比对 _time 是否一致 + 特征数值是否逐位相等）:' % len(pick))
        bad = 0
        for i in pick:
            lab_i = df.index[i]
            r = out.loc[lab_i]
            ok_t = (str(r['_time']) == str(df['_time'].iloc[i]))
            ok_v = all(bool(np.isclose(float(r[c]), float(feat[c].iloc[i]),
                                       rtol=1e-9, atol=0, equal_nan=True)) for c in chk)
            bad += (0 if (ok_t and ok_v) else 1)
            P('    第%-7d行 time_ok=%-5s val_ok=%-5s  %s'
              % (lab_i, ok_t, ok_v, str(df['_time'].iloc[i])[:19]))
        P('  抽查结果: %s' % ('全部通过 ✅' if bad == 0 else '❌ 有 %d 行对不上' % bad))
        if bad:
            raise SystemExit('抽查发现特征与行不匹配，已中止（未写出文件）')

    # --- 校验 4：关键列空值比例 ---
    if new_cols:
        P('  关键列空值比例:')
        for c in [new_cols[0], new_cols[min(1, len(new_cols) - 1)], new_cols[-1]]:
            v = out[c]
            P('    %-34s 非空 %6.1f%%' % (c[:34], 100.0 * v.notna().mean()))

    if a.cutrows:                      # 可选：只保留参与计算的行（特征无空窗）
        out = out[out[new_cols[0]].notna()].reset_index(drop=True) if new_cols else out
        P('  --cutrows: 仅保留特征非空行 → %d 行' % len(out))


    os.makedirs(OUT_DIR, exist_ok=True)
    base = 't1111_window_feat_full'          # 完整版：原始全部列 + 新增特征列
    dst = os.path.join(OUT_DIR, '%s.parquet' % base if a.parquet else '%s.csv' % base)
    def _write(pth, pq):
        if pq:
            out.to_parquet(pth, index=False)
        else:
            out.to_csv(pth, index=False, encoding='utf-8-sig')

    try:
        _write(dst, a.parquet)
    except PermissionError:
        import time
        alt = os.path.join(OUT_DIR, '%s_%s.%s'
                           % (base, time.strftime('%m%d_%H%M%S'),
                              'parquet' if a.parquet else 'csv'))
        P('  ⚠️ 原文件被占用（多半正被 Excel 打开），改存为 %s' % os.path.basename(alt))
        _write(alt, a.parquet)
        dst = alt
    except Exception as e:
        P('  保存失败(%s)，回退 CSV' % e)
        dst = os.path.join(OUT_DIR, '%s.csv' % base)
        out.to_csv(dst, index=False, encoding='utf-8-sig')
    sz = os.path.getsize(dst) / 1024.0 / 1024.0
    P('\n已保存: %s' % dst)
    P('规模: %d 行 × %d 列，文件 %.1f MB' % (len(out), out.shape[1], sz))
    if qual:
        P('\n窗口质量（以 %s 为例）:' % chs[0])
        P('%-10s %10s %18s' % ('窗口', '非空率', '窗口内点数中位'))
        P('-' * 42)
        for W, fill, nmed in qual:
            P('%-10s %9.1f%% %18.0f' % ('%d 分钟' % W, fill, nmed))
        P('\n注：点数偏少说明该窗口大部分时间没在切削，用 _n 列过滤掉稀疏行再分析。')
    P('\n字段命名规则: <通道>_<窗口分钟数>m_<统计量>')
    P('  mean 均值 / max 最大 / min 最小 / std 标准差 / ptp 峰峰值 / rms 均方根')
    P('  kurt 峭度(Pearson, 正态=3, 越大尖刺越多) / slope 最小二乘斜率(单位/分钟)')
    P('  spike 突跳次数(|一阶差分| > %.1f×窗口标准差) / margin 裕度因子 / crest 峰值因子'
      % SPIKE_K)
    P('  n 窗口内有效点数（小于理论值说明窗口被停机间隙稀释，该行慎用）')
    save('window.txt')


def _load_or_build_F(a):
    """取调用特征表：优先复用 out_T1111/t1111_calls.csv（除非 --rebuild）"""
    os.makedirs(OUT_DIR, exist_ok=True)
    cache = os.path.join(OUT_DIR, 't1111_calls.csv')
    if os.path.exists(cache) and not a.rebuild and a.file is None:
        F = pd.read_csv(cache, encoding='utf-8-sig')
        F['t0'] = pd.to_datetime(F['t0'])
        P('(复用已有特征表 %s，共 %d 次调用；--rebuild 可强制重算)'
          % (os.path.basename(cache), len(F)))
        sub = load_records()
        BOUND = cycles_in_range(sub, F['t0'].min(), F['t0'].max())
        return F, BOUND
    path = find_datafile(a.file)
    F, meta, BOUND = build_call_features(path)
    feats = [c + s for c in LOAD_CH for s in ['_mean', '_max', '_std', '_p95', '_int']
             if c + s in F.columns] + ['dur_s']
    F = add_rel(F, feats)
    F.to_csv(cache, index=False, encoding='utf-8-sig')
    P('特征表已保存: %s  (%d 次调用)' % (cache, len(F)))
    return F, BOUND


def cmd_feat(a):
    banner('T1111 调用级特征表')
    F, BOUND = _load_or_build_F(a)
    P('\n%-6s %8s %10s %10s %s' % ('周期', '调用次数', '起始', '结束', '主程序构成'))
    P('-' * 84)
    for cid in sorted(F['cycle'].unique()):
        g = F[F['cycle'] == cid]
        comp = ''
        if 'prog_no' in g.columns:
            vc = g['prog_no'].value_counts().head(3)
            comp = ' / '.join('%s:%d' % (k, v) for k, v in vc.items())
        P('%-6d %8d %10s %10s %s' % (cid, len(g), str(g['t0'].min())[5:16],
                                      str(g['t0'].max())[5:16], comp))
    P('\n合计 %d 次调用，%d 个周期' % (len(F), F['cycle'].nunique()))
    save('feat.txt')


def cmd_stats(a):
    banner('T1111 按主程序统计')
    F, BOUND = _load_or_build_F(a)
    if 'prog_no' not in F.columns or F['prog_no'].isna().all():
        P('数据中无主程序信息，stats 跳过'); save('stats.txt'); return
    P('\n[1] 按主程序汇总')
    P('%-28s %8s %7s %10s %14s %10s %10s' % (
        '主程序', '调用次数', '占比', '总时长h', '单次时长s(中位)', '主轴转速', '进给倍率'))
    P('-' * 96)
    tot = len(F)
    for mp, n in F['main_program'].value_counts().items():
        g = F[F['main_program'] == mp]
        P('%-28s %8d %6.1f%% %10.2f %14.1f %10.0f %10.0f' % (
            str(mp)[:28], n, 100.0 * n / tot, g['dur_s'].sum() / 3600.0,
            g['dur_s'].median(),
            g.get('sp_speed_mean', pd.Series(dtype=float)).median(),
            g.get('feed_ovr', pd.Series(dtype=float)).median()))
    P('')

    progs = sorted(F['prog_no'].dropna().unique().tolist())
    P('[2] 单位调用消耗对比（每次调用的平均负载积分）')
    P('%-24s %s   %s' % ('通道', ''.join('%14s' % ('程序' + p) for p in progs),
                          '14/12倍率'))
    P('-' * (26 + 14 * len(progs) + 12))
    for c in LOAD_CH:
        col = c + '_int'
        if col not in F.columns:
            continue
        ms = [F[F['prog_no'] == p][col].mean() if (F['prog_no'] == p).any() else np.nan
              for p in progs]
        ratio = ''
        if '12' in progs and '14' in progs:
            m12 = F[F['prog_no'] == '12'][col].mean()
            m14 = F[F['prog_no'] == '14'][col].mean()
            if m12 and not np.isnan(m12) and not np.isnan(m14):
                ratio = '%.2f' % (m14 / m12)
        P('%-24s %s   %s' % (c[:24],
                             ''.join('%14.1f' % v if not np.isnan(v) else '%14s' % '-'
                                     for v in ms), ratio))
    P('')

    P('[3] 关键问题：寿命差异能否用「累计载荷」解释？')
    P('%-6s %-24s %8s %16s %14s' % ('周期', '主程序构成', '调用次数', '主轴负载积分', '单位调用积分'))
    P('-' * 76)
    agg = []
    for cid in sorted(F['cycle'].unique()):
        g = F[F['cycle'] == cid]
        vc = g['prog_no'].value_counts()
        comp = ' / '.join('%s:%d' % (k, v) for k, v in vc.head(3).items())
        si = g['L_spindle_load_int'].sum() if 'L_spindle_load_int' in g.columns else np.nan
        agg.append((cid, len(g), si))
        P('%-6d %-24s %8d %16.0f %14.1f' % (cid, comp[:24], len(g), si, si / len(g)))
    arr = np.array([x[2] for x in agg], dtype=float)
    cnt = np.array([x[1] for x in agg], dtype=float)
    P('')
    if len(agg) > 1 and arr.mean() > 0:
        cv_l = arr.std(ddof=1) / arr.mean()
        cv_c = cnt.std(ddof=1) / cnt.mean()
        P('  累计主轴负载积分 CV = %.3f ；调用次数 CV = %.3f' % (cv_l, cv_c))
        if cv_l < cv_c - 0.02:
            P('  >> 累计载荷更集中：寿命差异【部分可被累计载荷解释】')
        else:
            P('  >> 累计载荷的离散度不小于调用次数：【无法用累计载荷解释寿命差异】')
    P('\n  寿命观测区间: %d ~ %d 次，中位 %.0f 次'
      % (cnt.min(), cnt.max(), np.median(cnt)))
    save('stats.txt')


def cmd_trend(a):
    from scipy import stats
    banner('T1111 退化趋势检验（按调用次数对齐）')
    F, BOUND = _load_or_build_F(a)
    feats = [c + s for c in LOAD_CH for s in ['_mean', '_max', '_std']
             if c + s in F.columns] + ['dur_s']
    cyc = sorted(F['cycle'].unique())

    P('\n[1] 判据')
    P('  单调爬升：每周期 Spearman r(相对倍数, 调用序号)；合格 = |r中位|>=%.2f 且同号>=%.0f%%'
      % (TREND_R_MIN, TREND_SAME_MIN * 100))
    P('  末期突变：前 %d%% vs 后 %d%% 调用；Cohen d>=%.2f 且多数周期同号'
      % (HEADTAIL_PCT * 100, HEADTAIL_PCT * 100, D_MIN))
    P('\n  周期: %s   合计 %d 次调用' % (cyc, len(F)))

    P('\n[2] 单调爬升检验（按 |r中位| 降序）')
    P('%-26s %s %8s %8s   %s' % ('特征', ''.join('%8s' % ('c' + str(c)) for c in cyc),
                                  'r中位', '同号', '判定'))
    P('-' * (30 + 8 * len(cyc) + 22))
    res = []
    for c in feats:
        col = c + '_rel'
        if col not in F.columns:
            continue
        rs = {}
        for cid in cyc:
            g = F[F['cycle'] == cid].dropna(subset=[col])
            rs[cid] = stats.spearmanr(g['call_idx'], g[col])[0] if len(g) >= 8 else np.nan
        vals = [v for v in rs.values() if not np.isnan(v)]
        if not vals:
            continue
        med = float(np.median(vals))
        same = max(sum(1 for v in vals if v > 0), sum(1 for v in vals if v < 0))
        ok = (abs(med) >= TREND_R_MIN) and (same >= TREND_SAME_MIN * len(vals))
        res.append((c, rs, med, same, len(vals), ok))
    res.sort(key=lambda x: -abs(x[2]))
    n_ok = 0
    for c, rs, med, same, nv, ok in res:
        n_ok += 1 if ok else 0
        P('%-26s %s %8.2f %5d/%-2d   %s' % (
            c[:26], ''.join('%8.2f' % rs[k] if not np.isnan(rs[k]) else '%8s' % '-'
                            for k in cyc), med, same, nv,
            '*** 通过 ***' if ok else ''))
    P('\n  检验 %d 个特征，通过 %d 个；|r中位| 最大 = %.3f (%s)'
      % (len(res), n_ok, abs(res[0][2]) if res else 0, res[0][0] if res else '-'))

    P('\n[3] 末期突变检验')
    P('%-26s %10s %10s %10s %8s %8s   %s' % (
        '特征', '前段中位', '后段中位', '差值', "Cohen'd", '同号', '判定'))
    P('-' * 90)
    res2 = []
    for c in feats:
        col = c + '_rel'
        if col not in F.columns:
            continue
        ds, hm, tm = {}, [], []
        for cid in cyc:
            g = F[F['cycle'] == cid].dropna(subset=[col]).sort_values('call_idx')
            n = len(g)
            if n < 10:
                continue
            k = max(1, int(round(n * HEADTAIL_PCT)))
            h, t = g[col].iloc[:k], g[col].iloc[-k:]
            sd = np.sqrt((h.std(ddof=1) ** 2 + t.std(ddof=1) ** 2) / 2.0) if k > 1 else np.nan
            ds[cid] = (t.median() - h.median()) / sd if sd and not np.isnan(sd) and sd > 0 else np.nan
            hm.append(float(h.median())); tm.append(float(t.median()))
        vals = [v for v in ds.values() if not np.isnan(v)]
        if not vals:
            continue
        pos = sum(1 for v in vals if v > 0)
        neg = sum(1 for v in vals if v < 0)
        same = max(pos, neg)
        dmed = float(np.median(vals))
        ok = (abs(dmed) >= D_MIN) and (same >= max(2, int(np.ceil(TREND_SAME_MIN * len(vals)))))
        hmed = float(np.median(hm)) if hm else np.nan
        tmed = float(np.median(tm)) if tm else np.nan
        res2.append((c, ds, dmed, same, len(vals), ok, hmed, tmed))
    res2.sort(key=lambda x: -abs(x[2]))
    for c, ds, dmed, same, nv, ok, hmed, tmed in res2:
        P('%-26s %10.3f %10.3f %10.3f %8.2f %5d/%-2d   %s'
          % (c[:26], hmed, tmed, tmed - hmed, dmed, same, nv,
             '*** 通过 ***' if ok else ''))

    P('\n[4] 结论')
    if n_ok == 0 and not any(r[5] for r in res2):
        P('  未发现跨周期一致的单调趋势，末期突变候选也被形态否定 → 无信号')
    else:
        P('  存在候选信号，务必看趋势图形态后再下结论（数字会被形状欺骗）')

    # ---------- 出图 ----------
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        for cand in ['DejaVu Sans', 'Microsoft YaHei', 'SimHei']:
            try:
                matplotlib.font_manager.findfont(cand, fallback_to_default=False)
                plt.rcParams['font.sans-serif'] = [cand]; break
            except Exception:
                continue
        plt.rcParams['axes.unicode_minus'] = False
        plot_ch = [c for c in LOAD_CH if c + '_mean_rel' in F.columns]
        nc = len(plot_ch)
        if nc == 0:
            P('\n[图] 无可用通道（缺 *_rel 列），跳过绘图')
            save('trend.txt')
            return
        ncol, nrow = 2, int(np.ceil(nc / 2.0))
        fig, axes = plt.subplots(nrow, ncol, figsize=(13, 3.4 * nrow), squeeze=False)
        for i, c in enumerate(plot_ch):
            ax = axes[i // ncol][i % ncol]
            for cid in cyc:
                g = F[F['cycle'] == cid].sort_values('prog')
                ax.plot(g['prog'], g[c + '_mean_rel'], '-', lw=1.0,
                        alpha=0.85, label='c%d' % cid)
                if c + '_max_rel' in g.columns:
                    ax.plot(g['prog'], g[c + '_max_rel'], '--', lw=0.7, alpha=0.45)
            ax.axhline(1.0, color='gray', ls=':', lw=1)
            ax.set_title(c, fontsize=9)
            ax.set_xlabel('cycle progress (0=new,1=change)', fontsize=7)
            ax.set_ylabel('rel (1.0 = new)', fontsize=7)
            ax.tick_params(labelsize=7)
            ax.legend(fontsize=6, ncol=3)
        for j in range(i + 1, nrow * ncol):
            axes[j // ncol][j % ncol].axis('off')
        fig.tight_layout()
        os.makedirs(OUT_DIR, exist_ok=True)
        png = os.path.join(OUT_DIR, 't1111_趋势图.png')
        fig.savefig(png, dpi=130)
        P('\n[图] %s' % png)
    except Exception as e:
        P('\n[图] 绘图失败（不影响结论）: %s' % e)
    save('trend.txt')


# ---------------- 相位对齐（xalign）辅助 ----------------
def _desc_run(x, tol=0.0, min_span=0.0):
    """最长「下降」连续段（允许 <=tol 的回弹，抗伺服抖动），返回 x 上的半开区间 (i0,i1)。
    优先取 X 跨度 >= min_span 的最长段；都不满足则退回最长段。"""
    n = len(x)
    if n < 2:
        return (0, n)
    dx = np.diff(x)
    runs = []
    i = 0
    while i < n - 1:
        if dx[i] <= tol:
            j = i
            while j + 1 < n - 1 and dx[j + 1] <= tol:
                j += 1
            runs.append((i, j + 2, j - i + 1, float(x[i] - x[j + 1])))
            i = j + 1
        else:
            i += 1
    if not runs:
        return (0, n)
    good = [r for r in runs if abs(r[3]) >= min_span]
    pool = good if good else runs
    # 按「跨度」优先（完整扫掠跨度最大），点数次之
    best = max(pool, key=lambda r: (abs(r[3]), r[2]))
    return (best[0], best[1])


def _spearman(a, b):
    from scipy import stats as _s
    a = np.asarray(a, dtype=float); b = np.asarray(b, dtype=float)
    ok = np.isfinite(a) & np.isfinite(b)
    if int(ok.sum()) < 8:
        return np.nan, np.nan
    r, p = _s.spearmanr(a[ok], b[ok])
    return float(r), float(p)


def _prog_of(s):
    return str(s).split('(')[0].strip()


def _xalign_piece(g, z0, metrics, cfg):
    """单件 → 定位切断段（X 单调下降 + 负载在线），各指标按 X 升序排好返回"""
    z = pd.to_numeric(g['L_abso_z'], errors='coerce')
    d = g[np.isclose(z, z0, atol=cfg['z_tol'])]
    if 'L_spindle_speed' in d.columns:
        d = d[pd.to_numeric(d['L_spindle_speed'], errors='coerce').fillna(0) > 0]
    if len(d) < cfg['min_points']:
        return None
    d = d.sort_values('_t')
    x = pd.to_numeric(d['L_abso_x'], errors='coerce').to_numpy(float)
    okx = np.isfinite(x)
    if int(okx.sum()) < cfg['min_points']:
        return None
    d = d[okx]; x = x[okx]
    # ① 负载筛选前置：先剔掉「快速趋近段」和空走（那段 X 也在降，但负载很低）。
    #    必须在段检测之前做，否则趋近段会和真扫掠连成一段，把 X 上界拉到 100/231。
    if 'L_spindle_load' in d.columns:
        ld = pd.to_numeric(d['L_spindle_load'], errors='coerce').to_numpy(float)
        pos = ld[np.isfinite(ld) & (ld > 0)]
        if len(pos) >= 5:
            keep = np.isfinite(ld) & (ld >= cfg['load_frac'] * float(np.median(pos)))
            d = d[keep]; x = x[keep]
    if len(d) < cfg['min_points']:
        return None
    # ② 下降段：容差合并啄切退刀，按跨度取最大段 = 完整扫掠
    i0, i1 = _desc_run(x, cfg['desc_tol'], cfg['min_span'])
    seg = d.iloc[i0:i1]
    if len(seg) < cfg['min_points']:
        return None
    if cfg['drop_cap'] and 'L_spindle_speed' in seg.columns:
        # 参考值用【本周期全局最高转速】，不能用段内最高（段内若没到封顶会误删正常点）
        ref = cfg.get('cap_ref')
        if ref is None or not np.isfinite(ref) or ref <= 0:
            ref = float(pd.to_numeric(d['L_spindle_speed'],
                                     errors='coerce').max()) if 'L_spindle_speed' in d else 0.0
        if ref and ref > 0:
            sp = pd.to_numeric(seg['L_spindle_speed'], errors='coerce')
            seg = seg[sp < cfg['cap_frac'] * ref]
    if len(seg) < cfg['min_points']:
        return None
    xs = pd.to_numeric(seg['L_abso_x'], errors='coerce').to_numpy(float)
    o = np.argsort(xs)
    xs = xs[o]
    out = {'xs': xs, 'x_lo': float(xs[0]), 'x_hi': float(xs[-1]), 'n': int(len(xs))}
    for m in metrics:
        if m not in seg.columns:
            continue
        v = pd.to_numeric(seg[m], errors='coerce').to_numpy(float)[o]
        if np.isfinite(v).sum() >= 3:
            out[m] = v
    return out


def cmd_xalign(a):
    banner('T1111 相位对齐磨损检验（按 X 直径对齐切断曲线）')
    path = find_labeled(a.labeled)
    P('\n输入: %s' % path)
    head = list(pd.read_csv(path, nrows=0, encoding='utf-8-sig').columns)
    metrics = [m for m in (a.metrics.split(',') if a.metrics else XALIGN_METRICS)
               if m in head]
    if not metrics:
        raise SystemExit('打标文件里找不到任何待检验指标列')
    cfg = dict(z_tol=XALIGN_Z_TOL, grid_n=a.grid or XALIGN_GRID_N,
               base_k=a.basek or XALIGN_BASE_K, head_k=XALIGN_HEAD_K,
               min_pieces=XALIGN_MIN_PIECES, min_points=XALIGN_MIN_POINTS,
               desc_tol=XALIGN_DESC_TOL, min_span=XALIGN_MIN_SPAN,
               keep_frac=XALIGN_KEEP_FRAC, load_frac=XALIGN_LOAD_FRAC,
               drop_cap=a.dropcap or XALIGN_DROP_CAP, cap_frac=XALIGN_CAP_FRAC,
               min_fill=XALIGN_MIN_FILL)
    need = ['_time', 'L_allnum', 'life_cycle', 'L_abso_x', 'L_abso_z',
            'L_spindle_load', 'L_spindle_speed', 'L_main_program',
            'life_used_pct_cut']
    use = [c for c in need + metrics if c in head]
    df = pd.read_csv(path, usecols=use, encoding='utf-8-sig', low_memory=False)
    df['_t'] = pd.to_datetime(df['_time'], errors='coerce')
    df = df.dropna(subset=['_t', 'life_cycle']).sort_values('_t').reset_index(drop=True)
    for c in ['L_abso_x', 'L_abso_z', 'L_spindle_load', 'L_spindle_speed'] + metrics:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    P('读入 %d 行，待检指标 %d 个: %s' % (len(df), len(metrics), metrics))

    P('\n[1] 判据')
    P('  对齐轴 = X（切断为径向进给；Z 全程锁定，不是对齐轴）')
    P('  单件流程: 定位切断Z带 → 取X单调下降段 → 剔低负载趋近段 → ')
    P('            各指标按X升序插值到 %d 点网格 → 除以前%d件基线 = HI' % (
        cfg['grid_n'], cfg['base_k']))
    P('  HI=1.0 即新刀水平；HI 随刀龄单调上升 = 磨损信号')
    P('  转速封顶段(G96失效区)处理: %s' % ('已剔除' if cfg['drop_cap'] else '保留'))

    # ---- 逐周期：几何定位 + 建曲线 ----
    P('\n[2] 逐周期几何定位')
    P('%-5s %-8s %6s %8s %8s %8s %7s' % (
        '周期', '程序', '件数', 'Z0', 'X网格起', 'X网格止', '网格覆盖'))
    P('-' * 60)
    CYC = {}
    for cid, d in df.groupby('life_cycle'):
        cid = int(cid)
        d = d.sort_values('_t')
        if d['L_allnum'].nunique() < cfg['min_pieces']:
            continue
        sp = d[d['L_spindle_speed'].fillna(0) > 0] if 'L_spindle_speed' in d else d
        if len(sp) < 50:
            continue
        zmode = sp['L_abso_z'].round(1).mode()
        if not len(zmode):
            continue
        z0 = float(zmode.iloc[0])
        # G96 封顶参考转速：本周期出现过的全局最高转速
        cfg['cap_ref'] = float(sp['L_spindle_speed'].max()) if \
            'L_spindle_speed' in sp.columns else 0.0
        pieces, keys = [], []
        for anum, g in d.groupby('L_allnum'):
            r = _xalign_piece(g, z0, metrics, cfg)
            if r is not None:
                pieces.append(r); keys.append(anum)
        if len(pieces) < cfg['min_pieces']:
            continue
        order = np.argsort(keys)
        pieces = [pieces[i] for i in order]
        keys = [keys[i] for i in order]
        los = np.array([p['x_lo'] for p in pieces])
        his = np.array([p['x_hi'] for p in pieces])
        glo = float(np.percentile(los, 90)); ghi = float(np.percentile(his, 10))
        if ghi - glo < 1.0:
            glo, ghi = float(np.median(los)), float(np.median(his))
        grid = np.linspace(glo, ghi, cfg['grid_n'])

        curves, skipped = {}, []
        for m in metrics:
            M = np.full((len(pieces), cfg['grid_n']), np.nan)
            for i, p in enumerate(pieces):
                if m not in p:
                    continue
                xs, vs = p['xs'], p[m]
                ok = np.isfinite(vs) & np.isfinite(xs)
                if int(ok.sum()) < 3:
                    continue
                msk = (grid >= p['x_lo']) & (grid <= p['x_hi'])
                M[i, msk] = np.interp(grid[msk], xs[ok], vs[ok])
            if np.isfinite(M).mean() < cfg['min_fill']:
                skipped.append((m, '覆盖不足')); continue
            cov = np.mean(np.isfinite(M), axis=0) >= cfg['keep_frac']
            M[:, ~cov] = np.nan
            # 质量闸：基线必须为正、且非恒定。伺服负载类很多轴恒为 0 或只有 3~5 个台阶，
            # 拿它们算 HI 会得到 0.000 / 无穷大 这种无意义值，必须挡掉。
            bk = min(cfg['base_k'], M.shape[0])
            b0 = np.nanmedian(M[:bk, :], axis=0)
            if np.nanmean(b0 > 0) < 0.90:
                skipped.append((m, '基线近零')); continue
            if not np.isfinite(np.nanstd(M)) or np.nanstd(M) <= 0:
                skipped.append((m, '数值恒定')); continue
            curves[m] = M
        if skipped:
            P('       （跳过: %s）' % ', '.join('%s-%s' % (m.replace('L_', ''), w)
                                              for m, w in skipped))
        if not curves:
            continue
        prog = _prog_of(d['L_main_program'].dropna().iloc[0]) if \
            d['L_main_program'].notna().any() else '?'
        npt = np.median([p['n'] for p in pieces])
        P('%-5d %-8s %6d %8.2f %8.2f %8.2f %6.0f点' % (
            cid, prog, len(pieces), z0, glo, ghi, npt))
        CYC[cid] = dict(prog=prog, z0=z0, grid=grid, curves=curves,
                        keys=keys, pieces=len(pieces),
                        used=[m for m in metrics if m in curves])
    if not CYC:
        P('\n没有任何周期满足件数/几何条件，中止'); save('xalign.txt'); return

    # ---- 逐周期逐指标：HI 与趋势 ----
    P('\n[3] 逐周期趋势（Spearman r 对刀龄；末段-首段 = 磨损抬升）')
    P('%-5s %-20s %8s %8s %9s %8s %8s' % (
        '周期', '指标', 'r', 'p', '末-首%', '网格同向', '末期HI'))
    P('-' * 74)
    HI_store = {}          # (cid, metric) -> 每件 HI 标量
    prog_of = {}
    for cid in sorted(CYC):
        C = CYC[cid]
        prog_of[cid] = C['prog']
        n = C['pieces']
        idx = np.arange(1, n + 1, dtype=float)
        for m in C['used']:
            M = C['curves'][m]
            bk = min(cfg['base_k'], n)
            with np.errstate(invalid='ignore', divide='ignore'):
                base = np.nanmedian(M[:bk, :], axis=0)
                HI = M / base.reshape(1, -1)
            HI[~np.isfinite(HI)] = np.nan
            hi_i = np.nanmedian(HI, axis=1)
            HI_store[(cid, m)] = hi_i
            r, p = _spearman(idx, hi_i)
            hk = min(cfg['head_k'], max(2, n // 4))
            fir = np.nanmedian(hi_i[:hk]); las = np.nanmedian(hi_i[-hk:])
            dlt = 100.0 * (las - fir) / fir if np.isfinite(fir) and fir != 0 else np.nan
            # 网格点同向率：末段中位数 vs 首段中位数，多少个网格点更高
            with np.errstate(invalid='ignore'):
                late = np.nanmedian(M[-hk:, :], axis=0)
                earl = np.nanmedian(M[:hk, :], axis=0)
            ok = np.isfinite(late) & np.isfinite(earl) & (earl != 0)
            fr = float(np.mean(late[ok] > earl[ok])) if ok.any() else np.nan
            P('%-5d %-20s %8.3f %8.4f %9.2f %8.0f%% %8.3f' % (
                cid, m[:20], r, p, dlt, 100 * fr if np.isfinite(fr) else -1,
                las if np.isfinite(las) else np.nan))

    # ---- 综合 HI：各指标先转相对变化(HI-1)再等权平均 ----
    P('\n[4] 综合 HI（各指标先取 HI-1 即相对变化，再等权平均，量纲无关）')
    HI_comb = {}
    for cid in sorted(CYC):
        n = CYC[cid]['pieces']
        stack = [HI_store[(cid, m)] - 1.0 for m in CYC[cid]['used']
                 if (cid, m) in HI_store]
        if not stack:
            continue
        HI_comb[cid] = 1.0 + np.nanmean(np.stack(stack), axis=0)

    # ---- 队列级合并：同程序+同Z，按寿命进度分箱 ----
    P('\n[5] 队列级合并（同程序+同Z 视为同工况，按寿命进度分 %d 段看形状）' % XALIGN_BINS)
    cohorts = {}
    for cid in sorted(CYC):
        k = (prog_of[cid], round(CYC[cid]['z0'], 1))
        cohorts.setdefault(k, []).append(cid)
    allm = sorted({m for c in CYC for m in CYC[c]['used']})
    P('\n%-14s %6s %s' % ('队列(程序,Z)', '周期数', ''.join('%9s' % m.replace('L_', '')[:9] for m in allm)))
    P('-' * (24 + 9 * len(allm)))
    pooled_rows = []
    for k in sorted(cohorts):
        cids = cohorts[k]
        if len(cids) < 2:
            continue
        xs, ys = [], []
        for cid in cids:
            n = CYC[cid]['pieces']
            pr = np.linspace(0.0, 1.0, n)
            for m in allm:
                if (cid, m) not in HI_store:
                    continue
                v = HI_store[(cid, m)]
                ok = np.isfinite(v)
                xs.append(pr[ok]); ys.append(v[ok])
        line = []
        for m in allm:
            rr = np.nan
            if len(xs):
                # 每个指标单独 pooled（这里只算综合口径，故用全部指标混合）
                pass
            line.append(rr)
        P('%-14s %6d %s' % ('%s,Z=%.1f' % (k[0], k[1]), len(cids),
                            ''.join('%9s' % '-' for _ in allm)))
        pooled_rows.append((k, cids))

    # 队列级：逐指标 pooled spearman + 分箱形状
    P('\n  队列级逐指标 pooled 检验（把所有周期的寿命进度拉到 0~1 后合并）')
    P('  %-14s %-20s %8s %10s   %s' % ('队列', '指标', 'r', 'p', '分箱中位数 HI（新刀→报废）'))
    P('  ' + '-' * 108)
    cohort_stat = []
    for k, cids in pooled_rows:
        for m in allm:
            pr_all, hi_all = [], []
            for cid in cids:
                if (cid, m) not in HI_store:
                    continue
                n = CYC[cid]['pieces']
                pr = np.linspace(0.0, 1.0, n)
                v = HI_store[(cid, m)]
                ok = np.isfinite(v)
                pr_all.append(pr[ok]); hi_all.append(v[ok])
            if not pr_all:
                continue
            x = np.concatenate(pr_all); y = np.concatenate(hi_all)
            if len(x) < 30:
                continue
            r, p = _spearman(x, y)
            edges = np.linspace(0, 1, XALIGN_BINS + 1)
            bmed = []
            for b in range(XALIGN_BINS):
                sel = (x >= edges[b]) & (x <= edges[b + 1] if b == XALIGN_BINS - 1
                                         else x < edges[b + 1])
                bmed.append(np.median(y[sel]) if sel.any() else np.nan)
            cohort_stat.append((k, m, r, p, bmed, len(x)))
            P('  %-14s %-20s %8.3f %10.2e   %s' % (
                '%s,Z=%.1f' % (k[0], k[1]), m[:20], r, p,
                ' '.join('%5.3f' % v if np.isfinite(v) else '   - ' for v in bmed)))
    # 综合 HI 的队列级
    P('\n  综合 HI 队列级:')
    comb_stat = []
    for k, cids in pooled_rows:
        pr_all, hi_all = [], []
        for cid in cids:
            if cid not in HI_comb:
                continue
            n = CYC[cid]['pieces']
            pr = np.linspace(0.0, 1.0, n)
            v = HI_comb[cid]
            ok = np.isfinite(v)
            pr_all.append(pr[ok]); hi_all.append(v[ok])
        if not pr_all:
            continue
        x = np.concatenate(pr_all); y = np.concatenate(hi_all)
        r, p = _spearman(x, y)
        edges = np.linspace(0, 1, XALIGN_BINS + 1)
        bmed = []
        for b in range(XALIGN_BINS):
            sel = (x >= edges[b]) & (x <= edges[b + 1] if b == XALIGN_BINS - 1
                                     else x < edges[b + 1])
            bmed.append(np.median(y[sel]) if sel.any() else np.nan)
        comb_stat.append((k, r, p, bmed))
        P('    %-14s r=%7.3f p=%9.2e n=%5d  %s' % (
            '%s,Z=%.1f' % (k[0], k[1]), r, p, len(x),
            ' '.join('%5.3f' % v if np.isfinite(v) else '   - ' for v in bmed)))

    # ---- 结论 ----
    P('\n[6] 结论')
    def _judge(stat, name):
        if not stat:
            P('  %s：无有效结果' % name); return
        rs = [s[2] if len(s) > 4 else s[1] for s in stat]
        pos = sum(1 for v in rs if np.isfinite(v) and v > 0)
        med = float(np.nanmedian(rs))
        P('  %s：队列数 %d，r 中位 %.3f，其中 r>0 的 %d 个 → %s' % (
            name, len(rs), med, pos,
            '存在一致的上升迹象' if (med >= 0.30 and pos >= max(2, int(np.ceil(0.8 * len(rs)))))
            else ('方向一致但量级偏弱' if (med > 0 and pos >= max(2, int(np.ceil(0.8 * len(rs)))))
                  else '未见一致的磨损规律')))
    _judge(cohort_stat, '单指标')
    _judge(comb_stat, '综合HI')

    # ---- 落盘 HI 明细 ----
    rows = []
    for cid in sorted(CYC):
        n = CYC[cid]['pieces']
        pr = np.linspace(0.0, 1.0, n)
        for i in range(n):
            r = {'life_cycle': cid, 'prog': prog_of[cid], 'piece_idx': i + 1,
                 'allnum': CYC[cid]['keys'][i], 'progress': pr[i]}
            for m in CYC[cid]['used']:
                if (cid, m) in HI_store:
                    r['HI_' + m] = HI_store[(cid, m)][i]
            if cid in HI_comb:
                r['HI_comb'] = HI_comb[cid][i]
            rows.append(r)
    HI_df = pd.DataFrame(rows)
    dst = os.path.join(OUT_DIR, 'xalign_HI.csv')
    HI_df.to_csv(dst, index=False, encoding='utf-8-sig')
    P('\n[输出] 逐件 HI 明细: %s （%d 行）' % (dst, len(HI_df)))

    # ---- 图 ----
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        for cand in ['DejaVu Sans', 'Microsoft YaHei', 'SimHei']:
            try:
                matplotlib.font_manager.findfont(cand, fallback_to_default=False)
                plt.rcParams['font.sans-serif'] = [cand]; break
            except Exception:
                continue
        plt.rcParams['axes.unicode_minus'] = False
        # 每个队列一张图，画【所有指标】的分箱曲线：谁在涨、谁在拖后腿，一眼看清
        if cohort_stat:
            ks = []
            for k, m, r, p, bmed, n in cohort_stat:
                if k not in ks:
                    ks.append(k)
            fig, axes = plt.subplots(1, len(ks), figsize=(6.2 * len(ks), 4.4),
                                     squeeze=False)
            xs = (np.arange(XALIGN_BINS) + 0.5) / XALIGN_BINS
            for j, k in enumerate(ks):
                ax = axes[0][j]
                for kk, m, r, p, bmed, n in cohort_stat:
                    if kk != k:
                        continue
                    lw = 2.2 if m == 'L_spindle_load' else 1.0
                    ax.plot(xs, bmed, 'o-', lw=lw, ms=4,
                            label='%s (r=%.2f)' % (m.replace('L_', ''), r))
                ax.axhline(1.0, color='gray', ls=':', lw=1)
                ax.set_title('程序 %s  Z=%.1f' % (k[0], k[1]), fontsize=11)
                ax.set_xlabel('life progress (0=new, 1=change)', fontsize=8)
                ax.set_ylabel('HI (1.0 = new tool)', fontsize=8)
                ax.tick_params(labelsize=8)
                ax.legend(fontsize=7)
            fig.tight_layout()
            png = os.path.join(OUT_DIR, 'xalign_各指标队列曲线.png')
            fig.savefig(png, dpi=130); P('[图] %s' % png)
    except Exception as e:
        P('[图] 绘图失败（不影响结论）: %s' % e)
    save('xalign.txt')


def cmd_alarm(a):
    """委托给已验证的通用工具 alarm_online.py（也服务 T2525）"""
    path = find_datafile(a.file)
    banner('T1111 无监督异常检测（委托 alarm_online.py）')
    P('\n数据文件: %s' % path)
    P('请直接使用（mode 为 fit / score）：')
    P('  ./.venv/Scripts/python.exe -X utf8 -W ignore alarm_online.py fit   --file "%s"' % path)
    P('  ./.venv/Scripts/python.exe -X utf8 -W ignore alarm_online.py score --file "%s"' % path)
    P('\n（该工具为通用件，T2525 已验证；T1111 专用参数如阈值应在 fit 后重校）')
    save('alarm.txt')


def cmd_archive(a):
    banner('归档散落的 T1111 旧脚本')
    dst = os.path.join(WORKSPACE, ARCHIVE_DIR)
    os.makedirs(dst, exist_ok=True)
    n = 0
    for f in ARCHIVE_FILES:
        src = os.path.join(WORKSPACE, f)
        if not os.path.exists(src):
            P('  跳过（不存在）: %s' % f); continue
        shutil.move(src, os.path.join(dst, f))
        P('  已移动: %s  ->  %s/' % (f, ARCHIVE_DIR)); n += 1
    P('\n共移动 %d 个文件。注意：这些文件的产出（txt/csv/png）留在原地未动。' % n)
    P('今后 T1111 的一切分析走 t1111_pipeline.py。')
    save('archive.txt')


def cmd_all(a):
    global _IN_ALL
    _IN_ALL = True
    cmd_mark(a)
    _LINES.append('')
    cmd_feat(a)
    _LINES.append('')
    cmd_stats(a)
    _LINES.append('')
    cmd_trend(a)
    save('all.txt')


# ================= 入口 =================
def main():
    ap = argparse.ArgumentParser(
        description='T1111 统一分析入口（设备 %s / %s 刀塔）' % (DEVICE, TOWER))
    ap.add_argument('--metrics', default=None,
                    help='xalign 待检指标，逗号分隔，覆盖 XALIGN_METRICS')
    ap.add_argument('--grid', type=int, default=None, help='xalign X 网格点数')
    ap.add_argument('--basek', type=int, default=None, help='xalign 新刀基线件数')
    ap.add_argument('--dropcap', action='store_true',
                    help='xalign 剔除主轴转速被封顶段（G96 失效区）')
    ap.add_argument('cmd', choices=['scan', 'mark', 'feat', 'stats', 'trend',
                                    'window', 'xalign', 'alarm', 'archive', 'all'])
    ap.add_argument('--labeled', default=None, help='指定打标后的 CSV（window 用）')
    ap.add_argument('--freq', action='store_true', help='window 时额外计算频域特征（较慢）')
    ap.add_argument('--ch', default=None, help='window 参与通道，逗号分隔，覆盖 WIN_CH')
    ap.add_argument('--parquet', action='store_true', help='window 输出 parquet（更小更快）')
    ap.add_argument('--cutrows', action='store_true',
                    help='window 只输出参与计算的行（默认输出原始全部行，未参与计算的行特征为空）')
    ap.add_argument('--file', default=None, help='数据文件（相对工作区或绝对路径）')
    ap.add_argument('--keep', default='tool', choices=['tool', 'all'],
                    help='mark 时保留范围，默认只留 T1111')
    ap.add_argument('--cols', default='all', choices=['all', 'need'],
                    help="读入列范围：all=源数据全部列(默认，输出不缺列) / "
                         "need=仅分析所需列(省内存)")
    ap.add_argument('--rebuild', action='store_true', help='强制重算调用特征表')
    a = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    {'scan': cmd_scan, 'mark': cmd_mark, 'feat': cmd_feat, 'stats': cmd_stats,
     'trend': cmd_trend, 'window': cmd_window, 'xalign': cmd_xalign,
     'alarm': cmd_alarm, 'archive': cmd_archive, 'all': cmd_all}[a.cmd](a)


if __name__ == '__main__':
    main()
