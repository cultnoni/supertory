"""Migration 35: virtual reader personas + 1:1 reader chat tables.

Timestamps are generated in Python (microsecond UTC), not SQLite
``strftime(..., 'now')``, so rapid inserts do not collide at millisecond
resolution.

Persona seed is loaded from ``data/virtual_reader_personas.json`` next to
the repo (or bundle) root — never from the writable ``DATA_DIR``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

MIGRATION_VERSION = 35
MIGRATION_NAME = "virtual_reader_personas"

PERSONA_CATEGORIES = (
    "genre_specialist",
    "sub_genre_specialist",
    "taste_preference",
    "narrative_critic",
    "structure_wildcard",
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
PERSONA_SEED_PATH = _REPO_ROOT / "data" / "virtual_reader_personas.json"


def utc_timestamp_now() -> str:
    """UTC timestamp with microseconds. Same shape as app.utc_timestamp_now()."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def persona_seed_path() -> Path:
    """Repo/bundle ``data/virtual_reader_personas.json`` (not DATA_DIR)."""
    return PERSONA_SEED_PATH


def load_personas(path: Path | None = None) -> list[dict]:
    seed_path = path or persona_seed_path()
    raw = json.loads(seed_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"persona seed must be a JSON array: {seed_path}")
    personas: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each persona must be a JSON object")
        personas.append(item)
    return personas


def _as_json_array(value: object) -> str:
    if not isinstance(value, list):
        raise ValueError("criteria / sample_responses must be a JSON array")
    return json.dumps(value, ensure_ascii=False)


def _create_tables(connection: sqlite3.Connection) -> None:
    category_list = ", ".join(f"'{item}'" for item in PERSONA_CATEGORIES)
    connection.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS virtual_reader_personas (
            id                   TEXT PRIMARY KEY,
            category             TEXT NOT NULL CHECK (category IN ({category_list})),
            name                 TEXT NOT NULL,
            identity             TEXT NOT NULL DEFAULT '',
            tone                 TEXT NOT NULL DEFAULT '',
            criteria             TEXT NOT NULL DEFAULT '[]',
            forbidden            TEXT NOT NULL DEFAULT '',
            sample_responses     TEXT NOT NULL DEFAULT '[]',
            discussion_attitude  TEXT NOT NULL DEFAULT '',
            display_order        INTEGER NOT NULL DEFAULT 0,
            created_at           TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS reader_chat_sessions (
            id          TEXT PRIMARY KEY,
            work_id     TEXT NOT NULL,
            persona_id  TEXT NOT NULL,
            session_key TEXT NOT NULL UNIQUE,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            FOREIGN KEY (work_id) REFERENCES project(id) ON DELETE RESTRICT,
            FOREIGN KEY (persona_id) REFERENCES virtual_reader_personas(id) ON DELETE RESTRICT
        );

        CREATE INDEX IF NOT EXISTS ix_reader_chat_sessions_work
            ON reader_chat_sessions(work_id, persona_id);

        CREATE TABLE IF NOT EXISTS reader_chat_messages (
            id          TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL,
            role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content     TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES reader_chat_sessions(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS ix_reader_chat_messages_session
            ON reader_chat_messages(session_id, created_at, id);
        """
    )


def ensure_personas(connection: sqlite3.Connection) -> int:
    """Insert any missing seed rows. Safe to call after version 35 is applied."""
    inserted = 0
    for persona in load_personas():
        created_at = utc_timestamp_now()
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO virtual_reader_personas (
                id, category, name, identity, tone, criteria, forbidden,
                sample_responses, discussion_attitude, display_order, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                persona["id"],
                persona["category"],
                persona["name"],
                persona["identity"],
                persona["tone"],
                _as_json_array(persona["criteria"]),
                persona["forbidden"],
                _as_json_array(persona["sample_responses"]),
                persona["discussion_attitude"],
                int(persona["display_order"]),
                created_at,
            ),
        )
        inserted += cursor.rowcount
    return inserted


def apply(connection: sqlite3.Connection) -> None:
    _create_tables(connection)
    ensure_personas(connection)
    connection.execute(
        "INSERT INTO schema_migration(version, name) VALUES (?, ?)",
        (MIGRATION_VERSION, MIGRATION_NAME),
    )
