-- Writing activity log, prefs, and phone-inbox stubs for SuperTORY.

CREATE TABLE IF NOT EXISTS writing_day (
    day_key         TEXT PRIMARY KEY,
    chars_added     INTEGER NOT NULL DEFAULT 0 CHECK (chars_added >= 0),
    active_seconds  INTEGER NOT NULL DEFAULT 0 CHECK (active_seconds >= 0),
    session_count   INTEGER NOT NULL DEFAULT 0 CHECK (session_count >= 0),
    first_start_at  TEXT,
    last_active_at  TEXT,
    breakdown_json  TEXT NOT NULL DEFAULT '{}',
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS writing_prefs (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    goal_chars              INTEGER NOT NULL DEFAULT 2000 CHECK (goal_chars >= 100),
    goal_notify             INTEGER NOT NULL DEFAULT 1 CHECK (goal_notify IN (0, 1)),
    lonely_days             INTEGER NOT NULL DEFAULT 3 CHECK (lonely_days >= 1),
    lonely_notify           INTEGER NOT NULL DEFAULT 1 CHECK (lonely_notify IN (0, 1)),
    idle_minutes            INTEGER NOT NULL DEFAULT 30 CHECK (idle_minutes >= 5),
    last_goal_notified_day  TEXT NOT NULL DEFAULT '',
    last_lonely_notified_day TEXT NOT NULL DEFAULT '',
    updated_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT OR IGNORE INTO writing_prefs(id) VALUES (1);

CREATE TABLE IF NOT EXISTS mobile_device (
    id            INTEGER PRIMARY KEY,
    pair_code     TEXT NOT NULL UNIQUE,
    device_name   TEXT NOT NULL DEFAULT '',
    paired_at     TEXT,
    last_seen_at  TEXT,
    revoked_at    TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS mobile_inbox (
    id            INTEGER PRIMARY KEY,
    device_id     INTEGER,
    title         TEXT NOT NULL DEFAULT '',
    body_md       TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT 'phone',
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    read_at       TEXT,
    FOREIGN KEY (device_id) REFERENCES mobile_device(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_mobile_inbox_created ON mobile_inbox(created_at DESC);

INSERT INTO schema_migration(version, name) VALUES (13, 'writing_log');
