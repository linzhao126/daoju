"""换刀决策后端 —— 标准库 http.server + sqlite3，零第三方依赖

跑法
    cd 2ndPrediction/tool_log
    ../../.venv/Scripts/python.exe server.py
    浏览器打开 http://127.0.0.1:8765/
    局域网访问（机床旁平板）: python server.py --host 0.0.0.0

数据流
    模拟器线程 ──16ms 一行──> raw_rows 表（原始采集流）
                                   │  按 allnum 封口
                                   ▼
                         件内处理（X 对齐 + 负载门控 + 取中位）
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

一件数据到底怎么变成决策
    raw_rows 里同一 allnum 的 N 行 = 一件工件
        → 按 x_pos 插值到 19~40 / 40 点网格（消掉几何项，这是关键）
        → 丢掉低于 50% 中位的点（快进/退刀）
        → 取中位 = load_med
    HI = load_med ÷ 前 K(=20) 件 load_med 的中位
    HI - 1 就是"当前负载比这把刀新的时候高了多少"

判定为什么这么设计
    base = ceil(0.4×M0)      评估触发点，不是换刀点
    extend = ceil(0.9×M0)    硬上限
    到 base 时看 HI 是否已 ≥1%：
        是 → 正常磨损路径，给"延长许可"，可用到 extend
        否 → 立刻换（宁可换早）
    信号只当"许可证"，算不出来就默认不给 → 系统挂了只会少赚，不会出事故
"""

import argparse
import json
import random
import sqlite3
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

import simulator

HERE = Path(__file__).parent
DB_PATH = HERE / "data" / "tool.db"
INDEX = HERE / "index.html"

# ---- 决策参数（与 bin_decision.py 保持一致）
K_BASE = 20        # 新刀基线取前几件
W = 10             # 信号平滑窗口
TAU = 0.01         # 延长许可门槛：HI 相对基线上升 >= 1%
BASE_F, EXT_F = 0.4, 0.9

# 历史样本门槛：n=1 时 median/p85/min/max 全等于那一条，四个数一样，等于没信息。
# 与 server.py 保持完全一致，否则两个版本页面显示的判定会不一样。
MIN_N_BASIC = 3
MIN_N_P85 = 5
EXP_M0 = 100.0

# 初始历史台账（模拟"以前积累的换刀记录"，让系统一开头就有 M0 可用）
SEED_HISTORY = simulator.LIFE_SEQ

_lock = threading.Lock()
_conn = None


# ---------------------------------------------------------------- 数据库
def db():
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
    return _conn


def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS raw_rows(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL UNIQUE, allnum INTEGER, prog INTEGER,
        x_pos REAL, z_pos REAL, spindle_load REAL, spindle_speed REAL, feedrate REAL);
    CREATE INDEX IF NOT EXISTS i_raw_allnum ON raw_rows(allnum);
    CREATE INDEX IF NOT EXISTS i_raw_ts ON raw_rows(ts);

    CREATE TABLE IF NOT EXISTS pieces(
        allnum INTEGER, prog INTEGER,
        load_med REAL, n_points INTEGER, ts TEXT, curve TEXT,
        PRIMARY KEY(allnum));

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

    # 老库补列（history.valid 是后加的）
    cols = [r[1] for r in c.execute("PRAGMA table_info(history)").fetchall()]
    if "valid" not in cols:
        c.execute("ALTER TABLE history ADD COLUMN valid INTEGER DEFAULT 1")
        c.commit()

    # 老库补列：raw_rows.z_pos + pieces.curve（与 server.py 完全一致的迁移）
    raw_cols = [r[1] for r in c.execute("PRAGMA table_info(raw_rows)").fetchall()]
    if "z_pos" not in raw_cols:
        c.execute("ALTER TABLE raw_rows ADD COLUMN z_pos REAL")
        c.commit()
        print("[迁移] raw_rows 已补 z_pos 列")
    piece_cols = [r[1] for r in c.execute("PRAGMA table_info(pieces)").fetchall()]
    if "curve" not in piece_cols:
        c.execute("ALTER TABLE pieces ADD COLUMN curve TEXT")
        c.commit()
        print("[迁移] pieces 已补 curve 列")

    # 只在第一次运行时灌入历史台账
    if c.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 0:
        rows = [(p, float(l), "seed") for p, seq in SEED_HISTORY.items() for l in seq]
        c.executemany("INSERT INTO history(prog,life,src) VALUES(?,?,?)", rows)
        c.commit()


def kv_get(k):
    r = db().execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return r[0] if r else None


def kv_set(k, v):
    db().execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
    db().commit()


def _rollback():
    """写操作抛异常后必须回滚（与 server.py 同一处教训，2026-10-22）。

    不回滚的话，异常跳过了 commit，事务就悬在这唯一的连接上一直占写锁。
    本文件全局只有一个连接，所以症状一样：按钮报一次错，之后所有写入
    "database is locked"。
    """
    try:
        db().rollback()
    except Exception:
        pass


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
        # ts 必须逐行唯一且定长：raw_rows.ts 有 UNIQUE 约束（去重靠它），
        # 长度也必须是 27 字符，否则和 server.py 写的数据混在一起时字符串比较会错
        t0 = datetime.now(timezone.utc)
        ts = simulator.row_ts(t0, len(rows) - 1)

        load_med, n_pts, curve = simulator.align_piece(rows)

        with _lock:
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
                              (self.allnum, self.prog, load_med, n_pts, ts,
                               None if not curve else json.dumps(
                                   [round(float(v), 4) for v in curve], separators=(",", ":"))))
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


SIM = Simulator()


# ---------------------------------------------------------------- 判定层
def compute_view():
    c = db()

    # ---- 当前刀基准：最后一次"已换新刀"的 allnum；没有就用最早的一件
    last_new = c.execute(
        "SELECT allnum FROM tool_events WHERE event='new' AND prog=? ORDER BY id DESC LIMIT 1",
        (SIM.prog,)).fetchone()
    r_min = c.execute("SELECT MIN(allnum) m, MAX(allnum) x FROM pieces WHERE prog=?",
                      (SIM.prog,)).fetchone()
    if r_min is None or r_min["m"] is None:
        return {"ok": False, "msg": "还没有数据，点下面的「开始」"}

    min_allnum, max_allnum = int(r_min["m"]), int(r_min["x"])
    base_allnum = int(last_new["allnum"]) if last_new else min_allnum - 1
    # 件数必须数 pieces 行数：L_allnum 是 9 把刀共用的全局计数器，
    # 用差值会把别的刀也算进来（详见 server.py 同一处的说明）
    used = c.execute("SELECT COUNT(*) FROM pieces WHERE prog=? AND allnum>?",
                     (SIM.prog, base_allnum)).fetchone()[0]

    # ---- 历史寿命 → 最短 / 中位 / 常见上限 / 最长
    # 样本不足一律返回 None，不能拿兜底值冒充（与 server.py 同一处同步）
    hist = [r["life"] for r in c.execute(
        "SELECT life FROM history WHERE prog=? AND COALESCE(valid,1)=1 ORDER BY id",
        (SIM.prog,)).fetchall()]
    n_invalid = c.execute(
        "SELECT COUNT(*) FROM history WHERE prog=? AND COALESCE(valid,1)=0",
        (SIM.prog,)).fetchone()[0]

    n_real = len(hist)
    no_hist = n_real == 0
    hmin = hmax = m0 = p85 = None
    if n_real >= MIN_N_BASIC:
        hmin = float(min(hist))
        hmax = float(max(hist))
        m0 = float(np.median(hist))
    if n_real >= MIN_N_P85:
        p85 = float(np.percentile(hist, 85))
    judge_ok = (m0 is not None and p85 is not None)

    b = int(np.ceil(BASE_F * m0)) if judge_ok else None
    h = int(np.ceil(EXT_F * m0)) if judge_ok else None

    # ---- 当前这把刀的 HI 序列
    rows = c.execute(
        "SELECT allnum, load_med FROM pieces WHERE prog=? AND allnum>? ORDER BY allnum",
        (SIM.prog, base_allnum)).fetchall()
    hi_series, hi_now = [], None
    if len(rows) >= K_BASE:
        vals = np.array([r["load_med"] for r in rows], dtype=float)
        baseline = float(np.median(vals[:K_BASE]))
        if baseline:
            hi_all = vals / baseline
            hi_series = [round(float(v), 4) for v in hi_all]
            hi_now = float(np.median(hi_all[-W:]))

    # ---- 延长许可：跨过 base 时评估一次，锁存，之后不再改
    gkey = "grant_%d_%d" % (SIM.prog, base_allnum)
    granted = None
    if not judge_ok:
        limit, limit_txt = None, "未标定，不给换刀点"
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
            limit, limit_txt = h, "已获延长许可"
        else:
            limit, limit_txt = b, "信号未确认，按基准换"

    # ---- 四档状态（判定只看 M0，不看 P85）；样本不足降级为 cnt
    if not judge_ok:
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
        (SIM.prog,)).fetchall()]

    raw_n = c.execute("SELECT COUNT(*) FROM raw_rows").fetchone()[0]

    r1 = lambda v: None if v is None else round(v, 1)

    return {
        "ok": True,
        "prog": SIM.prog,
        "no_hist": no_hist,
        "allnum": max_allnum,
        "base_allnum": base_allnum,
        "base_src": "event" if last_new else "cold",
        "used": used,
        "m0": r1(m0), "p85": r1(p85),
        "hmin": r1(hmin), "hmax": r1(hmax),
        "n_hist": n_real, "n_real": n_real, "n_invalid": n_invalid,
        "judge_ok": judge_ok, "min_n_basic": MIN_N_BASIC, "min_n_p85": MIN_N_P85,
        "exp_m0": EXP_M0,
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
        "n_curve": c.execute("SELECT COUNT(*) FROM pieces WHERE curve IS NOT NULL").fetchone()[0],
        "source": "sim",
        "ingest": None,
        "sim": {"running": SIM.running, "speed": SIM.speed,
                "phys_life": SIM.life_idx, "phys_limit": SIM.life_len,
                "phys_changes": SIM.phys_changes},
    }


def log_event(event, reason=""):
    """记一条员工操作

    关键护栏：换刀件数异常大时（超过历史最长 1.3 倍）判为"疑似漏点"，
    照记事件但不计入 history —— 否则一次忘点就会把 M0 / P85 永久带偏。
    实测：漏点一次就出现了 501 件的"寿命"，P85 从 108.8 飙到 192.1。
    """
    c = db()
    v = compute_view()
    if not v.get("ok"):
        return {"ok": False, "msg": "还没有数据"}
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    used = int(v["used"] or 0)
    counts = event in ("new", "fail")
    # hmax 为 None = 该程序历史不足 MIN_N_BASIC 条（compute_view 故意不给兜底值）。
    # 原来直接 1.3 * None → TypeError，且异常发生在 commit 之前 → 事务悬空锁库。
    hmax = v.get("hmax")
    n_hist = int(v.get("n_hist") or 0)
    suspect = False
    warn = ""
    if counts and hmax is not None:
        suspect = used > max(1.3 * hmax, hmax + 20)
        if suspect:
            warn = "本次记录 %d 件，超过历史最长 %d 件，疑似漏点按钮（已记录但不计入基准）" % (
                used, int(hmax))
    elif counts:
        warn = "程序 %d 历史只有 %d 条（不足 %d 条），无法判漏点 → 本次 %d 件照常计入" % (
            SIM.prog, n_hist, MIN_N_BASIC, used)
    try:
        c.execute("INSERT INTO tool_events(ts,event,prog,allnum,pieces,state,reason,limit_at)"
                  " VALUES(?,?,?,?,?,?,?,?)",
                  (ts, event, SIM.prog, v["allnum"], used, v["state"], reason, v["limit"]))
        if counts:
            c.execute("INSERT INTO history(prog,life,src,valid) VALUES(?,?,?,?)",
                      (SIM.prog, float(used), event, 0 if suspect else 1))
        c.commit()
    except Exception:
        _rollback()
        raise
    return {"ok": True, "warn": warn}


def undo_event():
    c = db()
    r = c.execute("SELECT id,event FROM tool_events WHERE prog=? ORDER BY id DESC LIMIT 1",
                  (SIM.prog,)).fetchone()
    if not r:
        return {"ok": False, "msg": "没有可撤销的记录"}
    c.execute("DELETE FROM tool_events WHERE id=?", (r["id"],))
    c.execute("DELETE FROM history WHERE id=(SELECT MAX(id) FROM history WHERE prog=? AND src!='seed')",
              (SIM.prog,))
    c.commit()
    return {"ok": True, "msg": "已撤销"}


def reset_all():
    c = db()
    with _lock:
        c.executescript("""
            DELETE FROM raw_rows; DELETE FROM pieces; DELETE FROM tool_events;
            DELETE FROM history WHERE src!='seed'; DELETE FROM kv;
        """)
        c.commit()
    SIM.allnum = 57800
    SIM.life_idx = 0
    SIM.seq_i = 0
    SIM.life_len = SEED_HISTORY[SIM.prog][0]
    SIM.phys_changes = 0
    return {"ok": True}


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "ToolLog/1.0"

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/api/state":
            return self._json(compute_view())
        if p in ("/", "/index.html"):
            body = INDEX.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self):
        p = self.path.split("?")[0]
        d = self._read_json()
        if p == "/api/event":
            return self._json(log_event(d.get("event", ""), d.get("reason", "")))
        if p == "/api/undo":
            return self._json(undo_event())
        if p == "/api/sim":
            a = d.get("action")
            if a == "start":
                SIM.running = True
            elif a == "stop":
                SIM.running = False
            elif a == "speed":
                SIM.speed = float(d.get("speed", 1))
            elif a == "prog":
                SIM.prog = int(d.get("prog", 14))
                SIM.life_idx = 0
                SIM.seq_i = 0
                SIM.life_len = SEED_HISTORY[SIM.prog][0]
            elif a == "reset":
                return self._json(reset_all())
            return self._json({"ok": True})
        self.send_error(404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()

    init_db()
    SIM.start()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print("=" * 62)
    print("换刀决策后端已启动")
    print("  页面   http://%s:%d/" % ("127.0.0.1" if a.host == "0.0.0.0" else a.host, a.port))
    print("  数据库 %s" % DB_PATH)
    print("  按 Ctrl+C 停止")
    print("=" * 62)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
