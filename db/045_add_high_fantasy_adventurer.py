"""Migration 45: add the high-fantasy adventurer reader persona.

Inserts ``high_fantasy_adventurer`` from ``data/virtual_reader_personas.json``.
Existing persona rows are left untouched.

Seed is read from repo/bundle ``data/virtual_reader_personas.json``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

MIGRATION_VERSION = 45
MIGRATION_NAME = "add_high_fantasy_adventurer"

NEW_ID = "high_fantasy_adventurer"

_REPO_ROOT = Path(__file__).resolve().parents[1]
PERSONA_SEED_PATH = _REPO_ROOT / "data" / "virtual_reader_personas.json"


def utc_timestamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def persona_seed_path() -> Path:
    return PERSONA_SEED_PATH


def _as_json_array(value: object) -> str:
    if not isinstance(value, list):
        raise ValueError("criteria / sample_responses must be a JSON array")
    return json.dumps(value, ensure_ascii=False)


def load_new_persona(path: Path | None = None) -> dict:
    seed_path = path or persona_seed_path()
    raw = json.loads(seed_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"persona seed must be a JSON array: {seed_path}")
    for item in raw:
        if isinstance(item, dict) and str(item.get("id") or "").strip() == NEW_ID:
            return item
    raise ValueError(f"persona seed missing id: {NEW_ID}")


def insert_high_fantasy_adventurer(connection: sqlite3.Connection) -> int:
    """INSERT the new seed row. Safe to run more than once."""
    persona = load_new_persona()
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
            utc_timestamp_now(),
        ),
    )
    return cursor.rowcount


def apply(connection: sqlite3.Connection) -> None:
    connection.execute("SAVEPOINT migration_045")
    try:
        insert_high_fantasy_adventurer(connection)
        connection.execute(
            "INSERT OR IGNORE INTO schema_migration(version, name) VALUES (?, ?)",
            (MIGRATION_VERSION, MIGRATION_NAME),
        )
        connection.execute("RELEASE SAVEPOINT migration_045")
    except Exception:
        connection.execute("ROLLBACK TO SAVEPOINT migration_045")
        connection.execute("RELEASE SAVEPOINT migration_045")
        raise
