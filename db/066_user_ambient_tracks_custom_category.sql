-- Custom uploads are their own ambient style, not mixed into built-in folders.

CREATE TABLE user_ambient_tracks__066 (
    id INTEGER PRIMARY KEY,
    original_filename TEXT NOT NULL,
    stored_filename TEXT NOT NULL UNIQUE,
    duration_seconds REAL NOT NULL DEFAULT 0,
    file_size_bytes INTEGER NOT NULL DEFAULT 0,
    category TEXT NOT NULL DEFAULT 'custom' CHECK (category IN ('frequency', 'noise', 'nature', 'ambient', 'custom')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT INTO user_ambient_tracks__066 (
    id, original_filename, stored_filename, duration_seconds, file_size_bytes, category, created_at
)
SELECT
    id,
    original_filename,
    stored_filename,
    duration_seconds,
    file_size_bytes,
    'custom',
    created_at
FROM user_ambient_tracks;

DROP TABLE user_ambient_tracks;
ALTER TABLE user_ambient_tracks__066 RENAME TO user_ambient_tracks;

CREATE INDEX IF NOT EXISTS ix_user_ambient_tracks_category
    ON user_ambient_tracks(category, created_at, id);

INSERT OR IGNORE INTO schema_migration(version, name) VALUES (66, 'user_ambient_tracks_custom_category');
