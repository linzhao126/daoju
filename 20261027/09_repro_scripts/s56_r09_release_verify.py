# -*- coding: utf-8 -*-
"""s56_r09_release_verify.py —— R09：工程版本交付核证

审核要求（R09，P1）
------------------
「源代码真实运行版本 hash、演示/线上/交付副本一致；源码差异、测试日志、数据哈希、环境依赖；
  必要时提供安全隔离的只读/模拟服务验证，不得要求为了文本回归停真实机。」

五段
----
A  三份副本一致性：线上 / 交付包 / 演示（stdlib）→ SHA256 + 差异行
B  运行版本：起一个**隔离副本**服务，读它 `/api/state` 里的 `code_sha`，证明"跑的那份"可核
    ⚠ 用 temp 目录 + 库快照副本，端口 18765，不碰 `data/tool.db`、不停真实机
C  依赖核证：线上 requirements.txt 的 pin 值 vs 实际 pip freeze vs 交付包 environment_requirements.txt
D  数据哈希：关键输入文件 SHA256 vs MANIFEST.csv 记录
E  测试日志：把 run_all 相关测试的汇总行抽出来（存在性 + 数字）

不做的事
--------
* 不启动真实采集（`--source influx`），避免向外拉数据
* 不改任何生产库；temp 目录跑完即删（`--keep` 可保留）
"""
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
ROOT = os.path.abspath(os.path.join(BASE, '..', '..', '..'))   # workBoddyHome刀具
LIVE = os.path.join(ROOT, '2ndPrediction', 'tool_log')
PKG = os.path.join(BASE, '02_源代码与配置', 'source')
OUT = os.path.join(BASE, '附件_证据', '59_R09_版本核证')
os.makedirs(OUT, exist_ok=True)
PY = sys.executable
PORT = 18765
LOG = []


def P(*a):
    s = ' '.join(str(x) for x in a)
    print(s)
    LOG.append(s)


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def part_a():
    pairs = [
        ('server.py', os.path.join(LIVE, 'server.py'), os.path.join(PKG, 'server.py')),
        ('index.html', os.path.join(LIVE, 'index.html'), os.path.join(PKG, 'index.html')),
    ]
    rows = []
    for name, live, pkg in pairs:
        sl = sha(live) if os.path.exists(live) else 'MISSING'
        sp = sha(pkg) if os.path.exists(pkg) else 'MISSING'
        rows.append(dict(文件=name, 线上=livelbl(sl), 交付包=livelbl(sp), 一致=livelbl(sl == sp),
                         线上完整=sl, 交付包完整=sp))
    # stdlib 版单独列出（演示用，不同实现）
    std = os.path.join(LIVE, 'server_stdlib.py')
    rows.append(dict(文件='server_stdlib.py（演示用零依赖版）',
                     线上=livelbl(sha(std)) if os.path.exists(std) else 'MISSING',
                     交付包='—', 一致='不适用（另一套实现）',
                     线上完整=sha(std) if os.path.exists(std) else 'MISSING', 交付包完整='—'))
    return rows


def livelbl(v):
    return ('一致' if v is True else '不一致' if v is False else str(v))


def part_b():
    """隔离副本服务验证：只读接口回归，不碰生产库"""
    tmp = tempfile.mkdtemp(prefix='r09_srv_')
    res = dict(tmp=tmp, ok=False, err='')
    try:
        for f in ('server.py', 'index.html', 'simulator.py', 'influx_source.py'):
            src = os.path.join(LIVE, f)
            if os.path.exists(src):
                shutil.copy2(src, tmp)
        os.makedirs(os.path.join(tmp, 'data'), exist_ok=True)
        snap = os.path.join(BASE, '03_原始数据与标签', 'tool_db_snapshot', 'tool.db.readonly_copy')
        dbcopy = os.path.join(tmp, 'data', 'tool.db')
        shutil.copy2(snap, dbcopy)
        res['db_sha_before'] = sha(dbcopy)

        env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
        # ⚠ 只用 sim 源（不向外拉数据）；库指向**副本**，不碰生产
        proc = subprocess.Popen([PY, 'server.py', '--host', '127.0.0.1', '--port', str(PORT),
                                 '--source', 'sim', '--db', dbcopy],
                                cwd=tmp, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        # 等端口
        up = False
        for _ in range(60):
            time.sleep(0.5)
            try:
                s = socket.create_connection(('127.0.0.1', PORT), timeout=0.5)
                s.close()
                up = True
                break
            except OSError:
                if proc.poll() is not None:
                    break
        if not up:
            res['err'] = '服务未起来：' + (proc.stdout.read(2000) if proc.stdout else '')
            return res
        # ⚠ 必须绕过代理：HTTP_PROXY 劫持 127.0.0.1 会返回假 502
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open('http://127.0.0.1:%d/api/state' % PORT, timeout=20) as r:
            body = r.read().decode('utf-8')
            res['status'] = r.status
        d = json.loads(body)
        res['ok'] = bool(d.get('ok'))
        res['code_sha'] = d.get('code_sha')
        res['policy_mode'] = d.get('policy_mode')
        res['safety_permit'] = d.get('safety_permit')
        res['judge_allowed'] = d.get('judge_allowed')
        res['base_src'] = d.get('base_src')
        res['used'] = d.get('used')
        res['exposure'] = d.get('exposure_by_segment')
        res['n_seg'] = d.get('tool_invocation_index')
        res['phys'] = d.get('phys_life_id')
        res['reference_rule'] = d.get('reference_rule')
        res['ref_limit_90'] = d.get('ref_limit_90')
        res['ref_limit_40'] = d.get('ref_limit_40')
        res['lag'] = d.get('lag')
        # 页面也能取到
        with op.open('http://127.0.0.1:%d/' % PORT, timeout=20) as r:
            res['index_bytes'] = len(r.read())
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        res['db_sha_after'] = sha(dbcopy)
        # 直接问库：老库是不是真的被补上了 recv_ts（迁移是否生效）
        try:
            import sqlite3 as _s3
            cc = _s3.connect(dbcopy)
            cols = [r[1] for r in cc.execute("PRAGMA table_info(raw_rows)").fetchall()]
            cc.close()
            res['raw_cols'] = cols
            res['recv_ts_migrated'] = ('recv_ts' in cols)
        except Exception as e:                     # noqa: BLE001
            res['raw_cols'] = ['ERR:%s' % e]
            res['recv_ts_migrated'] = False
    except Exception as e:                     # noqa: BLE001
        res['err'] = '%s: %s' % (type(e).__name__, e)
        try:
            proc.kill()
        except Exception:
            pass
    finally:
        res['file_sha'] = sha(os.path.join(LIVE, 'server.py'))
        if not os.environ.get('R09_KEEP'):
            shutil.rmtree(tmp, ignore_errors=True)
    return res


def part_c():
    req = os.path.join(LIVE, 'requirements.txt')
    pins = {}
    if os.path.exists(req):
        for line in open(req, encoding='utf-8'):
            line = line.strip()
            if '==' in line and not line.startswith('#'):
                k, v = line.split('==', 1)
                pins[k.strip().lower()] = v.strip()
    fr = subprocess.run([PY, '-m', 'pip', 'freeze'], capture_output=True, text=True)
    have = {}
    for line in fr.stdout.splitlines():
        if '==' in line:
            k, v = line.split('==', 1)
            have[k.strip().lower()] = v.strip()
    env_f = os.path.join(BASE, '01_交付清单与版本指纹', 'environment_requirements.txt')
    envv = {}
    if os.path.exists(env_f):
        for line in open(env_f, encoding='utf-8'):
            if '==' in line and not line.startswith('#'):
                k, v = line.split('==', 1)
                envv[k.strip().lower()] = v.strip()
    rows = []
    for k, v in sorted(pins.items()):
        rows.append(dict(包=k, 要求=v, 实际安装=have.get(k, '未安装'),
                         交付包environment=envv.get(k, '未记录'),
                         一致=(have.get(k) == v == envv.get(k, v))))
    return rows, len(envv)


def part_d():
    files = [
        '24_程序归属修正/HI_merged_28.csv',
        '34_P0-01收尾/P01_evidence_all60.csv',
        '附件_证据/52_R02_延长分支审计/r02_grant_audit.csv',
        '15_台账真值检验/input/三厂换刀记录.xlsx',
        '03_原始数据与标签/life_audit_4152.csv',
    ]
    man = {}
    mp = os.path.join(BASE, '01_交付清单与版本指纹', 'MANIFEST.csv')
    if os.path.exists(mp):
        import csv
        for row in csv.DictReader(open(mp, encoding='utf-8-sig')):
            man[row['Path'].replace('\\', '/')] = row['SHA256']
    rows = []
    for f in files:
        p = os.path.join(BASE, f)
        s = sha(p) if os.path.exists(p) else 'MISSING'
        m = man.get(f, '未登记')
        rows.append(dict(文件=f, 实测SHA256=s[:16] + '…' if s != 'MISSING' else 'MISSING',
                         MANIFEST=m[:16] + '…' if m not in ('未登记', 'MISSING') else m,
                         一致=(s == m)))
    return rows


def part_e():
    logs = {
        'test_f 件数口径': ('14_整改执行_v2b/logs/test_f.log', r'汇总：(\d+)\s*/\s*(\d+)\s*PASS'),
        'test_a 训练隔离': ('14_整改执行_v2b/logs/test_a.log', r'PASS (\d+) / FAIL (\d+)'),
        'test_g 起点确认': ('14_整改执行_v2b/logs/test_g.log', r'汇总：(\d+)\s*/\s*(\d+)\s*PASS'),
    }
    rows = []
    for name, (rel, pat) in logs.items():
        p = os.path.join(BASE, rel)
        if not os.path.exists(p):
            rows.append(dict(测试=name, 日志=rel, 结果='缺失（本轮未跑）'))
            continue
        txt = open(p, encoding='utf-8', errors='replace').read()
        m = re.search(pat, txt)
        rows.append(dict(测试=name, 日志=rel, 结果=(m.group(0) if m else '未匹配到汇总行'),
                         mtime=time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(p)))))
    return rows


if __name__ == '__main__':
    P('=' * 92)
    P('R09　工程版本交付核证')
    P('=' * 92)
    A = part_a()
    P('\n[A] 三份副本一致性')
    for r in A:
        P('  %-32s 线上 %s… 交付包 %s… → %s' % (r['文件'], str(r['线上完整'])[:12],
                                                str(r['交付包完整'])[:12], r['一致']))
    B = part_b()
    P('\n[B] 隔离副本服务（127.0.0.1:%d，sim 源，库=快照副本）' % PORT)
    if B.get('err'):
        P('  ⚠ 失败：%s' % B['err'])
    else:
        P('  HTTP %s  ok=%s' % (B.get('status'), B.get('ok')))
        P('  code_sha（运行中）= %s ；磁盘文件 sha256 前 12 位 = %s → %s'
          % (B.get('code_sha'), str(B.get('file_sha'))[:12],
             '一致' if B.get('code_sha') == str(B.get('file_sha'))[:12] else '不一致'))
        P('  policy_mode=%s  safety_permit=%s  judge_allowed=%s  base_src=%s  used=%s'
          % (B.get('policy_mode'), B.get('safety_permit'), B.get('judge_allowed'),
             B.get('base_src'), B.get('used')))
        P('  exposure_by_segment=%s（合计 %s）  段数=%s  物理ID=%s'
          % (B.get('exposure'), (sum(x['pieces'] for x in B['exposure'])
                                if isinstance(B.get('exposure'), list) else '—'),
             B.get('n_seg'), B.get('phys')))
        P('  reference_rule=%s  ref_limit_90=%s（0.9xM0）  ref_limit_40=%s（D-05）'
          % (B.get('reference_rule'), B.get('ref_limit_90'), B.get('ref_limit_40')))
        lg = B.get('lag') or {}
        P('  到达延迟 lag：n=%s 中位=%ss  P95=%ss  最大=%ss（%s）'
          % (lg.get('n'), lg.get('median_sec'), lg.get('p95_sec'),
             lg.get('max_sec'), lg.get('note')))
        P('  页面 index.html 取回 %s 字节' % B.get('index_bytes'))
        P('  副本库 SHA 前后：%s → %s（WAL 模式下写入落在 -wal，SHA 相同不能单独当"未写入"证据）'
          % (str(B.get('db_sha_before'))[:12], str(B.get('db_sha_after'))[:12]))
        P('  raw_rows 列（迁移后）：%s' % (B.get('raw_cols'),))
        P('  recv_ts 迁移是否生效：%s（老库→补列）' % B.get('recv_ts_migrated'))
    C, nenv = part_c()
    P('\n[C] 依赖核证（交付包 environment 共 %d 个包）' % nenv)
    for r in C:
        P('  %-10s 要求 %-10s 实际 %-10s environment %-10s → %s'
          % (r['包'], r['要求'], r['实际安装'], r['交付包environment'],
             '一致' if r['一致'] else '不一致'))
    D = part_d()
    P('\n[D] 数据哈希')
    for r in D:
        P('  %-46s %s vs %s → %s' % (r['文件'], r['实测SHA256'], r['MANIFEST'],
                                     '一致' if r['一致'] else '不一致/未登记'))
    E = part_e()
    P('\n[E] 测试日志')
    for r in E:
        P('  %-16s %s' % (r['测试'], r['结果']))

    import csv
    csv.writer(open(os.path.join(OUT, 'R09_copy_consistency.csv'), 'w', newline='',
                    encoding='utf-8-sig')).writerows(
        [['文件', '线上SHA256', '交付包SHA256', '一致']] +
        [[r['文件'], r['线上完整'], r['交付包完整'], r['一致']] for r in A])
    json.dump(B, open(os.path.join(OUT, 'R09_runtime_probe.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2, default=str)
    with open(os.path.join(OUT, 'R09_runtime_probe.json'), 'w', encoding='utf-8') as f:
        json.dump(B, f, ensure_ascii=False, indent=2, default=str)
    csv.writer(open(os.path.join(OUT, 'R09_dependencies.csv'), 'w', newline='',
                    encoding='utf-8-sig')).writerows(
        [['包', '要求', '实际安装', '交付包environment', '一致']] +
        [[r['包'], r['要求'], r['实际安装'], r['交付包environment'], r['一致']] for r in C])
    csv.writer(open(os.path.join(OUT, 'R09_data_hashes.csv'), 'w', newline='',
                    encoding='utf-8-sig')).writerows(
        [['文件', '实测SHA256', 'MANIFEST', '一致']] +
        [[r['文件'], r['实测SHA256'], r['MANIFEST'], r['一致']] for r in D])
    with open(os.path.join(OUT, 's56_console.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG))
    P('\nDONE → %s' % OUT)
