"""Recompute glump_highlight_moments.episode_order from binder folder DFS.

episode_id is a scene id. New episode_order is the 1-based 화수 from
list_scenes_in_binder_order (same walk as getEpisodeSequence).

Does not change chapter.sort_order. Default is dry-run (preview only).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app  # noqa: E402


class EpisodeOrderChange(NamedTuple):
    moment_id: str
    episode_id: str
    work_id: str
    scene_id: int
    project_id: int
    old_order: int
    new_order: int


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _scene_order_map(
    connection: sqlite3.Connection,
    project_id: int,
    cache: dict[int, dict[int, int]],
) -> dict[int, int]:
    if project_id <= 0:
        return {}
    cached = cache.get(project_id)
    if cached is not None:
        return cached
    mapping = {
        int(scene["id"]): index
        for index, scene in enumerate(
            app.list_scenes_in_binder_order(connection, project_id), start=1
        )
    }
    cache[project_id] = mapping
    return mapping


def plan_episode_order_updates(
    connection: sqlite3.Connection,
) -> tuple[list[EpisodeOrderChange], int]:
    """Return (changes, total_rows). changes only includes rows that would move."""
    try:
        rows = connection.execute(
            "SELECT id, work_id, episode_id, episode_order "
            "FROM glump_highlight_moments "
            "ORDER BY work_id, episode_id, id"
        ).fetchall()
    except sqlite3.OperationalError:
        return [], 0

    cache: dict[int, dict[int, int]] = {}
    changes: list[EpisodeOrderChange] = []
    for row in rows:
        episode_id = str(row["episode_id"] or "").strip()
        work_id = str(row["work_id"] or "").strip()
        scene_id = _as_int(episode_id)
        project_id = 0
        if scene_id > 0:
            scene = connection.execute(
                "SELECT project_id FROM scene WHERE id = ?",
                (scene_id,),
            ).fetchone()
            if scene is not None:
                project_id = _as_int(scene["project_id"])
        if project_id <= 0:
            project_id = _as_int(work_id)
        new_order = _scene_order_map(connection, project_id, cache).get(scene_id, 0)
        old_order = _as_int(row["episode_order"])
        if new_order == old_order:
            continue
        changes.append(
            EpisodeOrderChange(
                moment_id=str(row["id"]),
                episode_id=episode_id,
                work_id=work_id,
                scene_id=scene_id,
                project_id=project_id,
                old_order=old_order,
                new_order=new_order,
            )
        )
    return changes, len(rows)


def apply_episode_order_updates(
    connection: sqlite3.Connection, changes: list[EpisodeOrderChange]
) -> int:
    updated = 0
    for item in changes:
        connection.execute(
            "UPDATE glump_highlight_moments SET episode_order = ? WHERE id = ?",
            (int(item.new_order), item.moment_id),
        )
        updated += 1
    return updated


def format_change_log(
    changes: list[EpisodeOrderChange],
    *,
    total_rows: int,
    applied: bool,
    database_path: str,
) -> str:
    lines = [
        f"# recompute_highlight_episode_order {_utc_stamp()}",
        f"# database: {database_path}",
        f"# mode: {'apply' if applied else 'dry-run'}",
        f"# rows_total: {total_rows}",
        f"# rows_changed: {len(changes)}",
        f"# rows_unchanged: {total_rows - len(changes)}",
        "",
    ]
    if not changes:
        lines.append("# (no episode_order values need updating)")
        return "\n".join(lines) + "\n"
    for item in changes:
        lines.append(
            f"id={item.moment_id} episode_id={item.episode_id} "
            f"work_id={item.work_id} project_id={item.project_id} "
            f"episode_order {item.old_order} -> {item.new_order}"
        )
    return "\n".join(lines) + "\n"


def write_change_log(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _open_connection(db_path: Path, *, writable: bool) -> sqlite3.Connection:
    if writable:
        connection = sqlite3.connect(str(db_path))
    else:
        uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Recompute glump_highlight_moments.episode_order from binder order"
    )
    parser.add_argument(
        "--db",
        default="",
        help="SQLite path (default: app.DATABASE_PATH)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview how many rows would change (default if --apply is omitted)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write episode_order updates (omit this to only preview)",
    )
    parser.add_argument(
        "--log",
        default="",
        help="Log file path (default: data/recompute_highlight_episode_order-<utc>.log)",
    )
    args = parser.parse_args(argv)
    if args.dry_run and args.apply:
        parser.error("use either --dry-run or --apply, not both")

    apply_changes = bool(args.apply)
    db_path = Path(args.db) if args.db else Path(app.DATABASE_PATH)
    if not db_path.exists():
        print(f"database not found: {db_path}", file=sys.stderr)
        return 1

    log_path = (
        Path(args.log)
        if args.log
        else Path(app.DATA_DIR)
        / f"recompute_highlight_episode_order-{_utc_stamp()}.log"
    )

    connection = _open_connection(db_path, writable=apply_changes)
    try:
        changes, total = plan_episode_order_updates(connection)
        if apply_changes and changes:
            apply_episode_order_updates(connection, changes)
            connection.commit()
        log_text = format_change_log(
            changes,
            total_rows=total,
            applied=apply_changes,
            database_path=str(db_path),
        )
        write_change_log(log_path, log_text)
        print(log_text.rstrip())
        print(f"# log: {log_path}")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
