"""Migration 71: character.role may be NULL (미지정). Keep existing values.

The column already existed (protagonist/antagonist/supporting/minor, NOT NULL,
default supporting). SQLite cannot drop NOT NULL without a table rebuild.
Existing rows are copied as-is; new characters default to NULL.
"""

from __future__ import annotations

import sqlite3

MIGRATION_VERSION = 71
MIGRATION_NAME = "character_role_nullable"

ALLOWED_ROLES = ("protagonist", "antagonist", "supporting", "minor")

_CREATE_SQL = """
CREATE TABLE character (
    id                INTEGER PRIMARY KEY,
    project_id        INTEGER NOT NULL,
    name              TEXT NOT NULL CHECK (length(trim(name)) > 0),
    sort_name         TEXT NOT NULL DEFAULT '',
    role              TEXT DEFAULT NULL
                      CHECK (role IS NULL OR role IN ('protagonist', 'antagonist', 'supporting', 'minor')),
    short_description TEXT NOT NULL DEFAULT '',
    profile_md        TEXT NOT NULL DEFAULT '',
    author_notes_md  TEXT NOT NULL DEFAULT '',
    sort_order        INTEGER NOT NULL CHECK (sort_order >= 0),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at        TEXT,
    row_version       INTEGER NOT NULL DEFAULT 1 CHECK (row_version > 0),
    strengths_md      TEXT NOT NULL DEFAULT '',
    weaknesses_md     TEXT NOT NULL DEFAULT '',
    portrait_file     TEXT NOT NULL DEFAULT '',
    portrait_mime     TEXT NOT NULL DEFAULT '',
    UNIQUE (id, project_id),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE RESTRICT
)
"""

_INDEX_SQL = [
    "CREATE UNIQUE INDEX ux_character_active_order ON character(project_id, sort_order) WHERE deleted_at IS NULL",
    "CREATE INDEX ix_character_project_name ON character(project_id, name) WHERE deleted_at IS NULL",
]

_TRIGGER_SQL = [
    """
    CREATE TRIGGER character_search_create
    AFTER INSERT ON character
    WHEN NEW.deleted_at IS NULL
    BEGIN
        INSERT INTO character_search_content(character_id, project_id, name, aliases, short_description, profile_md, author_notes_md)
        VALUES (NEW.id, NEW.project_id, NEW.name, '', NEW.short_description, NEW.profile_md, NEW.author_notes_md);
    END
    """,
    """
    CREATE TRIGGER character_search_metadata_update
    AFTER UPDATE OF name, short_description, profile_md, author_notes_md ON character
    WHEN NEW.deleted_at IS NULL
    BEGIN
        UPDATE character_search_content
        SET name = NEW.name, short_description = NEW.short_description, profile_md = NEW.profile_md,
            author_notes_md = NEW.author_notes_md
        WHERE character_id = NEW.id;
    END
    """,
    """
    CREATE TRIGGER character_search_hide
    AFTER UPDATE OF deleted_at ON character
    WHEN OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL
    BEGIN
        DELETE FROM character_search_content WHERE character_id = NEW.id;
    END
    """,
    """
    CREATE TRIGGER character_search_restore
    AFTER UPDATE OF deleted_at ON character
    WHEN OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL
    BEGIN
        INSERT INTO character_search_content(character_id, project_id, name, aliases, short_description, profile_md, author_notes_md)
        VALUES (NEW.id, NEW.project_id, NEW.name,
                COALESCE((SELECT group_concat(alias, ' ') FROM character_alias WHERE character_id = NEW.id), ''),
                NEW.short_description, NEW.profile_md, NEW.author_notes_md);
    END
    """,
    """
    CREATE TRIGGER character_touch AFTER UPDATE ON character
    WHEN NEW.updated_at = OLD.updated_at AND NEW.row_version = OLD.row_version
    BEGIN UPDATE character SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), row_version = row_version + 1 WHERE id = NEW.id; END
    """,
]

_DROP_TRIGGERS = (
    "character_search_create",
    "character_search_metadata_update",
    "character_search_hide",
    "character_search_restore",
    "character_touch",
    "character_alias_search_insert",
    "character_alias_search_update",
    "character_alias_search_delete",
)

_ALIAS_TRIGGER_SQL = [
    """
    CREATE TRIGGER character_alias_search_insert
    AFTER INSERT ON character_alias
    WHEN EXISTS (SELECT 1 FROM character WHERE id = NEW.character_id AND deleted_at IS NULL)
    BEGIN
        UPDATE character_search_content
        SET aliases = COALESCE((SELECT group_concat(alias, ' ') FROM character_alias WHERE character_id = NEW.character_id), '')
        WHERE character_id = NEW.character_id;
    END
    """,
    """
    CREATE TRIGGER character_alias_search_update
    AFTER UPDATE ON character_alias
    BEGIN
        UPDATE character_search_content
        SET aliases = COALESCE((SELECT group_concat(alias, ' ') FROM character_alias WHERE character_id = OLD.character_id), '')
        WHERE character_id = OLD.character_id;
        UPDATE character_search_content
        SET aliases = COALESCE((SELECT group_concat(alias, ' ') FROM character_alias WHERE character_id = NEW.character_id), '')
        WHERE character_id = NEW.character_id;
    END
    """,
    """
    CREATE TRIGGER character_alias_search_delete
    AFTER DELETE ON character_alias
    BEGIN
        UPDATE character_search_content
        SET aliases = COALESCE((SELECT group_concat(alias, ' ') FROM character_alias WHERE character_id = OLD.character_id), '')
        WHERE character_id = OLD.character_id;
    END
    """,
]

COPY_COLUMNS = [
    "id",
    "project_id",
    "name",
    "sort_name",
    "role",
    "short_description",
    "profile_md",
    "author_notes_md",
    "sort_order",
    "created_at",
    "updated_at",
    "deleted_at",
    "row_version",
    "strengths_md",
    "weaknesses_md",
    "portrait_file",
    "portrait_mime",
]


def _table_columns(connection: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {
        str(row[1]): row
        for row in connection.execute("PRAGMA table_info(character)").fetchall()
    }


def role_is_nullable(connection: sqlite3.Connection) -> bool:
    cols = _table_columns(connection)
    role = cols.get("role")
    if role is None:
        return False
    return int(role[3] or 0) == 0


def _normalize_copied_role(value: object) -> str | None:
    key = str(value or "").strip()
    if key in ALLOWED_ROLES:
        return key
    return None


def _rebuild_character_table(connection: sqlite3.Connection) -> None:
    cols = _table_columns(connection)
    select_bits = []
    for name in COPY_COLUMNS:
        if name not in cols:
            if name == "role":
                select_bits.append("NULL")
            else:
                select_bits.append("''")
            continue
        if name == "role":
            select_bits.append("role")
        else:
            select_bits.append(name)
    select_sql = ", ".join(select_bits)
    insert_cols = ", ".join(COPY_COLUMNS)

    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("PRAGMA legacy_alter_table = ON")
    connection.execute("DROP VIEW IF EXISTS v_character_required_fields_missing")
    for name in _DROP_TRIGGERS:
        connection.execute(f"DROP TRIGGER IF EXISTS {name}")
    connection.execute(_CREATE_SQL.replace("CREATE TABLE character (", "CREATE TABLE character_new_071 (", 1))
    connection.execute(
        f"INSERT INTO character_new_071 ({insert_cols}) SELECT {select_sql} FROM character"
    )
    connection.execute(
        "UPDATE character_new_071 SET role = NULL "
        "WHERE role IS NOT NULL AND role NOT IN ('protagonist', 'antagonist', 'supporting', 'minor')"
    )
    connection.execute("DROP TABLE character")
    connection.execute("ALTER TABLE character_new_071 RENAME TO character")
    for sql in _INDEX_SQL:
        connection.execute(sql)
    for sql in _TRIGGER_SQL:
        connection.execute(sql)
    for sql in _ALIAS_TRIGGER_SQL:
        connection.execute(sql)
    connection.execute(
        """
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
          )
        """
    )
    connection.execute("PRAGMA foreign_key_check")
    connection.execute("PRAGMA legacy_alter_table = OFF")
    connection.execute("PRAGMA foreign_keys = ON")


def apply(connection: sqlite3.Connection) -> None:
    applied = {
        int(row[0])
        for row in connection.execute("SELECT version FROM schema_migration").fetchall()
    }
    if MIGRATION_VERSION in applied and role_is_nullable(connection):
        return
    if not role_is_nullable(connection):
        _rebuild_character_table(connection)
    connection.execute(
        "INSERT OR IGNORE INTO schema_migration(version, name) VALUES (?, ?)",
        (MIGRATION_VERSION, MIGRATION_NAME),
    )
