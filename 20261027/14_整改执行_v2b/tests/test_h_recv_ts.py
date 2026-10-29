# -*- coding: utf-8 -*-
"""test_h_recv_ts.py —— Test-H：数据到达延迟（recv_ts）可测

为什么有这个测试
----------------
`06_崩刃A专项/chipping_A_realtime_gap.md` 把链路拆成 L1~L4，其中 **L2（上传/入库）是唯一
"完全未知"的一段**：库里只有 `_time`（数据产生时刻），没有"我们什么时候拿到它"。

这一条决定了"1 秒"是不是可能：L2 若本身 15 秒，纯软件方案就不成立。
所谓"加一列 `recv_ts`"只是 0.5 人日，却把 L2 从**不可测**变成**可测**。

守六条
------
T1  DDL 两处都含 `recv_ts TEXT`（常量 + init_db）
T2  老库迁移：`ALTER TABLE raw_rows ADD COLUMN recv_ts TEXT`，且放在 z_pos 迁移之后
T3  `write_rows` 的 INSERT 列表含 recv_ts，且取 `r.get("recv_ts")`（模拟器无此字段不报错）
T4  `influx_source.to_rows` 给整批打同一个戳（同一批共用一个值 → lag 是上界）
T5  `lag_stats` 存在、暴露到 `/api/state` 的 `lag` 字段
T6  统计口径复刻验证：负值丢弃、>1 天丢弃、NULL 跳过；median/p95 与手算一致

判据：任一 FAIL → exit 1。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..', '..'))
SRV = os.path.join(BASE, '02_源代码与配置', 'source', 'server.py')
INF = os.path.join(BASE, '02_源代码与配置', 'source', 'influx_source.py')

PASS = FAIL = 0


def ck(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('  PASS  %-58s %s' % (name, detail))
    else:
        FAIL += 1
        print('  FAIL  %-58s %s' % (name, detail))


def lag_replica(pairs, limit_day=True):
    """复刻 lag_stats 的过滤与统计（含负值与 >1 天丢弃）"""
    vals, neg, stale = [], 0, 0
    for ts, recv in pairs:
        if recv is None:
            continue
        d = recv - ts
        if d < 0:
            neg += 1
            continue
        if limit_day and d > 86400:
            stale += 1
            continue
        vals.append(d)
    if not vals:
        return dict(n=0, neg=neg, stale=stale)
    v = sorted(vals)
    n = len(v)
    med = (v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0)
    k = min(n - 1, int(round(0.95 * (n - 1))))   # 与 np.percentile 线性插值近似
    return dict(n=n, neg=neg, stale=stale, median=med, p95=v[k], max=v[-1], min=v[0])


def main():
    srv = open(SRV, 'rb').read().decode('utf-8')
    inf = open(INF, 'rb').read().decode('utf-8')
    print('=' * 84)
    print('Test-H　数据到达延迟 recv_ts')
    print('=' * 84)

    # ---- T1 DDL
    print('\n[T1] DDL 是否含 recv_ts（两处）')
    ck('T1.1 常量 RAW_ROWS_DDL 含 recv_ts TEXT',
       bool(re.search(r'RAW_ROWS_DDL\s*=\s*"""[\s\S]{0,400}recv_ts TEXT', srv)))
    ck('T1.2 init_db 的 CREATE TABLE 含 recv_ts TEXT',
       len(re.findall(r'feedrate REAL,\s*\n\s*recv_ts TEXT', srv)) >= 2,
       '出现 %d 次' % len(re.findall(r'recv_ts TEXT', srv)))

    # ---- T2 迁移
    print('\n[T2] 老库迁移')
    ck('T2.1 有 ALTER TABLE raw_rows ADD COLUMN recv_ts TEXT',
       bool(re.search(r'ALTER TABLE raw_rows ADD COLUMN recv_ts TEXT', srv)))
    ck('T2.2 迁移带存在性判断（幂等）',
       bool(re.search(r'if "recv_ts" not in raw_cols:', srv)))
    i_z = srv.find('ALTER TABLE raw_rows ADD COLUMN z_pos')
    i_r = srv.find('ALTER TABLE raw_rows ADD COLUMN recv_ts')
    ck('T2.3 recv_ts 迁移在 z_pos 之后（重建迁移之后）', 0 < i_z < i_r,
       'z_pos@%d < recv_ts@%d' % (i_z, i_r))

    # ---- T3 写入
    print('\n[T3] write_rows 是否落 recv_ts')
    ck('T3.1 INSERT 列清单含 recv_ts',
       bool(re.search(r'INSERT OR IGNORE INTO raw_rows[\s\S]{0,200}recv_ts\)', srv)))
    ck('T3.2 取值用 r.get("recv_ts")（模拟器无此字段不炸）',
       bool(re.search(r'r\.get\("recv_ts"\)', srv)))
    ck('T3.3 VALUES 占位符与列数一致（9 个 ?）',
       bool(re.search(r'feedrate,recv_ts\) VALUES\(\?,\?,\?,\?,\?,\?,\?,\?,\?\)', srv)))

    # ---- T4 打戳位置
    print('\n[T4] influx_source 是否给整批打同一个戳')
    ck('T4.1 to_rows 内有 recv = iso(datetime.now(timezone.utc))',
       bool(re.search(r'recv = iso\(datetime\.now\(timezone\.utc\)\)', inf)))
    ck('T4.2 每行都赋 r["recv_ts"] = recv',
       bool(re.search(r'for r in out:\s*\n\s*r\["recv_ts"\] = recv', inf)))
    ck('T4.3 打戳在 return 之前、排序之前',
       inf.find('recv = iso(') < inf.find('out.sort(key=lambda r: r["ts"])'))

    # ---- T5 统计与暴露
    print('\n[T5] lag_stats 与 API 字段')
    ck('T5.1 存在 def lag_stats(', 'def lag_stats(' in srv)
    ck('T5.2 只取 recv_ts 非 NULL',
       bool(re.search(r'WHERE recv_ts IS NOT NULL', srv)))
    ck('T5.3 返回 median/p95/max/min',
       all(k in srv for k in ('"median_sec"', '"p95_sec"', '"max_sec"', '"min_sec"')))
    ck('T5.4 /api/state 返回 "lag"', bool(re.search(r'"lag":\s*lag_stats\(\)', srv)))
    ck('T5.5 口径说明里写明"上界"', '上界' in srv)

    # ---- T6 复刻验证
    print('\n[T6] 统计口径复刻（负值/超1天/NULL）')
    pairs = [(0, 3.0), (0, 5.0), (0, 9.0), (0, 20.0),      # 正常 4 条
             (0, -1.0),                                     # 负 → 丢弃
             (0, 90000.0),                                  # >1 天 → 丢弃
             (0, None)]                                     # NULL → 跳过
    r = lag_replica(pairs)
    ck('T6.1 n=4（NULL 与异常值都被排除）', r['n'] == 4, str(r))
    ck('T6.2 neg=1', r['neg'] == 1, str(r['neg']))
    ck('T6.3 stale=1', r['stale'] == 1, str(r['stale']))
    ck('T6.4 median=7.0（3,5,9,20 的中位）', abs(r['median'] - 7.0) < 1e-9, str(r['median']))
    ck('T6.5 max=20.0 / min=3.0', r['max'] == 20.0 and r['min'] == 3.0,
       'max=%s min=%s' % (r['max'], r['min']))
    r2 = lag_replica([(0, None), (0, None)])
    ck('T6.6 全 NULL 时 n=0 且不抛异常', r2['n'] == 0, str(r2))

    print('\n' + '=' * 84)
    print('汇总：%d / %d PASS' % (PASS, PASS + FAIL))
    print('=' * 84)
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == '__main__':
    main()
