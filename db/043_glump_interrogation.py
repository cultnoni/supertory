"""Migration 43: Glump ER character 1:1 interrogation sessions."""

from __future__ import annotations

import sqlite3

MIGRATION_VERSION = 43
MIGRATION_NAME = "glump_interrogation"


def _create_table(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS glump_interrogation_sessions (
            id              TEXT PRIMARY KEY,
            work_id         TEXT NOT NULL DEFAULT '',
            character_name  TEXT NOT NULL DEFAULT '',
            qa_json         TEXT NOT NULL DEFAULT '[]',
            status          TEXT NOT NULL DEFAULT 'in_progress'
                            CHECK (status IN ('in_progress', 'summarized')),
            created_at      TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS ix_glump_interrogation_sessions_work
            ON glump_interrogation_sessions(work_id, status, created_at);
        """
    )


def apply(connection: sqlite3.Connection) -> None:
    _create_table(connection)
    connection.execute(
        "INSERT OR IGNORE INTO schema_migration(version, name) VALUES (?, ?)",
        (MIGRATION_VERSION, MIGRATION_NAME),
    )
