"""Migration 41: Glump ER ping-pong relay sessions."""

from __future__ import annotations

import sqlite3

MIGRATION_VERSION = 41
MIGRATION_NAME = "glump_pingpong_sessions"


def _create_table(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS glump_pingpong_sessions (
            id                   TEXT PRIMARY KEY,
            work_id              TEXT NOT NULL DEFAULT '',
            episode_id           TEXT NOT NULL DEFAULT '',
            episode_excerpt      TEXT NOT NULL DEFAULT '',
            turns_json           TEXT NOT NULL DEFAULT '[]',
            chars_since_checkin  INTEGER NOT NULL DEFAULT 0,
            status               TEXT NOT NULL DEFAULT 'active'
                                 CHECK (status IN ('active', 'ended')),
            created_at           TEXT NOT NULL,
            updated_at           TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS ix_glump_pingpong_sessions_work
            ON glump_pingpong_sessions(work_id, episode_id, status, updated_at);
        """
    )


def apply(connection: sqlite3.Connection) -> None:
    _create_table(connection)
    connection.execute(
        "INSERT OR IGNORE INTO schema_migration(version, name) VALUES (?, ?)",
        (MIGRATION_VERSION, MIGRATION_NAME),
    )
