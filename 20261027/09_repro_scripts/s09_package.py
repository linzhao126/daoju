# -*- coding: utf-8 -*-
"""s09_package.py — P0-01 交付打包

产出
  01_交付清单与版本指纹/import_graph.csv
  01_交付清单与版本指纹/environment_requirements.txt
  01_交付清单与版本指纹/MANIFEST.csv
  01_交付清单与版本指纹/result_hashes.csv
  02_源代码与配置/source/*            （拷贝运行文件）
  02_源代码与配置/schema/*.sql
  02_源代码与配置/configs/params.csv, leakage_allow_deny_list.csv
  03_原始数据与标签/raw_or_traceable/raw_extract_T1111.csv.gz
  03_原始数据与标签/tool_db_snapshot/*
"""
import os, ast, sys, gzip, json, shutil, hashlib, subprocess
import pandas as pd

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import wsroot as _wsroot          # G2：不再数 '..'，按工作区特征向上定位
ROOT = _wsroot.find(_os.path.dirname(_os.path.abspath(__file__)))
BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SRC = os.path.join(ROOT, '2ndPrediction')
TL = os.path.join(SRC, 'tool_log')

D1 = os.path.join(BASE, '01_交付清单与版本指纹')
D2 = os.path.join(BASE, '02_源代码与配置')
D2S = os.path.join(D2, 'source')
D2H = os.path.join(D2, 'schema')
D2C = os.path.join(D2, 'configs')
D3 = os.path.join(BASE, '03_原始数据与标签')
D3R = os.path.join(D3, 'raw_or_traceable')
D3DB = os.path.join(D3, 'tool_db_snapshot')
D8 = os.path.join(BASE, '08_本轮可复现执行日志')
for p in (D1, D2S, D2H, D2C, D3R, D3DB, D8):
    os.makedirs(p, exist_ok=True)

RUN_FILES = [
    (os.path.join(TL, 'server.py'), 'server.py'),
    (os.path.join(TL, 'server_stdlib.py'), 'server_stdlib.py'),
    (os.path.join(TL, 'index.html'), 'index.html'),
    (os.path.join(TL, 'influx_source.py'), 'influx_source.py'),
    (os.path.join(TL, 'simulator.py'), 'simulator.py'),
    (os.path.join(SRC, 'bin_decision.py'), 'bin_decision.py'),
    (os.path.join(ROOT, 't1111_pipeline.py'), 't1111_pipeline.py'),
    # 下面这个不在原来"5 个文件"清单里：t1111_pipeline 在 mark 子命令内**延迟 import**，
    # 静态机械列清单会漏掉它 → 复现时 mark 会直接 ImportError
    (os.path.join(ROOT, 'label_tool_life.py'), 'label_tool_life.py'),
]

# ---------------- 1. import 图
STD = set(sys.stdlib_module_names) | {'__future__'}
THIRD = {'numpy', 'pandas', 'fastapi', 'uvicorn', 'pydantic', 'matplotlib', 'requests', 'scipy', 'sklearn'}
LOCAL = {n for _, n in RUN_FILES} | {'simulator', 'influx_source', 'label_tool_life'}

rows = []
for path, name in RUN_FILES:
    if not name.endswith('.py'):
        # index.html 不是 Python：只记录其外部脚本引用，不做 AST
        rows.append(dict(File=name, Module='(html: fetch /api/*  无外部 JS 依赖)',
                         Kind='local', Path=os.path.relpath(path, ROOT)))
        continue
    tree = ast.parse(open(path, encoding='utf-8').read())
    mods = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods += [a.name.split('.')[0] for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            mods.append(n.module.split('.')[0])
    for m in sorted(set(mods)):
        kind = ('stdlib' if m in STD else
                'third_party' if m in THIRD else
                'local' if m in LOCAL else 'unknown')
        rows.append(dict(File=name, Module=m, Kind=kind,
                         Path=os.path.relpath(path, ROOT)))
IG = pd.DataFrame(rows)
IG.to_csv(os.path.join(D1, 'import_graph.csv'), index=False, encoding='utf-8-sig')
unk = IG[IG.Kind == 'unknown'].Module.unique().tolist()
print('[1] import_graph.csv %d 行；未归类模块: %s' % (len(IG), unk or '无'))

# ---------------- 2. 环境
try:
    out = subprocess.run([sys.executable, '-m', 'pip', 'freeze'], capture_output=True, text=True, timeout=120)
    req = out.stdout.strip().splitlines()
except Exception as e:
    req = ['# pip freeze 失败: %s' % e]
open(os.path.join(D1, 'environment_requirements.txt'), 'w', encoding='utf-8').write(
    '\n'.join(['# python %s' % sys.version.replace('\n', ' '),
               '# platform %s' % sys.platform, ''] + req) + '\n')
print('[2] environment_requirements.txt %d 个包' % len(req))

# ---------------- 3. 源码拷贝
for path, name in RUN_FILES:
    shutil.copy2(path, os.path.join(D2S, name))
for f in ('数据库字段说明.md', '页面数据口径说明.md', 'README.md'):
    p = os.path.join(TL, f)
    if os.path.exists(p):
        shutil.copy2(p, os.path.join(D2S, f))
print('[3] source/ %d 个文件' % len(os.listdir(D2S)))

# ---------------- 4. schema
DDL = """-- tool.db 实际结构（由 s09 从 sqlite_master 导出，原样）
-- 注意：raw_rows 的 UNIQUE(ts) 是 2026-10-21 重建迁移加的；
--       老库若在迁移前，raw_rows 无该约束
"""
con = None
import sqlite3
dbp = os.path.join(TL, 'data', 'tool.db')
con = sqlite3.connect('file:%s?mode=ro' % dbp.replace('\\', '/'), uri=True)
for (sql,) in con.execute("select sql from sqlite_master where sql is not null order by type desc, name"):
    DDL += sql + ';\n\n'
open(os.path.join(D2H, 'tool_db_ddl.sql'), 'w', encoding='utf-8').write(DDL)

PROP = """-- 建议新增：预测日志表（P1-01，本轮仅设计，未落库）
CREATE TABLE IF NOT EXISTS predictions(
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_predict       TEXT NOT NULL,              -- 推理发生的真实时刻
    physical_life_id TEXT NOT NULL,              -- 物理刃口（不翻刃：1 周期=1 刃口）
    prog             INTEGER,
    tool_age_pieces  INTEGER NOT NULL,           -- 预测时已知刀龄
    tool_age_source  TEXT,                       -- event/cold/UNKNOWN
    data_watermark   TEXT,                       -- 输入数据水位线
    model_version    TEXT NOT NULL,
    feature_version  TEXT,
    rul_point        REAL,
    rul_lo           REAL,
    rul_hi           REAL,
    pi_level         REAL,
    calibrated       INTEGER DEFAULT 0,          -- 0=探索性区间，未校准
    quality_grade    TEXT,
    is_replay        INTEGER NOT NULL DEFAULT 0, -- 1=事后回放（不得冒充前瞻）
    actual_end_ts    TEXT,                       -- 后验
    actual_life      INTEGER,
    actual_label     TEXT,
    label_quality    TEXT);
CREATE INDEX IF NOT EXISTS i_pred_life ON predictions(physical_life_id, ts_predict);
"""
open(os.path.join(D2H, 'predictions_DDL_proposed.sql'), 'w', encoding='utf-8').write(PROP)
print('[4] schema/ 2 个 sql')

# ---------------- 5. 配置与泄漏清单
PAR = [
    ('K_BASE', 20, '线上 HI 基线件数', 'server.py:70'),
    ('W', 10, '线上 HI 平滑窗口', 'server.py:71'),
    ('TAU', 0.01, '延长许可阈值（相对上升）', 'server.py:72'),
    ('BASE_F', 0.4, '复核点 = 0.4×M0', 'server.py:73'),
    ('EXT_F', 0.9, '延长上限 = 0.9×M0', 'server.py:73'),
    ('MIN_N_BASIC', 3, '四格最少历史条数', 'server.py:80'),
    ('MIN_N_P85', 5, 'P85 与判定线最少历史条数', 'server.py:81'),
    ('EXP_M0', 100.0, '无历史时代码内经验值（非计算）', 'server.py:83'),
    ('SEAL_DELAY_SEC', 120, '封口延迟', 'server.py:91'),
    ('KEEP_RAW_DAYS', 7, 'raw_rows 保留天数', 'server.py:86'),
    ('KEEP_PIECES_DAYS', 30, 'pieces 保留天数', 'server.py:87'),
    ('XALIGN_Z_TOL', 1.0, '离线 Z 带容差 mm', 't1111_pipeline.py:216'),
    ('XALIGN_GRID_N', 40, '离线 X 网格点数', 't1111_pipeline.py:217'),
    ('XALIGN_BASE_K', 10, '离线 HI 基线件数', 't1111_pipeline.py:218'),
    ('XALIGN_DESC_TOL', 2.5, '啄切容差 mm', 't1111_pipeline.py:222'),
    ('XALIGN_MIN_PIECES', 40, '周期最少件数', 't1111_pipeline.py:220'),
    ('XALIGN_KEEP_FRAC', 0.70, '网格点最低覆盖率', 't1111_pipeline.py:226'),
    ('XALIGN_LOAD_FRAC', 0.50, '负载门控比例', 't1111_pipeline.py:227'),
    ('PARITY_K_CMP', 10, '本轮 parity 对比用的归一化件数', 's04_hi_parity.py'),
    ('CUSUM_K', 0.5, '崩刃检测 CUSUM 参考值', 's07_chipping.py'),
    ('CUSUM_H', 6.0, '崩刃检测 CUSUM 阈值', 's07_chipping.py'),
    ('NOISE_FLOOR', 0.01, '噪声尺度下限', 's07_chipping.py'),
]
pd.DataFrame(PAR, columns=['Param', 'Value', 'Meaning', 'Where']).to_csv(
    os.path.join(D2C, 'params.csv'), index=False, encoding='utf-8-sig')

LEAK = [
    ('progress', 'DENY', '终点比例，直接泄漏'),
    ('life_used_pct_cut', 'DENY', '已用寿命比例（泄漏字段）'),
    ('life_used_pct_time', 'DENY', '时间维度已用比例'),
    ('life_remain_pct_time', 'DENY', '剩余比例'),
    ('rul_sec', 'DENY', '剩余秒数（标签本体）'),
    ('rul_hour', 'DENY', '剩余小时'),
    ('elapsed_sec', 'DENY', '已耗时'),
    ('change_reason', 'DENY', '换刀原因（终点信息）'),
    ('real_failure', 'DENY', '打标脚本按"发生换刀"赋的值'),
    ('life_cycle', 'DENY', '生命周期编号（分组键，不是特征）'),
    ('cycle_cover_ratio', 'CAUTION', '周期覆盖率，可能含终点信息'),
    ('in_life_range', 'CAUTION', '打标区间标记'),
    ('L_allnum', 'ALLOW_AS_KEY', '全局件计数（9 把刀共用），不可当刀龄'),
    ('L_spindle_load', 'ALLOW', '主轴负载（主信号）'),
    ('L_X_servo_load', 'ALLOW', 'X 轴伺服负载'),
    ('L_abso_x', 'ALLOW_AS_PHASE', '绝对 X 位置（相位对齐用）'),
    ('L_abso_z', 'ALLOW_AS_PHASE', '绝对 Z 位置（工步定位用）'),
    ('L_spindle_speed', 'ALLOW', '实际转速'),
    ('L_spindle_speed_cmd', 'ALLOW', '指令转速'),
    ('L_tool_number', 'ALLOW_AS_KEY', '刀号（恒 T1111）'),
    ('L_wear_no', 'ALLOW', '磨耗补偿号（恒 11，无信息）'),
]
pd.DataFrame(LEAK, columns=['Field', 'Decision', 'Reason']).to_csv(
    os.path.join(D2C, 'leakage_allow_deny_list.csv'), index=False, encoding='utf-8-sig')
print('[5] configs/ params.csv + leakage_allow_deny_list.csv')

# ---------------- 6. 原始数据提取（gz）
RAW = os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv')
keep = ['_time', 'life_cycle', 'L_allnum', 'L_main_program', 'L_abso_x', 'L_abso_y', 'L_abso_z',
        'L_spindle_load', 'L_spindle_speed', 'L_spindle_speed_cmd', 'L_feed_override',
        'L_spindle_override', 'L_rate1', 'L_runtime', 'L_circle_time', 'L_state', 'L_alm_no',
        'L_tool_number', 'L_wear_no']
dd = pd.read_csv(RAW, usecols=keep, encoding='utf-8-sig')
gz = os.path.join(D3R, 'raw_extract_T1111_20cycles.csv.gz')
dd.to_csv(gz, index=False, encoding='utf-8-sig', compression='gzip')
print('[6] raw_extract %d 行 × %d 列 → %.1f MB'
      % (dd.shape[0], dd.shape[1], os.path.getsize(gz) / 1e6))
pd.DataFrame([dict(SourceFile=os.path.basename(RAW), RowsInSource=384595,
                   RowsExtracted=len(dd), ColumnsExtracted=len(keep),
                   Cycles=dd.life_cycle.nunique(),
                   TimeMin=str(dd['_time'].min()), TimeMax=str(dd['_time'].max()),
                   Note='列裁剪，行不裁剪；原始文件哈希见 MANIFEST')]).to_csv(
    os.path.join(D3R, 'extract_lineage.csv'), index=False, encoding='utf-8-sig')

# ---------------- 7. tool.db 快照
shutil.copy2(dbp, os.path.join(D3DB, 'tool.db.readonly_copy'))
for t in ('pieces', 'history', 'tool_events', 'kv', 'raw_rows'):
    try:
        df = pd.read_sql_query('select * from %s' % t, con)
        df.to_csv(os.path.join(D3DB, '%s.csv' % t), index=False, encoding='utf-8-sig')
    except Exception as e:
        print('  [warn] %s: %s' % (t, e))
con.close()
open(os.path.join(D3DB, 'SNAPSHOT_INFO.md'), 'w', encoding='utf-8').write(
    '# tool.db 只读快照\n\n'
    '- 源文件：`2ndPrediction/tool_log/data/tool.db`（2.4 MB，mtime 2026-10-22 14:42）\n'
    '- 拷贝方式：文件级复制，未修改任何行；查询用 `mode=ro` URI\n'
    '- 表：pieces / history / tool_events / kv / raw_rows / sqlite_sequence\n'
    '- ⚠ `history` 16 条全部 `src=seed`（离线 `simulator.LIFE_SEQ` 灌入），**不是在线登记**\n'
    '- ⚠ `tool_events` 0 条 —— 从未有人点过「已换新刀」\n'
    '- 脱敏：本库不含 deviceName/departNo 等机构字段，无需掩码；如贵方另有要求请提出\n')
print('[7] tool_db_snapshot/ ok')

# ---------------- 8. MANIFEST + result_hashes
def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


man, res = [], []
for dirpath, _, files in os.walk(BASE):
    if os.sep + '09_repro_scripts' in dirpath:
        pass
    for fn in files:
        p = os.path.join(dirpath, fn)
        rel = os.path.relpath(p, BASE).replace('\\', '/')
        # 排除自指文件：它们在本次运行结束前又被改写，写进清单里会是过期哈希
        if rel in ('01_交付清单与版本指纹/MANIFEST.csv',
                   '08_本轮可复现执行日志/result_hashes.csv'):
            continue
        try:
            h = sha256(p)
        except Exception:
            continue
        man.append(dict(Path=rel, SHA256=h, Bytes=os.path.getsize(p)))
MAN = pd.DataFrame(man).sort_values('Path')
# 外部输入（不随包交付，但要能对上）
EXT = [
    ('../influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv', os.path.join(ROOT, 'influxdb_4152_FanucNormalData_20261012_104944_L_T1111_T1111_life_labeled.csv')),
    ('../out_T1111/xalign_HI.csv', os.path.join(ROOT, 'out_T1111', 'xalign_HI.csv')),
    ('../tool_life_records.csv', os.path.join(ROOT, 'tool_life_records.csv')),
    ('../out_T1111/在线落地_台账对齐验证.txt', os.path.join(ROOT, 'out_T1111', '在线落地_台账对齐验证.txt')),
    ('../2ndPrediction/tool_log/data/tool.db', os.path.join(TL, 'data', 'tool.db')),
]
ext_rows = []
for rel, p in EXT:
    if os.path.exists(p):
        ext_rows.append(dict(Path='EXTERNAL:' + rel, SHA256=sha256(p), Bytes=os.path.getsize(p)))
pd.concat([MAN, pd.DataFrame(ext_rows)], ignore_index=True).to_csv(
    os.path.join(D1, 'MANIFEST.csv'), index=False, encoding='utf-8-sig')
print('[8] MANIFEST.csv %d 行（含 %d 个外部输入）' % (len(MAN) + len(ext_rows), len(ext_rows)))

# result_hashes：本轮所有运行产物
for rel in sorted(os.listdir(os.path.join(BASE, '04_RUL复现实验'))):
    p = os.path.join(BASE, '04_RUL复现实验', rel)
    if os.path.isfile(p):
        res.append(dict(Artifact='04/' + rel, SHA256=sha256(p)[:16], Bytes=os.path.getsize(p)))
for rel in sorted(os.listdir(os.path.join(BASE, '03_原始数据与标签'))):
    p = os.path.join(BASE, '03_原始数据与标签', rel)
    if os.path.isfile(p):
        res.append(dict(Artifact='03/' + rel, SHA256=sha256(p)[:16], Bytes=os.path.getsize(p)))
for sub in ('05_HI与换刀规则审计', '06_崩刃A专项'):
    for rel in sorted(os.listdir(os.path.join(BASE, sub))):
        p = os.path.join(BASE, sub, rel)
        if os.path.isfile(p):
            res.append(dict(Artifact=sub.split('_')[0] + '/' + rel, SHA256=sha256(p)[:16],
                            Bytes=os.path.getsize(p)))
pd.DataFrame(res).to_csv(os.path.join(D8, 'result_hashes.csv'), index=False, encoding='utf-8-sig')
print('[9] result_hashes.csv %d 行' % len(res))
