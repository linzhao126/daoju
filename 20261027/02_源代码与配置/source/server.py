"""换刀决策后端 —— FastAPI + SQLite(WAL)

跑法
    cd 2ndPrediction/tool_log
    ../../.venv/Scripts/python.exe server.py
    浏览器打开 http://127.0.0.1:8765/
    接口文档（FastAPI 自带）  http://127.0.0.1:8765/docs

    局域网访问（机床旁平板）: python server.py --host 0.0.0.0

    零依赖备选：server_stdlib.py（标准库 http.server 版，厂里装不了包时用）

数据流（两种数据源，用 --source 选）
    [sim]    Simulator 线程 ──一件 60 行──┐
    [influx] InfluxPuller 线程 ──15s 窗口──┤
                                          ▼
                                    raw_rows 表（原始层，ts 唯一去重）
                                          │  按 allnum 封口（延迟 120s 才封）
                                          ▼
                             件内处理（裁 x 段 + X 对齐 + 负载门控 + 取中位）
                                          │
                                          ▼
                                   pieces 表（一件一个 load_med）
                                          │  ÷ 前 K 件基线
                                          ▼
                                        HI
                                          │
                                两级判定（0.4×M0 触发 → 信号许可 → 0.9×M0）
                                          │
                                          ▼
                                   GET /api/state ──> 页面轮询

保留策略（2026-10-21 新增，见 cleanup()）
    raw_rows  滚动 7 天   pieces 滚动 30 天   history / tool_events 永久
    ⚠ 绝对不能按系统时钟 now 删：放假 10 天后程序一启动，
      max(ts) 已经是 10 天前，now-7d 会把库删空。
      所以 cutoff 用**数据水位线** W=max(ts)，W-7d。放假多久都一行不删。

并发模型（改成 FastAPI 后最关键的一处）
    路由全部用同步 def → FastAPI 自动丢进线程池，不阻塞事件循环
    SQLite 连接用 threading.local()，一个请求线程一个连接
    写操作统一走 _wlock（RLock）串行化 —— WAL 只允许一个写者，不加锁会 SQLITE_BUSY
    busy_timeout=5000 兜住跨线程的短暂争用
"""

import os
import argparse
import hashlib
import json
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import simulator
from influx_source import InfluxPuller, iso, parse_iso

HERE = Path(__file__).parent
DB_PATH = HERE / "data" / "tool.db"
INDEX = HERE / "index.html"


def _self_sha():
    """本文件自身的 SHA256（前 12 位）。

    ⭐ 审核 R09 要求「源代码真实运行版本 hash」：光有文件哈希不够，因为分不清
       "磁盘上这份"和"进程里跑的那份"。启动时算一次、随 /api/state 返回，
       才能证明"页面上看到的判定来自哪个版本"。
    """
    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
    except Exception:
        return "UNKNOWN"


CODE_SHA = _self_sha()

# ---- 决策参数（与 bin_decision.py 保持一致）
K_BASE = 20        # 新刀基线取前几件
W = 10             # 信号平滑窗口
TAU = 0.01         # 延长判断门槛：HI 相对基线上升 >= 1%
BASE_F, EXT_F = 0.4, 0.9

# ---- 历史样本量门槛（2026-10-21 加）
# 为什么必须有门槛：这四格（最短/中位/常见上限/最长）本来就是"同一个寿命分布的
# 四种统计"。样本只有 1 条时，median = p85 = min = max = 那一条，
# 四个数字完全相等 —— 看着确凿，其实等于什么都没说。真实案例：prog 17 没有历史，
# 兜底值 100 被原样显示成四格，用户以为系统算出了四个值。
MIN_N_BASIC = 3      # 最短 / 中位 / 最长 至少要 3 条历史
MIN_N_P85 = 5        # 常见上限 + 0.4/0.9 判定线 至少要 5 条
                     # 依据：n=7 时 P85 的 bootstrap 区间是 [85,197]，跨度 103%
EXP_M0 = 100.0       # 无历史时的"经验提示值"，只作提示线，不参与任何判定

# 本服务只接一把刀（pieces 表无刀号列），但这个标识必须显式存在：
# R03 要求把 "PhysicalToolLifeId" 与 "ProgramSegmentId" 分开命名，不能靠"整库只有一把刀"隐含。
TOOL_NO = os.environ.get("TOOL_NO", "T1111")

# ---------------------------------------------------------------- 分组粒度
# ⭐⭐ 0 号位 2026-10-29 裁决（D-04，见 19-00）：
#     **按主程序计件与统计，不使用「品番」这一层。**
#
# 为什么推翻 10-28 的"按品番"：
#   1) 「品番」不是数据字段，也不是台账字段——是我从程序名 `C5G25-02-BAR331` 里劈出来的
#      推断（台账 Sheet2 只有一句"同缸径品番段取，刀具基本共通"，没有编码）。
#      生产系统的口径不能建立在一个推断出来的层上。
#   2) 精度上本来就不成立：28 周期、剔 c38/c51、按主程序 vs 按品番
#        N=20/40 主程序 **13.571** vs 品番 14.192（主程序更好）
#        N=60    主程序  9.944  vs 品番 **9.045**
#        N=80    主程序  8.000  vs 品番 **7.694**
#      差 ≤1 件、方向随截断点翻转（见 51-00），不足以支撑"品番更优"。
#   3) 按主程序每组 8/9/6/2/1/1/1 把刀 → 有 3 个程序样本 <3，必须走全局回退，
#      这比"合并成 2 组假装样本够"更诚实。
#
# ⚠ 仍然存在的缺陷（**未修，属 R03，见 49-00**）：
#   按主程序过滤会漏掉"同一把刀跨程序加工"的件（c33 按 prog=19 只数 56，实际 107）。
#   正确解法是物理刃口 ID + 跨程序累计，不是把程序合并成"品番"。
#
# `GROUP_MODE=part` 仅保留作对照/排查（已弃用，不参与默认口径）。
PART_PROGS = {
    '02': [11, 13, 14],          # ⚠ 推断层（程序名前缀），非台账字段
    '03': [16, 17, 19, 20],      # ⚠ 同上
}

# 默认 = 按主程序（D-04）。设 GROUP_MODE=part 才走已弃用的品番合并。
GROUP_MODE = os.environ.get("GROUP_MODE", "prog")

# ---------------------------------------------------------------- 判定策略（2026-10-29 加）
# ⭐ 审核 P0-MOD-01 要求：线上判定暂定位为 MONITOR_ONLY / EXPERIMENTAL_POLICY，不得提供安全许可。
#   含义：本服务只**观测**、只给"参考线"，不给任何形式的"可以继续用 / 安全延寿"许可。
#   `safety_permit` 恒 False，任何调用方都不许把它当换刀依据；
#   要改成真正参与换刀决策，必须另有 0 号位的工艺评审批准（不是改个环境变量就生效）。
POLICY_MODE = os.environ.get("POLICY_MODE", "MONITOR_ONLY")
# 允许出现的取值：MONITOR_ONLY（默认，影子）/ EXPERIMENTAL_POLICY（仍不出许可，仅标记为实验口径）
POLICY_MODES = ("MONITOR_ONLY", "EXPERIMENTAL_POLICY")
if POLICY_MODE not in POLICY_MODES:
    POLICY_MODE = "MONITOR_ONLY"
SAFETY_PERMIT = False        # 写死：本版本永不出具安全许可

# ---------------------------------------------------------------- 默认参考线（0 号位 D-05，2026-10-29）
# 裁决原文：「k（崩后平均几件才发现）说不清 → 走默认」。
# 默认 = 一律到 0.9·M0 就换（COUNT_H），依据见 61-00 §1.4：
#   k=0 时它在 ρ>0.25 的**整个区间**都最省；k 说不清时它也是全区间接近最优、
#   且不依赖任何信号（不需要 HI、不需要延长判断、不需要台账），出错面最小。
#
# ⚠ 这只是**参考线**，不是换刀许可：
#   1) `SAFETY_PERMIT` 恒 False；2) `judge_allowed` 门禁仍生效（起点未确认不给线）；
#   3) 两档逻辑（grant 锁存）**原样保留**，作为 EXPERIMENTAL 对照，页面并排显示。
REFERENCE_RULE = os.environ.get("REFERENCE_RULE", "COUNT_H")   # COUNT_H | TWO_STAGE
if REFERENCE_RULE not in ("COUNT_H", "TWO_STAGE"):
    REFERENCE_RULE = "COUNT_H"


def group_progs(prog):
    """返回参与"同一把刀历史"统计的程序号集合。

    GROUP_MODE='prog' → 只返回自身（默认，D-04 按主程序）
    GROUP_MODE='part' → 同"品番"下的全部程序（**已弃用**，仅对照用）
    未知程序（不在映射表里）→ 只返回自身，安全回退
    """
    try:
        prog = int(prog)
    except (TypeError, ValueError):
        return [prog]
    if GROUP_MODE != "part":
        return [prog]
    for _part, ps in PART_PROGS.items():
        if prog in ps:
            return list(ps)
    return [prog]


def part_of(prog):
    """prog → 品番号（'02' / '03'）；未知返回 None"""
    try:
        prog = int(prog)
    except (TypeError, ValueError):
        return None
    for part, ps in PART_PROGS.items():
        if prog in ps:
            return part
    return None


def _ph(n):
    """n 个占位符"""
    return '(' + ','.join('?' * n) + ')'




# ---- 保留策略
KEEP_RAW_DAYS = 7            # raw_rows 滚动保留天数
KEEP_PIECES_DAYS = 30        # pieces 滚动保留天数
MIN_RAW_ROWS = 50000         # 无论如何至少留这么多行（≈1 天量），防删空
MIN_PIECES = 300             # 无论如何至少留这么多件
CLEAN_EVERY_SEC = 3600       # 清理节拍：每小时一次（不要每 15s，DELETE 写 WAL 太频繁）
SEAL_DELAY_SEC = 120         # 一件最后一行超过这么久没更新，才认为这件收齐了

# 初始历史台账（模拟"以前积累的换刀记录"，让系统一开头就有 M0 可用）
SEED_HISTORY = simulator.LIFE_SEQ

# 数据源：sim（模拟器）/ influx（真实采集）
SOURCE = "sim"
PULLER = None

_tls = threading.local()
_wlock = threading.RLock()

RAW_ROWS_DDL = """
CREATE TABLE raw_rows(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL UNIQUE,
    allnum INTEGER, prog INTEGER,
    x_pos REAL, z_pos REAL, spindle_load REAL, spindle_speed REAL, feedrate REAL,
    recv_ts TEXT);
"""


# ---------------------------------------------------------------- 数据库
def db():
    """每线程一个连接。SQLite 连接不能跨线程共享，threading.local 是最省事的隔离。"""
    c = getattr(_tls, "conn", None)
    if c is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5.0)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=5000")
        c.execute("PRAGMA synchronous=NORMAL")
        if str(c.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
            c.execute("PRAGMA journal_mode=WAL")
        _tls.conn = c
    return c


def init_db():
    c = db()
    with _wlock:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS raw_rows(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL UNIQUE,
            allnum INTEGER, prog INTEGER,
            x_pos REAL, z_pos REAL, spindle_load REAL, spindle_speed REAL, feedrate REAL,
            recv_ts TEXT);
        CREATE INDEX IF NOT EXISTS i_raw_allnum ON raw_rows(allnum);
        CREATE INDEX IF NOT EXISTS i_raw_ts ON raw_rows(ts);

        CREATE TABLE IF NOT EXISTS pieces(
            allnum INTEGER, prog INTEGER,
            load_med REAL, n_points INTEGER, ts TEXT,
            curve TEXT,
            PRIMARY KEY(allnum));
        CREATE INDEX IF NOT EXISTS i_pieces_ts ON pieces(ts);

        CREATE TABLE IF NOT EXISTS tool_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, event TEXT, prog INTEGER, allnum INTEGER,
            pieces INTEGER, state TEXT, reason TEXT, limit_at INTEGER);

        CREATE TABLE IF NOT EXISTS history(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prog INTEGER, life REAL, src TEXT, valid INTEGER DEFAULT 1);

        CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
        """)
        c.commit()

        # ---- 老库补列（history.valid 是后加的）
        cols = [r[1] for r in c.execute("PRAGMA table_info(history)").fetchall()]
        if "valid" not in cols:
            c.execute("ALTER TABLE history ADD COLUMN valid INTEGER DEFAULT 1")
            c.commit()

        # ---- 老库迁移：raw_rows 早期没有 UNIQUE(ts)，去重挡不住重复行
        row = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='raw_rows'").fetchone()
        if row and "UNIQUE" not in row[0].upper():
            n_old = c.execute("SELECT COUNT(*) FROM raw_rows").fetchone()[0]
            c.execute("ALTER TABLE raw_rows RENAME TO raw_rows_old")
            c.execute(RAW_ROWS_DDL)
            if n_old:
                # 旧模拟器一件 60 行共用同一个 ts，迁移时 UNIQUE 只会留下每件 1 行。
                # 旧数据本来就是假的，直接丢；这里保留可迁移的部分。
                c.execute("""INSERT OR IGNORE INTO raw_rows(ts,allnum,prog,x_pos,spindle_load,
                             spindle_speed,feedrate)
                             SELECT ts,allnum,prog,x_pos,spindle_load,spindle_speed,feedrate
                             FROM raw_rows_old""")
            c.execute("DROP TABLE raw_rows_old")
            c.executescript("""
                CREATE INDEX IF NOT EXISTS i_raw_allnum ON raw_rows(allnum);
                CREATE INDEX IF NOT EXISTS i_raw_ts ON raw_rows(ts);
            """)
            c.commit()
            print("[迁移] raw_rows 已重建并加上 UNIQUE(ts)，旧行 %d 条" % n_old)

        # ---- 老库补列：raw_rows.z_pos（Z 带过滤要用）+ pieces.curve（重算用）
        # 放在重建迁移之后：重建用的是 RAW_ROWS_DDL，本身已含 z_pos，
        # 先补再重建的话这次补列等于白做、日志还会误导。
        # ALTER TABLE ADD COLUMN 是 O(1) 元数据操作，几万行也不用重建。
        # 补出来的列在老行上是 NULL：
        #   raw_rows.z_pos 为 NULL（整件都 NULL）时，align_piece 跳过 Z 带，退回旧口径
        #   pieces.curve   为 NULL 说明这件是加列前封口的，只剩 load_med 可用
        raw_cols = [r[1] for r in c.execute("PRAGMA table_info(raw_rows)").fetchall()]
        if "z_pos" not in raw_cols:
            c.execute("ALTER TABLE raw_rows ADD COLUMN z_pos REAL")
            c.commit()
            print("[迁移] raw_rows 已补 z_pos 列（旧行为 NULL，对齐时自动跳过 Z 带）")

        # ⭐ 2026-10-29（R06 P0-a）：补 recv_ts —— "我们拿到这一行的时刻"
        #   有了它，数据到达延迟 L2 从"完全未知"变成"每行可算"：
        #       lag = parse_iso(recv_ts) − parse_iso(ts)
        #   recv_ts 是**批量写入时**打的戳（同一批共用一个值），所以 lag 是**上界**
        #   （包含查询+传输+解析），不是点值。要更细就得在采集盒侧打点。
        #   旧行为 NULL → lag 统计自动跳过，不污染。
        if "recv_ts" not in raw_cols:
            c.execute("ALTER TABLE raw_rows ADD COLUMN recv_ts TEXT")
            c.commit()
            print("[迁移] raw_rows 已补 recv_ts 列（旧行为 NULL，延迟统计自动跳过）")

        piece_cols = [r[1] for r in c.execute("PRAGMA table_info(pieces)").fetchall()]
        if "curve" not in piece_cols:
            c.execute("ALTER TABLE pieces ADD COLUMN curve TEXT")
            c.commit()
            print("[迁移] pieces 已补 curve 列（旧行为 NULL）")

        # ---- 只在第一次运行时灌入历史台账
        if c.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 0:
            rows = [(p, float(l), "seed") for p, seq in SEED_HISTORY.items() for l in seq]
            c.executemany("INSERT INTO history(prog,life,src) VALUES(?,?,?)", rows)
            c.commit()


def kv_get(k):
    r = db().execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return r[0] if r else None


def kv_set(k, v):
    with _wlock:
        try:
            c = db()
            c.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                      (k, str(v)))
            c.commit()
        except Exception:
            _rollback()
            raise


def _rollback():
    """任何写操作抛异常后必须回滚当前连接。

    不滚的后果（2026-10-22 实测踩到，很隐蔽）
        sqlite3 的 DML 会隐式开启事务，异常跳过了 commit，这个事务就**悬在**
        线程池那一个连接上，一直占着库的写锁。
        于是：按钮报一次错 → 之后拉取线程每 15 秒写入全部 "database is locked"，
        整个采集链路跟着瘫；更糟的是这个悬空事务可能被同一连接上**后面某次
        commit 顺手带上**，于是"报错的操作"其实半截落了库（实测 tool_events
        留下了 4 条，history 却一条没有，两边不一致）。
    """
    try:
        db().rollback()
    except Exception:
        pass


# ---------------------------------------------------------------- 落库 / 封口 / 清理
def write_rows(rows):
    """influx 拉来的一批行：靠 raw_rows.ts 的 UNIQUE 去重后落库。返回实际新增行数。

    为什么靠数据库去重而不是在内存里判断：
        方案 A 的窗口会和上一轮重叠（OVERLAP），同一行会被拉两次。
        用 INSERT OR IGNORE + UNIQUE(ts) 是最省心、也最不怕重启的做法。
    """
    if not rows:
        return 0
    with _wlock:
        try:
            c = db()
            before = c.total_changes
            c.executemany(
                "INSERT OR IGNORE INTO raw_rows(ts,allnum,prog,x_pos,z_pos,spindle_load,"
                "spindle_speed,feedrate,recv_ts) VALUES(?,?,?,?,?,?,?,?,?)",
                [(r["ts"], r["allnum"], r["prog"], r["x_pos"], r.get("z_pos"),
                  r["spindle_load"], r["spindle_speed"], r["feedrate"],
                  r.get("recv_ts")) for r in rows])
            c.commit()
            return c.total_changes - before
        except Exception:
            _rollback()
            raise


def curve_json(curve):
    """40 点曲线 -> 紧凑 JSON（约 320~400 字节）。

    存"插值后、门控前"的原始对齐值，不存 HI、不存归一化后的数：
    归一化需要基线，而基线会随算法变；存原始值才能任何时点重算。
    """
    if not curve:
        return None
    return json.dumps([round(float(v), 4) for v in curve], separators=(",", ":"))


def seal_pending_pieces():
    """把 raw_rows 里"已经收齐"的件算成 pieces，返回新封口件数。

    收齐判据（两条同时满足）：
      allnum < MAX(allnum)              最后一件可能还在加工，不封
      该件最后一行已过 SEAL_DELAY_SEC    数据可能还在路上，再等等

    为什么要延迟 120 秒再封：拉取是 15 秒一批，一件的最后几行常和后一批一起到。
    立刻封口会少几行 → 插值端点偏移 → load_med 偏低 → HI 会往下跳一下。
    """
    c = db()
    mm = c.execute("SELECT MIN(allnum), MAX(allnum) FROM raw_rows").fetchone()
    if mm is None or mm[1] is None:
        return 0
    min_an, max_an = int(mm[0]), int(mm[1])
    upto = int(kv_get("sealed_upto") or 0)
    if upto == 0:
        upto = min_an - 1

    cands = [int(r[0]) for r in c.execute(
        "SELECT DISTINCT allnum FROM raw_rows WHERE allnum > ? AND allnum < ? ORDER BY allnum",
        (upto, max_an)).fetchall()]
    if not cands:
        return 0

    now_utc = datetime.now(timezone.utc)
    n_new = 0
    last_done = None
    with _wlock:
        try:
            for an in cands:
                r = c.execute("SELECT MAX(ts) FROM raw_rows WHERE allnum=?", (an,)).fetchone()
                last_ts = r[0] if r else None
                if not last_ts:
                    continue
                try:
                    age = (now_utc - parse_iso(last_ts)).total_seconds()
                except ValueError:
                    age = SEAL_DELAY_SEC + 1
                if age < SEAL_DELAY_SEC:
                    break      # 后面的件只会更晚，统统先不封

                rows = [{"x_pos": q[0], "z_pos": q[1], "spindle_load": q[2]} for q in
                        c.execute("SELECT x_pos, z_pos, spindle_load FROM raw_rows WHERE allnum=?",
                                  (an,))]
                pr = c.execute("SELECT prog FROM raw_rows WHERE allnum=? AND prog>0 LIMIT 1",
                               (an,)).fetchone()
                load_med, n_pts, curve = simulator.align_piece(rows)
                if load_med is not None:
                    c.execute("INSERT OR REPLACE INTO pieces(allnum,prog,load_med,n_points,ts,curve)"
                              " VALUES(?,?,?,?,?,?)",
                              (an, int(pr[0]) if pr else 0, load_med, n_pts, last_ts,
                               curve_json(curve)))
                    n_new += 1
                last_done = an     # 算不出来也推进，否则永远卡在这一件

            if last_done is not None:
                c.execute("INSERT INTO kv(k,v) VALUES('sealed_upto',?) "
                          "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(last_done),))
            c.commit()
        except Exception:
            # 封口跑在拉取线程里，异常不滚就会把写锁挂死，下一轮写入直接 database is locked
            _rollback()
            raise
    return n_new


_LAST_CLEAN = 0.0


def cleanup(force=False):
    """按**数据水位线**滚动删除 raw_rows(7天) / pieces(30天)。

    为什么不能用 now（这是本项目最容易犯的一类错）
        cutoff = now - 7d 在"放假"场景下会删空数据：
        放假 10 天，程序启动时 max(ts) 已经是 10 天前，
        所有数据都早于 now-7d → 一次 DELETE 把库删空。
        这正是用户提的场景。

    正确做法
        W = max(ts)（数据自己走到哪儿）
        cutoff = W - KEEP_DAYS
        放假 7 天 / 10 天 / 30 天，W 都不动，cutoff 也不动 → 一行不删。
        正常生产时 W ≈ now，等价于"保留最近 7 天"。
        设备永久停机时库不缩 —— 用"水位线停滞"告警处理，不靠删数据。

    第二层保险
        双条件：既早于 cutoff，又不在最近 MIN_* 条之内。两个都满足才删。
        raw_rows 少于 MIN_RAW_ROWS 时 OFFSET 返回 None → 一行都不删。

    history（16 条 M0/P85 依据）和 tool_events（换刀台账）永久保留，不参与清理。
    """
    global _LAST_CLEAN
    now_ts = time.time()
    if not force and now_ts - _LAST_CLEAN < CLEAN_EVERY_SEC:
        return {"skipped": True, "next_in": int(CLEAN_EVERY_SEC - (now_ts - _LAST_CLEAN))}
    _LAST_CLEAN = now_ts

    out = {"raw_deleted": 0, "pieces_deleted": 0}
    with _wlock:
        c = db()

        w = c.execute("SELECT MAX(ts) FROM raw_rows").fetchone()[0]
        if w:
            try:
                cutoff = iso(parse_iso(w) - timedelta(days=KEEP_RAW_DAYS))
            except ValueError:
                cutoff = None
            if cutoff:
                keep = c.execute("SELECT id FROM raw_rows ORDER BY id DESC LIMIT 1 OFFSET ?",
                                 (MIN_RAW_ROWS,)).fetchone()
                if keep:
                    cur = c.execute("DELETE FROM raw_rows WHERE ts < ? AND id < ?",
                                    (cutoff, int(keep[0])))
                    out["raw_deleted"] = cur.rowcount
                out["raw_watermark"] = w
                out["raw_cutoff"] = cutoff

        w = c.execute("SELECT MAX(ts) FROM pieces").fetchone()[0]
        if w:
            try:
                cutoff = iso(parse_iso(w) - timedelta(days=KEEP_PIECES_DAYS))
            except ValueError:
                cutoff = None
            if cutoff:
                keep = c.execute("SELECT allnum FROM pieces ORDER BY allnum DESC LIMIT 1 OFFSET ?",
                                 (MIN_PIECES,)).fetchone()
                if keep:
                    cur = c.execute("DELETE FROM pieces WHERE ts < ? AND allnum < ?",
                                    (cutoff, int(keep[0])))
                    out["pieces_deleted"] = cur.rowcount

        c.commit()
        # 删掉大量行后文件不会自动缩小，靠 freelist 复用；只在大清理后整理一次
        if out["raw_deleted"] > 5000 or out["pieces_deleted"] > 2000:
            try:
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
    return out


def current_prog():
    """当前在加工的程序号。

    真实数据里 prog 会随换程序而变（设备 4152 现在跑 17，不是标定用的 14/20/11），
    所以不能写死 SIM.prog，要以 pieces 里最新一件为准。
    """
    r = db().execute("SELECT prog FROM pieces ORDER BY allnum DESC LIMIT 1").fetchone()
    if r and r[0]:
        return int(r[0])
    return SIM.prog


# ---------------------------------------------------------------- 模拟器
class Simulator(threading.Thread):
    """模拟机床在加工 —— 它自己会换刀，但**不通知系统**

    这一点是整个演示的重点：物理上换刀了，系统并不知道，
    必须靠员工在页面上点"已换新刀"，系统才能重置计数基准。
    这正是"换刀时刻在数据上不可观测"的直接后果。
    """

    def __init__(self):
        super().__init__(daemon=True)
        self.running = False
        self.speed = 1.0
        self.allnum = 57800
        self.prog = 14
        self.seq_i = 0
        self.life_idx = 0
        self.life_len = SEED_HISTORY[14][0]
        self.rng = np.random.default_rng()
        self.phys_changes = 0     # 物理换刀次数（系统看不见）
        self._stop = False

    def step(self):
        """加工一件：写 60 行原始数据 + 一件汇总"""
        if self.life_idx >= self.life_len:
            # 物理换刀：磨损归零。注意——这里没有任何东西写进数据库
            self.life_idx = 0
            self.seq_i = (self.seq_i + 1) % len(SEED_HISTORY[self.prog])
            self.life_len = SEED_HISTORY[self.prog][self.seq_i]
            self.phys_changes += 1

        rows = simulator.make_piece(self.allnum, self.prog, self.life_idx, self.life_len, self.rng)
        # ts 必须逐行唯一：raw_rows.ts 有 UNIQUE 约束（靠它去重），
        # 旧版一件 60 行共用一个 ts，加约束后会只剩 1 行
        t0 = datetime.now(timezone.utc)
        ts_last = simulator.row_ts(t0, len(rows) - 1)

        load_med, n_pts, curve = simulator.align_piece(rows)

        with _wlock:
            try:
                c = db()
                c.executemany(
                    "INSERT OR IGNORE INTO raw_rows(ts,allnum,prog,x_pos,z_pos,spindle_load,"
                    "spindle_speed,feedrate) VALUES(?,?,?,?,?,?,?,?)",
                    [(simulator.row_ts(t0, i), r["allnum"], r["prog"], r["x_pos"], r["z_pos"],
                      r["spindle_load"], r["spindle_speed"], r["feedrate"])
                     for i, r in enumerate(rows)])
                if load_med is not None:
                    c.execute("INSERT OR REPLACE INTO pieces(allnum,prog,load_med,n_points,ts,curve)"
                              " VALUES(?,?,?,?,?,?)",
                              (self.allnum, self.prog, load_med, n_pts, ts_last,
                               curve_json(curve)))
                c.commit()
            except Exception:
                _rollback()
                raise

        self.allnum += 1
        self.life_idx += 1

    def run(self):
        while not self._stop:
            if not self.running:
                time.sleep(0.1)
                continue
            try:
                self.step()
            except Exception as e:
                print("[模拟器异常]", e)
            time.sleep(max(0.04, 0.32 / max(self.speed, 0.1)))

    def stop(self):
        self._stop = True
        self.join(timeout=3)


SIM = Simulator()


# ---------------------------------------------------------------- 到达延迟（R06 P0-a）
def lag_stats(limit=2000):
    """数据到达延迟 L2 = recv_ts − ts（秒）。

    ⚠ 口径必须说清：
      * `ts`     = 数据在机床侧产生的时刻（_time）
      * `recv_ts`= **我们把这一批写进库**的时刻，同一批共用一个值
      * 所以 lag 是**上界**：含 Influx 查询 + 网络 + 解析。
        若要"采集盒→库"的点值，必须在采集盒侧打点（本轮做不到）。
      * 只统计 recv_ts 非 NULL 的行：加列之前的老行自动跳过。
      * 负值（时钟不同步）丢弃并计数；>1 天的丢弃（停机跨天不算延迟）。

    为什么要它：`06-00` 里 L2 是"未知"，而它是"1 秒能不能做到"的第一决定项。
    有了这组数，"当前链路 135 秒"就从断言变成可复算。
    """
    try:
        rows = db().execute(
            "SELECT ts, recv_ts FROM raw_rows WHERE recv_ts IS NOT NULL "
            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    except Exception:
        return {"n": 0, "note": "raw_rows 读取失败"}
    vals, neg, stale = [], 0, 0
    for r in rows:
        try:
            d = (parse_iso(r["recv_ts"]) - parse_iso(r["ts"])).total_seconds()
        except Exception:
            continue
        if d < 0:
            neg += 1
            continue
        if d > 86400:
            stale += 1
            continue
        vals.append(d)
    if not vals:
        return {"n": 0, "neg": neg, "stale": stale,
                "note": "还没有带 recv_ts 的行（采集重启后才有）"}
    v = np.array(sorted(vals), dtype=float)
    return {
        "n": int(len(v)),
        "median_sec": round(float(np.median(v)), 3),
        "p95_sec": round(float(np.percentile(v, 95)), 3),
        "max_sec": round(float(v.max()), 3),
        "min_sec": round(float(v.min()), 3),
        "neg": neg,           # 时钟不同步的条数（应为 0）
        "stale": stale,       # 超过 1 天的（停机跨天，不算延迟）
        "note": "recv_ts 是批量写入时刻 → 该值是上界（含查询+传输+解析）",
    }


# ---------------------------------------------------------------- 判定层
def compute_view():
    c = db()

    # ---- 程序号：以 pieces 里最新一件为准
    # 真实设备会换程序（4152 现在跑 17），写死 SIM.prog 会拿错 M0
    prog = current_prog()

    # ---- 两套口径，必须分开（D-04 + 2026-10-29 件数修正）
    #   【口径 A：物理件数】"这把刀一共干了多少件" —— **不加任何程序过滤**
    #       pieces 表整库只有 T1111 一把刀的数据（无刀号列），所以 COUNT(*) 就是这把刀的件数。
    #       加 prog 过滤会把同一把刀跨程序的件劈掉：c33 实测 107 件 = prog20 的 51 + prog19 的 56，
    #       只数 prog19 会显示 56 件 → 直接导致延迟换刀。
    #   【口径 B：统计基准】"这类刀通常能干多少件" —— 按主程序（D-04），即当前 prog 的历史。
    progs = group_progs(prog)      # 只用于口径 B（hist / M0 / P85）
    ph = _ph(len(progs))
    part = part_of(prog)

    # 基准与件数都走口径 A：任何程序下的"已换新刀"都该重置这把刀的刀龄
    last_new = c.execute(
        "SELECT allnum FROM tool_events WHERE event='new' ORDER BY id DESC LIMIT 1").fetchone()
    r_min = c.execute("SELECT MIN(allnum) m, MAX(allnum) x FROM pieces").fetchone()
    if r_min is None or r_min["m"] is None:
        # 文案要跟数据源匹配：influx 模式下"点下面的开始"是模拟器专用说法，会让人乱按
        if SOURCE == "influx":
            msg = ("正在等待 T1111 的第一件数据 —— 采集流每 15 秒拉一次，"
                   "只有这把刀在切削时才会有行（9 把刀共用一条流）")
        else:
            msg = "还没有数据，点下面的「开始」把模拟数据灌进来"
        return {"ok": False, "msg": msg, "source": SOURCE}

    min_allnum, max_allnum = int(r_min["m"]), int(r_min["x"])
    base_allnum = int(last_new["allnum"]) if last_new else min_allnum - 1

    # ⚠ 件数必须数 pieces 的行数，不能用 allnum 差值（2026-10-21 实测踩到）
    # L_allnum 是**全厂/全设备共用的**全局件计数器：9 把刀混流，
    # 别的刀每加工一件 allnum 也 +1。实时流里 T1111 的 allnum 从 61000 跳到 61143，
    # 差值 143，但其中只有 43 件是 T1111 干的。
    # 离线 CSV 是过滤后的单刀数据，差值恰好等于件数，所以当年没暴露出这个问题。
    # ⚠ 但不加 prog 过滤是对的（2026-10-29 改）：pieces 只存 T1111，跨程序仍属同一把刀。
    used = c.execute("SELECT COUNT(*) FROM pieces WHERE allnum>?",
                     (base_allnum,)).fetchone()[0]

    # ---- 物理刃口的暴露明细（审核 P0-MOD-03 / R03）
    # 一把刀从 base 到现在参加过哪些程序段、各多少件。
    # 这就是 "ExposureBySegment"：**刀龄跨全部程序累计，只是把它摊开能看到**。
    # 程序段数（RunCount）用"相邻 prog 变化次数 + 1"算，不能用 distinct prog 数——
    # 11→20→11 是两个程序段还是一次调用，必须按**连续段**计。
    seg_seq = [r[0] for r in c.execute(
        "SELECT prog FROM pieces WHERE allnum>? ORDER BY allnum", (base_allnum,)).fetchall()]
    exposure, n_segments, cur = [], 0, None
    for pg in seg_seq:
        if cur is None or pg != cur:
            n_segments += 1
            exposure.append({"seg": n_segments, "prog": pg, "pieces": 0})
            cur = pg
        exposure[-1]["pieces"] += 1
    # 物理刃口 ID：本库只存一把刀（pieces 无刀号列），所以 ID 由"起点件号"唯一确定。
    # 不随程序切换而变 —— 这是 R03 要求的"PhysicalToolLifeId 不随品番切换"。
    phys_life_id = "phys_%s_%d" % (TOOL_NO, base_allnum)

    # ---- 历史寿命 → 最短 / 中位 / 常见上限 / 最长
    # ⚠ 这四格统计的是"同程序历史换刀记录"，跟当前这把刀无关，换刀不会改变它们。
    # ⚠ 样本不足必须返回 None，不能拿兜底值冒充（2026-10-21 修）：
    #   原来无历史时 hist=[100.0]，于是 median/p85/min/max 四个全等于 100，
    #   页面显示四个一模一样的"确凿数字"，用户完全没法判断这是算出来的还是猜的。
    hist = [r["life"] for r in c.execute(
        "SELECT life FROM history WHERE prog IN " + ph + " AND COALESCE(valid,1)=1 ORDER BY id",
        progs).fetchall()]
    n_invalid = c.execute(
        "SELECT COUNT(*) FROM history WHERE prog IN " + ph + " AND COALESCE(valid,1)=0",
        progs).fetchone()[0]

    n_real = len(hist)                    # 真实历史换刀记录条数（不再是兜底数组长度）
    no_hist = n_real == 0
    hmin = hmax = m0 = p85 = None
    if n_real >= MIN_N_BASIC:
        hmin = float(min(hist))
        hmax = float(max(hist))
        m0 = float(np.median(hist))
    if n_real >= MIN_N_P85:
        p85 = float(np.percentile(hist, 85))
    # judge_ok=False → 页面只准显示件数，不许给"延长判断/建议换刀"这类判定
    judge_ok = (m0 is not None and p85 is not None)
    # ⭐ 审核 P0-MOD-04：`judge_ok` 只检查"历史样本够不够"，**不检查这把刀的起点是不是人工确认过的新刀**。
    #    冷启动时 `base_allnum = min_allnum-1`，件数是"库里这把刀全部件数"，可能横跨好几次换刀 →
    #    这种刀拿到复核点 / 延长线毫无依据（虽然数字方向偏大、安全，但仍属"用未知起点做判定"）。
    #    所以把判定资格单独拆一个字段：样本够 ≠ 有资格判定。
    start_confirmed = (last_new is not None)
    judge_allowed = bool(judge_ok and start_confirmed)

    # 判定线只在样本够**且起点已确认**的时候才有意义；否则 None，页面显示「—」
    b = int(np.ceil(BASE_F * m0)) if judge_allowed else None
    h = int(np.ceil(EXT_F * m0)) if judge_allowed else None

    # ---- 当前这把刀的 HI 序列
    rows = c.execute(
        "SELECT allnum, load_med FROM pieces WHERE prog IN " + ph + " AND allnum>? ORDER BY allnum",
        progs + [base_allnum]).fetchall()
    hi_series, hi_now = [], None
    if len(rows) >= K_BASE:
        vals = np.array([r["load_med"] for r in rows], dtype=float)
        baseline = float(np.median(vals[:K_BASE]))
        if baseline:
            hi_all = vals / baseline
            hi_series = [round(float(v), 4) for v in hi_all]
            hi_now = float(np.median(hi_all[-W:]))

    # ---- 延长判断：跨过 base 时评估一次，锁存，之后不再改
    # 键里**不带 prog**：换程序不等于换刀，锁存状态必须跟着这把刀走（原 660 行带 prog，
    # 换程序后会读不到旧键、重新评估一次，属 R03 缺陷 D3）
    gkey = "grant_b%d" % base_allnum
    granted = None
    if not judge_allowed:
        # 样本不足 **或** 起点未确认：不做延长判断，也不写 kv。
        # 写 kv 会让锁存状态一直留着，等以后样本够了还沿用当时那个没依据的判断。
        limit, limit_txt = None, ("未标定，不给换刀点" if not judge_ok else "起点未确认，只报件数")

    else:
        gv = kv_get(gkey)
        if gv is not None:
            granted = (gv == "1")
        elif used >= b:
            granted = bool(hi_now is not None and (hi_now - 1.0) >= TAU)
            kv_set(gkey, "1" if granted else "0")

        if granted is None:
            limit, limit_txt = b, "到复核点再判"
        elif granted:
            limit, limit_txt = h, "信号未见异常上升，仅供参考"
        else:
            limit, limit_txt = b, "信号未确认，按基准复核"

    # ---- 四档状态（判定只看 M0，不看 P85）
    # 样本不足 / 起点未确认时降到 cnt（件数提示），不给任何"该不该换"的判断
    if not judge_allowed:
        st = "cnt"
    elif used >= m0:
        st = "bad"
    elif used >= h:
        st = "sug"
    elif used >= b:
        st = "chk"
    else:
        st = "ok"

    ev = [dict(r) for r in c.execute(
        "SELECT ts,event,pieces,state,reason FROM tool_events WHERE prog=? ORDER BY id DESC LIMIT 6",
        (prog,)).fetchall()]

    raw_n = c.execute("SELECT COUNT(*) FROM raw_rows").fetchone()[0]

    r1 = lambda v: None if v is None else round(v, 1)

    return {
        "ok": True,
        "prog": prog,
        # 分组口径：prog = 按主程序（默认，D-04）
        #           part = 按"品番"合并（已弃用，仅 GROUP_MODE=part 时出现）
        "group": {
            "mode": GROUP_MODE,
            "part": part,
            "progs": progs,
            "label": ("品番 C5G25-" + part + "（已弃用口径）")
                     if (GROUP_MODE == "part" and part)
                     else ("主程序 " + str(prog)),
        },
        "no_hist": no_hist,
        "allnum": max_allnum,
        "base_allnum": base_allnum,
        # base_src 决定页面怎么显示件数：
        #   event = 有人点过"换新刀"，件数精确
        #   cold  = 冷启动，件数只能当"至少这么多件"（上限），页面显示 ≥N
        "base_src": "event" if last_new else "cold",
        # ---- R03 的五个标识，必须分开命名（审核 P0-MOD-03）
        "phys_life_id": phys_life_id,          # 物理刃口，换程序不变
        "segment_id": "p%d" % prog,            # 当前程序段（PartSegmentId/ProgramSegmentId 同源）
        "base_segment_id": "p%d" % (exposure[0]["prog"] if exposure else prog),
        "tool_invocation_index": n_segments,   # 从起点到现在跨过几个连续程序段（RunCount）
        "exposure_by_segment": exposure,       # 各段件数，合计 = used
        "used": used,
        "m0": r1(m0), "p85": r1(p85),
        "hmin": r1(hmin), "hmax": r1(hmax),
        "n_hist": n_real,              # 真实历史条数（0 = 没标定）
        "n_real": n_real,
        "n_invalid": n_invalid,
        "judge_ok": judge_ok,                  # 样本够不够（统计层面）
        "judge_allowed": judge_allowed,        # 有没有资格给换刀判定（还要起点已确认）
        "start_confirmed": start_confirmed,    # 这把刀的起点是不是人工确认过的新刀
        "code_sha": CODE_SHA,                  # 运行中这份 server.py 的 SHA256 前 12 位（R09）
        # R06 P0-a：数据到达延迟（上界口径）
        "lag": lag_stats(),
        # D-05：默认参考线（只作参考，不是许可）
        "reference_rule": REFERENCE_RULE,      # COUNT_H（默认）/ TWO_STAGE（实验对照）
        "ref_limit_90": h,                     # 一律 0.9·M0
        "ref_limit_40": b,                     # 一律 0.4·M0
        "policy_mode": POLICY_MODE,            # MONITOR_ONLY / EXPERIMENTAL_POLICY
        "safety_permit": SAFETY_PERMIT,        # 恒 False：本版本不出具任何安全许可
        "min_n_basic": MIN_N_BASIC,
        "min_n_p85": MIN_N_P85,
        "exp_m0": EXP_M0,              # 无历史时的经验提示线（不是判定依据）
        "b": b, "h": h,
        "state": st,
        "hi": None if hi_now is None else round(hi_now, 4),
        "hi_series": hi_series[-60:],
        "granted": granted,
        "limit": limit,
        "limit_txt": limit_txt,
        "events": ev,
        "raw_rows": raw_n,
        "pieces_total": c.execute("SELECT COUNT(*) FROM pieces").fetchone()[0],
        # 可回溯性体检：多少件存了 40 点曲线 / 多少件的原始行有 z（Z 带真的在生效）
        # 这两个数低，说明阈值是按"弱一档"的口径标定的，页面会提示
        "n_curve": c.execute("SELECT COUNT(*) FROM pieces WHERE curve IS NOT NULL").fetchone()[0],
        "n_z": c.execute("SELECT COUNT(DISTINCT allnum) FROM raw_rows WHERE z_pos IS NOT NULL"
                         ).fetchone()[0],
        # 采集体检：轮询次数/失败数/数据滞后几秒 —— 页面拿它回答"到底有没有在算"
        "ingest": PULLER.report() if PULLER is not None else None,
        "sim": {"running": SIM.running, "speed": SIM.speed,
                "phys_life": SIM.life_idx, "phys_limit": SIM.life_len,
                "phys_changes": SIM.phys_changes},
        "source": SOURCE,
        "keep": {"raw_days": KEEP_RAW_DAYS, "pieces_days": KEEP_PIECES_DAYS},
    }


def log_event(event, reason=""):
    """记一条员工操作

    关键护栏：换刀件数异常大时（超过历史最长 1.3 倍）判为"疑似漏点"，
    照记事件但不计入 history —— 否则一次忘点就会把 M0 / P85 永久带偏。
    实测：漏点一次就出现了 501 件的"寿命"，P85 从 108.8 飙到 192.1。

    ⚠ hmax 可能是 None（2026-10-22 修，线上必现）
        该程序历史不足 MIN_N_BASIC(=3) 条时，compute_view() 会把
        hmin / hmax / m0 **全部返回 None**（不拿兜底值冒充，这是有意的）。
        原来这里直接 `1.3 * v["hmax"]` → TypeError: float * NoneType，
        而且是先 INSERT tool_events 再抛异常，commit 没执行 →
        这个写事务悬在连接上把库锁住 → 拉取线程跟着 "database is locked"。
        触发条件很常见：设备一换零件程序就必现（2026-10-22 从 17 切到 16，
        prog 16 一条历史都没有）。
        没有"历史最长"就没有判断依据 → suspect 只能 False，照常计入基准，
        但明确告诉用户这是第几条。
    """
    v = compute_view()
    if not v.get("ok"):
        return {"ok": False, "msg": "还没有数据"}
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pg = v.get("prog", SIM.prog)
    used = int(v["used"] or 0)
    counts = event in ("new", "fail")      # 只有这两种才算"一把刀用完了"
    hmax = v.get("hmax")                   # None = 该程序还没攒够历史
    n_hist = int(v.get("n_hist") or 0)

    suspect = False
    warn = ""
    if counts and hmax is not None:
        suspect = used > max(1.3 * hmax, hmax + 20)
        if suspect:
            warn = "本次记录 %d 件，超过历史最长 %d 件，疑似漏点按钮（已记录但不计入基准）" % (
                used, int(hmax))
    elif counts:
        # 没有历史最长可比 → 无法判断漏点，正常入库，但别让用户以为已经标定过了
        warn = "程序 %d 历史只有 %d 条（不足 %d 条），无法判漏点 → 本次 %d 件照常计入" % (
            pg, n_hist, MIN_N_BASIC, used)

    with _wlock:
        try:
            c = db()
            c.execute("INSERT INTO tool_events(ts,event,prog,allnum,pieces,state,reason,limit_at)"
                      " VALUES(?,?,?,?,?,?,?,?)",
                      (ts, event, pg, v["allnum"], used, v["state"], reason, v["limit"]))
            if counts:
                c.execute("INSERT INTO history(prog,life,src,valid) VALUES(?,?,?,?)",
                          (pg, float(used), event, 0 if suspect else 1))
            c.commit()
        except Exception:
            _rollback()
            raise
    return {"ok": True, "warn": warn}


def undo_event():
    pg = current_prog()
    with _wlock:
        try:
            c = db()
            r = c.execute("SELECT id,event FROM tool_events WHERE prog=? ORDER BY id DESC LIMIT 1",
                          (pg,)).fetchone()
            if not r:
                return {"ok": False, "msg": "没有可撤销的记录"}
            c.execute("DELETE FROM tool_events WHERE id=?", (r["id"],))
            c.execute("DELETE FROM history WHERE id=(SELECT MAX(id) FROM history WHERE prog=? AND src!='seed')",
                      (pg,))
            c.commit()
        except Exception:
            _rollback()
            raise
    return {"ok": True, "msg": "已撤销"}


def reset_all():
    global PULLER
    with _wlock:
        try:
            c = db()
            c.executescript("""
                DELETE FROM raw_rows; DELETE FROM pieces; DELETE FROM tool_events;
                DELETE FROM history WHERE src!='seed'; DELETE FROM kv;
            """)
            c.commit()
        except Exception:
            _rollback()
            raise
    SIM.allnum = 57800
    SIM.life_idx = 0
    SIM.seq_i = 0
    SIM.life_len = SEED_HISTORY[SIM.prog][0]
    SIM.phys_changes = 0
    if PULLER is not None:
        PULLER.w = None     # 水位线清空，下一轮重新回溯
    return {"ok": True}


# ---------------------------------------------------------------- FastAPI 应用
class Janitor(threading.Thread):
    """保留策略清理线程。启动先跑一次，之后每分钟来看一眼（内部按小时节拍执行）。"""

    def __init__(self):
        super().__init__(daemon=True)
        self._stop = False
        self.last = None

    def run(self):
        try:
            self.last = cleanup(force=True)
            print("[清理] 启动检查:", self.last)
        except Exception as e:
            _rollback()      # 大 DELETE 中途失败会留下写事务，不滚就把库锁死
            print("[清理异常]", e)
        while not self._stop:
            time.sleep(60)
            try:
                self.last = cleanup()
            except Exception as e:
                _rollback()
                print("[清理异常]", e)

    def stop(self):
        self._stop = True
        self.join(timeout=3)


JANITOR = Janitor()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global PULLER
    init_db()

    if SOURCE == "influx":
        # 水位线从库里的 MAX(ts) 恢复 —— 这就是数据实际走到哪儿，比另存一份 kv 更可靠
        w_init = None
        m = db().execute("SELECT MAX(ts) FROM raw_rows").fetchone()[0]
        if m:
            try:
                w_init = parse_iso(m)
            except ValueError:
                w_init = None

        PULLER = InfluxPuller(write_rows, on_seal=seal_pending_pieces, w_init=w_init)
        PULLER.start()
        print("[数据源] influx  水位线起于 %s" % (PULLER.w or "回溯 2 小时"))
    else:
        SIM.start()
        print("[数据源] sim（模拟器）—— 写入演示库，不碰真实数据")

    JANITOR.start()

    print("=" * 62)
    print("换刀决策后端已启动（FastAPI）")
    print("  页面   http://127.0.0.1:8765/")
    print("  接口文档 http://127.0.0.1:8765/docs")
    print("  数据库 %s" % DB_PATH)
    print("  保留   raw_rows %d 天 / pieces %d 天（按数据水位线）" % (KEEP_RAW_DAYS, KEEP_PIECES_DAYS))
    print("=" * 62)
    yield

    if PULLER is not None:
        PULLER.stop()
    SIM.stop()
    JANITOR.stop()
    # 退出时把 WAL 合并回主库，避免 -wal 越积越大
    with _wlock:
        try:
            db().execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
    print("已停止")


app = FastAPI(
    title="换刀决策后端",
    description="CNC 刀具寿命二档换刀决策：件数配额 + 信号参考线（不构成延长许可）",
    version="2.0",
    lifespan=lifespan,
)

# 页面和后端同源时用不到；加了方便从别处（其它端口/平板）打开页面调试
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class EventIn(BaseModel):
    event: str = Field(default="", description="new=换新刀, hold=继续加工, fail=刀废了")
    reason: str = Field(default="", description="刀废了的原因")


class SimIn(BaseModel):
    action: str = Field(description="start/stop/speed/prog/reset")
    speed: float = 1.0
    prog: int = 14


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(INDEX, media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-store"})


@app.get("/api/state")
def api_state():
    """页面每 700ms 轮询一次。同步 def → 跑在线程池，不阻塞事件循环。"""
    return compute_view()


@app.post("/api/event")
def api_event(body: EventIn):
    return log_event(body.event, body.reason)


@app.post("/api/undo")
def api_undo():
    return undo_event()


@app.post("/api/sim")
def api_sim(body: SimIn):
    a = body.action
    if a == "start":
        SIM.running = True
    elif a == "stop":
        SIM.running = False
    elif a == "speed":
        SIM.speed = float(body.speed)
    elif a == "prog":
        SIM.prog = int(body.prog)
        SIM.life_idx = 0
        SIM.seq_i = 0
        SIM.life_len = SEED_HISTORY[SIM.prog][0]
    elif a == "reset":
        return reset_all()
    return {"ok": True}


@app.get("/api/health")
def api_health():
    """给运维看的存活探针（也方便厂里做简单监控）。"""
    c = db()
    d = {
        "ok": True,
        "source": SOURCE,
        "db": str(DB_PATH),
        "prog": current_prog(),
        "raw_rows": c.execute("SELECT COUNT(*) FROM raw_rows").fetchone()[0],
        "pieces": c.execute("SELECT COUNT(*) FROM pieces").fetchone()[0],
        "raw_ts_range": dict(zip(("min", "max"),
                                 c.execute("SELECT MIN(ts), MAX(ts) FROM raw_rows").fetchone())),
        "keep": {"raw_days": KEEP_RAW_DAYS, "pieces_days": KEEP_PIECES_DAYS},
        "last_cleanup": JANITOR.last,
    }
    if PULLER is not None:
        d["influx"] = PULLER.report()
    else:
        d["sim"] = {"running": SIM.running, "speed": SIM.speed}
    return d


@app.post("/api/maintenance")
def api_maintenance():
    """手动触发一次清理（测试/运维用）。不带 force 语义，强制跑。"""
    return cleanup(force=True)


def main():
    global SOURCE, DB_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--source", choices=["sim", "influx"], default="sim",
                    help="数据源：sim=模拟器（演示），influx=真实采集流")
    ap.add_argument("--db", default=None,
                    help="数据库路径（默认 sim 用 data/tool_sim.db，influx 用 data/tool.db）")
    ap.add_argument("--reload", action="store_true", help="改代码自动重载（开发用）")
    a = ap.parse_args()
    SOURCE = a.source

    # ⚠ 两个数据源必须用不同的库文件（2026-10-21 实测踩到）
    # 原来都用 data/tool.db：先在 influx 模式回填了真实数据，再开模拟器演示，
    # 模拟器把 29 件假数据（allnum 57800 段）直接灌进了真实库。
    # 假数据混进真实库后，pieces 会被算进件数、污染 M0，而且清理策略不会删它们
    # （7 天保留期内），只能手工挑出来删。
    if a.db:
        DB_PATH = Path(a.db)
    elif a.source == "sim":
        DB_PATH = HERE / "data" / "tool_sim.db"
    else:
        DB_PATH = HERE / "data" / "tool.db"
    _tls.conn = None        # 必须在第一次 db() 之前改，否则连接已按旧路径建好

    if a.reload:
        # reload 需要以 import 字符串方式启动，才能监控文件变化
        uvicorn.run("server:app", host=a.host, port=a.port, reload=True,
                    log_level="info", access_log=False)
    else:
        uvicorn.run(app, host=a.host, port=a.port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
