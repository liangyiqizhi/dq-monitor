PRAGMA journal_mode = WAL;

-- 批次表：每次采集一行，记录状态、行数、以及批次级汇总指标（给波动性规则做基线）
CREATE TABLE IF NOT EXISTS batches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'running',
    row_count   INTEGER NOT NULL DEFAULT 0,
    metrics     TEXT    NOT NULL DEFAULT '{}',
    error       TEXT,
    started_at  TEXT    NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_batches_source_time ON batches (source, started_at);

-- 明细表：只保留最新快照，主键 (source, biz_key) 保证重复采集幂等
CREATE TABLE IF NOT EXISTS records (
    source     TEXT NOT NULL,
    biz_key    TEXT NOT NULL,
    payload    TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (source, biz_key)
);

-- 规则结果表：看板的全部数据来源
CREATE TABLE IF NOT EXISTS rule_results (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id   INTEGER NOT NULL,
    source     TEXT    NOT NULL,
    rule_id    TEXT    NOT NULL,
    rule_type  TEXT    NOT NULL,
    status     TEXT    NOT NULL,
    observed   REAL,
    threshold  REAL,
    detail     TEXT,
    created_at TEXT    NOT NULL,
    FOREIGN KEY (batch_id) REFERENCES batches (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_results_rule ON rule_results (source, rule_id, created_at);
CREATE INDEX IF NOT EXISTS idx_results_batch ON rule_results (batch_id);

-- 告警去重状态：同一规则在抑制窗口内只推送一次
CREATE TABLE IF NOT EXISTS alert_state (
    alert_key    TEXT PRIMARY KEY,
    last_sent_at TEXT NOT NULL,
    hit_count    INTEGER NOT NULL DEFAULT 1
);
