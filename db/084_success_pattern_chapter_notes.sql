CREATE TABLE IF NOT EXISTS success_pattern_chapter_notes (
    id INTEGER PRIMARY KEY,
    profile_id INTEGER NOT NULL,
    note_order INTEGER NOT NULL,
    section_key TEXT NOT NULL,
    section_label TEXT NOT NULL DEFAULT '',
    episode_title TEXT NOT NULL DEFAULT '',
    episode_index INTEGER,
    char_count INTEGER NOT NULL DEFAULT 0,
    observation_json TEXT NOT NULL DEFAULT '{}',
    used_mock INTEGER NOT NULL DEFAULT 0 CHECK (used_mock IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (profile_id) REFERENCES success_pattern_profile(id) ON DELETE CASCADE,
    UNIQUE (profile_id, note_order)
);

CREATE INDEX IF NOT EXISTS idx_success_pattern_chapter_notes_profile
    ON success_pattern_chapter_notes(profile_id, note_order);

CREATE INDEX IF NOT EXISTS idx_success_pattern_chapter_notes_section
    ON success_pattern_chapter_notes(profile_id, section_key, episode_index);

INSERT INTO schema_migration(version, name)
VALUES (84, 'success_pattern_chapter_notes');
