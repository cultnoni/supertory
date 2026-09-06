-- Per-project settings for the live Tory Check dock widget
-- (preset, viewpoint person/tense, forbidden-word list).

CREATE TABLE IF NOT EXISTS project_tory_check (
    project_id INTEGER PRIMARY KEY,
    preset TEXT NOT NULL DEFAULT 'normal'
        CHECK (preset IN ('strict', 'normal', 'loose')),
    viewpoint_person TEXT
        CHECK (viewpoint_person IS NULL OR viewpoint_person IN ('first', 'third')),
    viewpoint_tense TEXT
        CHECK (viewpoint_tense IS NULL OR viewpoint_tense IN ('past', 'present')),
    forbidden_words_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT
);

INSERT INTO schema_migration(version, name)
VALUES (86, 'project_tory_check');
