# -*- coding: utf-8 -*-
"""test_i_segment_contract.py —— Test-I：刀龄/程序段/调用三者数据契约（审核 N02 / P0-02）

守的是什么：
  审核 P0-02：`tool_invocation_index = n_segments` 用"连续程序段数"冒充"刀具调用次数"；
  `segment_id='p'+prog` 无法唯一标识同一程序的第二次进入段。
  整改后的契约（见 02_源代码与配置/数据契约_刀龄段调用.md）：
    program_run_segment_count = 连续同程序段数（相邻 prog 变化次数 + 1）
    tool_invocation_index     = "UNKNOWN"（无 FOCAS 调用事件源，禁止臆造）
    current_program           = 当前主程序号（原 segment_id 的如实含义）
    segment_seq_id            = "s{第n段}_p{prog}"，同一程序第 2 次进入也可区分

断言分两层：
  行为层 —— 从 server.py 源码里用 AST 抠出**真实的 run_segments 函数**执行
            （不 import server，避免拉起 FastAPI/simulator 依赖）；
  源码层 —— 五个键的赋值形态必须与契约一致，旧冒名写法必须消失。

判据：每条 PASS 打印实际值；任一 FAIL → exit 1。
"""
import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRV = os.path.abspath(os.path.join(
    HERE, '..', '..', '02_源代码与配置', 'source', 'server.py'))

PASS = FAIL = 0


def ck(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('  PASS  %-58s %s' % (name, detail))
    else:
        FAIL += 1
        print('  FAIL  %-58s %s' % (name, detail))


def load_run_segments():
    """从 server.py 源码中提取 run_segments 函数体并单独执行。"""
    src = io.open(SRV, encoding='utf-8').read()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == 'run_segments':
            mod = ast.Module(body=[node], type_ignores=[])
            ns = {}
            exec(compile(mod, SRV, 'exec'), ns)
            return ns['run_segments'], src
    raise RuntimeError('run_segments not found in %s' % SRV)


def main():
    run_segments, src = load_run_segments()

    print('[行为层] run_segments 切段语义')
    # I1 空序列 = 0 段（冷启动无件）
    exp, n = run_segments([])
    ck('I1 空序列 → 0 段', n == 0 and exp == [], 'n=%d' % n)

    # I2 跨程序 02→03→02：3 段，件数合计=used，物理段序正确
    exp, n = run_segments([2, 2, 3, 3, 2])
    ck('I2 02→03→02 → 3 段', n == 3,
       'segs=%s' % [(e['seg'], e['prog'], e['pieces']) for e in exp])
    ck('I3 各段件数合计 = 总件数', sum(e['pieces'] for e in exp) == 5)

    # I4 同一程序内多次调用/多次切入：数据上仍是 1 段 —— 段数≠调用次数
    exp, n = run_segments([17] * 90)
    ck('I4 同程序90件 → 1 段（段数≠调用次数）', n == 1, 'n=%d' % n)

    # I5 同程序第 2 次进入（17→14→17）：3 段，段号可区分两个 17 段
    exp, n = run_segments([17, 17, 14, 17])
    ids = ['s%d_p%d' % (e['seg'], e['prog']) for e in exp]
    ck('I5 17→14→17 → 3 段', n == 3, 'ids=%s' % ids)
    ck('I6 segment_seq_id 唯一（旧 p+prog 不唯一）',
       len(set(ids)) == len(ids) and ids[0] != ids[-1],
       '首=%s 末=%s' % (ids[0], ids[-1]))

    print('[源码层] server.py 五键契约')
    ck('I7 tool_invocation_index = "UNKNOWN"',
       '"tool_invocation_index": "UNKNOWN"' in src)
    ck('I8 program_run_segment_count = n_segments',
       '"program_run_segment_count": n_segments' in src)
    ck('I9 current_program / segment_seq_id 键存在',
       '"current_program"' in src and '"segment_seq_id"' in src)
    ck('I10 旧冒名写法已消失',
       '"tool_invocation_index": n_segments' not in src)
    ck('I11 使用点已改为调 run_segments（逻辑单一来源）',
       'run_segments(seg_seq)' in src)

    print('\n[test_i] PASS=%d FAIL=%d' % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == '__main__':
    main()
