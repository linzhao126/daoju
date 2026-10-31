# -*- coding: utf-8 -*-
"""P0-D01 交付件补充：从主清单表里选出**本轮真的给审核方的**文件（4152/T1111 相关），
排除非本轮范围的 2496/2428 数据与 4152 非 T1111 刀号数据（后者可选附加）。

规则（任务书 §6 第一批门槛聚焦 4152 T1111；非本轮数据延后至第二批）：
  - 必给（4152 device + 含 T1111）：3 个 CSV
  - 可选（4152 device 但仅其他刀号）：2 个 CSV
  - 不给（2496/2428 或其他机床）：延后至第二批
  - 台账/字段字典/SYNTHESIZED 库：全部必给
"""
import csv
import hashlib
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..'))
WORK = os.path.abspath(os.path.join(REPO, '..', '..'))
OUT = os.path.join(REPO, 'P0_数据与代码移交', 'P0-D01_本轮移交清单.csv')

HAND = os.path.join(WORK, '手工提供文件')

# (分类, 文件所在, 绝对路径, 设备, 刀号, 本轮定位, 备注)
ITEMS = [
    ('原始数据·必给', '手工提供文件', os.path.join(HAND, 'influxdb_4152_FanucNormalData_20261027_200034.csv'),
     '4152', 'T1111 等', '任务书点名的 3.3 GB 全量', '主数据源；审核方独立复算的入口'),
    ('原始数据·必给', '手工提供文件', os.path.join(HAND, 'influxdb_FanucNormalData_20260914_081959.csv'),
     '4152', 'T0404/T0909/T1111', '旧批全量', '时间 09-14 之前'),
    ('原始数据·必给', '手工提供文件', os.path.join(HAND, 'influxdb_FanucNormalData_20260910_085752_4152.csv'),
     '4152', 'T0404/T0909/T1111', '旧批全量', ''),

    ('原始数据·可选附加', '手工提供文件', os.path.join(HAND, 'influxdb_FanucNormalData_20260831_104331.csv'),
     '4152', 'T0202', '同机对照刀', '⚠ 非 T1111；给则用于对照，不给不影响本轮复算'),
    ('原始数据·可选附加', '手工提供文件', os.path.join(HAND, 'influxdb_FanucNormalData_20260901_091924.csv'),
     '4152', 'T0505/T0202/T1212', '同机对照刀', '⚠ 非 T1111；同上'),

    ('字段字典', '手工提供文件', os.path.join(HAND, 'Fanuc_Jolin(2).xlsx'),
     '通用', '—', '70 个 FOCAS/OPC UA 点位定义', '⚠ 不入 Git；含 NodeId；任务书 §3-D01 item 3 原件'),
    ('台账原件', '手工提供文件', os.path.join(HAND, '三厂换刀记录.xlsx'),
     '4152', 'T1111', '60 条换刀台账', '⚠ 不入 Git；任务书 §3-D02 原件；含 是否真实失效 / 换刀原因 / 刀寿 N 等字段'),

    ('SYNTHESIZED 可运行库', '手工提供文件', os.path.join(HAND, 'tool.db.synthesized'),
     '4152', 'T1111', 's70 从 CSV 灌出的可运行 DB', 'raw_rows/pieces/history 可复算；tool_events/kv 空属预期；时间覆盖 2026-09-17→10-27 约 41 天；⚠ 不声称"影子服务运行时行为"'),
    ('台账导出', '手工提供文件', os.path.join(HAND, 'ledger_changes.csv'),
     '4152', 'T1111', 'xlsx 60 行导出', '不混入 tool_events；服务运行时事件 ≠ 车间换刀文字'),
]

DELAYED = [
    ('延后·2496', '工作区根·不再拷贝', 'merged_csv_20260901_160345.csv', '2496', 'T2525/T2222/T0606',
     '第二批跨机床验证素材', '已从 手工提供文件/ 移出；原文件保留在工作区根'),
    ('延后·2428', '工作区根·不再拷贝', 'merged_csv_20260908_131914.csv', '2428', 'T3434/T2100/T3232',
     '第二批跨机床验证素材', '已从 手工提供文件/ 移出；原文件保留在工作区根'),
    ('延后·2428', '工作区根·不再拷贝', 'influxdb_FanucNormalData_20260829_144941.csv', '2428', 'T1515/T1313',
     '第二批跨机床验证素材', '已从 手工提供文件/ 移出；原文件保留在工作区根'),
    ('延后·2428 (⚠ 文件名误导)', '工作区根·不再拷贝', 'influxdb_FanucNormalData_20260829_144941_T1111_life_labeled.csv',
     '2428', 'T1515/T1313（数据里**没有 T1111**）',
     '第二批跨机床验证素材', '已从 手工提供文件/ 移出；**文件名带 T1111 但 deviceName=2428**，属历史命名遗留；任务书 §3-D01 item 2 要求"是否经过过滤/替换/标签注入"逐条说明，此类坑需在第二批启动前更正'),
    ('延后·2428', '工作区根·不再拷贝', 'influxdb_FanucNormalData_20260924_170215.csv', '2428', 'T2100/T3232',
     '第二批跨机床验证素材', '已从 手工提供文件/ 移出；原文件保留在工作区根'),
]

COLS = ['分类', '所在位置', '文件', '设备实测(deviceName)', '刀号', '大小_MB', 'SHA256_12',
        '本轮定位', '备注']


def sha12(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()[:12]


rows = []
for cat, loc, p, dev, tool, role, note in ITEMS:
    if not os.path.exists(p):
        rows.append(dict(分类=cat, 所在位置=loc, 文件=os.path.basename(p),
                         **{'设备实测(deviceName)': dev, '刀号': tool, '大小_MB': '',
                            'SHA256_12': 'MISS', '本轮定位': role, '备注': '文件不在指定位置'}))
        continue
    sz = os.path.getsize(p) / 1024 / 1024
    rows.append(dict(分类=cat, 所在位置=loc, 文件=os.path.basename(p),
                     **{'设备实测(deviceName)': dev, '刀号': tool,
                        '大小_MB': round(sz, 2), 'SHA256_12': sha12(p),
                        '本轮定位': role, '备注': note}))

for cat, loc, fn, dev, tool, role, note in DELAYED:
    rows.append(dict(分类=cat, 所在位置=loc, 文件=fn,
                     **{'设备实测(deviceName)': dev, '刀号': tool, '大小_MB': '',
                        'SHA256_12': '(不在移交清单)', '本轮定位': role, '备注': note}))

with open(OUT, 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.DictWriter(f, fieldnames=COLS)
    w.writeheader()
    for r in rows:
        w.writerow(r)

give = sum(1 for r in rows if r['大小_MB'] != '')
defer = sum(1 for r in rows if r['SHA256_12'] == '(不在移交清单)')
print('DONE →', OUT)
print('本轮给审核方：%d 项（含 SHA/大小）· 延后：%d 项' % (give, defer))
