# -*- coding: utf-8 -*-
"""s64_n06_shadow_safety.py —— N06：隔离影子运行测试 + 安全不变量回归（本地证据）

对应审核：Commit 0338a5d §4 P1-02、P1-05、§5 **N06**。
通过标准：**不影响生产、不输出许可、不自动停机**，给可核的状态/字段测试。

做法（本地可跑、不停真实机、不碰生产库）：
  1) 复用 `s56_r09_release_verify.part_b()` 的隔离副本启动（temp 目录 + 库快照副本 +
     `--source sim` + 端口 18765），读运行中 `/api/state` 的真实字段；
  2) 断言一组**安全不变量**（下面 INV-*）；
  3) 静态核证：`server.py` 无任何 FOCAS 写/停机调用；`index.html` 在 MONITOR_ONLY 下
     显示"影子观测"、无"自动停机"；起点未确认时前端给"不做换刀判定"；
  4) 前后对比快照库 SHA：证明探针过程没写生产/快照库。

输出：`N06_隔离影子测试/N06_shadow_safety_result.txt`（PASS/FAIL 明细 + 捕获字段）。
任一不变量 FAIL → 退出码 1（供 run_all 作硬门禁）。
"""
import os
import re
import sys
import json
import time
import socket
import shutil
import tempfile
import subprocess
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
PKG = os.path.join(BASE, '02_源代码与配置', 'source')
SNAP = os.path.join(BASE, '03_原始数据与标签', 'tool_db_snapshot', 'tool.db.readonly_copy')
OUT = os.path.join(BASE, 'N06_隔离影子测试')
PORT = 18766                                   # 与 s56(18765) 错开，避免端口撞车
os.makedirs(OUT, exist_ok=True)


def sha(p):
    import hashlib
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(65536), b''):
            h.update(b)
    return h.hexdigest()
sys.path.insert(0, HERE)

LOG = []
PASS = FAIL = 0


def P(*a):
    s = ' '.join(str(x) for x in a)
    print(s)
    LOG.append(s)


def ck(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        P('  PASS  %-52s %s' % (name, detail))
    else:
        FAIL += 1
        P('  FAIL  %-52s %s' % (name, detail))


def main():
    P('=' * 84)
    P('N06　隔离影子安全回归（不影响生产 / 不出许可 / 不停机）')
    P('=' * 84)

    # ---- 1) 起隔离副本（自带启动，源=仓库内 PKG，库=快照副本，不碰生产），读运行中 /api/state
    tmp = tempfile.mkdtemp(prefix='n06_srv_')
    res = dict(ok=False, err='')
    try:
        for f in ('server.py', 'index.html', 'simulator.py', 'influx_source.py'):
            s = os.path.join(PKG, f)
            if os.path.exists(s):
                shutil.copy2(s, tmp)
        os.makedirs(os.path.join(tmp, 'data'), exist_ok=True)
        dbcopy = os.path.join(tmp, 'data', 'tool.db')
        shutil.copy2(SNAP, dbcopy)
        res['db_sha_before'] = sha(dbcopy)
        env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
        # 只用 sim 源（不向外拉数据）；库指向副本，不碰生产
        proc = subprocess.Popen([sys.executable, 'server.py', '--host', '127.0.0.1',
                                 '--port', str(PORT), '--source', 'sim', '--db', dbcopy],
                                cwd=tmp, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        up = False
        for _ in range(60):
            time.sleep(0.5)
            try:
                socket.create_connection(('127.0.0.1', PORT), timeout=0.5).close()
                up = True
                break
            except OSError:
                if proc.poll() is not None:
                    break
        if not up:
            res['err'] = '服务未起来：' + (proc.stdout.read(2000) if proc.stdout else '')
        else:
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with op.open('http://127.0.0.1:%d/api/state' % PORT, timeout=20) as r:
                d = json.loads(r.read().decode('utf-8'))
                res['status'] = r.status
            res.update(ok=bool(d.get('ok')), policy_mode=d.get('policy_mode'),
                       safety_permit=d.get('safety_permit'), state=d.get('state'),
                       judge_allowed=d.get('judge_allowed'), granted=d.get('granted'),
                       base_src=d.get('base_src'), used=d.get('used'),
                       invocation=d.get('tool_invocation_index'),
                       n_seg=d.get('program_run_segment_count'), code_sha=d.get('code_sha'))
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        res['db_sha_after'] = sha(dbcopy)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not res.get('ok'):
        P('[!] 隔离副本未能正常返回：', str(res.get('err', ''))[:300])
    P('\n[捕获字段]（来自运行中的隔离服务，非磁盘静态）')
    for k in ('status', 'policy_mode', 'safety_permit', 'state', 'judge_allowed',
              'granted', 'base_src', 'used', 'invocation', 'n_seg', 'code_sha'):
        P('    %-14s = %r' % (k, res.get(k)))

    # ---- 2) 安全不变量
    P('\n[安全不变量]')
    ck('INV-1 HTTP /api/state 200', res.get('status') == 200)
    ck('INV-2 safety_permit 恒 False（不出任何安全许可）', res.get('safety_permit') is False)
    ck('INV-3 policy_mode ∈ {MONITOR_ONLY, EXPERIMENTAL_POLICY}',
       res.get('policy_mode') in ('MONITOR_ONLY', 'EXPERIMENTAL_POLICY'), res.get('policy_mode'))
    ck('INV-4 state ∈ {ok,chk,sug,bad,cnt}（无停机/报警态）',
       res.get('state') in ('ok', 'chk', 'sug', 'bad', 'cnt'), res.get('state'))
    ck('INV-5 起点未确认(judge_allowed=False)→state=cnt 且 granted=None（不锁存/不写kv）',
       (res.get('judge_allowed') is True) or
       (res.get('state') == 'cnt' and res.get('granted') is None),
       'judge_allowed=%s state=%s granted=%s' % (res.get('judge_allowed'), res.get('state'), res.get('granted')))
    ck('INV-6 tool_invocation_index=UNKNOWN（不冒充调用次数，P0-02）',
       res.get('invocation') == 'UNKNOWN', res.get('invocation'))
    ck('INV-7 快照库 SHA 前后一致（探针不写库）',
       res.get('db_sha_before') == res.get('db_sha_after'),
       'before=%s after=%s' % (str(res.get('db_sha_before'))[:12], str(res.get('db_sha_after'))[:12]))

    # ---- 3) 静态核证：无 FOCAS 写/停机；前端影子标注
    srv = open(os.path.join(PKG, 'server.py'), encoding='utf-8').read()
    P('\n[静态核证 server.py]')
    ck('S-1 无任何 FOCAS 写接口(cnc_wr*/cnc_sysexecmd/wrparam)',
       not re.search(r'cnc_wr|cnc_sysexecmd|cnc_wrparam|cnc_set', srv))
    ck('S-2 SAFETY_PERMIT 写死 False（非环境变量可改）',
       bool(re.search(r'^SAFETY_PERMIT\s*=\s*False', srv, re.M)))
    ck('S-3 POLICY_MODE 非法值兜底回 MONITOR_ONLY',
       bool(re.search(r'POLICY_MODE\s*=\s*"MONITOR_ONLY"', srv)))
    ck('S-4 非 judge_allowed 不写 grant kv',
       'if not judge_allowed' in srv and '不做延长判断，也不写 kv' in srv)

    html = open(os.path.join(PKG, 'index.html'), encoding='utf-8').read()
    P('\n[静态核证 index.html（车间误认风险，P1-02）]')
    ck('U-1 MONITOR_ONLY 时显示"影子观测"', '影子观测' in html and 'MONITOR_ONLY' in html)
    ck('U-2 起点未确认时显示"不做换刀判定"', '不做换刀判定' in html)
    ck('U-3 前端无"自动停机/立即停机"类指令措辞',
       not re.search(r'自动停机|立即停机|已停机', html))
    # 残留风险登记（不判 FAIL，交 0 号位/工艺裁决措辞）
    sug_imperative = ('建议换刀' in html)
    P('  [登记] 前端 state=sug 仍显示"建议换刀"字样（%s）——祈使式措辞，' % sug_imperative)
    P('        虽有 MONITOR_ONLY"影子观测"横幅兜底，是否需弱化为"参考线到达"属对外口径，交 0 号位/工艺裁决。')

    # ---- 4) 汇总
    P('\n' + '=' * 84)
    P('汇总：PASS=%d FAIL=%d' % (PASS, FAIL))
    with open(os.path.join(OUT, 'N06_shadow_safety_result.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG))
    return FAIL


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
