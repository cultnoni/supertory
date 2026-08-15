"""Migration 40: Glump ER fill-in-the-blank session resume state."""

from __future__ import annotations

import sqlite3

MIGRATION_VERSION = 40
MIGRATION_NAME = "glump_fill_blank"


def _create_table(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS glump_fill_blank_sessions (
            id             TEXT PRIMARY KEY,
            work_id        TEXT NOT NULL DEFAULT '',
            episode_id     TEXT NOT NULL DEFAULT '',
            skeleton_json  TEXT NOT NULL DEFAULT '{}',
            status         TEXT NOT NULL DEFAULT 'in_progress'
                           CHECK (status IN ('in_progress', 'completed', 'abandoned')),
            created_at     TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS ix_glump_fill_blank_sessions_work
            ON glump_fill_blank_sessions(work_id, episode_id, status, created_at);
        """
    )


def apply(connection: sqlite3.Connection) -> None:
    _create_table(connection)
    connection.execute(
        "INSERT OR IGNORE INTO schema_migration(version, name) VALUES (?, ?)",
        (MIGRATION_VERSION, MIGRATION_NAME),
    )
