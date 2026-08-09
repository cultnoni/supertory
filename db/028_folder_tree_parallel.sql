-- Parallel binder schema (phase 1): empty folder tree + scene.folder_id (nullable).
-- Does not migrate part/chapter data; does not drop or alter existing part/chapter columns.
-- Backfill is a later step.

CREATE TABLE IF NOT EXISTS folder (
    id                INTEGER PRIMARY KEY,
    project_id        INTEGER NOT NULL,
    parent_id         INTEGER,
    title             TEXT NOT NULL CHECK (length(trim(title)) > 0),
    synopsis_md       TEXT NOT NULL DEFAULT '',
    notes_md          TEXT NOT NULL DEFAULT '',
    goal_word_count   INTEGER NOT NULL DEFAULT 0 CHECK (goal_word_count >= 0),
    is_box            INTEGER NOT NULL DEFAULT 0 CHECK (is_box IN (0, 1)),
    sort_order        INTEGER NOT NULL CHECK (sort_order >= 0),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at        TEXT,
    row_version       INTEGER NOT NULL DEFAULT 1 CHECK (row_version > 0),
    -- Backfill provenance (NULL for folders created natively later)
    source_kind       TEXT CHECK (source_kind IS NULL OR source_kind IN ('part', 'chapter')),
    source_id         INTEGER,
    UNIQUE (id, project_id),
    CHECK (parent_id IS NULL OR parent_id != id),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT,
    FOREIGN KEY (parent_id, project_id) REFERENCES folder(id, project_id) ON DELETE RESTRICT
);

-- Sibling order among active folders under the same parent (NULL parent = root).
CREATE UNIQUE INDEX IF NOT EXISTS ux_folder_sibling_order
    ON folder(project_id, COALESCE(parent_id, 0), sort_order)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_folder_parent
    ON folder(parent_id, sort_order)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_folder_project
    ON folder(project_id, sort_order)
    WHERE deleted_at IS NULL;

-- One map entry per legacy part/chapter row after backfill.
CREATE UNIQUE INDEX IF NOT EXISTS ux_folder_source
    ON folder(source_kind, source_id)
    WHERE source_kind IS NOT NULL AND source_id IS NOT NULL;

-- Parallel column only: existing scenes keep chapter_id; folder_id filled later.
ALTER TABLE scene ADD COLUMN folder_id INTEGER
    REFERENCES folder(id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS ix_scene_folder
    ON scene(folder_id, sort_order)
    WHERE deleted_at IS NULL AND folder_id IS NOT NULL;

INSERT INTO schema_migration(version, name) VALUES (28, 'folder_tree_parallel');
