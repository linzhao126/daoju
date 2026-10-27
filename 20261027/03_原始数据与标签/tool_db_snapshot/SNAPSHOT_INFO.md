# tool.db 只读快照

- 源文件：`2ndPrediction/tool_log/data/tool.db`（2.4 MB，mtime 2026-10-22 14:42）
- 拷贝方式：文件级复制，未修改任何行；查询用 `mode=ro` URI
- 表：pieces / history / tool_events / kv / raw_rows / sqlite_sequence
- ⚠ `history` 16 条全部 `src=seed`（离线 `simulator.LIFE_SEQ` 灌入），**不是在线登记**
- ⚠ `tool_events` 0 条 —— 从未有人点过「已换新刀」
- 脱敏：本库不含 deviceName/departNo 等机构字段，无需掩码；如贵方另有要求请提出
