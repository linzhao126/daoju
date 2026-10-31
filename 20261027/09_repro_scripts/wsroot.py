# -*- coding: utf-8 -*-
"""wsroot.py — 工作区根目录定位器（G2）

背景：本目录原位于 `<工作区根>/20261026/回复/20261027/09_repro_scripts`，
脚本里的 ROOT 按当时层级写死了 `'..'` 的个数；仓库重构到
`<工作区根>/daoju_repo/20261027/09_repro_scripts` 后层级差一级，
32 个脚本的 ROOT 因此落到 `C:\abc`，读不到工作区根的原始 CSV/台账，
在干净 clone 上跑 A 段会批量 FileNotFoundError（审核 P1-01 可复现性）。

修法：不再数 `'..'`，而是**向上找带工作区特征的目录**；找不到就退回当前布局的
`<本目录>/../../..`（即 daoju_repo 的上一级），保证行为可预测。

⚠ 标记选择踩过坑：不能用 `out_T1111` / `influx_daily_check` 这类**仓库内也有一份**的名字做标记
（会先命中 `daoju_repo` 本身，导致 ROOT 少上溯一级）。现在只用两条无歧义特征：
“含 `daoju_repo` 子目录的目录”（= 仓库的父目录）与 `.venv` / `手工提供文件`（仅开发机工作区根有）。
"""
import os

MARKERS = ('daoju_repo', '.venv', '手工提供文件')
# 注意：'daoju_repo' 作为标记意味着“p 下面有一个叫 daoju_repo 的子目录”，即 p 就是仓库父目录。


def _hit(p):
    return any(os.path.isdir(os.path.join(p, m)) for m in MARKERS)


def find(start=None, levels=8):
    """返回工作区根绝对路径。"""
    p = os.path.abspath(start or os.path.dirname(os.path.abspath(__file__)))
    # 从本目录的父级开始找（本目录在仓库内部，不应被当作根）
    p = os.path.dirname(p)
    for _ in range(levels):
        if _hit(p):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    here = os.path.abspath(os.path.dirname(os.path.abspath(__file__)))
    return os.path.abspath(os.path.join(here, '..', '..', '..'))


if __name__ == '__main__':
    print('工作区根 =', find())
