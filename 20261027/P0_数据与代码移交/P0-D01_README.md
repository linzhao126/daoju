# P0-D01　数据清单表（4152 全量 / 旧批 / labeled / xalign / tool.db / 台账）

> 对应《TOOL-RUL-CHIP-0-REQ-20261029》任务书 §3-P0-D01 与 §6 第一批 D1。
> 本目录交付**清单本身**（文件名 / 体积 / 时间覆盖 / 行数 / SHA256 / 位置），**不含大文件本体**。大文件按任务书 §3-D01 第 1 条走授权网盘 / 离线硬盘 / 内网共享，⛔ 不上公开 GitHub。
> 生成脚本：`09_repro_scripts/s67_build_data_manifest.py`（工作区本地路径 + 交付包路径统一枚举，UTF-8-sig）。

## 表结构
`P0-D01_数据清单表.csv` 列：
| 列 | 含义 |
|---|---|
| 角色 | RAW-全量 / RAW-旧批 / LABELED-衍生 / MERGED-中间件 / ALIGN产物 / 原始抽样-交付 / DB-快照 / 台账原始 |
| 位置 | 工作区本地（未入 GitHub）／交付包内（`daoju_repo/20261027/…`，可克隆获取） |
| 文件 / 路径 | 文件名与工作区相对路径 |
| 存在 | 是 / 否（否＝本机不存在，通常已被替换或清理） |
| 字节 / MB | 文件体积 |
| SHA256 | 全文件哈希（>200 MB 标 `(未计算，>200MB)`） |
| 行数 | 逐行 `\n` 计数（非行式文件标 `(非行式)`；`.gz` 标待解 gz） |
| 首行_time / 末行_time | 从首行数据与末尾 256 KB 提取的时间戳，用作时间覆盖的近似边界 |
| 备注 | 缺失原因或下一步动作 |

## 当前统计（2026-10-30）
- 枚举 34 个文件；本机全部命中（`存在=否` 计数 0）。
- **已实算 SHA + 行数**：24 个 ≤ 200 MB 的文件。
- **10 个 > 200 MB 的大文件**仅登记体积，SHA/行数/时间覆盖按需生成。

## 大文件按需生成命令
本地 PowerShell（Windows 自带 `Get-FileHash`）：
```powershell
Get-FileHash -Algorithm SHA256 "c:\abc\workBoddyHome刀具\influxdb_4152_FanucNormalData_20261027_200034.csv"
```
或 Python（同时出行数）：
```bash
python -X utf8 -c "import hashlib,os,sys;p=sys.argv[1];h=hashlib.sha256();n=0;f=open(p,'rb');
while b:=f.read(1<<20): h.update(b); n+=b.count(b'\n')
print(p, os.path.getsize(p), n, h.hexdigest())" "influxdb_4152_FanucNormalData_20261027_200034.csv"
```
> 3.3 GB 单文件哈希在机械盘约 3–5 分钟，SSD 约 40 秒。

## 大文件（10 个 > 200 MB）清单
| MB | 文件 | 建议移交方式 |
|---:|---|---|
| 3173.9 | influxdb_4152_FanucNormalData_20261027_200034.csv（**任务书点名的 3.3 GB 全量**） | 内网共享 / 离线硬盘 / 授权网盘，⛔ 不上 GitHub |
| 1078.1 | influxdb_FanucNormalData_20260914_081959.csv | 同上 |
| 656.2 | influxdb_FanucNormalData_20260910_085752_4152.csv | 同上 |
| 435.6 | influxdb_FanucNormalData_20260924_170215.csv | 同上 |
| 408.7 | merged_csv_20260901_160345.csv | 中间件，可只交 SHA + 生成脚本 |
| 403.3 | merged_csv_20260908_131914.csv | 同上 |
| 360.2 | influxdb_FanucNormalData_20260831_104331.csv | 原始，同上 |
| 289.1 | influxdb_FanucNormalData_20260901_091924.csv | 原始，同上 |
| 265.9 | influxdb_FanucNormalData_20260829_144941_T1111_life_labeled.csv | labeled 衍生，同上 |
| 234.5 | influxdb_FanucNormalData_20260829_144941.csv | 原始，同上 |

## 已知数据链衍生关系（供 §3-D01 第 2 条追溯）
- **原始 → 手工标注**：`merged_20260810_113424-notoolLift.csv` →（手工填寿命）→ `merged_csv_20260816_123219手动标记刀具寿命.csv` → `merged_csv_20260901_160345.csv` → `merged_csv_20260901_160345_T2525.csv` → `merged_csv_20260901_160345_T2525_life_labeled.csv` → `..._rollingfeat.csv`
- **Influx 全量 → 单台单刀 labeled**：`influxdb_4152_FanucNormalData_20261027_200034.csv` →（`split_by_tool.py` / `label_tool_life.py`）→ `influxdb_4152_..._life_labeled.csv` →（`t1111_pipeline.py`）→ `22_c48-c60_xalign/labeled_T1111_new.csv` + `xalign_HI_new_c49c60.csv`
- **数据库快照（只读副本）**：`03_原始数据与标签/tool_db_snapshot/tool.db.readonly_copy` 与已 CSV 化的 `raw_rows.csv` / `pieces.csv` / `tool_events.csv` / `history.csv` / `kv.csv`；WAL/SHM 附同目录，⚠ 若首次拷贝时 WAL 未 checkpoint，快照可能不完整（详见 `SNAPSHOT_INFO.md`）

## 尚缺证据（本任务书 §3-D01 明确要求的项）
1. **每通道实际采样间隔分布**：需从原始 `_time` 列做统计（AI 可脚本化，等待确认要否单独出表）。
2. **丢失 / 重复 / 乱序 / 跨班停机段清单**：同上。
3. **采集盒 → Influx → 应用三端 `_time / recv_ts` 时间戳定义**：见 `N05_时间戳链路定义/N05-00_时间戳与延迟口径.md`（N05 交付）。
4. **`tool.db` WAL 一致备份**：本次快照的 `-wal` 文件为 0 字节，说明 WAL 已 checkpoint，主库完整；若后续再拷，需走 `VACUUM INTO` 或 `sqlite3 .backup`。

## 复算本清单
```bash
cd daoju_repo/20261027
PY=python bash run_all.sh --only s67      # 或直接：
python -X utf8 09_repro_scripts/s67_build_data_manifest.py
```
脚本只读、不移动文件；输出固定到本目录。
