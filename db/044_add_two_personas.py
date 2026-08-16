"""Migration 44: add two reader personas and refresh SF critic copy.

Inserts ``game_system_maniac`` and ``alt_history_analyst``.
Updates only ``identity`` and ``sample_responses`` on ``sf_hardcore_critic``.
Other existing persona rows are left untouched.

Seed is read from repo/bundle ``data/virtual_reader_personas.json``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

MIGRATION_VERSION = 44
MIGRATION_NAME = "add_two_personas"

NEW_IDS = (
    "game_system_maniac",
    "alt_history_analyst",
)
UPDATE_ID = "sf_hardcore_critic"

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


def _load_seed(path: Path | None = None) -> dict[str, dict]:
    seed_path = path or persona_seed_path()
    raw = json.loads(seed_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"persona seed must be a JSON array: {seed_path}")
    found: dict[str, dict] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        persona_id = str(item.get("id") or "").strip()
        if persona_id:
            found[persona_id] = item
    wanted = (*NEW_IDS, UPDATE_ID)
    missing = [persona_id for persona_id in wanted if persona_id not in found]
    if missing:
        raise ValueError(f"persona seed missing ids: {', '.join(missing)}")
    return found


def insert_two_personas(connection: sqlite3.Connection, seed: dict[str, dict] | None = None) -> int:
    """INSERT the two new seed rows. Safe to run more than once."""
    personas = seed or _load_seed()
    inserted = 0
    for persona_id in NEW_IDS:
        persona = personas[persona_id]
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
        inserted += cursor.rowcount
    return inserted


def update_sf_hardcore_critic(connection: sqlite3.Connection, seed: dict[str, dict] | None = None) -> int:
    """UPDATE identity and sample_responses only. Other columns stay as-is."""
    personas = seed or _load_seed()
    persona = personas[UPDATE_ID]
    cursor = connection.execute(
        """
        UPDATE virtual_reader_personas
        SET identity = ?,
            sample_responses = ?
        WHERE id = ?
        """,
        (
            persona["identity"],
            _as_json_array(persona["sample_responses"]),
            UPDATE_ID,
        ),
    )
    return cursor.rowcount


def apply(connection: sqlite3.Connection) -> None:
    connection.execute("SAVEPOINT migration_044")
    try:
        seed = _load_seed()
        insert_two_personas(connection, seed)
        update_sf_hardcore_critic(connection, seed)
        connection.execute(
            "INSERT OR IGNORE INTO schema_migration(version, name) VALUES (?, ?)",
            (MIGRATION_VERSION, MIGRATION_NAME),
        )
        connection.execute("RELEASE SAVEPOINT migration_044")
    except Exception:
        connection.execute("ROLLBACK TO SAVEPOINT migration_044")
        connection.execute("RELEASE SAVEPOINT migration_044")
        raise
