-- Translation pipeline orchestration: extra job fields and expanded status values.
-- status: draft | awaiting_review | in_progress | translated | completed

PRAGMA foreign_keys = OFF;

CREATE TABLE translation_jobs_063 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    local_project_id INTEGER NOT NULL,
    target_language TEXT NOT NULL,
    cliffhanger_chapter INTEGER,
    style_guide_json TEXT,
    culture_localization_level TEXT
        CHECK (culture_localization_level IS NULL
               OR culture_localization_level IN ('tight', 'moderate', 'as_is')),
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN (
            'draft',
            'awaiting_review',
            'in_progress',
            'translated',
            'completed'
        )),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    narrative_formatting_rules_json TEXT,
    pipeline_failed_step TEXT,
    pipeline_error TEXT,
    proper_nouns_confirmed INTEGER NOT NULL DEFAULT 0,
    proper_nouns_extracted INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (local_project_id) REFERENCES project(id) ON DELETE CASCADE
);

INSERT INTO translation_jobs_063 (
    id, local_project_id, target_language, cliffhanger_chapter, style_guide_json,
    culture_localization_level, status, created_at, updated_at
)
SELECT
    id, local_project_id, target_language, cliffhanger_chapter, style_guide_json,
    culture_localization_level, status, created_at, updated_at
FROM translation_jobs;

DROP TABLE translation_jobs;
ALTER TABLE translation_jobs_063 RENAME TO translation_jobs;

CREATE INDEX IF NOT EXISTS ix_translation_jobs_project
    ON translation_jobs(local_project_id);

PRAGMA foreign_keys = ON;

INSERT INTO schema_migration(version, name) VALUES (63, 'translation_pipeline_orchestration');
