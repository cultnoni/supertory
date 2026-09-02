"""Migration 81: refresh identity/criteria for five virtual-reader personas.

Only ``identity`` and ``criteria`` change. Ids, names, avatars, and chat
sessions are untouched. The other 18 personas are not written.

Seed is read from repo/bundle ``data/virtual_reader_personas.json``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

MIGRATION_VERSION = 81
MIGRATION_NAME = "update_reader_personas_v2"

TARGET_IDS = (
    "roppan_cider",
    "roppan_narrative",
    "modern_romance_flutter",
    "modern_romance_tension",
    "hunter_speedrunner",
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
PERSONA_SEED_PATH = _REPO_ROOT / "data" / "virtual_reader_personas.json"


def persona_seed_path() -> Path:
    return PERSONA_SEED_PATH


def _as_json_array(value: object) -> str:
    if not isinstance(value, list):
        raise ValueError("criteria must be a JSON array")
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


def update_reader_personas_v2(connection: sqlite3.Connection) -> int:
    """UPDATE identity/criteria for the five target rows. Safe to run more than once."""
    updated = 0
    for persona in load_target_personas():
        cursor = connection.execute(
            """
            UPDATE virtual_reader_personas
            SET identity = ?,
                criteria = ?
            WHERE id = ?
            """,
            (
                persona["identity"],
                _as_json_array(persona["criteria"]),
                persona["id"],
            ),
        )
        updated += cursor.rowcount
    return updated


def apply(connection: sqlite3.Connection) -> None:
    connection.execute("SAVEPOINT migration_081")
    try:
        update_reader_personas_v2(connection)
        connection.execute(
            "INSERT OR IGNORE INTO schema_migration(version, name) VALUES (?, ?)",
            (MIGRATION_VERSION, MIGRATION_NAME),
        )
        connection.execute("RELEASE SAVEPOINT migration_081")
    except Exception:
        connection.execute("ROLLBACK TO SAVEPOINT migration_081")
        connection.execute("RELEASE SAVEPOINT migration_081")
        raise
