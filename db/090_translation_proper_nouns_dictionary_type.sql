-- Allow translation_proper_nouns.term_type = 'dictionary' for Tory dictionary seeds.
-- SQLite cannot ALTER a CHECK constraint, so the table is rebuilt.

PRAGMA foreign_keys = OFF;

CREATE TABLE translation_proper_nouns_090 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    translation_job_id INTEGER NOT NULL,
    source_term TEXT NOT NULL,
    term_type TEXT
        CHECK (term_type IS NULL
               OR term_type IN ('character', 'place', 'item', 'organization', 'dictionary')),
    fit_judgment TEXT
        CHECK (fit_judgment IS NULL
               OR fit_judgment IN ('fits', 'does_not_fit')),
    judgment_reason TEXT,
    suggested_alternatives_json TEXT,
    user_decision TEXT
        CHECK (user_decision IS NULL
               OR user_decision IN ('keep_romanized', 'rename', 'keep_as_is')),
    final_term TEXT,
    source TEXT NOT NULL DEFAULT 'ai_detected',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (translation_job_id) REFERENCES translation_jobs(id) ON DELETE CASCADE
);

INSERT INTO translation_proper_nouns_090 (
    id, translation_job_id, source_term, term_type, fit_judgment, judgment_reason,
    suggested_alternatives_json, user_decision, final_term, source, created_at
)
SELECT
    id,
    translation_job_id,
    source_term,
    term_type,
    fit_judgment,
    judgment_reason,
    suggested_alternatives_json,
    user_decision,
    final_term,
    COALESCE(NULLIF(source, ''), 'ai_detected'),
    created_at
FROM translation_proper_nouns;

DROP TABLE translation_proper_nouns;
ALTER TABLE translation_proper_nouns_090 RENAME TO translation_proper_nouns;

CREATE INDEX IF NOT EXISTS ix_translation_proper_nouns_job
    ON translation_proper_nouns(translation_job_id);

PRAGMA foreign_keys = ON;

INSERT INTO schema_migration(version, name)
VALUES (90, 'translation_proper_nouns_dictionary_type');
