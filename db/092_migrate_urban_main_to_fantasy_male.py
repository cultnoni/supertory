"""Migration 92: rewrite leftover main_genre='urban' into fantasy/male/urban.

Logs affected row count and ids before and after the UPDATE.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATION_VERSION = 92
MIGRATION_NAME = "migrate_urban_main_to_fantasy_male"
SQL_PATH = Path(__file__).with_suffix(".sql")


def _ids(rows: list) -> list[int]:
    out: list[int] = []
    for row in rows:
        try:
            out.append(int(row[0]))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def apply(connection: sqlite3.Connection) -> None:
    before_rows = connection.execute(
        "SELECT id FROM project WHERE main_genre = 'urban' ORDER BY id"
    ).fetchall()
    before_ids = _ids(before_rows)
    print(
        f"[092] before: count={len(before_ids)} ids={before_ids}",
        flush=True,
    )
    connection.executescript(SQL_PATH.read_text(encoding="utf-8"))
    leftover_rows = connection.execute(
        "SELECT id FROM project WHERE main_genre = 'urban' ORDER BY id"
    ).fetchall()
    leftover_ids = _ids(leftover_rows)
    migrated_rows = connection.execute(
        "SELECT id FROM project "
        "WHERE main_genre = 'fantasy' AND sub_genre = 'male' "
        "AND genre_detail = 'urban' ORDER BY id"
    ).fetchall()
    migrated_ids = _ids(migrated_rows)
    still_urban = set(before_ids) & set(leftover_ids)
    rewritten = [pid for pid in before_ids if pid not in still_urban]
    print(
        f"[092] after: leftover_urban count={len(leftover_ids)} ids={leftover_ids}",
        flush=True,
    )
    print(
        f"[092] after: rewritten count={len(rewritten)} ids={rewritten} "
        f"(fantasy/male/urban now count={len(migrated_ids)} ids={migrated_ids})",
        flush=True,
    )
