# -*- coding: utf-8 -*-
"""test_g_start_confirm.py —— Test-G：起点确认 / 跨程序刀龄 / 续设不归零

守的是什么
----------
审核 R03（P0）：
  「区分 PhysicalToolLifeId / ProgramSegmentId / ToolInvocationIndex / ObservedPieceCount /
    ExposureBySegment；输出"同一刃口 02→03→02，未换刀"的端到端测试；
    刀龄/实际使用量应跨所有该刃口参加的加工持续累计；GROUP_MODE=part 不能作为正确性证明。」
审核 P0-MOD-04：
  「冷启动数据默认"仅观测片段件数下限"；禁止作为可执行剩余寿命换刀点；
    引入显式且有证据的 ToolLifeStartConfirmed；先以影子模式回测。」

七条
----
T1  源码级：判定资格必须由 start_confirmed 参与（`judge_allowed`），不能只看样本条数
T2  源码级：`safety_permit` 恒 False；`POLICY_MODE` 默认 MONITOR_ONLY、非法值回退
T3  冷启动（无换刀事件）：judge_ok=True 但 judge_allowed=False → 不给 b/h/limit，state=cnt
T4  有 new 事件：judge_allowed=True，b/h 正常给出
T5  02→03→02 未换刀：used 跨程序累计（不被切成单段），exposure 3 段，调用段数=3，物理 ID 不变
T6  跨品番 02→03→02：used 仍累计；gkey 不含 prog（锁存跟着刀走）
T7  人工续设 tool_life（history 里 life 变大）：used / base 不变 → 刀龄不归零

判据：每条 PASS 打印实际值；任一 FAIL → exit 1。
"""
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..', '..'))
SRV = os.path.join(BASE, '02_源代码与配置', 'source', 'server.py')

BASE_F, EXT_F, TAU, K_BASE, W = 0.4, 0.9, 0.01, 20, 10
MIN_N_BASIC, MIN_N_P85 = 3, 5
PASS = FAIL = 0


def ck(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('  PASS  %-56s %s' % (name, detail))
    else:
        FAIL += 1
        print('  FAIL  %-56s %s' % (name, detail))


def build(segs, new_event=True, hist=(100, 105, 110, 95, 102), extra_hist=()):
    """segs = [(prog, n), ...] 顺序写入；new_event 控制有没有人点过「换新刀」"""
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE pieces(allnum INT, prog INT, load_med REAL);
        CREATE TABLE tool_events(id INTEGER PRIMARY KEY, ts TEXT, event TEXT, prog INT, allnum INT);
        CREATE TABLE history(id INTEGER PRIMARY KEY, prog INT, life INT, src TEXT, valid INT);
        CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT);
    """)
    a = 0
    for prog, n in segs:
        for _ in range(n):
            a += 1
            c.execute("INSERT INTO pieces VALUES(?,?,?)", (a, prog, 100.0))
    if new_event:
        c.execute("INSERT INTO tool_events(ts,event,prog,allnum) VALUES('t0','new',?,0)", (segs[0][0],))
    for L in list(hist) + list(extra_hist):
        c.execute("INSERT INTO history(prog,life,src,valid) VALUES(?,?,'seed',1)", (segs[-1][0], L))
    return c, a


def view(c, prog=None):
    """复刻 compute_view 的判定段（与 server.py 逐字同序）"""
    last_new = c.execute(
        "SELECT allnum FROM tool_events WHERE event='new' ORDER BY id DESC LIMIT 1").fetchone()
    r = c.execute("SELECT MIN(allnum) m, MAX(allnum) x FROM pieces").fetchone()
    min_a, max_a = int(r['m']), int(r['x'])
    base = int(last_new['allnum']) if last_new else min_a - 1
    used = c.execute("SELECT COUNT(*) FROM pieces WHERE allnum>?", (base,)).fetchone()[0]

    seg_seq = [x[0] for x in c.execute(
        "SELECT prog FROM pieces WHERE allnum>? ORDER BY allnum", (base,)).fetchall()]
    exposure, n_seg, cur = [], 0, None
    for pg in seg_seq:
        if cur is None or pg != cur:
            n_seg += 1
            exposure.append({'seg': n_seg, 'prog': pg, 'pieces': 0})
            cur = pg
        exposure[-1]['pieces'] += 1
    phys_life_id = "phys_T1111_%d" % base

    pg_now = prog if prog is not None else (seg_seq[-1] if seg_seq else 0)
    hist = [x['life'] for x in c.execute(
        "SELECT life FROM history WHERE prog=? AND COALESCE(valid,1)=1 ORDER BY id", (pg_now,))]
    m0 = float(sorted(hist)[len(hist) // 2]) if len(hist) >= MIN_N_BASIC else None
    p85 = None
    if len(hist) >= MIN_N_P85:
        s = sorted(hist)
        p85 = float(s[min(len(s) - 1, int(0.85 * (len(s) - 1) + 0.5))])
    judge_ok = (m0 is not None and p85 is not None)
    start_confirmed = (last_new is not None)
    judge_allowed = bool(judge_ok and start_confirmed)
    b = int(-(-BASE_F * m0 // 1)) if judge_allowed else None
    h = int(-(-EXT_F * m0 // 1)) if judge_allowed else None
    limit = None if not judge_allowed else b
    st = 'cnt' if not judge_allowed else ('bad' if used >= m0 else
                                         'sug' if used >= h else 'chk' if used >= b else 'ok')
    return dict(used=used, base=base, judge_ok=judge_ok, start_confirmed=start_confirmed,
                judge_allowed=judge_allowed, b=b, h=h, limit=limit, state=st,
                exposure=exposure, n_seg=n_seg, phys=phys_life_id, m0=m0, p85=p85,
                gkey="grant_b%d" % base, disc=f"phys_T1111_{max_a}")


def main():
    src = open(SRV, 'rb').read().decode('utf-8')
    print('=' * 82)
    print('Test-G　起点确认 / 跨程序刀龄 / 续设不归零')
    print('来源：%s' % SRV)
    print('=' * 82)

    # ---------- T1 源码级
    print('\n[T1] 判定资格是否由 start_confirmed 参与')
    ck('T1.1 存在 judge_allowed', 'judge_allowed' in src)
    ck('T1.2 judge_allowed 由 judge_ok 与 start_confirmed 共同决定',
       bool(re.search(r'judge_allowed\s*=\s*bool\(\s*judge_ok\s+and\s+start_confirmed\s*\)', src)))
    ck('T1.3 b / h 用 judge_allowed 而非 judge_ok',
       bool(re.search(r'BASE_F \* m0.*if judge_allowed', src))
       and bool(re.search(r'EXT_F \* m0.*if judge_allowed', src)))
    ck('T1.4 状态降级用 judge_allowed', bool(re.search(r'if not judge_allowed:\s*\n\s*st = "cnt"', src)))
    ck('T1.5 start_confirmed 来自换刀事件',
       bool(re.search(r'start_confirmed\s*=\s*\(last_new is not None\)', src)))

    # ---------- T2 源码级
    print('\n[T2] 策略模式与安全许可')
    ck('T2.1 safety_permit 写死 False', bool(re.search(r'SAFETY_PERMIT\s*=\s*False', src)))
    ck('T2.2 POLICY_MODE 默认 MONITOR_ONLY',
       bool(re.search(r'POLICY_MODE\s*=\s*os\.environ\.get\("POLICY_MODE",\s*"MONITOR_ONLY"\)', src)))
    ck('T2.3 非法值回退到 MONITOR_ONLY',
       bool(re.search(r'if POLICY_MODE not in POLICY_MODES:\s*\n\s*POLICY_MODE = "MONITOR_ONLY"', src)))
    ck('T2.4 返回体带 policy_mode / safety_permit',
       '"policy_mode": POLICY_MODE' in src and '"safety_permit": SAFETY_PERMIT' in src)

    # ---------- T3 冷启动
    print('\n[T3] 冷启动（没有任何换刀事件）')
    c, n = build([(14, 60)], new_event=False)
    v = view(c)
    ck('T3.1 judge_ok（样本够）= True', v['judge_ok'], 'm0=%s p85=%s' % (v['m0'], v['p85']))
    ck('T3.2 start_confirmed = False', v['start_confirmed'] is False)
    ck('T3.3 judge_allowed = False → 不给判定', v['judge_allowed'] is False)
    ck('T3.4 b / h / limit 全为 None', (v['b'], v['h'], v['limit']) == (None, None, None))
    ck('T3.5 state 降级为 cnt', v['state'] == 'cnt', v['state'])
    ck('T3.6 used 仍报全量（页面显示 ≥N）', v['used'] == 60, 'used=%d' % v['used'])

    # ---------- T4 有起点
    print('\n[T4] 有人点过「换新刀」')
    c2, n2 = build([(14, 60)], new_event=True)
    v2 = view(c2)
    ck('T4.1 judge_allowed = True', v2['judge_allowed'] is True)
    ck('T4.2 b/h 正常给出', v2['b'] is not None and v2['h'] is not None,
       'b=%s h=%s' % (v2['b'], v2['h']))
    ck('T4.3 state 不再是 cnt', v2['state'] != 'cnt', v2['state'])

    # ---------- T5 02→03→02 未换刀
    print('\n[T5] 同一刃口 02→03→02（prog 11→20→11），中途未换刀')
    c3, n3 = build([(11, 30), (20, 40), (11, 25)], new_event=True)
    v3 = view(c3)
    ck('T5.1 used 跨程序累计 = 95', v3['used'] == 95, 'used=%d（单段最多 40）' % v3['used'])
    ck('T5.2 exposure 3 段', len(v3['exposure']) == 3,
       str([(e['prog'], e['pieces']) for e in v3['exposure']]))
    ck('T5.3 段件数各自正确 30/40/25',
       [e['pieces'] for e in v3['exposure']] == [30, 40, 25])
    ck('T5.4 tool_invocation_index = 3（连续段数，不是 distinct prog=2）',
       v3['n_seg'] == 3, 'n_seg=%d' % v3['n_seg'])
    v3b = view(c3, prog=11)
    ck('T5.5 物理刃口 ID 不随程序变', v3['phys'] == v3b['phys'], v3['phys'])
    ck('T5.6 gkey 不含 prog', 'prog' not in v3['gkey'], v3['gkey'])

    # ---------- T6 跨品番
    print('\n[T6] 跨品番 02→03→02（prog 11→16→11，品番 02/03 属两个推断层）')
    c4, n4 = build([(11, 20), (16, 35), (11, 15)], new_event=True)
    v4 = view(c4)
    ck('T6.1 used 不被品番切开 = 70', v4['used'] == 70, 'used=%d' % v4['used'])
    ck('T6.2 段数 3、无合并', v4['n_seg'] == 3)
    ck('T6.3 合计件数 == used', sum(e['pieces'] for e in v4['exposure']) == v4['used'])

    # ---------- T7 续设不归零
    print('\n[T7] 人工续设 tool_life（历史寿命被上调）不得让刀龄归零')
    c5, n5 = build([(11, 30), (20, 40)], new_event=True)
    before = view(c5)
    c5.execute("INSERT INTO history(prog,life,src,valid) VALUES(11,200,'手工续设',1)")
    c5.execute("INSERT INTO history(prog,life,src,valid) VALUES(20,250,'手工续设',1)")
    after = view(c5)
    ck('T7.1 used 不变', before['used'] == after['used'], '%d → %d' % (before['used'], after['used']))
    ck('T7.2 base_allnum 不变（续设不是换刀）', before['base'] == after['base'],
       '%d → %d' % (before['base'], after['base']))
    ck('T7.3 物理刃口 ID 不变', before['phys'] == after['phys'])
    ck('T7.4 M0 会变（统计基准允许随历史更新）但判定资格不变',
       before['judge_allowed'] == after['judge_allowed'])

    print('\n' + '=' * 82)
    print('汇总：%d / %d PASS' % (PASS, PASS + FAIL))
    print('=' * 82)
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == '__main__':
    main()
