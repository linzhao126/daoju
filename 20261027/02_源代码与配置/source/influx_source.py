"""InfluxDB 拉取器 —— 把真实采集流按 15 秒窗口拉进来

为什么起点不能按"系统时钟步进"算（第一版设计的 bug，2026-10-21 修正）
    错误写法：end = T0 + (k-1)*15, start = end - 45
    起点完全由时钟决定，跟"数据实际追到哪儿"无关。
    若链路延迟 40s：第 2 次在 10:42:00 查，最新只返回到 10:41:20；
    第 3 次窗口从 10:41:30 开始 —— 10:41:20~10:41:30 这段
    第 2 次时还没入库、第 3 次起点已越过，**永久丢失**。
    只要延迟 > 重叠量就会漏，延迟越大漏得越宽。

正确做法（方案 A：水位线驱动）
    W      = 上次实际拿到的最新 _time（是"数据"的位置，不是 now）
    start  = min(W, now - MIN_STEP) - OVERLAP
    end    = now
    若 now - W > MAX_LOOKBACK:  W = now - MAX_LOOKBACK   （跳过无数据区，别每次白查 6 小时）
    若 end - start > MAX_LOOKBACK: start = end - MAX_LOOKBACK

    OVERLAP 只需要很小（5s）：W 本身已经把链路延迟包含进去了。
    延迟 40s → 窗口自动变 45s；延迟 5s → 窗口自动变 10s。这就是自适应。
    W 只在**成功拉取**后推进；失败时保持不动，下一轮窗口自动变长补回来。

为什么在服务端 pivot（实测数据，2026-10-21）
    L_tool_number 是 field 不是 tag，直接 filter 返回 0 行。
    不 pivot 就只能把全部刀的行都拉下来再在客户端筛：
    6 小时窗口 21.4 万行，T1111 只占 721 行（0.34%）—— 白传 99.7%。
    改成 pivot + filter 后：1 小时 1435 行、0.9 秒。

实测到的链路情况（2026-10-21 12:20）
    lag_sec ≈ 1.5 秒          —— 延迟很小，但这是"正在切削"时的值，
                                 停机时最新数据会是几分钟前（不代表传输延迟）
    设备 4152 当前只跑 prog 17（17(C5G25-03-AAR331)），
    与离线标定用的 14/20/11 **不是同一个零件程序**，M0 无历史可用（见 README）

去重
    pivot 后一个时间点一行，去重键 = ts（定长 UTC，微秒精度，27 字符），
    靠 raw_rows.ts 的 UNIQUE 约束 + INSERT OR IGNORE 自动挡掉重复。
"""

import csv
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------- 连接配置
INFLUX_URL = os.environ.get("INFLUX_URL", "http://10.196.32.63:8086")
INFLUX_ORG = os.environ.get("INFLUX_ORG", "s")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "iotdata")
# token 优先取环境变量；这里的默认值只为本机调试，不要提交到版本库
INFLUX_TOKEN = os.environ.get(
    "INFLUX_TOKEN",
    "tOpdgdFM2DBCY6-ZWZUn1s5bigxwmcrNGRNdNvHy79BSJq_yF9gFxHykKyUWRTWhCOu02SmJQmKEeaurU77hPw==",
)

DEVICE = os.environ.get("INFLUX_DEVICE", "4152")   # 只取这台设备
TOOL_NO = os.environ.get("INFLUX_TOOL", "T1111")   # 只取这把刀（9 把刀混流，T1111 只占 0.34%）

# ---------------------------------------------------------------- 窗口参数
POLL_SEC = 15              # 拉取间隔
MIN_STEP = 15              # 起点至少往回 15 秒（零延迟时也有基本窗口）
OVERLAP = 5                # 在 W 上再往回多要几秒，纯保险
MAX_LOOKBACK = 6 * 3600    # 单次窗口上限（停机恢复兜底）
INIT_LOOKBACK = 2 * 3600   # 首次启动回溯（预热 20 件只需 ~10 分钟，2 小时足够）

# 需要的字段。L_tool_number 只用于过滤，不入库。
N_NAMES = [
    "L_allnum",
    "L_main_program",
    "L_abso_x",
    "L_abso_z",        # Z 带过滤要用；实测 1701/1701 行全非空
    "L_spindle_load",
    "L_spindle_speed",
    "L_tool_number",
]

TS_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"   # 27 字符定长


def iso(dt):
    """UTC datetime -> 定长字符串（微秒精度）。

    必须定长：保留策略靠字符串比较 ts < cutoff，
    长度不一致（有无毫秒/有无 Z）会让字典序比较算错。
    用微秒而不是毫秒，是因为 influx 原始 _time 就是微秒
    （2026-10-21T03:23:24.222518Z），截断会把同一毫秒内两个采样点压成一行。
    """
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def parse_iso(s):
    """解析任意精度的 RFC3339 时间。

    ⚠ influx 的 _time 是 100ns 精度（7 位小数）：
        2026-10-21T04:22:32.0866076Z
      而 Python 的 %f 只接受 1~6 位，直接 strptime 会 ValueError。
      第一版就是栽在这里：拉取线程每轮都在这一步抛异常，
      水位线推不动、封口也因为异常跳过，结果 pieces 一直是 0。

    规整成 6 位再解析（截断到微秒）。采样间隔约 16ms，
    两个点落在同一微秒内的概率可以忽略。
    """
    s = s.strip()
    if s[-1:] in ("Z", "z"):
        s = s[:-1]
    if "." in s:
        head, frac = s.split(".", 1)
        s = head + "." + (frac + "000000")[:6]
    else:
        s = s + ".000000"
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc)


class InfluxError(RuntimeError):
    pass


class InfluxPuller(threading.Thread):
    """从 influx 按水位线窗口拉数据。

    on_rows(rows) -> int   由 server.py 提供：把一批 dict 落库，返回实际新写入行数
    on_seal()              一轮拉完让上层去做"件封口"（raw_rows -> pieces）
    w_init                起始水位线（UTC datetime），从 kv 或 DB 的 MAX(ts) 恢复
    """

    def __init__(self, on_rows, on_seal=None, w_init=None, log=print):
        super().__init__(daemon=True)
        self.on_rows = on_rows
        self.on_seal = on_seal
        self.log = log
        self.w = w_init
        self._stop = False
        self._running = True
        self.state = {
            "last_ok": None,
            "last_err": None,
            "last_rows": 0,
            "lag_sec": None,     # 数据落后时钟多少秒
            "window_sec": None,  # 本次窗口长度（能看到自适应效果）
            "total_rows": 0,
            "n_poll": 0,
            "n_fail": 0,
        }

    # ------------------------------------------------------------ HTTP
    def _post_flux(self, query, timeout=25):
        req = urllib.request.Request(
            "%s/api/v2/query?org=%s" % (INFLUX_URL, INFLUX_ORG),
            data=query.encode("utf-8"),
            headers={
                "Authorization": "Token " + INFLUX_TOKEN,
                "Content-Type": "application/vnd.flux",
                "Accept": "application/csv",
            },
            method="POST",
        )
        # 显式禁用代理：Windows 上 HTTP_PROXY 会把内网请求劫持到本地代理
        # （本项目实测过：首页 502 只有 94 字节，就是代理劫持）
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:400]
            raise InfluxError("HTTP %s: %s" % (e.code, body))
        except urllib.error.URLError as e:
            raise InfluxError("连接失败: %s" % e.reason)

    @staticmethod
    def _parse_annotated_csv(text):
        """influx annotated CSV：跳过 # 注释行，第一条非注释行是表头。"""
        header = None
        out = []
        for line in text.splitlines():
            if not line or line.startswith("#"):
                continue
            parts = next(csv.reader([line]))
            if header is None:
                header = parts
                continue
            out.append(dict(zip(header, parts)))
        return out

    # ------------------------------------------------------------ 查询
    def fetch(self, start, stop):
        pat = "|".join(n[2:] for n in N_NAMES)   # allnum|main_program|...
        q = (
            'from(bucket: "%s")\n'
            '  |> range(start: time(v: "%s"), stop: time(v: "%s"))\n'
            '  |> filter(fn: (r) => r._measurement == "FanucNormalData")\n'
            '  |> filter(fn: (r) => r.nName =~ /^L_(%s)$/)\n'
            '  |> pivot(rowKey: ["_time"], columnKey: ["nName"], valueColumn: "_value")\n'
            '  |> filter(fn: (r) => r.L_tool_number == "%s")\n'
        ) % (INFLUX_BUCKET, iso(start), iso(stop), pat, TOOL_NO)
        return self._parse_annotated_csv(self._post_flux(q))

    def to_rows(self, wide):
        """pivot 出来的宽表 -> raw_rows 要的 dict 列表。

        不做 x 范围过滤：raw_rows 是唯一可回溯的原始层，
        非切断段（x≈238 快进/换刀、x≈0 中心）也要留着做诊断，
        对齐时再由 align_piece 筛 19~40。
        """
        out = []
        for w in wide:
            t = w.get("_time")
            if not t:
                continue
            try:
                allnum = int(float(w["L_allnum"]))
                x = float(w["L_abso_x"])
                load = float(w["L_spindle_load"])
            except (KeyError, TypeError, ValueError):
                continue
            try:
                spd = float(w.get("L_spindle_speed") or 0.0)
            except (TypeError, ValueError):
                spd = 0.0
            # z 可以为空：这一行若没采到 z，就存 NULL，align_piece 会跳过 Z 带
            try:
                z = float(w["L_abso_z"])
            except (KeyError, TypeError, ValueError):
                z = None
            prog = 0
            m = re.match(r"^\s*(\d+)", str(w.get("L_main_program") or ""))
            if m:
                prog = int(m.group(1))
            try:
                ts = iso(parse_iso(t))
            except ValueError:
                # 解析不了的时间戳绝不能入库：ts 是定长字符串，
                # 混入 7 位/9 位小数会让保留策略和排序都算错
                continue
            out.append({
                "ts": ts,
                "allnum": allnum,
                "prog": prog,
                "x_pos": x,
                "z_pos": z,
                "spindle_load": load,
                "spindle_speed": spd,
                # 真实采集流里没有进给 F 值字段（只有 L_feed_override 倍率），
                # 这个程序固定 F100，按工艺值填，不参与计算
                "feedrate": 100.0,
            })
        # ⭐ recv_ts：**这一批被我们拿到的时刻**（R06 P0-a，2026-10-29）
        #   同一批共用一个值，所以 lag = recv_ts − ts 是**上界**
        #   （含服务端查询 + 网络传输 + 本地解析）。要拿到点值必须在采集盒侧打点。
        #   以前完全没有这个字段 → "数据到达延迟"只能"未知"，1 秒目标无法评估。
        recv = iso(datetime.now(timezone.utc))
        for r in out:
            r["recv_ts"] = recv
        out.sort(key=lambda r: r["ts"])
        return out

    # ------------------------------------------------------------ 一轮
    def step(self):
        now = datetime.now(timezone.utc)

        if self.w is not None and (now - self.w).total_seconds() > MAX_LOOKBACK:
            # 停机太久，前面那段本来就没数据，别每次白查
            self.w = now - timedelta(seconds=MAX_LOOKBACK)

        if self.w is None:
            start = now - timedelta(seconds=INIT_LOOKBACK)
        else:
            base = min(self.w, now - timedelta(seconds=MIN_STEP))
            start = base - timedelta(seconds=OVERLAP)
        if (now - start).total_seconds() > MAX_LOOKBACK:
            start = now - timedelta(seconds=MAX_LOOKBACK)

        self.state["n_poll"] += 1
        try:
            wide = self.fetch(start, now)
        except InfluxError as e:
            # 失败绝不动 W —— 动了就漏数据
            self.state["n_fail"] += 1
            self.state["last_err"] = str(e)
            return None

        rows = self.to_rows(wide)
        n_new = self.on_rows(rows) if rows else 0

        if rows:
            w_new = parse_iso(rows[-1]["ts"])
            if self.w is None or w_new > self.w:
                self.w = w_new
        elif self.w is None:
            self.w = start      # 首次什么都没拉到，别下次还回溯 2 小时

        self.state.update({
            "last_ok": iso(now),
            "last_err": None,
            "last_rows": n_new,
            "total_rows": self.state["total_rows"] + n_new,
            "lag_sec": None if self.w is None else round((now - self.w).total_seconds(), 1),
            "window_sec": round((now - start).total_seconds(), 1),
        })

        if self.on_seal:
            try:
                self.on_seal()
            except Exception as e:
                self.log("[封口异常] %s" % e)
        return n_new

    def run(self):
        while not self._stop:
            if self._running:
                t0 = time.time()
                try:
                    self.step()
                except Exception as e:
                    self.state["last_err"] = "%s: %s" % (type(e).__name__, e)
                    self.log("[拉取异常] %s" % e)
                # 固定节拍：把本轮耗时从间隔里扣掉，避免累积漂移
                time.sleep(max(1.0, POLL_SEC - (time.time() - t0)))
            else:
                time.sleep(0.2)

    def stop(self):
        self._stop = True
        self.join(timeout=6)

    def report(self):
        d = dict(self.state)
        d.update({"url": INFLUX_URL, "bucket": INFLUX_BUCKET,
                  "device": DEVICE, "tool": TOOL_NO,
                  "w": iso(self.w) if self.w else None})
        return d


# ---------------------------------------------------------------- 自检
if __name__ == "__main__":
    """不写库，只打几行看通不通：python influx_source.py"""
    def sink(rows):
        print("拉到 %d 行" % len(rows))
        if rows:
            print("首行:", json.dumps(rows[0], ensure_ascii=False))
            print("末行:", json.dumps(rows[-1], ensure_ascii=False))
        return len(rows)

    p = InfluxPuller(sink)
    p.step()
    print("状态:", json.dumps(p.report(), ensure_ascii=False, indent=2))
