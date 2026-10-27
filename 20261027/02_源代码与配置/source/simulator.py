"""模拟采集数据生成器 —— 模拟"机床每 16ms 上报一行"的数据流

为什么需要它
    真实环境里数据是 16ms 一行不停进来的，而且换刀时刻在数据上不可观测
    （已实测：换刀段无数据行、件间空档不可分、负载回落检不出）。
    这个模拟器把"原始数据一条条写入"这件事复现出来，用来验证
    整条链路：原始行 -> 件内对齐 -> HI -> 两级判定 -> 页面。

噪声为什么要分两层（这是全文件最关键的一点）
    件级偏移  sigma=2.5%  毛坯余量、材料硬度、装夹差异 —— 一整件一起偏移，
                          件内取 40 个点的中位数消不掉 → 这才让 r 落到 0.3 左右
    点级噪声  sigma=0.8%  采样抖动、振动 —— 件内取中位数会平均掉大部分
    如果只加"每点独立"的噪声，40 点中位数会把它平均成 σ/√40，
    HI 会平滑得像教科书，测出来的相关系数接近 1，那是假的。
    真实数据的 r 只有 +0.291（xalign 后），所以这里必须让件级偏移占主导。

磨损幅度 3%（从新刀到寿命终点），与实测"全寿命抬升 1.2%~3.3%"一致。
"""

import numpy as np

# 与 influx_source.py 的 iso() 必须保持一致：
# ts 靠字符串比较做去重和保留策略，长度/格式不一致会算错。
# 时长 27 字符（微秒精度），UTC。
TS_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def row_ts(t0, i, step_ms=0.5):
    """第 i 行的时间戳。

    raw_rows.ts 加了 UNIQUE 约束（靠它去重），所以同一件内每行必须不同。
    默认 0.5ms：一件 60 行只占 30ms，比模拟器最快件间隔 40ms 还短，
    否则高倍速跑起来相邻两件的时间戳会交叉。
    """
    from datetime import timedelta
    return iso(t0 + timedelta(milliseconds=step_ms * i))


# 与 xalign 实际使用的网格一致：X 从 19.0 到 40.0，40 点
X_MIN, X_MAX = 19.0, 40.0

# Z 带容差，与 t1111_pipeline.py XALIGN_Z_TOL 保持一致
Z_TOL = 1.0

# 切断工序的 Z 位置。实测值：
#   prog 14 / 20（历史 CSV）  Z0 = -38.30 / -55.30
#   prog 17（2026-10-21 实时）Z0 = -32.30
# 这里用 prog 14 的数，模拟器默认跑的就是 prog 14。
Z_CUT = -38.3
Z_RAPID = 189.36        # 快进/换刀位（实时流实测值）

# 各程序真实寿命序列（取自 xalign_HI.csv，按程序分组）
LIFE_SEQ = {
    14: [52, 80, 92, 85, 99, 50, 197],
    20: [95, 107, 109, 104, 108, 107],
    11: [97, 109, 47],
}

WEAR_AMP = 0.03        # 全寿命负载抬升幅度
PIECE_SIGMA = 0.025    # 件级偏移（消不掉）
POINT_SIGMA = 0.008    # 点级噪声（件内中位可平均掉）
BASE_LOAD = 30.0       # 新刀外圆处主轴负载基准 %
N_ROWS = 60            # 每件生成多少行（真实约 143 行，这里压缩以便快速演示）
N_RAPID = 8            # 其中末尾几行是快进/换刀位（x≈238, z=189），占位 13%
                       # 真实一件 143 行里这类占 45%，压缩后按比例缩小

# 短刀为什么不升反降
#   实测：寿命明显短的刀（c41 47 件、c25 52 件、c37 50 件），HI 斜率分别是
#   -0.027 / -0.047 / +0.005，全是负的或平的。原因不是"磨损慢"，
#   而是这类刀失效于崩刀、刀柄/装夹问题，根本不走渐进磨损那条路。
#   模拟必须复现这个规律，否则演示会假装"所有刀都能被信号看见"。
SHORT_RATIO = 0.7      # 寿命低于同程序中位 70% 视为短刀
SHORT_DRIFT = -0.012   # 短刀的负载随件数轻微下降


def make_piece(allnum, prog, life_idx, life_len, rng=None, n_rows=N_ROWS):
    """生成一件工件的原始采集行

    allnum    全局件计数（真实环境里 L_allnum 单调递增，换刀不重置）
    life_idx  这把刀已经加工到第几件（物理真值，系统不知道）
    life_len  这把刀的真实寿命

    返回 list[dict]，每个 dict 就是"一行原始数据"

    为什么每件要掺几行"换刀位"（2026-10-21 补）
        真实一件 143 行里只有约 79 行在切断段（x 19~41），其余 64 行是
        x≈238 的快进/换刀位、z=189。不掺这些行，模拟数据就比真实"干净"，
        测不出 x 段裁剪和 Z 带过滤到底有没有用。
        掺进来以后：x 段过滤先剔一遍，Z 带再剔一遍（两者独立），
        只要一件算出来的 HI 和没掺时一致，就说明过滤链是对的。
    """
    rng = rng or np.random.default_rng()

    n_cut = n_rows - N_RAPID          # 前 n_cut 行是切断段
    n_cut = max(n_cut, 12)

    # 切断工序：X 从外圆 40 一路走到 19（真实是 G96 恒线速，转速随直径变）
    x = np.linspace(X_MAX, X_MIN, n_cut)

    # 几何项：越接近中心，切削宽度越大 → 负载越高。件内变化 ~80%，远大于磨损的 3%
    geom = 1.0 + 0.8 * (X_MAX - x) / (X_MAX - X_MIN)

    # 磨损项：随这把刀已用寿命线性上升
    frac = min(1.0, life_idx / max(life_len - 1, 1))
    med_life = float(np.median(LIFE_SEQ.get(prog, [life_len])))
    if life_len < SHORT_RATIO * med_life:
        # 短刀：崩刀/刀柄问题，不走渐进磨损，负载反而略微走低
        wear = 1.0 + SHORT_DRIFT * frac
    else:
        wear = 1.0 + WEAR_AMP * frac

    # 件级偏移（消不掉）+ 点级噪声（可平均掉）
    piece_off = 1.0 + rng.normal(0.0, PIECE_SIGMA)
    noise = 1.0 + rng.normal(0.0, POINT_SIGMA, n_cut)

    load = BASE_LOAD * geom * wear * piece_off * noise

    # G96：转速随直径减小而升高，保证线速度恒定
    speed = 1290.0 * (40.0 / np.maximum(x, 1e-6)) * 0.55
    speed = np.where(speed < 200, 200, speed)

    # 切断段 Z 恒定，加 ±0.05mm 的伺服抖动（真实数据是精确恒定，
    # 但抖动能让 Z 带过滤的容差逻辑真的被执行到，而不是只做等值比较）
    z_cut = Z_CUT + rng.normal(0.0, 0.05, n_cut)

    rows = []
    for i in range(n_cut):
        rows.append({
            "allnum": int(allnum),
            "prog": int(prog),
            "x_pos": round(float(x[i]), 3),
            "z_pos": round(float(z_cut[i]), 3),
            "spindle_load": round(float(load[i]), 3),
            "spindle_speed": round(float(speed[i]), 1),
            "feedrate": 100.0,
        })

    # 件尾的快进/换刀位：x 退到 238、z 到 189、主轴几乎空转
    # （真实一件 143 行里这类占 45%，是 x 段裁剪存在的理由）
    for _ in range(n_rows - n_cut):
        rows.append({
            "allnum": int(allnum),
            "prog": int(prog),
            "x_pos": 238.0 + round(float(rng.normal(0, 0.3)), 3),
            "z_pos": Z_RAPID + round(float(rng.normal(0, 0.05)), 3),
            "spindle_load": round(float(max(0.5, rng.normal(1.2, 0.3))), 3),
            "spindle_speed": 0.0,
            "feedrate": 100.0,
        })
    return rows


def z_band_keep(rows, z_tol=Z_TOL):
    """Z 带：从"x 段已经筛过"的行里定位切断工步，返回 (保留的行, z0, 剔除行数)

    为什么不取"z 的众数"（第一版写法，实测被推翻）
        众数是"出现次数最多的那个精确值"。真实数据 z 恒为 -32.300，众数很好用；
        但只要 z 带一点抖动（伺服跟随误差 ±0.05mm 很常见），
        round(z,1) 会把同一个工步打散成 -38.3/-38.4/-38.2 三个值，
        而干扰工步的值恰好精确 → 干扰反而赢了众数。
        实测：给一件掺 52 行 z=189.36 后，众数选中的是 189.36，
        把真正切削的 52 行全剔掉，load_med 从 42.2 涨到 92.9。

    改用"分层 + 扫掠跨度"
        1) 把 z 排序，相邻差 > 2*z_tol 就切开 → 得到若干层
        2) 只有一层 → 本来就是同一个工步，不做过滤
        3) 多层 → 选 **x 跨度最大** 的那层。切断工序的物理特征就是
           "Z 锁定 + X 从 40 扫到 19"，跨度必然最大；
           换刀位/倒角/退刀只覆盖局部 x，跨度小。
           平票时再取行数多的（退化保护）。
        4) 最后按 |z - 该层中位| <= z_tol 保留

    为什么 x 段内的 None（没采到 z 的行）要一起剔
        这一件明明有 z，个别行没采到 → 无法证明它在切断位。
        Z 带的意义就是"只留能证明在切削位的行"，宁可少几行。
    """
    pts = [(r["z_pos"], r["x_pos"]) for r in rows if r.get("z_pos") is not None]
    if len(pts) < 8:
        return rows, None, 0

    pts.sort(key=lambda p: p[0])
    clusters, cur = [], [pts[0]]
    for prev, now in zip(pts, pts[1:]):
        if now[0] - prev[0] > 2.0 * z_tol:
            clusters.append(cur)
            cur = [now]
        else:
            cur.append(now)
    clusters.append(cur)
    if len(clusters) == 1:
        return rows, None, 0      # 只有一个工步，不需要分层

    def span(c):
        xs = [p[1] for p in c]
        return max(xs) - min(xs)

    best = max(clusters, key=lambda c: (round(span(c), 3), len(c)))
    z0 = float(np.median([p[0] for p in best]))
    kept = [r for r in rows
            if r.get("z_pos") is not None and abs(r["z_pos"] - z0) <= z_tol]
    if len(kept) < 8:
        # 选出来的层太小，不敢用；宁可不过滤（后面还有覆盖度检查兜底）
        return rows, None, 0
    return kept, z0, len(rows) - len(kept)


def median_from_curve(curve):
    """从 40 点曲线重算 load_med（与 align_piece 同口径）。

    为什么要这个函数
        pieces.curve 存的是"插值后、门控前"的 40 个点，约 400 字节。
        raw_rows 只保留 7 天，之后曲线就是唯一的原始证据。
        以后想换门控规则（比如 50% 改成 60%）或换算法，
        直接对 curve 重算即可，不必重采、也不必等 7 天数据回填。
    """
    if not curve:
        return None
    vals = np.asarray(curve, dtype=float)
    vals = vals[np.isfinite(vals)]
    if len(vals) < len(curve) * 0.5:
        return None
    med = float(np.median(vals))
    keep = vals[vals >= 0.5 * med]
    if len(keep) < len(vals) * 0.5:
        return None
    return float(np.median(keep))


def align_piece(rows, grid_n=40):
    """件内处理：把这一件的行对齐到统一 X 网格，返回 (中位负载, 有效点数, 40点曲线)

    这正是 t1111_pipeline.py xalign 的核心，顺序和离线一致：
      0) 按 x_pos 裁出切断段（x 19~41）
      1) Z 带过滤：只留 |z - z0| <= 1.0mm 的行（z0 = 该件切断段的 Z 众数）
      2) 按 x_pos 排序，插值到固定的 19.0~40.0 / 40 点网格（消掉几何项）
      3) 负载门控：丢掉明显偏低的点（快进/退刀/空转），再取中位
    仍比离线简化的是"X 单调下降段 + 啄切容差 2.5mm"这一层，见文末说明。

    为什么必须先裁 x 段（2026-10-21 实测）
        真实 T1111 一件 143 行里，只有 79 行的 x 落在 19~41：
        其余是 x≈238 的快进/换刀位、x≈0 的中心收尾。
        不裁的话它们的 x 会进入插值序列，把 19 和 40 两个端点带偏。

    为什么要 Z 带（2026-10-21 补，实测支撑）
        实时流里切断段内 z 恒为 -32.3（prog 17），而全件 z 还有
        -377.44（换刀位）和 189.36（快进位）。x 段裁剪已经能剔掉大部分，
        但换程序/换工序后完全可能出现"x 相同、z 不同"的另一个工步，
        那时只有 Z 带分得开。这是离线 xalign 里本来就有的步骤，
        之前 raw_rows 没存 L_abso_z 才省掉的。

    为什么仍不做"X 单调下降段"
        线上是逐件增量封口，需要在"这件已经收齐"时立刻出结果；
        单调段提取依赖完整时间序列做回溯，且啄切容差(2.5mm)和
        真实退刀(退到 x=40, +40mm)只差 16 倍，参数错了会静默切错段。
        当前用"x 段 + Z 带 + 负载门控"已经能把非切削行剔干净
        （实测切断段内 z 只有一个值、负载门控再兜一层），
        先不引入这个参数风险；等有真实换刀样本做对照再加。
    """
    if not rows:
        return None, 0, None

    # ---- 0) x 段裁剪
    rows = [r for r in rows if r.get("x_pos") is not None
            and X_MIN - 1.0 <= r["x_pos"] <= X_MAX + 1.0]
    if len(rows) < 8:
        return None, 0, None

    # ---- 1) Z 带过滤
    # 只在"有足够多 z 值"时启用：早期库里的行 z_pos 是 NULL（加列前入库的），
    # 这种件不能因为滤不了就被判废，直接跳过这一层，退回旧口径。
    zs = [r["z_pos"] for r in rows if r.get("z_pos") is not None]
    if len(zs) >= max(8, len(rows) * 0.6):
        rows, _z0, _nz = z_band_keep(rows)

    rows = sorted(rows, key=lambda r: r["x_pos"])
    xs = np.array([r["x_pos"] for r in rows], dtype=float)
    ys = np.array([r["spindle_load"] for r in rows], dtype=float)

    if xs.max() - xs.min() < 1.0:
        return None, 0, None
    # 覆盖不足时插值全靠端点外推，算出来的值不可信，直接判无效
    if xs.min() > X_MIN + 3.0 or xs.max() < X_MAX - 3.0:
        return None, 0, None

    grid = np.linspace(X_MIN, X_MAX, grid_n)
    order = np.argsort(xs)
    vals = np.interp(grid, xs[order], ys[order])
    curve = [round(float(v), 4) for v in vals]

    # 负载门控：只保留 >= 50% 中位的点（剔除异常低点）
    med = float(np.median(vals))
    keep = vals[vals >= 0.5 * med]
    if len(keep) < grid_n * 0.5:
        return None, 0, None
    return float(np.median(keep)), len(keep), curve



# ---------------------------------------------------------------- 独立运行
if __name__ == "__main__":
    """单独跑：往 data/tool.db 里灌模拟数据，不启服务
    用法: python simulator.py            # 默认灌 60 件
          python simulator.py 200        # 灌 200 件
    """
    import sqlite3
    import sys
    from datetime import datetime, timezone
    from pathlib import Path

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    db = Path(__file__).parent / "data" / "tool.db"
    db.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS raw_rows(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL UNIQUE, allnum INTEGER, prog INTEGER,
            x_pos REAL, z_pos REAL, spindle_load REAL, spindle_speed REAL, feedrate REAL);
        CREATE INDEX IF NOT EXISTS i_raw_allnum ON raw_rows(allnum);
    """)

    rng = np.random.default_rng(20261019)
    prog, allnum, life_idx, seq_i = 14, 57800, 0, 0
    life_len = LIFE_SEQ[prog][seq_i]
    t0 = datetime(2026, 10, 19, 14, 0, 0, tzinfo=timezone.utc)
    for k in range(n):
        if life_idx >= life_len:          # 物理上换刀了，但系统看不到
            life_idx = 0
            seq_i = (seq_i + 1) % len(LIFE_SEQ[prog])
            life_len = LIFE_SEQ[prog][seq_i]
        rows = make_piece(allnum, prog, life_idx, life_len, rng)
        conn.executemany(
            "INSERT OR IGNORE INTO raw_rows(ts,allnum,prog,x_pos,z_pos,spindle_load,"
            "spindle_speed,feedrate) VALUES(?,?,?,?,?,?,?,?)",
            [(row_ts(t0, i), r["allnum"], r["prog"], r["x_pos"], r["z_pos"],
              r["spindle_load"], r["spindle_speed"], r["feedrate"])
             for i, r in enumerate(rows)])
        t0 = t0.replace(microsecond=0)
        from datetime import timedelta
        t0 = t0 + timedelta(seconds=32)   # 一件约 31.5 秒，下一件不重叠
        allnum += 1
        life_idx += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM raw_rows").fetchone()[0]
    print(f"已写入 {n} 件 / {total} 行 -> {db}")
