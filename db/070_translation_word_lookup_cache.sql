CREATE TABLE IF NOT EXISTS translation_word_lookup_cache (
    segment_id INTEGER NOT NULL REFERENCES translation_segments(id) ON DELETE CASCADE,
    word TEXT NOT NULL,
    result_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY(segment_id, word)
);

CREATE INDEX IF NOT EXISTS ix_translation_word_lookup_cache_segment
    ON translation_word_lookup_cache(segment_id);

INSERT OR IGNORE INTO schema_migration(version, name)
VALUES (70, 'translation_word_lookup_cache');
