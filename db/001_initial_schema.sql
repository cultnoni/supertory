-- SuperTory initial SQLite schema.
-- Apply this file on a fresh database connection.  `foreign_keys` is
-- connection-local, so the application must also enable it on every connect.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS schema_migration (
    version      INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    applied_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE project (
    id                INTEGER PRIMARY KEY,
    title             TEXT NOT NULL CHECK (length(trim(title)) > 0),
    description_md    TEXT NOT NULL DEFAULT '',
    default_language  TEXT NOT NULL DEFAULT 'ko',
    goal_word_count   INTEGER NOT NULL DEFAULT 0 CHECK (goal_word_count >= 0),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at        TEXT,
    row_version       INTEGER NOT NULL DEFAULT 1 CHECK (row_version > 0),
    UNIQUE (id, title)
);

CREATE TABLE part (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL,
    title           TEXT NOT NULL CHECK (length(trim(title)) > 0),
    synopsis_md     TEXT NOT NULL DEFAULT '',
    sort_order      INTEGER NOT NULL CHECK (sort_order >= 0),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at      TEXT,
    row_version     INTEGER NOT NULL DEFAULT 1 CHECK (row_version > 0),
    UNIQUE (id, project_id),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX ux_part_active_order
    ON part(project_id, sort_order) WHERE deleted_at IS NULL;

CREATE TABLE chapter (
    id                INTEGER PRIMARY KEY,
    project_id        INTEGER NOT NULL,
    part_id           INTEGER,
    title             TEXT NOT NULL CHECK (length(trim(title)) > 0),
    synopsis_md       TEXT NOT NULL DEFAULT '',
    notes_md          TEXT NOT NULL DEFAULT '',
    goal_word_count   INTEGER NOT NULL DEFAULT 0 CHECK (goal_word_count >= 0),
    sort_order        INTEGER NOT NULL CHECK (sort_order >= 0),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at        TEXT,
    row_version       INTEGER NOT NULL DEFAULT 1 CHECK (row_version > 0),
    UNIQUE (id, project_id),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT,
    FOREIGN KEY (part_id, project_id) REFERENCES part(id, project_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX ux_chapter_active_order
    ON chapter(project_id, COALESCE(part_id, 0), sort_order)
    WHERE deleted_at IS NULL;
CREATE INDEX ix_chapter_project_part ON chapter(project_id, part_id, sort_order);

CREATE TABLE scene (
    id                INTEGER PRIMARY KEY,
    project_id        INTEGER NOT NULL,
    chapter_id        INTEGER NOT NULL,
    title             TEXT NOT NULL CHECK (length(trim(title)) > 0),
    synopsis_md       TEXT NOT NULL DEFAULT '',
    notes_md          TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'idea'
                      CHECK (status IN ('idea', 'outline', 'draft', 'revision', 'complete')),
    goal_word_count   INTEGER NOT NULL DEFAULT 0 CHECK (goal_word_count >= 0),
    sort_order        INTEGER NOT NULL CHECK (sort_order >= 0),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at        TEXT,
    row_version       INTEGER NOT NULL DEFAULT 1 CHECK (row_version > 0),
    UNIQUE (id, project_id),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT,
    FOREIGN KEY (chapter_id, project_id) REFERENCES chapter(id, project_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX ux_scene_active_order
    ON scene(chapter_id, sort_order) WHERE deleted_at IS NULL;
CREATE INDEX ix_scene_project_chapter ON scene(project_id, chapter_id, sort_order);
CREATE INDEX ix_scene_active_status ON scene(project_id, status, sort_order) WHERE deleted_at IS NULL;

CREATE TABLE scene_revision (
    id              INTEGER PRIMARY KEY,
    scene_id        INTEGER NOT NULL,
    revision_no     INTEGER NOT NULL CHECK (revision_no > 0),
    content_md      TEXT NOT NULL DEFAULT '',
    word_count      INTEGER NOT NULL DEFAULT 0 CHECK (word_count >= 0),
    save_note       TEXT NOT NULL DEFAULT '',
    is_checkpoint   INTEGER NOT NULL DEFAULT 0 CHECK (is_checkpoint IN (0, 1)),
    is_current      INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (id, scene_id),
    UNIQUE (scene_id, revision_no),
    FOREIGN KEY (scene_id) REFERENCES scene(id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX ux_scene_revision_current
    ON scene_revision(scene_id) WHERE is_current = 1;
CREATE INDEX ix_scene_revision_history ON scene_revision(scene_id, revision_no DESC);

CREATE VIEW v_current_scene_revision AS
SELECT
    s.id AS scene_id,
    s.project_id,
    s.chapter_id,
    s.title,
    s.synopsis_md,
    s.notes_md,
    s.status,
    s.goal_word_count,
    s.sort_order,
    s.created_at AS scene_created_at,
    s.updated_at AS scene_updated_at,
    s.deleted_at AS scene_deleted_at,
    r.id AS revision_id,
    r.revision_no,
    r.content_md,
    r.word_count,
    r.save_note,
    r.is_checkpoint,
    r.created_at AS revision_created_at
FROM scene AS s
JOIN scene_revision AS r ON r.scene_id = s.id AND r.is_current = 1;

CREATE TABLE tag (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER NOT NULL,
    name        TEXT NOT NULL COLLATE NOCASE CHECK (length(trim(name)) > 0),
    color       TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (id, project_id),
    UNIQUE (project_id, name),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT
);

CREATE TABLE character (
    id                INTEGER PRIMARY KEY,
    project_id        INTEGER NOT NULL,
    name              TEXT NOT NULL CHECK (length(trim(name)) > 0),
    sort_name         TEXT NOT NULL DEFAULT '',
    role              TEXT NOT NULL DEFAULT 'supporting'
                      CHECK (role IN ('protagonist', 'antagonist', 'supporting', 'minor')),
    short_description TEXT NOT NULL DEFAULT '',
    profile_md        TEXT NOT NULL DEFAULT '',
    author_notes_md   TEXT NOT NULL DEFAULT '',
    sort_order        INTEGER NOT NULL CHECK (sort_order >= 0),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at        TEXT,
    row_version       INTEGER NOT NULL DEFAULT 1 CHECK (row_version > 0),
    UNIQUE (id, project_id),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX ux_character_active_order
    ON character(project_id, sort_order) WHERE deleted_at IS NULL;
CREATE INDEX ix_character_project_name ON character(project_id, name) WHERE deleted_at IS NULL;

CREATE TABLE scene_tag (
    scene_id    INTEGER NOT NULL,
    tag_id      INTEGER NOT NULL,
    project_id  INTEGER NOT NULL,
    PRIMARY KEY (scene_id, tag_id),
    FOREIGN KEY (scene_id, project_id) REFERENCES scene(id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (tag_id, project_id) REFERENCES tag(id, project_id) ON DELETE RESTRICT
);
CREATE INDEX ix_scene_tag_by_tag ON scene_tag(project_id, tag_id, scene_id);

CREATE TABLE character_tag (
    character_id  INTEGER NOT NULL,
    tag_id        INTEGER NOT NULL,
    project_id    INTEGER NOT NULL,
    PRIMARY KEY (character_id, tag_id),
    FOREIGN KEY (character_id, project_id) REFERENCES character(id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (tag_id, project_id) REFERENCES tag(id, project_id) ON DELETE RESTRICT
);
CREATE INDEX ix_character_tag_by_tag ON character_tag(project_id, tag_id, character_id);

CREATE TABLE character_alias (
    id              INTEGER PRIMARY KEY,
    character_id    INTEGER NOT NULL,
    project_id      INTEGER NOT NULL,
    alias           TEXT NOT NULL COLLATE NOCASE CHECK (length(trim(alias)) > 0),
    alias_type      TEXT NOT NULL DEFAULT 'other',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (character_id, alias),
    FOREIGN KEY (character_id, project_id) REFERENCES character(id, project_id) ON DELETE RESTRICT
);
CREATE INDEX ix_character_alias_lookup ON character_alias(project_id, alias);

CREATE TABLE scene_character (
    scene_id        INTEGER NOT NULL,
    character_id    INTEGER NOT NULL,
    project_id      INTEGER NOT NULL,
    appearance_role TEXT NOT NULL DEFAULT 'supporting'
                    CHECK (appearance_role IN ('primary', 'supporting', 'cameo', 'mentioned')),
    is_pov          INTEGER NOT NULL DEFAULT 0 CHECK (is_pov IN (0, 1)),
    notes_md        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (scene_id, character_id),
    FOREIGN KEY (scene_id, project_id) REFERENCES scene(id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (character_id, project_id) REFERENCES character(id, project_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX ux_scene_character_one_pov
    ON scene_character(scene_id) WHERE is_pov = 1;
CREATE INDEX ix_scene_character_by_character ON scene_character(project_id, character_id, scene_id);

CREATE TABLE character_relationship (
    id                  INTEGER PRIMARY KEY,
    project_id          INTEGER NOT NULL,
    from_character_id   INTEGER NOT NULL,
    to_character_id     INTEGER NOT NULL,
    relationship_type   TEXT NOT NULL CHECK (length(trim(relationship_type)) > 0),
    strength            INTEGER NOT NULL DEFAULT 0 CHECK (strength BETWEEN -5 AND 5),
    description_md      TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at          TEXT,
    row_version         INTEGER NOT NULL DEFAULT 1 CHECK (row_version > 0),
    CHECK (from_character_id <> to_character_id),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT,
    FOREIGN KEY (from_character_id, project_id) REFERENCES character(id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (to_character_id, project_id) REFERENCES character(id, project_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX ux_character_relationship_active
    ON character_relationship(from_character_id, to_character_id, relationship_type)
    WHERE deleted_at IS NULL;
CREATE INDEX ix_character_relationship_from ON character_relationship(project_id, from_character_id)
    WHERE deleted_at IS NULL;
CREATE INDEX ix_character_relationship_to ON character_relationship(project_id, to_character_id)
    WHERE deleted_at IS NULL;

CREATE TABLE character_field_definition (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL,
    field_key       TEXT NOT NULL CHECK (field_key GLOB '[a-z][a-z0-9_]*'),
    label           TEXT NOT NULL CHECK (length(trim(label)) > 0),
    field_type      TEXT NOT NULL CHECK (field_type IN
                    ('text', 'markdown', 'integer', 'real', 'boolean', 'date', 'single_select', 'multi_select')),
    is_required     INTEGER NOT NULL DEFAULT 0 CHECK (is_required IN (0, 1)),
    sort_order      INTEGER NOT NULL CHECK (sort_order >= 0),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at      TEXT,
    row_version     INTEGER NOT NULL DEFAULT 1 CHECK (row_version > 0),
    UNIQUE (id, project_id),
    UNIQUE (project_id, field_key),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX ux_character_field_definition_active_order
    ON character_field_definition(project_id, sort_order) WHERE deleted_at IS NULL;

CREATE TABLE character_field_option (
    id                  INTEGER PRIMARY KEY,
    project_id          INTEGER NOT NULL,
    field_definition_id INTEGER NOT NULL,
    option_key          TEXT NOT NULL CHECK (option_key GLOB '[a-z][a-z0-9_]*'),
    label               TEXT NOT NULL CHECK (length(trim(label)) > 0),
    sort_order          INTEGER NOT NULL DEFAULT 0 CHECK (sort_order >= 0),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (id, field_definition_id),
    UNIQUE (field_definition_id, option_key),
    FOREIGN KEY (field_definition_id, project_id)
        REFERENCES character_field_definition(id, project_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX ux_character_field_option_order
    ON character_field_option(field_definition_id, sort_order);

CREATE TABLE character_field_value (
    character_id        INTEGER NOT NULL,
    field_definition_id INTEGER NOT NULL,
    project_id          INTEGER NOT NULL,
    text_value          TEXT,
    integer_value       INTEGER,
    real_value          REAL,
    boolean_value       INTEGER CHECK (boolean_value IN (0, 1)),
    date_value          TEXT,
    option_id           INTEGER,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (character_id, field_definition_id),
    CHECK (
        (text_value IS NOT NULL) + (integer_value IS NOT NULL) + (real_value IS NOT NULL) +
        (boolean_value IS NOT NULL) + (date_value IS NOT NULL) + (option_id IS NOT NULL) = 1
    ),
    FOREIGN KEY (character_id, project_id) REFERENCES character(id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (field_definition_id, project_id)
        REFERENCES character_field_definition(id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (option_id, field_definition_id)
        REFERENCES character_field_option(id, field_definition_id) ON DELETE RESTRICT
);
CREATE INDEX ix_character_field_value_definition
    ON character_field_value(project_id, field_definition_id, character_id);

CREATE TABLE character_field_multi_option (
    character_id        INTEGER NOT NULL,
    field_definition_id INTEGER NOT NULL,
    option_id           INTEGER NOT NULL,
    project_id          INTEGER NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (character_id, field_definition_id, option_id),
    FOREIGN KEY (character_id, project_id) REFERENCES character(id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (field_definition_id, project_id)
        REFERENCES character_field_definition(id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (option_id, field_definition_id)
        REFERENCES character_field_option(id, field_definition_id) ON DELETE RESTRICT
);
CREATE INDEX ix_character_field_multi_option_definition
    ON character_field_multi_option(project_id, field_definition_id, character_id);

CREATE VIEW v_character_required_fields_missing AS
SELECT c.id AS character_id, c.project_id, d.id AS field_definition_id, d.field_key
FROM character AS c
JOIN character_field_definition AS d
  ON d.project_id = c.project_id AND d.is_required = 1 AND d.deleted_at IS NULL
WHERE c.deleted_at IS NULL
  AND (
    (d.field_type = 'multi_select' AND NOT EXISTS (
        SELECT 1 FROM character_field_multi_option AS m
        WHERE m.character_id = c.id AND m.field_definition_id = d.id
    ))
    OR
    (d.field_type <> 'multi_select' AND NOT EXISTS (
        SELECT 1 FROM character_field_value AS v
        WHERE v.character_id = c.id AND v.field_definition_id = d.id
    ))
  );

-- Search source tables retain only active entities.  The FTS tables below use
-- them as external content, preserving referential integrity in ordinary tables.
CREATE TABLE scene_search_content (
    scene_id      INTEGER PRIMARY KEY,
    project_id    INTEGER NOT NULL,
    title         TEXT NOT NULL,
    synopsis_md   TEXT NOT NULL,
    content_md    TEXT NOT NULL,
    FOREIGN KEY (scene_id, project_id) REFERENCES scene(id, project_id) ON DELETE RESTRICT
);

CREATE VIRTUAL TABLE scene_fts USING fts5(
    title, synopsis_md, content_md,
    content = 'scene_search_content', content_rowid = 'scene_id',
    tokenize = 'unicode61'
);

CREATE TABLE character_search_content (
    character_id        INTEGER PRIMARY KEY,
    project_id          INTEGER NOT NULL,
    name                TEXT NOT NULL,
    aliases             TEXT NOT NULL,
    short_description   TEXT NOT NULL,
    profile_md          TEXT NOT NULL,
    author_notes_md     TEXT NOT NULL,
    FOREIGN KEY (character_id, project_id) REFERENCES character(id, project_id) ON DELETE RESTRICT
);

CREATE VIRTUAL TABLE character_fts USING fts5(
    name, aliases, short_description, profile_md, author_notes_md,
    content = 'character_search_content', content_rowid = 'character_id',
    tokenize = 'unicode61'
);

-- Keep external-content FTS indexes in sync with their ordinary source tables.
CREATE TRIGGER scene_search_content_ai AFTER INSERT ON scene_search_content BEGIN
    INSERT INTO scene_fts(rowid, title, synopsis_md, content_md)
    VALUES (NEW.scene_id, NEW.title, NEW.synopsis_md, NEW.content_md);
END;
CREATE TRIGGER scene_search_content_ad AFTER DELETE ON scene_search_content BEGIN
    INSERT INTO scene_fts(scene_fts, rowid, title, synopsis_md, content_md)
    VALUES ('delete', OLD.scene_id, OLD.title, OLD.synopsis_md, OLD.content_md);
END;
CREATE TRIGGER scene_search_content_au AFTER UPDATE ON scene_search_content BEGIN
    INSERT INTO scene_fts(scene_fts, rowid, title, synopsis_md, content_md)
    VALUES ('delete', OLD.scene_id, OLD.title, OLD.synopsis_md, OLD.content_md);
    INSERT INTO scene_fts(rowid, title, synopsis_md, content_md)
    VALUES (NEW.scene_id, NEW.title, NEW.synopsis_md, NEW.content_md);
END;

CREATE TRIGGER character_search_content_ai AFTER INSERT ON character_search_content BEGIN
    INSERT INTO character_fts(rowid, name, aliases, short_description, profile_md, author_notes_md)
    VALUES (NEW.character_id, NEW.name, NEW.aliases, NEW.short_description, NEW.profile_md, NEW.author_notes_md);
END;
CREATE TRIGGER character_search_content_ad AFTER DELETE ON character_search_content BEGIN
    INSERT INTO character_fts(character_fts, rowid, name, aliases, short_description, profile_md, author_notes_md)
    VALUES ('delete', OLD.character_id, OLD.name, OLD.aliases, OLD.short_description, OLD.profile_md, OLD.author_notes_md);
END;
CREATE TRIGGER character_search_content_au AFTER UPDATE ON character_search_content BEGIN
    INSERT INTO character_fts(character_fts, rowid, name, aliases, short_description, profile_md, author_notes_md)
    VALUES ('delete', OLD.character_id, OLD.name, OLD.aliases, OLD.short_description, OLD.profile_md, OLD.author_notes_md);
    INSERT INTO character_fts(rowid, name, aliases, short_description, profile_md, author_notes_md)
    VALUES (NEW.character_id, NEW.name, NEW.aliases, NEW.short_description, NEW.profile_md, NEW.author_notes_md);
END;

-- Revision bodies are append-only except for toggling which revision is current.
CREATE TRIGGER scene_revision_sequential_insert
BEFORE INSERT ON scene_revision
WHEN NEW.revision_no <> COALESCE((SELECT MAX(revision_no) + 1 FROM scene_revision WHERE scene_id = NEW.scene_id), 1)
BEGIN
    SELECT RAISE(ABORT, 'scene revision numbers must be consecutive');
END;
CREATE TRIGGER scene_revision_immutable_content
BEFORE UPDATE OF revision_no, content_md, word_count, save_note, is_checkpoint, created_at ON scene_revision
BEGIN
    SELECT RAISE(ABORT, 'scene revisions are immutable');
END;
CREATE TRIGGER scene_revision_no_delete
BEFORE DELETE ON scene_revision
BEGIN
    SELECT RAISE(ABORT, 'scene revisions are retained');
END;

-- Value rows must use the value column prescribed by their field definition.
CREATE TRIGGER character_field_value_validate_insert
BEFORE INSERT ON character_field_value
WHEN NOT EXISTS (
    SELECT 1 FROM character_field_definition AS d
    WHERE d.id = NEW.field_definition_id AND d.project_id = NEW.project_id
      AND (
        (d.field_type IN ('text', 'markdown') AND NEW.text_value IS NOT NULL
            AND NEW.integer_value IS NULL AND NEW.real_value IS NULL AND NEW.boolean_value IS NULL
            AND NEW.date_value IS NULL AND NEW.option_id IS NULL)
        OR (d.field_type = 'integer' AND NEW.integer_value IS NOT NULL
            AND NEW.text_value IS NULL AND NEW.real_value IS NULL AND NEW.boolean_value IS NULL
            AND NEW.date_value IS NULL AND NEW.option_id IS NULL)
        OR (d.field_type = 'real' AND NEW.real_value IS NOT NULL
            AND NEW.text_value IS NULL AND NEW.integer_value IS NULL AND NEW.boolean_value IS NULL
            AND NEW.date_value IS NULL AND NEW.option_id IS NULL)
        OR (d.field_type = 'boolean' AND NEW.boolean_value IN (0, 1)
            AND NEW.text_value IS NULL AND NEW.integer_value IS NULL AND NEW.real_value IS NULL
            AND NEW.date_value IS NULL AND NEW.option_id IS NULL)
        OR (d.field_type = 'date' AND NEW.date_value GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
            AND date(NEW.date_value) IS NOT NULL AND NEW.text_value IS NULL
            AND NEW.integer_value IS NULL AND NEW.real_value IS NULL AND NEW.boolean_value IS NULL
            AND NEW.option_id IS NULL)
        OR (d.field_type = 'single_select' AND NEW.option_id IS NOT NULL
            AND NEW.text_value IS NULL AND NEW.integer_value IS NULL AND NEW.real_value IS NULL
            AND NEW.boolean_value IS NULL AND NEW.date_value IS NULL)
      )
)
BEGIN
    SELECT RAISE(ABORT, 'value does not match character field type');
END;
CREATE TRIGGER character_field_value_validate_update
BEFORE UPDATE ON character_field_value
WHEN NOT EXISTS (
    SELECT 1 FROM character_field_definition AS d
    WHERE d.id = NEW.field_definition_id AND d.project_id = NEW.project_id
      AND (
        (d.field_type IN ('text', 'markdown') AND NEW.text_value IS NOT NULL AND NEW.integer_value IS NULL AND NEW.real_value IS NULL AND NEW.boolean_value IS NULL AND NEW.date_value IS NULL AND NEW.option_id IS NULL)
        OR (d.field_type = 'integer' AND NEW.integer_value IS NOT NULL AND NEW.text_value IS NULL AND NEW.real_value IS NULL AND NEW.boolean_value IS NULL AND NEW.date_value IS NULL AND NEW.option_id IS NULL)
        OR (d.field_type = 'real' AND NEW.real_value IS NOT NULL AND NEW.text_value IS NULL AND NEW.integer_value IS NULL AND NEW.boolean_value IS NULL AND NEW.date_value IS NULL AND NEW.option_id IS NULL)
        OR (d.field_type = 'boolean' AND NEW.boolean_value IN (0, 1) AND NEW.text_value IS NULL AND NEW.integer_value IS NULL AND NEW.real_value IS NULL AND NEW.date_value IS NULL AND NEW.option_id IS NULL)
        OR (d.field_type = 'date' AND NEW.date_value GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' AND date(NEW.date_value) IS NOT NULL AND NEW.text_value IS NULL AND NEW.integer_value IS NULL AND NEW.real_value IS NULL AND NEW.boolean_value IS NULL AND NEW.option_id IS NULL)
        OR (d.field_type = 'single_select' AND NEW.option_id IS NOT NULL AND NEW.text_value IS NULL AND NEW.integer_value IS NULL AND NEW.real_value IS NULL AND NEW.boolean_value IS NULL AND NEW.date_value IS NULL)
      )
)
BEGIN
    SELECT RAISE(ABORT, 'value does not match character field type');
END;
CREATE TRIGGER character_field_multi_option_validate_insert
BEFORE INSERT ON character_field_multi_option
WHEN COALESCE((SELECT field_type FROM character_field_definition
               WHERE id = NEW.field_definition_id AND project_id = NEW.project_id), '') <> 'multi_select'
BEGIN
    SELECT RAISE(ABORT, 'multiple options require a multi_select field');
END;
CREATE TRIGGER character_field_multi_option_validate_update
BEFORE UPDATE ON character_field_multi_option
WHEN COALESCE((SELECT field_type FROM character_field_definition
               WHERE id = NEW.field_definition_id AND project_id = NEW.project_id), '') <> 'multi_select'
BEGIN
    SELECT RAISE(ABORT, 'multiple options require a multi_select field');
END;
CREATE TRIGGER character_field_option_validate_insert
BEFORE INSERT ON character_field_option
WHEN COALESCE((SELECT field_type FROM character_field_definition
               WHERE id = NEW.field_definition_id AND project_id = NEW.project_id), '')
     NOT IN ('single_select', 'multi_select')
BEGIN
    SELECT RAISE(ABORT, 'options require a select field');
END;
CREATE TRIGGER character_field_option_validate_update
BEFORE UPDATE ON character_field_option
WHEN COALESCE((SELECT field_type FROM character_field_definition
               WHERE id = NEW.field_definition_id AND project_id = NEW.project_id), '')
     NOT IN ('single_select', 'multi_select')
BEGIN
    SELECT RAISE(ABORT, 'options require a select field');
END;
CREATE TRIGGER character_field_definition_type_change_guard
BEFORE UPDATE OF field_type ON character_field_definition
WHEN NEW.field_type <> OLD.field_type
 AND (
    EXISTS (SELECT 1 FROM character_field_value WHERE field_definition_id = OLD.id)
    OR EXISTS (SELECT 1 FROM character_field_multi_option WHERE field_definition_id = OLD.id)
    OR EXISTS (SELECT 1 FROM character_field_option WHERE field_definition_id = OLD.id)
 )
BEGIN
    SELECT RAISE(ABORT, 'cannot change a field type after values or options exist');
END;

-- Soft-delete propagation only flows downward. Restoring children is explicit
-- and is blocked while its parent remains deleted.
CREATE TRIGGER part_soft_delete_chapters
AFTER UPDATE OF deleted_at ON part
WHEN OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL
BEGIN
    UPDATE chapter SET deleted_at = NEW.deleted_at
    WHERE part_id = NEW.id AND deleted_at IS NULL;
END;
CREATE TRIGGER chapter_soft_delete_scenes
AFTER UPDATE OF deleted_at ON chapter
WHEN OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL
BEGIN
    UPDATE scene SET deleted_at = NEW.deleted_at
    WHERE chapter_id = NEW.id AND deleted_at IS NULL;
END;
CREATE TRIGGER chapter_restore_parent_check
BEFORE UPDATE OF deleted_at ON chapter
WHEN OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL AND NEW.part_id IS NOT NULL
     AND EXISTS (SELECT 1 FROM part WHERE id = NEW.part_id AND deleted_at IS NOT NULL)
BEGIN
    SELECT RAISE(ABORT, 'cannot restore a chapter into a deleted part');
END;
CREATE TRIGGER scene_restore_parent_check
BEFORE UPDATE OF deleted_at ON scene
WHEN OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL
     AND EXISTS (SELECT 1 FROM chapter WHERE id = NEW.chapter_id AND deleted_at IS NOT NULL)
BEGIN
    SELECT RAISE(ABORT, 'cannot restore a scene into a deleted chapter');
END;

-- Search source maintenance for scenes and their current revision.
CREATE TRIGGER scene_search_create
AFTER INSERT ON scene
WHEN NEW.deleted_at IS NULL
BEGIN
    INSERT INTO scene_search_content(scene_id, project_id, title, synopsis_md, content_md)
    VALUES (NEW.id, NEW.project_id, NEW.title, NEW.synopsis_md, '');
END;
CREATE TRIGGER scene_search_metadata_update
AFTER UPDATE OF title, synopsis_md ON scene
WHEN NEW.deleted_at IS NULL
BEGIN
    UPDATE scene_search_content SET title = NEW.title, synopsis_md = NEW.synopsis_md
    WHERE scene_id = NEW.id;
END;
CREATE TRIGGER scene_search_hide
AFTER UPDATE OF deleted_at ON scene
WHEN OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL
BEGIN
    DELETE FROM scene_search_content WHERE scene_id = NEW.id;
END;
CREATE TRIGGER scene_search_restore
AFTER UPDATE OF deleted_at ON scene
WHEN OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL
BEGIN
    INSERT INTO scene_search_content(scene_id, project_id, title, synopsis_md, content_md)
    VALUES (NEW.id, NEW.project_id, NEW.title, NEW.synopsis_md,
            COALESCE((SELECT content_md FROM scene_revision
                      WHERE scene_id = NEW.id AND is_current = 1), ''));
END;
CREATE TRIGGER scene_search_revision_insert
AFTER INSERT ON scene_revision
WHEN NEW.is_current = 1 AND EXISTS (SELECT 1 FROM scene WHERE id = NEW.scene_id AND deleted_at IS NULL)
BEGIN
    UPDATE scene_search_content SET content_md = NEW.content_md WHERE scene_id = NEW.scene_id;
END;
CREATE TRIGGER scene_search_revision_current
AFTER UPDATE OF is_current ON scene_revision
WHEN NEW.is_current = 1 AND OLD.is_current = 0
     AND EXISTS (SELECT 1 FROM scene WHERE id = NEW.scene_id AND deleted_at IS NULL)
BEGIN
    UPDATE scene_search_content SET content_md = NEW.content_md WHERE scene_id = NEW.scene_id;
END;

-- Search source maintenance for characters and aliases.
CREATE TRIGGER character_search_create
AFTER INSERT ON character
WHEN NEW.deleted_at IS NULL
BEGIN
    INSERT INTO character_search_content(character_id, project_id, name, aliases, short_description, profile_md, author_notes_md)
    VALUES (NEW.id, NEW.project_id, NEW.name, '', NEW.short_description, NEW.profile_md, NEW.author_notes_md);
END;
CREATE TRIGGER character_search_metadata_update
AFTER UPDATE OF name, short_description, profile_md, author_notes_md ON character
WHEN NEW.deleted_at IS NULL
BEGIN
    UPDATE character_search_content
    SET name = NEW.name, short_description = NEW.short_description, profile_md = NEW.profile_md,
        author_notes_md = NEW.author_notes_md
    WHERE character_id = NEW.id;
END;
CREATE TRIGGER character_search_hide
AFTER UPDATE OF deleted_at ON character
WHEN OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL
BEGIN
    DELETE FROM character_search_content WHERE character_id = NEW.id;
END;
CREATE TRIGGER character_search_restore
AFTER UPDATE OF deleted_at ON character
WHEN OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL
BEGIN
    INSERT INTO character_search_content(character_id, project_id, name, aliases, short_description, profile_md, author_notes_md)
    VALUES (NEW.id, NEW.project_id, NEW.name,
            COALESCE((SELECT group_concat(alias, ' ') FROM character_alias WHERE character_id = NEW.id), ''),
            NEW.short_description, NEW.profile_md, NEW.author_notes_md);
END;
CREATE TRIGGER character_alias_search_insert
AFTER INSERT ON character_alias
WHEN EXISTS (SELECT 1 FROM character WHERE id = NEW.character_id AND deleted_at IS NULL)
BEGIN
    UPDATE character_search_content
    SET aliases = COALESCE((SELECT group_concat(alias, ' ') FROM character_alias WHERE character_id = NEW.character_id), '')
    WHERE character_id = NEW.character_id;
END;
CREATE TRIGGER character_alias_search_update
AFTER UPDATE ON character_alias
BEGIN
    UPDATE character_search_content
    SET aliases = COALESCE((SELECT group_concat(alias, ' ') FROM character_alias WHERE character_id = OLD.character_id), '')
    WHERE character_id = OLD.character_id;
    UPDATE character_search_content
    SET aliases = COALESCE((SELECT group_concat(alias, ' ') FROM character_alias WHERE character_id = NEW.character_id), '')
    WHERE character_id = NEW.character_id;
END;
CREATE TRIGGER character_alias_search_delete
AFTER DELETE ON character_alias
BEGIN
    UPDATE character_search_content
    SET aliases = COALESCE((SELECT group_concat(alias, ' ') FROM character_alias WHERE character_id = OLD.character_id), '')
    WHERE character_id = OLD.character_id;
END;

-- Optimistic-concurrency metadata. Applications issue UPDATE ... WHERE
-- row_version = :expected_version; this trigger advances the stored version.
CREATE TRIGGER project_touch AFTER UPDATE ON project
WHEN NEW.updated_at = OLD.updated_at AND NEW.row_version = OLD.row_version
BEGIN UPDATE project SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), row_version = row_version + 1 WHERE id = NEW.id; END;
CREATE TRIGGER part_touch AFTER UPDATE ON part
WHEN NEW.updated_at = OLD.updated_at AND NEW.row_version = OLD.row_version
BEGIN UPDATE part SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), row_version = row_version + 1 WHERE id = NEW.id; END;
CREATE TRIGGER chapter_touch AFTER UPDATE ON chapter
WHEN NEW.updated_at = OLD.updated_at AND NEW.row_version = OLD.row_version
BEGIN UPDATE chapter SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), row_version = row_version + 1 WHERE id = NEW.id; END;
CREATE TRIGGER scene_touch AFTER UPDATE ON scene
WHEN NEW.updated_at = OLD.updated_at AND NEW.row_version = OLD.row_version
BEGIN UPDATE scene SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), row_version = row_version + 1 WHERE id = NEW.id; END;
CREATE TRIGGER character_touch AFTER UPDATE ON character
WHEN NEW.updated_at = OLD.updated_at AND NEW.row_version = OLD.row_version
BEGIN UPDATE character SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), row_version = row_version + 1 WHERE id = NEW.id; END;
CREATE TRIGGER character_relationship_touch AFTER UPDATE ON character_relationship
WHEN NEW.updated_at = OLD.updated_at AND NEW.row_version = OLD.row_version
BEGIN UPDATE character_relationship SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), row_version = row_version + 1 WHERE id = NEW.id; END;
CREATE TRIGGER character_field_definition_touch AFTER UPDATE ON character_field_definition
WHEN NEW.updated_at = OLD.updated_at AND NEW.row_version = OLD.row_version
BEGIN UPDATE character_field_definition SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), row_version = row_version + 1 WHERE id = NEW.id; END;

INSERT OR IGNORE INTO schema_migration(version, name) VALUES (1, 'initial_supertory_schema');
