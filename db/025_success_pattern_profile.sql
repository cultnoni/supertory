-- Independent success-formula analysis profiles (not tied to a project).
CREATE TABLE IF NOT EXISTS success_pattern_profile (
    id INTEGER PRIMARY KEY,
    work_title TEXT NOT NULL,
    total_chapters INTEGER,
    analyzed_sections_json TEXT NOT NULL DEFAULT '[]',
    profile_json TEXT NOT NULL DEFAULT '{}',
    quantitative_json TEXT NOT NULL DEFAULT '{}',
    built_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT INTO schema_migration(version, name) VALUES (25, 'success_pattern_profile');
