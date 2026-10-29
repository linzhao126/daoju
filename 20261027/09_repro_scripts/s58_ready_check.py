# -*- coding: utf-8 -*-
"""s58_ready_check.py —— 历史样本就绪自检：还差几把刀才能给判定

为什么要它
----------
0 号位 2026-10-29 的第四项指示：「现场再采 prog20 / prog11 各 1 把刀」。
但"差几把"这个数**不能凭记忆说**——`51-00` 报的 prog20=6 / prog11=7 / prog14=8 是**离线 28 周期
的 HI 数据**，而**线上 `history` 表只有 16 条 seed**（prog11=3 / prog14=7 / prog20=6，其余 0）。
两个口径完全不同，混着说就会给出错误的采购/排产建议。

本脚本只回答三件事：
  1. 线上每个主程序现在有几条**可用**历史（`valid=1`）；
  2. 距 `MIN_N_BASIC=3`（能出最短/中位/最长）与 `MIN_N_P85=5`（能出判定线）各还差几条；
  3. **当前在跑哪个程序、它还差几条** —— 这才是最该先补的。

口径提醒
--------
* "一条历史" = 一次**人工确认的换刀**（`tool_events.event='new'` → 该刀寿命写入 `history`）
* `history.src='seed'` 是标定期灌入的，不是线上采集
* 线上从未被人工确认过起点（`tool_events` 0 行）→ 所以**"补 2 把"实际是"补 N 次换刀登记"**
"""
import argparse
import os
import sqlite3
import sys

MIN_N_BASIC, MIN_N_P85 = 3, 5

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
SNAP = os.path.join(BASE, '03_原始数据与标签', 'tool_db_snapshot', 'tool.db.readonly_copy')


def load(db_path):
    c = sqlite3.connect('file:%s?mode=ro' % db_path.replace('\\', '/'), uri=True)
    c.row_factory = sqlite3.Row
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=SNAP, help='库路径（默认用交付包里的只读快照）')
    a = ap.parse_args()
    if not os.path.exists(a.db):
        print('找不到库：%s' % a.db)
        sys.exit(2)
    c = load(a.db)

    hist = [dict(r) for r in c.execute(
        "SELECT prog, life, src, COALESCE(valid,1) v FROM history").fetchall()]
    try:
        pieces = [dict(r) for r in c.execute(
            "SELECT prog, COUNT(*) n, MAX(allnum) mx FROM pieces GROUP BY prog").fetchall()]
    except Exception:
        pieces = []
    try:
        n_ev = c.execute("SELECT COUNT(*) FROM tool_events").fetchone()[0]
    except Exception:
        n_ev = -1

    cur = None
    if pieces:
        cur = max(pieces, key=lambda r: r['mx'])['prog']

    progs = sorted({h['prog'] for h in hist} | {p['prog'] for p in pieces})
    print('=' * 78)
    print('历史样本就绪自检　库=%s' % os.path.basename(a.db))
    print('=' * 78)
    print('线上换刀登记（tool_events）行数：%d%s'
          % (n_ev, '　← 0 行意味着"起点从未被人工确认过，判定全程关闭"' if n_ev == 0 else ''))
    print('当前在跑的主程序（pieces 里最新一件）：%s' % (cur if cur is not None else '无数据'))
    print()
    print('%-6s %-6s %-6s %-6s %-6s %-8s %s' %
          ('prog', '有效', 'seed', '非seed', '需补P85', '状态', '说明'))
    print('-' * 78)
    rows = []
    for p in progs:
        hs = [h for h in hist if h['prog'] == p and h['v'] == 1]
        n = len(hs)
        n_seed = sum(1 for h in hs if h['src'] == 'seed')
        need = max(0, MIN_N_P85 - n)
        if n >= MIN_N_P85:
            st = '可判定'
            note = '四格齐全，可给 0.4/0.9 判定线'
        elif n >= MIN_N_BASIC:
            st = '仅统计'
            note = '能出最短/中位/最长，P85 与判定线仍是「—」'
        elif n == 0:
            st = '无历史'
            note = '页面只显示件数，不给任何基准'
        else:
            st = '不足'
            note = '连中位都不可靠'
        if p == cur:
            note += '　★ 当前程序，优先补'
        rows.append(dict(prog=p, n=n, seed=n_seed, live=n - n_seed, need=need, st=st, note=note))
        print('%-6s %-6d %-6d %-6d %-8d %-8s %s' %
              (p, n, n_seed, n - n_seed, need, st, note))

    need_cur = next((r['need'] for r in rows if r['prog'] == cur), None)
    print()
    print('=' * 78)
    print('结论')
    print('=' * 78)
    if cur is None:
        print('· 库里没有 pieces，无法判断当前程序。')
    elif need_cur == 0:
        print('· 当前程序 %s 已有 %d 条历史 → 已可给判定。' % (cur, MIN_N_P85))
        print('  但要留意：这些历史里 seed 占多数，真机验证仍需至少 2 次**线上确认**的换刀。')
    else:
        print('· 当前程序 **%s** 还差 **%d** 条历史才到 %d 条（= %d 次人工确认的换刀）。'
              % (cur, need_cur, MIN_N_P85, need_cur))
        print('· 每完成一次「换新刀」登记，历史 +1（`history.src` 会记成 live，不再算 seed）。')
    print('· ⚠ 51-00 里的 prog20=6/prog11=7/prog14=8 是**离线 28 周期**口径，不是线上 history；')
    print('  两边不能混着报。要让线上能给判定，补的是**线上换刀登记**，不是再导一批离线 CSV。')
    print()
    print('作业卡：`64-00_现场补样本作业卡.md`；采集回归判据见 §3。')
    c.close()


if __name__ == '__main__':
    main()
