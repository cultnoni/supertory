-- Items (props / artifacts) with aliases, pending Tory notes, and trait history.

CREATE TABLE IF NOT EXISTS item (
    id                  INTEGER PRIMARY KEY,
    project_id          INTEGER NOT NULL,
    name                TEXT NOT NULL CHECK (length(trim(name)) > 0),
    description         TEXT NOT NULL DEFAULT '',
    owner_character_id INTEGER,
    sort_order          INTEGER NOT NULL CHECK (sort_order >= 0),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at          TEXT,
    row_version         INTEGER NOT NULL DEFAULT 1 CHECK (row_version > 0),
    UNIQUE (id, project_id),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT,
    FOREIGN KEY (owner_character_id) REFERENCES character(id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_item_active_order
    ON item(project_id, sort_order) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_item_project_name
    ON item(project_id, name) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_item_owner
    ON item(owner_character_id);

CREATE TABLE IF NOT EXISTS item_alias (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL,
    project_id  INTEGER NOT NULL,
    alias       TEXT NOT NULL COLLATE NOCASE CHECK (length(trim(alias)) > 0),
    alias_type  TEXT NOT NULL DEFAULT 'other',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (item_id, alias),
    FOREIGN KEY (item_id, project_id) REFERENCES item(id, project_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS ix_item_alias_lookup ON item_alias(project_id, alias);

CREATE TABLE IF NOT EXISTS item_tori_analysis (
    id                INTEGER PRIMARY KEY,
    item_id           INTEGER NOT NULL,
    field_name        TEXT NOT NULL CHECK (length(trim(field_name)) > 0),
    analyzed_content  TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (item_id, field_name),
    FOREIGN KEY (item_id) REFERENCES item(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_item_tori_analysis_item
    ON item_tori_analysis(item_id);

CREATE TABLE IF NOT EXISTS item_trait_history (
    id                INTEGER PRIMARY KEY,
    item_id           INTEGER NOT NULL,
    project_id        INTEGER NOT NULL,
    scene_id          INTEGER NOT NULL,
    field_name        TEXT NOT NULL CHECK (length(trim(field_name)) > 0),
    detected_content  TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (item_id) REFERENCES item(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT,
    FOREIGN KEY (scene_id) REFERENCES scene(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_item_trait_history_item
    ON item_trait_history(item_id, created_at, id);
CREATE INDEX IF NOT EXISTS ix_item_trait_history_scene
    ON item_trait_history(scene_id, id);

INSERT INTO schema_migration(version, name) VALUES (73, 'item');
