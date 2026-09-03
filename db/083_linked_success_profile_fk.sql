-- Enforce project -> success-pattern profile integrity.
-- SQLite cannot add a table-level foreign key with ALTER TABLE, so rebuild
-- project while preserving its columns, index, trigger, and existing rows.

PRAGMA foreign_keys = OFF;
BEGIN IMMEDIATE;

UPDATE project
SET linked_success_profile_id = NULL
WHERE linked_success_profile_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM success_pattern_profile
      WHERE success_pattern_profile.id = project.linked_success_profile_id
  );

CREATE TABLE project_083 (
    id                          INTEGER PRIMARY KEY,
    title                       TEXT NOT NULL CHECK (length(trim(title)) > 0),
    description_md              TEXT NOT NULL DEFAULT '',
    default_language            TEXT NOT NULL DEFAULT 'ko',
    goal_word_count             INTEGER NOT NULL DEFAULT 0 CHECK (goal_word_count >= 0),
    created_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at                  TEXT,
    row_version                 INTEGER NOT NULL DEFAULT 1 CHECK (row_version > 0),
    purpose                     TEXT NOT NULL DEFAULT 'novel',
    uuid                        TEXT,
    package_path                TEXT,
    worldbuilding_md            TEXT NOT NULL DEFAULT '',
    logline_md                  TEXT NOT NULL DEFAULT '',
    main_genre                  TEXT NOT NULL DEFAULT '',
    sub_genre                   TEXT NOT NULL DEFAULT '',
    intro_md                    TEXT NOT NULL DEFAULT '',
    intent_md                   TEXT NOT NULL DEFAULT '',
    keywords                    TEXT NOT NULL DEFAULT '[]',
    last_opened_at              TEXT,
    list_sort_order             INTEGER NOT NULL DEFAULT 0,
    tory_priority_md            TEXT NOT NULL DEFAULT '',
    outline_summary             TEXT NOT NULL DEFAULT '',
    linked_success_profile_id   INTEGER,
    import_delimiter_config     TEXT,
    cluster_id                  TEXT NOT NULL DEFAULT '',
    genre_detail                TEXT NOT NULL DEFAULT '',
    content_rating              TEXT NOT NULL DEFAULT '',
    completion_guide_shown      INTEGER NOT NULL DEFAULT 0,
    UNIQUE (id, title),
    FOREIGN KEY (linked_success_profile_id)
        REFERENCES success_pattern_profile(id) ON DELETE SET NULL
);

INSERT INTO project_083 (
    id, title, description_md, default_language, goal_word_count,
    created_at, updated_at, deleted_at, row_version, purpose, uuid,
    package_path, worldbuilding_md, logline_md, main_genre, sub_genre,
    intro_md, intent_md, keywords, last_opened_at, list_sort_order,
    tory_priority_md, outline_summary, linked_success_profile_id,
    import_delimiter_config, cluster_id, genre_detail, content_rating,
    completion_guide_shown
)
SELECT
    id, title, description_md, default_language, goal_word_count,
    created_at, updated_at, deleted_at, row_version, purpose, uuid,
    package_path, worldbuilding_md, logline_md, main_genre, sub_genre,
    intro_md, intent_md, keywords, last_opened_at, list_sort_order,
    tory_priority_md, outline_summary, linked_success_profile_id,
    import_delimiter_config, cluster_id, genre_detail, content_rating,
    completion_guide_shown
FROM project;

DROP TABLE project;
ALTER TABLE project_083 RENAME TO project;

CREATE UNIQUE INDEX ux_project_uuid
    ON project(uuid) WHERE uuid IS NOT NULL AND deleted_at IS NULL;

CREATE TRIGGER project_touch AFTER UPDATE ON project
WHEN NEW.updated_at = OLD.updated_at AND NEW.row_version = OLD.row_version
BEGIN
    UPDATE project
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
        row_version = row_version + 1
    WHERE id = NEW.id;
END;

INSERT INTO schema_migration(version, name)
VALUES (83, 'linked_success_profile_fk');

COMMIT;
PRAGMA foreign_keys = ON;
