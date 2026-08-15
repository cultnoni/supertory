"""Migration 42: Glump ER lucky-sentence redraw history."""

from __future__ import annotations

import sqlite3

MIGRATION_VERSION = 42
MIGRATION_NAME = "glump_lucky_sentence"


def _create_table(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS glump_lucky_draws (
            id          TEXT PRIMARY KEY,
            work_id     TEXT NOT NULL DEFAULT '',
            session_id  TEXT NOT NULL DEFAULT '',
            sentence    TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS ix_glump_lucky_draws_session
            ON glump_lucky_draws(session_id, created_at);
        """
    )


def apply(connection: sqlite3.Connection) -> None:
    _create_table(connection)
    connection.execute(
        "INSERT OR IGNORE INTO schema_migration(version, name) VALUES (?, ?)",
        (MIGRATION_VERSION, MIGRATION_NAME),
    )
