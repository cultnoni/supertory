-- User-uploaded ambient tracks (re-encoded MP3s, separate from bundled sounds).

CREATE TABLE IF NOT EXISTS user_ambient_tracks (
    id INTEGER PRIMARY KEY,
    original_filename TEXT NOT NULL,
    stored_filename TEXT NOT NULL UNIQUE,
    duration_seconds REAL NOT NULL DEFAULT 0,
    file_size_bytes INTEGER NOT NULL DEFAULT 0,
    category TEXT NOT NULL CHECK (category IN ('frequency', 'noise', 'nature', 'ambient')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS ix_user_ambient_tracks_category
    ON user_ambient_tracks(category, created_at, id);

INSERT INTO schema_migration(version, name) VALUES (59, 'user_ambient_tracks');
