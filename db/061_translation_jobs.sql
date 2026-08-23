-- Submission-oriented multilingual translation jobs (투고용 다국어 번역하기).

CREATE TABLE IF NOT EXISTS translation_jobs (
    id                           INTEGER PRIMARY KEY AUTOINCREMENT,
    local_project_id             INTEGER NOT NULL,
    target_language              TEXT NOT NULL,
    cliffhanger_chapter          INTEGER,
    style_guide_json             TEXT,
    culture_localization_level   TEXT
                                 CHECK (culture_localization_level IS NULL
                                        OR culture_localization_level IN ('tight', 'moderate', 'as_is')),
    status                       TEXT NOT NULL DEFAULT 'draft'
                                 CHECK (status IN ('draft', 'in_progress', 'completed')),
    created_at                   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at                   TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (local_project_id) REFERENCES project(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_translation_jobs_project
    ON translation_jobs(local_project_id);

CREATE TABLE IF NOT EXISTS translation_scene_contexts (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    translation_job_id   INTEGER NOT NULL,
    chapter_number       INTEGER NOT NULL,
    scene_order          INTEGER NOT NULL,
    relationship_tag     TEXT,
    mood_tag             TEXT,
    situation_note       TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (translation_job_id) REFERENCES translation_jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_translation_scene_contexts_job
    ON translation_scene_contexts(translation_job_id);

CREATE TABLE IF NOT EXISTS translation_proper_nouns (
    id                           INTEGER PRIMARY KEY AUTOINCREMENT,
    translation_job_id           INTEGER NOT NULL,
    source_term                  TEXT NOT NULL,
    term_type                    TEXT
                                 CHECK (term_type IS NULL
                                        OR term_type IN ('character', 'place', 'item', 'organization')),
    fit_judgment                 TEXT
                                 CHECK (fit_judgment IS NULL
                                        OR fit_judgment IN ('fits', 'does_not_fit')),
    judgment_reason              TEXT,
    suggested_alternatives_json  TEXT,
    user_decision                TEXT
                                 CHECK (user_decision IS NULL
                                        OR user_decision IN ('keep_romanized', 'rename', 'keep_as_is')),
    final_term                   TEXT,
    created_at                   TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (translation_job_id) REFERENCES translation_jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_translation_proper_nouns_job
    ON translation_proper_nouns(translation_job_id);

CREATE TABLE IF NOT EXISTS translation_segments (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    translation_job_id       INTEGER NOT NULL,
    scene_context_id         INTEGER,
    chapter_number           INTEGER NOT NULL,
    segment_order            INTEGER NOT NULL,
    source_text              TEXT NOT NULL,
    translated_text          TEXT,
    translation_notes_json   TEXT,
    polish_text              TEXT,
    is_approved              INTEGER NOT NULL DEFAULT 0 CHECK (is_approved IN (0, 1)),
    created_at               TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at               TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (translation_job_id) REFERENCES translation_jobs(id) ON DELETE CASCADE,
    FOREIGN KEY (scene_context_id) REFERENCES translation_scene_contexts(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_translation_segments_job
    ON translation_segments(translation_job_id);
CREATE INDEX IF NOT EXISTS ix_translation_segments_scene_context
    ON translation_segments(scene_context_id);

CREATE TABLE IF NOT EXISTS translation_chat_messages (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    translation_job_id   INTEGER NOT NULL,
    segment_id           INTEGER,
    dragged_text         TEXT,
    role                 TEXT NOT NULL CHECK (role IN ('user', 'tori')),
    message              TEXT NOT NULL,
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (translation_job_id) REFERENCES translation_jobs(id) ON DELETE CASCADE,
    FOREIGN KEY (segment_id) REFERENCES translation_segments(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_translation_chat_messages_job
    ON translation_chat_messages(translation_job_id);
CREATE INDEX IF NOT EXISTS ix_translation_chat_messages_segment
    ON translation_chat_messages(segment_id);

CREATE TABLE IF NOT EXISTS translation_submission_package (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    translation_job_id       INTEGER NOT NULL,
    synopsis_translated      TEXT,
    logline_translated       TEXT,
    sample_chapters_range    TEXT,
    generated_at             TEXT,
    FOREIGN KEY (translation_job_id) REFERENCES translation_jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_translation_submission_package_job
    ON translation_submission_package(translation_job_id);

INSERT INTO schema_migration(version, name) VALUES (61, 'translation_jobs');
