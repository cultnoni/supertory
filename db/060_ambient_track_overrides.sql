-- Per-track title overrides and toggle-popup visibility (bundled + custom).

CREATE TABLE IF NOT EXISTS ambient_track_overrides (
    track_id TEXT PRIMARY KEY,
    custom_title TEXT,
    enabled_in_popup INTEGER NOT NULL DEFAULT 1 CHECK (enabled_in_popup IN (0, 1)),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT INTO schema_migration(version, name) VALUES (60, 'ambient_track_overrides');
