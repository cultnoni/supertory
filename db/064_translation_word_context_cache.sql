-- Word-click context cache for submission translation.

CREATE TABLE IF NOT EXISTS translation_word_context_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    segment_id INTEGER NOT NULL,
    word TEXT NOT NULL,
    explanation TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (segment_id) REFERENCES translation_segments(id) ON DELETE CASCADE,
    UNIQUE(segment_id, word)
);

CREATE INDEX IF NOT EXISTS ix_translation_word_context_cache_segment
    ON translation_word_context_cache(segment_id);

INSERT INTO schema_migration(version, name) VALUES (64, 'translation_word_context_cache');
