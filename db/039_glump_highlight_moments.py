"""Migration 39: Glump Mental Vitamin highlight moments + per-episode progress."""

from __future__ import annotations

import sqlite3

MIGRATION_VERSION = 39
MIGRATION_NAME = "glump_highlight_moments"


def _create_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS glump_highlight_moments (
            id            TEXT PRIMARY KEY,
            work_id       TEXT NOT NULL DEFAULT '',
            episode_id    TEXT NOT NULL DEFAULT '',
            episode_order INTEGER NOT NULL DEFAULT 0,
            moment_type   TEXT NOT NULL DEFAULT '',
            excerpt       TEXT NOT NULL DEFAULT '',
            reason        TEXT NOT NULL DEFAULT '',
            created_at    TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS ix_glump_highlight_moments_work
            ON glump_highlight_moments(work_id, episode_order, created_at);

        CREATE INDEX IF NOT EXISTS ix_glump_highlight_moments_episode
            ON glump_highlight_moments(episode_id, moment_type);

        CREATE TABLE IF NOT EXISTS glump_highlight_progress (
            episode_id            TEXT PRIMARY KEY,
            last_analyzed_length  INTEGER NOT NULL DEFAULT 0,
            updated_at            TEXT NOT NULL
        );
        """
    )


def apply(connection: sqlite3.Connection) -> None:
    _create_tables(connection)
    connection.execute(
        "INSERT OR IGNORE INTO schema_migration(version, name) VALUES (?, ?)",
        (MIGRATION_VERSION, MIGRATION_NAME),
    )
