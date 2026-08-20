"""Migration 53: recompute glump_highlight_moments.episode_order from binder DFS.

Startup applies this once via schema_migration. The CLI in
scripts/recompute_highlight_episode_order.py reuses the same functions.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

MIGRATION_VERSION = 53
MIGRATION_NAME = "recompute_highlight_episode_order"


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
    try:
        import app

        mapping = {
            int(scene["id"]): index
            for index, scene in enumerate(
                app.list_scenes_in_binder_order(connection, project_id), start=1
            )
        }
    except Exception:
        mapping = {}
    cache[project_id] = mapping
    return mapping


def plan_episode_order_updates(
    connection: sqlite3.Connection,
) -> tuple[list[EpisodeOrderChange], int]:
    """Return (changes, total_rows). Skip unmapped scenes instead of writing 0."""
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'glump_highlight_moments'"
        ).fetchone()
        if exists is None:
            return [], 0
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
        try:
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
            if scene_id <= 0 or project_id <= 0:
                continue
            new_order = _scene_order_map(connection, project_id, cache).get(scene_id, 0)
            if new_order <= 0:
                continue
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
        except Exception:
            continue
    return changes, len(rows)


def apply_episode_order_updates(
    connection: sqlite3.Connection, changes: list[EpisodeOrderChange]
) -> int:
    updated = 0
    for item in changes:
        try:
            connection.execute(
                "UPDATE glump_highlight_moments SET episode_order = ? WHERE id = ?",
                (int(item.new_order), item.moment_id),
            )
            updated += 1
        except sqlite3.Error:
            continue
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


def _log_path() -> Path:
    try:
        import app

        data_dir = Path(app.DATA_DIR)
    except Exception:
        data_dir = Path(__file__).resolve().parents[1] / "data"
    return data_dir / f"recompute_highlight_episode_order-{_utc_stamp()}.log"


def apply(connection: sqlite3.Connection) -> None:
    """Idempotent via schema_migration version 53. Never raises to the starter."""
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'glump_highlight_moments'"
        ).fetchone()
        count = 0
        if exists is not None:
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM glump_highlight_moments"
                ).fetchone()[0]
            )
        if exists is not None and count > 0:
            changes, total = plan_episode_order_updates(connection)
            updated = apply_episode_order_updates(connection, changes)
            try:
                import app

                database_path = str(app.DATABASE_PATH)
            except Exception:
                database_path = ""
            log_text = format_change_log(
                changes,
                total_rows=total,
                applied=True,
                database_path=database_path,
            )
            write_change_log(_log_path(), log_text)
            print(
                f"마이그레이션 53: 명장면 episode_order {updated}건 갱신 "
                f"(대상 {total}행)",
                flush=True,
            )
    except Exception as error:
        print(f"경고: 마이그레이션 53 재계산을 건너뜁니다: {error}", flush=True)
    connection.execute(
        "INSERT OR IGNORE INTO schema_migration(version, name) VALUES (?, ?)",
        (MIGRATION_VERSION, MIGRATION_NAME),
    )
