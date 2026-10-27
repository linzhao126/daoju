-- tool.db 实际结构（由 s09 从 sqlite_master 导出，原样）
-- 注意：raw_rows 的 UNIQUE(ts) 是 2026-10-21 重建迁移加的；
--       老库若在迁移前，raw_rows 无该约束
CREATE TABLE history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        prog INTEGER, life REAL, src TEXT, valid INTEGER DEFAULT 1);

CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT);

CREATE TABLE pieces(
        allnum INTEGER, prog INTEGER,
        load_med REAL, n_points INTEGER, ts TEXT, curve TEXT,
        PRIMARY KEY(allnum));

CREATE TABLE raw_rows(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL UNIQUE,
    allnum INTEGER, prog INTEGER,
    x_pos REAL, spindle_load REAL, spindle_speed REAL, feedrate REAL, z_pos REAL);

CREATE TABLE sqlite_sequence(name,seq);

CREATE TABLE tool_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, event TEXT, prog INTEGER, allnum INTEGER,
        pieces INTEGER, state TEXT, reason TEXT, limit_at INTEGER);

CREATE INDEX i_pieces_ts ON pieces(ts);

CREATE INDEX i_raw_allnum ON raw_rows(allnum);

CREATE INDEX i_raw_ts ON raw_rows(ts);

