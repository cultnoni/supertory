-- Remember proper nouns the user deleted so refresh/extract cannot revive them.

CREATE TABLE IF NOT EXISTS translation_proper_noun_suppressions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    translation_job_id INTEGER NOT NULL,
    source_term_key TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(translation_job_id, source_term_key),
    FOREIGN KEY (translation_job_id) REFERENCES translation_jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_translation_proper_noun_suppressions_job
    ON translation_proper_noun_suppressions(translation_job_id);

INSERT INTO schema_migration(version, name)
VALUES (78, 'translation_proper_noun_suppressions');
