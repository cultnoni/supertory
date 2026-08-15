"""Migration 38: shared usage log for Glump ER tools (count only)."""

from __future__ import annotations

import sqlite3

MIGRATION_VERSION = 38
MIGRATION_NAME = "glump_tool_logs"


def _create_table(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS glump_tool_logs (
            id          TEXT PRIMARY KEY,
            work_id     TEXT NOT NULL DEFAULT '',
            tool_id     TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS ix_glump_tool_logs_tool
            ON glump_tool_logs(tool_id, created_at);

        CREATE INDEX IF NOT EXISTS ix_glump_tool_logs_work
            ON glump_tool_logs(work_id, tool_id);
        """
    )


def apply(connection: sqlite3.Connection) -> None:
    _create_table(connection)
    connection.execute(
        "INSERT OR IGNORE INTO schema_migration(version, name) VALUES (?, ?)",
        (MIGRATION_VERSION, MIGRATION_NAME),
    )
