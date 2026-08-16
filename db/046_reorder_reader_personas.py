"""Migration 46: reorder virtual reader persona cards.

Only ``display_order`` changes. Ids, copy, and avatars stay the same.

Seed is read from repo/bundle ``data/virtual_reader_personas.json``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

MIGRATION_VERSION = 46
MIGRATION_NAME = "reorder_reader_personas"

_REPO_ROOT = Path(__file__).resolve().parents[1]
PERSONA_SEED_PATH = _REPO_ROOT / "data" / "virtual_reader_personas.json"


def persona_seed_path() -> Path:
    return PERSONA_SEED_PATH


def load_display_orders(path: Path | None = None) -> list[tuple[str, int]]:
    seed_path = path or persona_seed_path()
    raw = json.loads(seed_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"persona seed must be a JSON array: {seed_path}")
    orders: list[tuple[str, int]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        persona_id = str(item.get("id") or "").strip()
        if not persona_id:
            continue
        orders.append((persona_id, int(item["display_order"])))
    if not orders:
        raise ValueError("persona seed has no display_order rows")
    return orders


def update_display_orders(connection: sqlite3.Connection) -> int:
    """UPDATE display_order for every seeded id. Safe to run more than once."""
    updated = 0
    for persona_id, display_order in load_display_orders():
        cursor = connection.execute(
            """
            UPDATE virtual_reader_personas
            SET display_order = ?
            WHERE id = ?
            """,
            (display_order, persona_id),
        )
        updated += cursor.rowcount
    return updated


def apply(connection: sqlite3.Connection) -> None:
    connection.execute("SAVEPOINT migration_046")
    try:
        update_display_orders(connection)
        connection.execute(
            "INSERT OR IGNORE INTO schema_migration(version, name) VALUES (?, ?)",
            (MIGRATION_VERSION, MIGRATION_NAME),
        )
        connection.execute("RELEASE SAVEPOINT migration_046")
    except Exception:
        connection.execute("ROLLBACK TO SAVEPOINT migration_046")
        connection.execute("RELEASE SAVEPOINT migration_046")
        raise
