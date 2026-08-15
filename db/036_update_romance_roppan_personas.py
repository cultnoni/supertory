"""Migration 36: refresh romance/roppan persona copy for four existing ids.

Only ``name``, ``identity``, ``criteria``, and ``sample_responses`` change.
``id`` stays the same, so avatars and chat sessions are untouched.

Seed is read from repo/bundle ``data/virtual_reader_personas.json``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

MIGRATION_VERSION = 36
MIGRATION_NAME = "update_romance_roppan_personas"

TARGET_IDS = (
    "roppan_cider",
    "roppan_narrative",
    "modern_romance_flutter",
    "modern_romance_tension",
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
PERSONA_SEED_PATH = _REPO_ROOT / "data" / "virtual_reader_personas.json"


def persona_seed_path() -> Path:
    return PERSONA_SEED_PATH


def _as_json_array(value: object) -> str:
    if not isinstance(value, list):
        raise ValueError("criteria / sample_responses must be a JSON array")
    return json.dumps(value, ensure_ascii=False)


def load_target_personas(path: Path | None = None) -> list[dict]:
    seed_path = path or persona_seed_path()
    raw = json.loads(seed_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"persona seed must be a JSON array: {seed_path}")
    wanted = set(TARGET_IDS)
    found: dict[str, dict] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        persona_id = str(item.get("id") or "").strip()
        if persona_id in wanted:
            found[persona_id] = item
    missing = [persona_id for persona_id in TARGET_IDS if persona_id not in found]
    if missing:
        raise ValueError(f"persona seed missing ids: {', '.join(missing)}")
    return [found[persona_id] for persona_id in TARGET_IDS]


def update_romance_roppan_personas(connection: sqlite3.Connection) -> int:
    """UPDATE the four romance/roppan rows. Safe to run more than once."""
    updated = 0
    for persona in load_target_personas():
        cursor = connection.execute(
            """
            UPDATE virtual_reader_personas
            SET name = ?,
                identity = ?,
                criteria = ?,
                sample_responses = ?
            WHERE id = ?
            """,
            (
                persona["name"],
                persona["identity"],
                _as_json_array(persona["criteria"]),
                _as_json_array(persona["sample_responses"]),
                persona["id"],
            ),
        )
        updated += cursor.rowcount
    return updated


def apply(connection: sqlite3.Connection) -> None:
    connection.execute("SAVEPOINT migration_036")
    try:
        update_romance_roppan_personas(connection)
        connection.execute(
            "INSERT OR IGNORE INTO schema_migration(version, name) VALUES (?, ?)",
            (MIGRATION_VERSION, MIGRATION_NAME),
        )
        connection.execute("RELEASE SAVEPOINT migration_036")
    except Exception:
        connection.execute("ROLLBACK TO SAVEPOINT migration_036")
        connection.execute("RELEASE SAVEPOINT migration_036")
        raise
