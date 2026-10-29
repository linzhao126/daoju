"""
channel_map.py —— 通道映射层（审核 P1-08）

【要解决的问题】
`t1111_pipeline.py` / `hi_asof.py` / `s2x` 里把字段名写死成 `L_spindle_load` 这类形式。
`L_` 是**设备 4152 的加工通道前缀**，不是通用名。同一套方法论要跑 2428（`R_` 通道 / T3434）、
或任何新设备时，必须改代码——这使"公共方案"退化成"4152 专用脚本"。

【做法】
1. 定义**逻辑量 → 物理字段名**的映射（`LOGICAL`）。逻辑量如 `load` / `x` / `z` / `allnum`。
2. `ChannelMap(prefix)` 提供属性访问与列清单，下游一律走逻辑名，不再出现 `L_` 字面量。
3. `detect(cols)` 从表头自动判别前缀（`L_` / `R_`），新设备只要表头有前缀即可接入。
4. `validate(cols, prefix)` 检查某份数据是否满足契约，缺哪个字段一目了然。

【为什么 L 和 R 需要不同映射】
`L_`/`R_` 不是"左刀塔/右刀塔"，而是**两个独立的加工通道**（历史结论）。两者的
`abso_z` 量纲、程序号体系、`allnum` 语义都可能不同：
  - 4152/L/T1111：切断 Z 约 −38.30（prog11/13/14）、−32.30（prog16/17）、−55.30（prog19/20）
  - 2428/R/T3434：待实测（见 Z0_MEASURED）
所以 Z0 这类"绝对位置"参数**必须按通道分别标定**，不能跨通道继承。

【不做什么】
- **不改 `t1111_pipeline.py` 的既有行为**：它是审核方 E01 证据的生成器，改动需先报备。
  本模块先在我们自己的整改脚本（`hi_asof` 及以后）上落地，验证可行后再评估是否进主流水线。
- 不做单位换算：字段同名不代表同单位（历史 CSV 与实时流的 `L_spindle_load` 中位差 8.6 倍），
  跨源比较前必须单独校准。
"""

import re

# ---------------------------------------------------------------- 逻辑量定义
# required=True 表示缺了就无法计算 HI（见 hi_asof 的依赖）
LOGICAL = {
    'time':    {'L': '_time',            'R': '_time',            'required': True,
                'desc': '采集时刻（毫秒精度，作为唯一键）'},
    'load':    {'L': 'L_spindle_load',   'R': 'R_spindle_load',   'required': True,
                'desc': '主轴负载（HI 的度量对象）'},
    'x':       {'L': 'L_abso_x',         'R': 'R_abso_x',         'required': True,
                'desc': 'X 轴绝对位置（xalign 的插值自变量）'},
    'z':       {'L': 'L_abso_z',         'R': 'R_abso_z',         'required': True,
                'desc': 'Z 轴绝对位置（切断工步的 Z 带定位）'},
    'allnum':  {'L': 'L_allnum',         'R': 'R_allnum',         'required': True,
                'desc': '机床产品件号（切件依据）'},
    'spd':     {'L': 'L_spindle_speed',  'R': 'R_spindle_speed',  'required': True,
                'desc': '主轴转速（剔除非切削行的门控量）'},
    'program': {'L': 'L_main_program',   'R': 'R_main_program',   'required': False,
                'desc': '主程序号+品番（M0 分组依据）'},
    'tool_no': {'L': 'L_tool_number',    'R': 'R_tool_number',    'required': False,
                'desc': '刀号（多刀混采时过滤）'},
}

SUPPORTED_PREFIX = ('L', 'R')

# ---------------------------------------------------------------- Z0 标定
# 切断工步的典型 Z 位置。**必须按通道分别标定**，不可跨通道继承。
Z0_MEASURED = {
    'L': {       # 4152 / T1111，实测（见 24_程序归属修正、25_品番混批诊断）
        '11': -38.30, '13': -38.30, '14': -38.30,
        '16': -32.30, '17': -32.30,
        '19': -55.30, '20': -55.30,
    },
    'R': {},     # 2428 / T3434：待实测。空表示"未标定"，调用方必须自己从数据求 Z 众数
}

# ---------------------------------------------------------------- 品番映射
# 7 个程序 → 2 个品番（程序名形如 `14(C5G25-02-BAR331)`）
# 仅对 4152/L 通道成立；R 通道待确认
PART_PROGS = {
    '02': ['11', '13', '14'],
    '03': ['16', '17', '19', '20'],
}


def detect(cols):
    """从表头自动判别通道前缀。返回 'L' / 'R' / None。

    判据：哪个前缀的**必需字段命中数**最多。允许部分缺失（用于诊断"缺哪几个"）。
    """
    cols = list(cols)
    best, best_n = None, 0
    for p in SUPPORTED_PREFIX:
        n = sum(1 for v in LOGICAL.values() if v[p] in cols)
        if n > best_n:
            best, best_n = p, n
    # 至少要命中一半必需字段才认，否则可能根本不是这份数据
    need = sum(1 for v in LOGICAL.values() if v['required'])
    return best if best_n >= need // 2 + 1 else None


def validate(cols, prefix):
    """返回 (ok, missing_required, missing_optional)"""
    cols = set(cols)
    miss_req = [k for k, v in LOGICAL.items() if v['required'] and v[prefix] not in cols]
    miss_opt = [k for k, v in LOGICAL.items() if not v['required'] and v[prefix] not in cols]
    return (len(miss_req) == 0), miss_req, miss_opt


class ChannelMap(object):
    """逻辑量 → 物理字段名。下游一律用 `cm.load` / `cm['load']`，不写 `L_` 字面量。"""

    def __init__(self, prefix='L'):
        if prefix not in SUPPORTED_PREFIX:
            raise ValueError('不支持的通道前缀 %r（支持 %s）' % (prefix, SUPPORTED_PREFIX))
        self.prefix = prefix

    # ---- 逻辑名 → 物理名
    def __getattr__(self, name):
        # 只有 LOGICAL 里的逻辑量才允许属性访问；其它属性交给正常查找，避免掩盖拼写错误
        if name in LOGICAL:
            return LOGICAL[name][self.prefix]
        raise AttributeError('ChannelMap 没有逻辑量 %r（可用：%s）'
                             % (name, ', '.join(LOGICAL)))

    def __getitem__(self, name):
        return getattr(self, name)

    # ---- 常用组合
    def cols(self, logicals, extra=()):
        """按逻辑名列表返回物理列名，可附加原样透传的列（如 'life_cycle'）"""
        out = [self[k] for k in logicals]
        out.extend(extra)
        return out

    @property
    def all_cols(self):
        return [v[self.prefix] for v in LOGICAL.values()]

    # ---- 可选：把物理列改名成逻辑名，便于一份代码处理多通道
    def rename_to_logical(self, df):
        """返回新 DataFrame，列名换成逻辑名（`L_spindle_load` → `load`）

        ⚠ 只在需要"同一份下游代码同时吃 L 与 R"时用；日常不必，避免与历史代码的列名约定冲突。
        """
        mapping = {v[self.prefix]: k for k, v in LOGICAL.items() if v[self.prefix] in df.columns}
        return df.rename(columns=mapping)

    def to_pipeline_names(self, df):
        """把本通道的物理列名改写成 **L_ 形式**，供 `t1111_pipeline._xalign_piece` 使用。

        ⚠ 这是一个**过渡垫片，不是架构解**：`_xalign_piece` 内部有 10+ 处写死 `L_` 字段，
        而它是审核方 E01 证据的生成器，本轮不动。用本方法桥接，让其他通道也能跑通几何定位。
        真正的修法是给 `_xalign_piece` 加 channel_map 参数（需先向审核方报备）。
        """
        if self.prefix == 'L':
            return df
        ren = {}
        for v in LOGICAL.values():
            src, dst = v[self.prefix], v['L']
            if src in df.columns and dst not in df.columns:
                ren[src] = dst
        return df.rename(columns=ren)

    def z0_of(self, prog):
        """该程序的标定切断 Z；未标定返回 None（调用方须自己从数据求众数）"""
        return Z0_MEASURED.get(self.prefix, {}).get(str(prog).strip())

    def __repr__(self):
        return '<ChannelMap %s: load=%s z=%s>' % (
            self.prefix, self.load, self.z)


# ---------------------------------------------------------------- 自检
def _self_test():
    """最小自检：detect / validate / z0_of 在 L 与 R 两套表头上都应正常"""
    L_cols = ['_time', 'life_cycle', 'L_allnum', 'L_abso_x', 'L_abso_z',
              'L_spindle_load', 'L_spindle_speed', 'L_main_program', 'L_tool_number',
              'L_abso_y', 'L_alm_no']
    R_cols = ['_time', 'life_cycle', 'R_allnum', 'R_abso_x', 'R_abso_z',
              'R_spindle_load', 'R_spindle_speed', 'R_main_program', 'R_tool_number',
              'R_circle_time', 'R_distance']
    assert detect(L_cols) == 'L', detect(L_cols)
    assert detect(R_cols) == 'R', detect(R_cols)
    for p, cols in (('L', L_cols), ('R', R_cols)):
        ok, mr, mo = validate(cols, p)
        assert ok, (p, mr)
    # 缺字段必须被发现
    ok, mr, _ = validate([c for c in R_cols if c != 'R_abso_z'], 'R')
    assert (not ok) and mr == ['z'], mr
    cm = ChannelMap('R')
    assert cm.load == 'R_spindle_load' and cm.z == 'R_abso_z'
    assert cm.z0_of(14) is None          # R 通道未标定
    assert ChannelMap('L').z0_of('14') == -38.30
    assert ChannelMap('L').z0_of('16') == -32.30   # prog16 是 −32.30，不是 −38.30
    # 非法通道必须报错
    try:
        ChannelMap('X')
    except ValueError:
        pass
    else:
        raise AssertionError('非法前缀应报错')
    # 逻辑名拼错必须报错，不能静默返回 None
    try:
        cm.loadd
    except AttributeError:
        pass
    else:
        raise AssertionError('拼错的逻辑名应报 AttributeError')
    print('channel_map self-test: 12/12 PASS')


if __name__ == '__main__':
    _self_test()
