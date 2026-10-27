-- 建议新增：预测日志表（P1-01，本轮仅设计，未落库）
CREATE TABLE IF NOT EXISTS predictions(
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_predict       TEXT NOT NULL,              -- 推理发生的真实时刻
    physical_life_id TEXT NOT NULL,              -- 物理刃口（不翻刃：1 周期=1 刃口）
    prog             INTEGER,
    tool_age_pieces  INTEGER NOT NULL,           -- 预测时已知刀龄
    tool_age_source  TEXT,                       -- event/cold/UNKNOWN
    data_watermark   TEXT,                       -- 输入数据水位线
    model_version    TEXT NOT NULL,
    feature_version  TEXT,
    rul_point        REAL,
    rul_lo           REAL,
    rul_hi           REAL,
    pi_level         REAL,
    calibrated       INTEGER DEFAULT 0,          -- 0=探索性区间，未校准
    quality_grade    TEXT,
    is_replay        INTEGER NOT NULL DEFAULT 0, -- 1=事后回放（不得冒充前瞻）
    actual_end_ts    TEXT,                       -- 后验
    actual_life      INTEGER,
    actual_label     TEXT,
    label_quality    TEXT);
CREATE INDEX IF NOT EXISTS i_pred_life ON predictions(physical_life_id, ts_predict);
