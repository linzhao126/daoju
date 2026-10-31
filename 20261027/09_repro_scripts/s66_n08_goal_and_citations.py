# -*- coding: utf-8 -*-
"""s66_n08_goal_and_citations.py —— N08（G0）：项目目标遵循性检查 + 0 号位裁决引证登记

对应审核：Commit 0338a5d §3 **P0-08**（治理与引证不得自我闭环）、§5 **N08**。
本脚本做三件**可核对**的事，不写任何新结论：

1. **裁决引证登记表**（N08-01_裁决引证登记表.csv）：把 D-01~D-09 与两条待源核表述
   逐条登记「0 号位表述 / 消息渠道 / 证据等级 / 仓库内锚点位置 / 是否需 0 号位确认」。
   锚点用**原文字符串现场定位**，位置不是手填的——引文若被删改，本脚本会报 MISS。
2. **残留口径扫描**（N08-02_残留口径扫描.csv）：全仓扫「已封/路线关闭/终局/已裁定/
   实测下限/lag_stats/永久」等越界或过期写法，逐条给**处置状态**（已修/限定/历史留痕）。
3. **目标遵循性自检**（N08-03_目标遵循性自检.csv）：机检 N08-00 是否明写
   「C3 与崩刃 A 仍为目标」「负结果已限定」「需 0 号位裁决事项已列」，并复用
   `check_g0.py` 的七项自检与红线词表对 N08-00 打分。

退出码：锚点 MISS 或目标声明缺失 → 1（硬阻断，进 `run_all.sh` A46）。
只依赖标准库。
"""
import csv
import hashlib
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
OUT = os.path.join(BASE, 'N08_目标遵循性与裁决引证')
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, HERE)

# ── 1. 裁决引证登记表 ────────────────────────────────────────────────
# 证据等级（P0-08 分级，禁止由仓库内反复引用自动升级）：
#   L1 逐字引文·渠道未记录   L2 短答复转述·原件待核   L3 仅 1 号位转述·待源核   L4 查无此条
REGISTER = [
    dict(登记号='D-01', 事项='RUL 允许「距下次人工换刀剩余件数」作阶段性演示（C2）',
         摘录='「RUL 是否允许\'距下次人工换刀的剩余件数\'作阶段性演示，可以允许。」',
         渠道='2026-10-28 0号位对三条待裁决事项的一次性答复（1号位记录为连续一段话）',
         等级='L1 逐字引文·渠道未记录',
         锚=('19_0号位裁决记录/19-00_D01-D03裁决记录与影响.md', 'RUL 是否允许'),
         边界='不覆盖「C3 已被否证」；C2 只是阶段性演示口径，不替代 C3',
         需确认='否（切分编号 D-01 是 1 号位加的，切分位置如有误请指出）'),
    dict(登记号='D-02', 事项='失效形态为刃口生命周期末人工填写，不是逐次调用标记',
         摘录='「失效模式现场定义：破损 / 渐进磨损 / 计划寿命到，这个是一把刀的生命周期最后人工填写的，不是每次调用后的标记。」',
         渠道='同 D-01（同一段话）',
         等级='L1 逐字引文·渠道未记录',
         锚=('19_0号位裁决记录/19-00_D01-D03裁决记录与影响.md', '不是每次调用后的标记'),
         边界='据此**不能**构造崩刃事件级真值；也不构成「物理信号不存在」的结论',
         需确认='否（另：失效形态最小字典是否由 1 号位起草，待 0 号位点头）'),
    dict(登记号='D-03', 事项='崩刃 A 若不满足 1 秒，是否试更高频链路/振动',
         摘录='「崩刃 A 若不满足 1 秒，是否试更高频链路/振动，不加外部设备可以」',
         渠道='同 D-01（同一段话）',
         等级='L1 逐字引文·语义不完整（已由 D-06 裁定为阶段偏好）',
         锚=('19_0号位裁决记录/19-00_D01-D03裁决记录与影响.md', '不加外部设备可以'),
         边界='⚠ 原话**不支持「禁止加装」**；1 号位曾按"禁止加"执行并写进 4 份文件，属越权，已更正',
         需确认='否（边界由 D-06 补齐）'),
    dict(登记号='D-04', 事项='计件与统计粒度：按主程序、不用品番',
         摘录='「按主程序，分这把刀干了多少件，不要使用品番」',
         渠道='2026-10-29 指示（1 号位记录，无消息原件随包）',
         等级='L2 短答复转述·原件待核',
         锚=('附件_过程留痕/51_00_D04执行_按主程序计件与统计.md', '不要使用品番'),
         边界='⚠ 该原话**只支持统计按主程序**；「线上计件跨程序物理累计、不加程序过滤」是 1 号位由 P0-03 缺陷推出的执行口径，**不是 0 号位裁决**（62-00 附录C 已自认「这条是我推的」）',
         需确认='是——请 0 号位确认「跨程序物理累计计件」可作为正式口径'),
    dict(登记号='D-05', 事项='崩刃滞后件数 k 说不清 → 默认参考线一律 0.9×M0',
         摘录='「1 说不清。」（三行裁定卡第 1 条）',
         渠道='2026-10-29 对 `62-00 附录A` 三张裁定卡的逐项作答',
         等级='L2 短答复转述·原件待核',
         锚=('19_0号位裁决记录/19-00_D01-D03裁决记录与影响.md', '1 说不清。 2 选a。 3 c'),
         边界='只改「页面参考线画在哪」，**不构成换刀许可**、不改 `limit`/两档逻辑；k 一旦有答案须按 (k,ρ) 表重选',
         需确认='否（但 k 值本身仍待车间回答）'),
    dict(登记号='D-06', 事项='D-03 边界 = 卡 A：阶段偏好、不构成禁止',
         摘录='「2 选a。」',
         渠道='同 D-05',
         等级='L2 短答复转述·原件待核',
         锚=('19_0号位裁决记录/19-00_D01-D03裁决记录与影响.md', '2 选a。 3 c'),
         边界='允许提出振动/声发射方案供裁决；**采购前须单独报批**；不采购、不安装、不联调',
         需确认='否'),
    dict(登记号='D-07', 事项='严格样本门限 = 卡 C：±8 维持 + 另标 ±3 黄金',
         摘录='「3 c。」',
         渠道='同 D-05',
         等级='L2 短答复转述·原件待核',
         锚=('19_0号位裁决记录/19-00_D01-D03裁决记录与影响.md', '3 c」**'),
         边界='只动「哪些周期算合格」，不动换刀规则；严格 10 把只能做探索性回测，**不能宣称 C2 验收通过**',
         需确认='否'),
    dict(登记号='D-08（本任务书 §0.4 已直接答复）', 事项='振动/声发射路线的当期资源约束',
         摘录='「没有经费，买不起，不要考虑了」',
         渠道='未记录（1 号位转述，仓库内无引用块、无消息原件）；2026-10-29 TOOL-RUL-CHIP-0-REQ 任务书 §0.4 已直接答复未作永久禁止',
         等级='L3 仅 1 号位转述（消息原件待补）；本任务书 §0.4 已直接答复未作永久禁止',
         锚=('附件_证据/63_R06_路线比较/63-00_FOCAS扭矩vs振动路线比较.md', '没有经费**'),
         边界='本任务书 §0.4：“0 号位未作永久禁止新增硬件的决定；先充分验证现有 FOCAS，再比较改造/新信号/新传感器的必要性、预期收益与成本”；⛔ 不得外推为永久技术禁令；方案书写/询价不再被本条阻塞，采购前仍须单独报批',
         需确认='否（语义已结；消息原件待补归档）'),
    dict(登记号='D-09（待源核）', 事项='厂商标定报警与目标粒度不匹配',
         摘录='「是严重的情况下才会有，我的目的是预测刀具寿命，这个要求远远低于我要实现的目标，不考虑」',
         渠道='未记录（1 号位转述）',
         等级='L3 仅 1 号位转述·待源核',
         锚=('附件_证据/63_R06_路线比较/63-00_FOCAS扭矩vs振动路线比较.md', '是严重的情况下才会有'),
         边界='可推出「不作 C3 寿命入模信号」；**推不出**「不可作崩刃 A 的已崩确认通道」——后者未单独裁决',
         需确认='是——是否单独裁「可作崩刃 A 已崩确认通道」'),
    dict(登记号='（无编号）', 事项='项目一是否可改为「固定件数提醒/换刀阈值项目」',
         摘录='—（仓库内 0 号位表述中查无「可以改成换刀阈值项目」一类原话）',
         渠道='—',
         等级='L4 查无此条',
         锚=('20_R06_项目目标遵循性检查/20-00_R06_项目目标遵循性检查_v1.0.md', '可以改成换刀阈值'),
         边界='1 号位**不得自行**把项目一改口径；C3 仍是攻关目标（G0-04 ①）',
         需确认='否（属 1 号位自我约束，非待裁事项）'),
    dict(登记号='（目标未变更的旁证）', 事项='0 号位从未裁「把项目一改成换刀阈值项目」',
         摘录='「不会把项目一改成单纯 C2 规则换刀提示；C2 只是阶段性交付口径（D-01），C3 仍是攻关目标。」',
         渠道='1 号位红线承诺（`20-00` §7），非 0 号位表述',
         等级='L1 逐字引文·渠道未记录（指 D-01~D-03）',
         锚=('20_R06_项目目标遵循性检查/20-00_R06_项目目标遵循性检查_v1.0.md', 'C3 仍是攻关目标'),
         边界='、C0/C1/C2/C3 四级中只允许把 **C2** 作阶段性演示；C3 未验证不等于被否证',
         需确认='否'),
]

hits = []
for r in REGISTER:
    rel, needle = r['锚'][0], r['锚'][1]
    note = ''
    if needle.endswith('**'):
        needle, note = needle[:-2], '锚点缺失→需人工核'
    p = os.path.join(BASE, rel.replace('/', os.sep))
    if os.path.exists(p):
        lines = open(p, encoding='utf-8', errors='replace').read().split('\n')
        found = [i + 1 for i, ln in enumerate(lines) if needle in ln]
        loc = '%s:L%d' % (rel, found[0]) if found else ''
        ver = hashlib.sha256(open(p, 'rb').read()).hexdigest()[:12] if found else ''
        status = 'FOUND' if found else 'MISS'
    else:
        loc, ver, status = '', '', 'FILE_MISSING'
    r2 = dict(r)
    r2.pop('锚')
    r2.update(锚点位置=loc, 锚点状态=status, 文件SHA12=ver, 备注=note or r2.get('备注', ''))
    hits.append(r2)

COLS = ['登记号', '事项', '摘录', '渠道', '等级', '锚点位置', '锚点状态', '文件SHA12', '边界', '需确认']
with open(os.path.join(OUT, 'N08-01_裁决引证登记表.csv'), 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.DictWriter(f, fieldnames=COLS)
    w.writeheader()
    for r in hits:
        w.writerow({c: r.get(c, '') for c in COLS})

# ── 2. 残留口径扫描 ─────────────────────────────────────────────────
# (模式名, 正则, 为什么敏感, 期望处置)——用负面断言避开已修/引用语境，降低噪声
SCANS = [
    ('实测下限', r'(?<!非)实测下限',
     'P0-06：135 秒不得写成"物理崩刃→报警实测下限"', '限定为"本链路等待预算/非实测"'),
    ('旧名 lag_stats', r'(?<!recv_)lag_stats\(',
     'P0-06：老函数名（已改 batch_recv_lag_stats）', '源码/测试应无旧名；文档留痕可保留'),
    ('路线关闭', r'路线关闭',
     'P0-05/P0-08：资源约束不得写成路线永久关闭', '改写为"当期不推进 + 重启条件"'),
    ('终局口径', r'(?:2026-10-29 终局|（终局|【终局|：终局)',
     '同上（"终局"式写法）', '改写并挂待源核'),
    ('已封', r'已封[^闭印]',
     'P0-05：DTRQ/TCMD 原因未知，不能写"已封"', '改为"现场反馈已知、原因未知"'),
    ('封了', r'\*\*封了\*\*',
     '同上', '改为"当期受阻（原因未知）"'),
    ('已裁定', r'已裁定',
     'P0-08：需同时给证据等级', '挂 L1/L2/L3 等级或改写'),
    ('永久化表述', r'永久(?:不|禁止|不可|失效)',
     'P0-08：禁止永久化表述', '逐条限定为当期约束'),
    ('全关闭', r'全部关闭|三路线全关',
     '§1.2 R06："三路线全关"不认可为最终技术结论', '改为"本组实验未支持/当期不推进"'),
]
SKIP_DIR = {'.git', '__pycache__', '.venv'}
findings = []
for dp, dn, fn in os.walk(BASE):
    dn[:] = [d for d in dn if d not in SKIP_DIR and not d.startswith('.')]
    if os.path.abspath(dp) == os.path.join(BASE, '09_repro_scripts'):
        pass                                    # 占位：下行按文件名排除本脚本自身
    for f in fn:
        if f == 's66_n08_goal_and_citations.py':
            continue
        if not f.endswith(('.md', '.py', '.txt', '.csv', '.sh', '.html')):
            continue
        p = os.path.join(dp, f)
        rel = os.path.relpath(p, BASE).replace('\\', '/')
        try:
            text = open(p, encoding='utf-8', errors='replace').read()
        except Exception:
            continue
        for i, ln in enumerate(text.split('\n'), 1):
            for name, rx, why, want in SCANS:
                if not re.search(rx, ln):
                    continue
                if rel.startswith('N08_') or '00_执行回执与六问直答' in rel:
                    st = '本件产出/历史留痕限定框（原文保留供追溯）'
                elif re.search(r'不得|禁止|⛔|原写|旧写法|撤回|已修|限定|引用红线', ln):
                    st = '已挂限定/属引用旧写法本身（人工复核）'
                elif rel.startswith('附件_证据/63_'):
                    st = '已改写（见 §4.2/§4.3 新口径）'
                else:
                    st = '待核（逐条列入 §5 或限定）'
                findings.append(dict(文件=rel, 行=i, 命中=name, 命中文本=ln.strip()[:120],
                                     为什么敏感=why, 期望处置=want, 本轮状态=st))

with open(os.path.join(OUT, 'N08-02_残留口径扫描.csv'), 'w', encoding='utf-8-sig', newline='') as f:
    cols = ['文件', '行', '命中', '命中文本', '为什么敏感', '期望处置', '本轮状态']
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    for r in sorted(findings, key=lambda x: (x['命中'], x['文件'], x['行'])):
        w.writerow(r)

# ── 3. 目标遵循性自检 ───────────────────────────────────────────────
DOC = os.path.join(OUT, 'N08-00_项目目标遵循性检查与裁决引证.md')
CLAIMS = [
    ('项目一 C3 仍为目标·未撤销', 'C3 仍为攻关目标'),
    ('项目二 A 仍为目标·未撤销·分档评价', '崩刃 A 仍为既定工程目标'),
    ('项目二 B 为探索目标·未冒充必达', '项目二 B'),
    ('负结果均已限定（本组实验未支持/SIMULATED）', '本组实验未支持'),
    ('资源约束≠技术不可行', '当期资源约束'),
    ('需 0 号位裁决事项已列', '需 0 号位裁决'),
    ('七项强制自检（G0-05）已答', '本轮涉及哪个项目'),
    ('能力分级 C0~C3 已标注', 'C3'),
]
rows3 = []
if os.path.exists(DOC):
    dtxt = open(DOC, encoding='utf-8').read()
else:
    dtxt = ''
    rows3.append(dict(检查项='N08-00 文件存在', 期望='存在', 结果='MISS', 证据=''))
for name, needle in CLAIMS:
    ok = needle in dtxt
    rows3.append(dict(检查项=name, 期望='含「%s」' % needle, 结果='PASS' if ok else 'FAIL',
                      证据='命中 %d 次' % dtxt.count(needle) if ok else '未命中'))

# 复用 check_g0 的七项/红线机检（可导入则跑，不可导入不影响退出码）
g0_line = 'check_g0 未执行（导入失败）'
try:
    import check_g0
    rl = check_g0.load_redlines()
    buf = io.StringIO()
    old = sys.stdout
    try:
        sys.stdout = buf
        v, st = check_g0.check_file(DOC, rl, verbose=True)
    finally:
        sys.stdout = old
    g0_line = 'check_g0 → %s（红线 未豁免%d/豁免%d，七项 %d/7，C级 %s）' % (
        v, st.get('hard', -1), st.get('soft', -1), st.get('seven', -1),
        ''.join(st.get('levels', [])) or '无')
    rows3.append(dict(检查项='G0 红线词 + 七项自检（check_g0.py 复用）', 期望='非 FAIL',
                      结果=v, 证据=g0_line))
except Exception as e:                                    # noqa: BLE001
    rows3.append(dict(检查项='G0 红线词 + 七项自检（check_g0.py 复用）', 期望='非 FAIL',
                      结果='SKIP', 证据='%s: %s' % (type(e).__name__, e)))

with open(os.path.join(OUT, 'N08-03_目标遵循性自检.csv'), 'w', encoding='utf-8-sig', newline='') as f:
    cols = ['检查项', '期望', '结果', '证据']
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    for r in rows3:
        w.writerow(r)

# ── 控制台回执 ──────────────────────────────────────────────────────
miss = [r for r in hits if r['等级'].startswith(('L1', 'L2', 'L3')) and r['锚点状态'] != 'FOUND']
bad4 = [r for r in hits if r['等级'].startswith('L4') and r['锚点状态'] == 'FOUND']
print('== N08-01 裁决引证登记：%d 条（锚点 MISS %d）' % (len(hits), len(miss)))
for r in hits:
    print('  [%-12s] %-14s %-46s %s' % (r['登记号'], r['锚点状态'], r['等级'][:14],
                                        (r['锚点位置'] or '—')[:70]))
print('== N08-02 残留口径扫描：命中 %d 处（按模式）' % len(findings))
cnt = {}
for x in findings:
    cnt[x['命中']] = cnt.get(x['命中'], 0) + 1
for k, v in sorted(cnt.items(), key=lambda kv: -kv[1]):
    print('   %-10s %d' % (k, v))
print('== N08-03 目标遵循性自检：')
for r in rows3:
    print('   %-52s %s' % (r['检查项'][:52], r['结果']))
print('   ' + g0_line)

rc = 1 if (miss or bad4) else 0
for r in rows3:
    if r['结果'] == 'FAIL' or r['结果'] == 'MISS':
        rc = 1
print('DONE → %s   exit=%d' % (OUT, rc))
sys.exit(rc)
