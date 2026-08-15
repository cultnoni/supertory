"""Migration 37: glump emergency-room diagnosis log (analytics only).

Timestamps are generated in Python (microsecond UTC), not SQLite
``strftime(..., 'now')``.
"""

from __future__ import annotations

import sqlite3

MIGRATION_VERSION = 37
MIGRATION_NAME = "glump_diagnosis_log"

Q1_ANSWERS = ("block", "perfectionism", "self_doubt", "burnout")
Q2_ANSWERS = ("event", "sentence_struggle", "start", "together")


def _create_table(connection: sqlite3.Connection) -> None:
    q1_list = ", ".join(f"'{item}'" for item in Q1_ANSWERS)
    q2_list = ", ".join(f"'{item}'" for item in Q2_ANSWERS)
    connection.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS glump_diagnosis_logs (
            id                TEXT PRIMARY KEY,
            work_id           TEXT NOT NULL DEFAULT '',
            q1_answer         TEXT NOT NULL CHECK (q1_answer IN ({q1_list})),
            q2_answer         TEXT CHECK (q2_answer IS NULL OR q2_answer IN ({q2_list})),
            recommended_tool  TEXT,
            created_at        TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS ix_glump_diagnosis_logs_q1
            ON glump_diagnosis_logs(q1_answer);

        CREATE INDEX IF NOT EXISTS ix_glump_diagnosis_logs_q2
            ON glump_diagnosis_logs(q2_answer);

        CREATE INDEX IF NOT EXISTS ix_glump_diagnosis_logs_work
            ON glump_diagnosis_logs(work_id, created_at);
        """
    )


def apply(connection: sqlite3.Connection) -> None:
    _create_table(connection)
    connection.execute(
        "INSERT OR IGNORE INTO schema_migration(version, name) VALUES (?, ?)",
        (MIGRATION_VERSION, MIGRATION_NAME),
    )
