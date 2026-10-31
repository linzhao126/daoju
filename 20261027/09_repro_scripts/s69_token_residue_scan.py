# -*- coding: utf-8 -*-
"""S0-C04 ⑤：全仓 + 工作区衍生文件的 token 明文兜底扫描（AI 可代跑）。"""
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
SKIP_DIRS = {'.venv', '__pycache__', '.git', 'node_modules', 'HTML', 'crawl_focas'}
# 已知真实 token 的前缀/长度特征（InfluxDB v2 token 通常 60~100 位 base64-ish 串）
# 也扫通用签名
PATTERNS = [
    ('Bearer 后接长串', re.compile(r'Bearer\s+[A-Za-z0-9_\-\.\=~:+/\*]{40,}')),
    ('Token 后接长串', re.compile(r'\bToken\s+[A-Za-z0-9_\-\.\=~:+/\*]{40,}')),
    ('Authorization 明文头', re.compile(r'Authorization\s*[:=]\s*["\']?(?:Token|Bearer)\s+[A-Za-z0-9_\-\.\=~:+/\*]{40,}')),
    ('INFLUX_TOKEN=字面长值', re.compile(r'INFLUX_TOKEN\s*=\s*["\']?[^"\'\s]{40,}')),
    ('password 字面', re.compile(r'(?i)password\s*[:=]\s*["\']?[^\s"\']{6,}')),
    ('secret 字面', re.compile(r'(?i)\bsecret\s*[:=]\s*["\']?[^\s"\']{6,}')),
    # JSON 形态："token": "长串"  /  "Token": "长串"（前一轮漏扫，导致 influx_health_report.json 13 份报告没抓到）
    ('JSON "token" 长串', re.compile(r'"[Tt]oken"\s*:\s*"[A-Za-z0-9_\-\.\=~:+/\*]{40,}"')),
    ('JSON "api_key"/"apikey" 长串', re.compile(r'"[Aa]pi[_]?[Kk]ey"\s*:\s*"[^"]{20,}"')),
]
# 豁免：占位符 / 引用 / 明显假值 / 掩码 / 长串是 SHA/哈希
EXEMPT_SUBSTR = ['<令牌>', '<token>', 'YOUR_', 'REPLACE', 'xxxxx', 'PLACEHOLDER',
                 '示例', '占位符', 'os.environ.get', 'require_token', 'Authorization\': "Token "']


def hit_is_benign(m_line, m_full):
    low = m_line.lower()
    if any(s.lower() in low for s in EXEMPT_SUBSTR):
        return True
    # 若是纯 64 位十六进制（SHA256）或 12 位 SHA12，视为哈希不是 token
    if re.fullmatch(r'[0-9a-fA-F]{12}', m_full) or re.fullmatch(r'[0-9a-fA-F]{64}', m_full):
        return True
    # acceptance_check.csv Pattern 列里含 (?<!...) 之类判据正则串，视为源码判据
    if '(?<' in m_line or r'\d' in m_line or '^{' in m_line:
        return True
    return False


findings = []
scanned = 0
for dp, dn, fn in os.walk(ROOT):
    dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.startswith('.')]
    for f in fn:
        if not f.endswith(('.csv', '.log', '.txt', '.md', '.json', '.py', '.sh', '.html',
                           '.yml', '.yaml', '.env', '.ini', '.cfg', '.conf', '.joblib-meta',
                           '.readable')):
            continue
        p = os.path.join(dp, f)
        try:
            if os.path.getsize(p) > 30 * 1024 * 1024:      # >30MB 单文件跳过（太大，抽样另计）
                continue
            with open(p, 'r', encoding='utf-8', errors='replace') as fh:
                text = fh.read()
        except Exception:
            continue
        scanned += 1
        for i, ln in enumerate(text.split('\n'), 1):
            for name, rx in PATTERNS:
                m = rx.search(ln)
                if not m:
                    continue
                if hit_is_benign(ln, m.group(0)):
                    continue
                # ⚠ 报告必须掩码，不允许把长串写回日志或控制台
                masked = rx.sub(lambda mm: mm.group(0)[:12] + '…【已掩码 len=%d】' % len(mm.group(0)), ln)
                findings.append((os.path.relpath(p, ROOT).replace('\\', '/'), i, name,
                                 masked.strip()[:180]))

print('扫描文件数：%d' % scanned)
print('可疑命中：%d' % len(findings))
for rel, ln_no, name, txt in findings[:50]:
    print('  [%s] %s:L%d  %s' % (name, rel, ln_no, txt[:150]))
sys.exit(1 if findings else 0)
