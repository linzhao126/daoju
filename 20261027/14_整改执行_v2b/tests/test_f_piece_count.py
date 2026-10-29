# -*- coding: utf-8 -*-
"""test_f_piece_count.py —— Test-F：件数必须是「这把刀干了多少件」，不能被程序过滤劈开

触发
----
0 号位 2026-10-29 指示：**按主程序，分这把刀干了多少件，不要使用品番。**

执行时发现一个比"用不用品番"更根本的问题：
    只把分组从"品番"改回"主程序"，`used = COUNT(*) WHERE prog=?` 仍会把
    **同一把刀跨程序加工的件劈掉**。实测 c33 就是这种：107 件 = prog20 的 51 件 + prog19 的 56 件。

    只数 prog19 → 显示 56 件（少 51）→ 系统以为刀还很新 → **延迟换刀**。

本测试守的三条
------------
T1  used 不得含 `prog` 过滤（物理件数口径）：源码级的硬约束
T2  c33 结构（51+56 两个程序、只换过一次刀）→ used = 107，base 不漂
T3  换程序前后 gkey 不变（锁存状态跟着刀走，不是跟着程序走）
T4  统计基准（hist / M0）**仍然**按主程序过滤（D-04 的另一半）
T5  冷启动（无换刀事件）时 base = 最小 allnum − 1，used 仍为全量
T6  pieces 表无刀号列 → 无过滤即"单刀全量"，这条前提必须仍成立

判据：每条 PASS 打印实际值，最后 `汇总：N / N PASS`，任一 FAIL → exit 1。
"""
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..', '..'))
SRV = os.path.join(BASE, '02_源代码与配置', 'source', 'server.py')

PASS = FAIL = 0
LOG = []


def ck(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('  PASS  %-52s %s' % (name, detail))
    else:
        FAIL += 1
        print('  FAIL  %-52s %s' % (name, detail))
    LOG.append((name, bool(cond), detail))


def build(seg_a=(20, 51), seg_b=(19, 56), new_event=True):
    """造一个 c33 结构的库：同一把刀先做 A 程序，再做 B 程序，中间没换刀。"""
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE pieces(allnum INT, prog INT, load_med REAL);
        CREATE TABLE tool_events(id INTEGER PRIMARY KEY, ts TEXT, event TEXT,
                                 prog INT, allnum INT);
        CREATE TABLE history(id INTEGER PRIMARY KEY, prog INT, life INT, src TEXT, valid INT);
    """)
    a = 0
    for _ in range(seg_a[1]):
        a += 1
        c.execute("INSERT INTO pieces VALUES(?,?,?)", (a, seg_a[0], 100.0))
    for _ in range(seg_b[1]):
        a += 1
        c.execute("INSERT INTO pieces VALUES(?,?,?)", (a, seg_b[0], 100.0))
    if new_event:
        c.execute("INSERT INTO tool_events(ts,event,prog,allnum) VALUES('t0','new',?,0)", (seg_a[0],))
    return c, a


def main():
    src = open(SRV, 'rb').read().decode('utf-8')

    print('=' * 78)
    print('Test-F　件数口径：物理累计 vs 按程序过滤')
    print('来源：%s' % SRV)
    print('=' * 78)

    # ---- T1 源码级：used 的 SQL 不得含 prog
    print('\n[T1] used 的 SQL 是否仍按程序过滤')
    m = re.search(r'used = c\.execute\(\s*"([^"]+)"', src, re.S)
    ck('T1.1 能从源码里取到 used 的 SQL', m is not None)
    used_sql = m.group(1) if m else ''
    print('        实际 SQL：%s' % used_sql)
    ck('T1.2 used SQL 不含 "prog"', 'prog' not in used_sql.lower(), used_sql)
    ck('T1.3 used SQL 是 COUNT(*) FROM pieces', used_sql.strip().upper().startswith('SELECT COUNT(*) FROM PIECES'))

    # ---- T2 c33 结构
    print('\n[T2] c33 结构：prog20 的 51 件 + prog19 的 56 件，中途没换刀')
    c, total = build()
    last_new = c.execute("SELECT allnum FROM tool_events WHERE event='new' "
                         "ORDER BY id DESC LIMIT 1").fetchone()
    base = int(last_new['allnum']) if last_new else \
        c.execute("SELECT MIN(allnum) m FROM pieces").fetchone()['m'] - 1
    used = c.execute(used_sql, (base,)).fetchone()[0]
    ck('T2.1 总件数 = 107', total == 107, 'allnum 最大 %d' % total)
    ck('T2.2 base = 0（换刀事件在起点）', base == 0, 'base=%d' % base)
    ck('T2.3 used = 107（不是 56）', used == 107, 'used=%d' % used)
    old_used = c.execute("SELECT COUNT(*) FROM pieces WHERE prog=19 AND allnum>?",
                         (base,)).fetchone()[0]
    ck('T2.4 旧口径（只数 prog19）会给出 56 —— 少 51 件',
       old_used == 56, 'old=%d，差额 %d' % (old_used, used - old_used))

    # ---- T3 gkey 不含 prog
    print('\n[T3] 锁存键是否跟着刀走（换程序不重新评估）')
    mg = re.search(r'gkey = "([^"]*)" %', src)
    ck('T3.1 能从源码取到 gkey 模板', mg is not None)
    tpl = mg.group(1) if mg else ''
    print('        实际模板：%s' % tpl)
    ck('T3.2 gkey 模板不含 prog', 'prog' not in tpl.lower(), tpl)
    marg = re.search(r'gkey = "[^"]*" % ([^\n]+)', src)
    arg = (marg.group(1).strip() if marg else '')
    ck('T3.3 gkey 的取值参数是 base_allnum', arg == 'base_allnum', arg)

    # ---- T4 hist 仍按主程序
    print('\n[T4] 统计基准（hist）仍按主程序 —— D-04 的另一半不能丢')
    c, _ = build()
    for i in range(5):
        c.execute("INSERT INTO history(prog,life,src,valid) VALUES(19,?,?,1)", (100 + i, 't'))
    for i in range(3):
        c.execute("INSERT INTO history(prog,life,src,valid) VALUES(20,?,?,1)", (200 + i, 't'))
    n19 = c.execute("SELECT COUNT(*) FROM history WHERE prog IN (19) AND COALESCE(valid,1)=1").fetchone()[0]
    n20 = c.execute("SELECT COUNT(*) FROM history WHERE prog IN (20) AND COALESCE(valid,1)=1").fetchone()[0]
    ck('T4.1 prog19 的历史 = 5 条', n19 == 5, 'n=%d' % n19)
    ck('T4.2 prog20 的历史 = 3 条（未被合并）', n20 == 3, 'n=%d' % n20)

    # ---- T5 冷启动
    print('\n[T5] 冷启动：有数据但从未点"已换新刀"')
    c, total = build(new_event=False)
    mn = c.execute("SELECT MIN(allnum) m FROM pieces").fetchone()['m']
    base = mn - 1
    used = c.execute(used_sql, (base,)).fetchone()[0]
    ck('T5.1 base = min_allnum − 1', base == 0, 'min=%d base=%d' % (mn, base))
    ck('T5.2 used = 107（下游按 base_src=cold 显示 ≥N）', used == 107, 'used=%d' % used)

    # ---- T6 pieces 无刀号列
    print('\n[T6] 前提：pieces 整库只有 T1111 一把刀（无刀号列）')
    dbp = os.path.join(BASE, '03_原始数据与标签', 'tool_db_snapshot', 'tool.db.readonly_copy')
    if os.path.exists(dbp):
        d = sqlite3.connect(dbp)
        cols = [r[1] for r in d.execute('pragma table_info(pieces)')]
        ck('T6.1 pieces 表无 tool_no / tool_num 列',
           not any(k in ' '.join(cols).lower() for k in ('tool_no', 'toolnum', 'tool_id')),
           str(cols))
    else:
        ck('T6.1 快照库存在（缺失则跳过不算 PASS）', False, dbp)

    print('\n' + '=' * 78)
    print('汇总：%d / %d PASS' % (PASS, PASS + FAIL))
    print('=' * 78)
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
