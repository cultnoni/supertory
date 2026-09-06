"""Per-project SQLite snapshots (작품 전체 시점 백업/복구).

Option A: copy the live DB with sqlite backup(), then drop every other
project's rows so the file holds one work only. Restoring replaces that
project in the live DB and leaves other works untouched.

Scene revisions stay append-only at runtime; snapshot restore temporarily
drops triggers so a full project replace is possible.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KEEP_COUNT = 4
DEFAULT_INTERVAL_DAYS = 7
SNAPSHOTS_DIRNAME = "snapshots"
FILENAME_RE = re.compile(
    r"^project_(?P<project_id>\d+)_(?P<stamp>\d{8}T\d{6}Z)\.sqlite3$"
)
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"

# App-global tables: keep in the snapshot file (tiny) but never restore them.
GLOBAL_TABLES = frozenset(
    {
        "schema_migration",
        "writing_day",
        "writing_prefs",
        "mobile_device",
        "mobile_inbox",
        "user_ambient_tracks",
        "ambient_track_overrides",
        "gitsi_rooms",
        "success_pattern_profile",
        "success_pattern_chapter_notes",
        "virtual_reader_personas",
        "sqlite_sequence",
        "sqlite_stat1",
        "sqlite_stat2",
        "sqlite_stat3",
        "sqlite_stat4",
    }
)

PROJECT_ID_COLUMNS = ("project_id", "local_project_id", "work_id")
_LOCK = threading.RLock()


def interval_days() -> int:
    raw = str(os.environ.get("SUPERTORY_SNAPSHOT_INTERVAL_DAYS") or "").strip()
    if not raw:
        return DEFAULT_INTERVAL_DAYS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_INTERVAL_DAYS
    return max(0, value)


def snapshots_dir(data_dir: Path) -> Path:
    path = Path(data_dir) / SNAPSHOTS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def snapshot_path(data_dir: Path, project_id: int, when: datetime) -> Path:
    stamp = _utc(when).strftime(STAMP_FORMAT)
    return snapshots_dir(data_dir) / f"project_{int(project_id)}_{stamp}.sqlite3"


def parse_snapshot_filename(name: str) -> tuple[int, datetime] | None:
    match = FILENAME_RE.fullmatch(Path(name).name)
    if not match:
        return None
    stamp = datetime.strptime(match.group("stamp"), STAMP_FORMAT).replace(tzinfo=timezone.utc)
    return int(match.group("project_id")), stamp


def list_snapshots(data_dir: Path, project_id: int) -> list[dict[str, Any]]:
    """Newest first."""
    items: list[dict[str, Any]] = []
    folder = snapshots_dir(data_dir)
    wanted = int(project_id)
    for path in folder.glob("project_*.sqlite3"):
        parsed = parse_snapshot_filename(path.name)
        if parsed is None or parsed[0] != wanted or not path.is_file():
            continue
        when = parsed[1]
        items.append(_snapshot_payload(path, wanted, when))
    items.sort(key=lambda row: str(row["created_at"]), reverse=True)
    return items


def latest_snapshot_time(data_dir: Path, project_id: int) -> datetime | None:
    items = list_snapshots(data_dir, project_id)
    if not items:
        return None
    parsed = parse_snapshot_filename(str(items[0]["filename"]))
    return parsed[1] if parsed else None


def create_snapshot(
    live_db_path: Path,
    data_dir: Path,
    project_id: int,
    *,
    now: datetime | None = None,
    prune: bool = True,
    preserve: str | None = None,
) -> dict[str, Any]:
    """Export the given project to a pruned SQLite snapshot file."""
    project_id = int(project_id)
    when = _utc(now or datetime.now(timezone.utc))
    dest = snapshot_path(data_dir, project_id, when)
    if dest.exists():
        when = when + timedelta(seconds=1)
        dest = snapshot_path(data_dir, project_id, when)

    with _LOCK:
        _assert_project_exists(live_db_path, project_id)
        tmp = dest.with_name(dest.name + ".tmp")
        if tmp.exists():
            tmp.unlink()
        try:
            _backup_live_db(live_db_path, tmp)
            _prune_other_projects(tmp, project_id)
            tmp.replace(dest)
        except Exception:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise
        if prune:
            prune_snapshots(data_dir, project_id, preserve=preserve)
        return _snapshot_payload(dest, project_id, when)


def restore_snapshot(
    live_db_path: Path,
    data_dir: Path,
    project_id: int,
    filename: str,
    *,
    create_safety: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Replace the live project's rows with the snapshot. Other projects stay."""
    project_id = int(project_id)
    parsed = parse_snapshot_filename(filename)
    if parsed is None or parsed[0] != project_id:
        raise ValueError("이 작품의 스냅샷 파일이 아닙니다.")
    source = snapshots_dir(data_dir) / Path(filename).name
    if not source.is_file():
        raise LookupError("스냅샷 파일을 찾을 수 없습니다.")

    safety: dict[str, Any] | None = None
    with _LOCK:
        _assert_project_exists(live_db_path, project_id)
        if create_safety:
            safety = create_snapshot(
                live_db_path,
                data_dir,
                project_id,
                now=now,
                prune=False,
                preserve=source.name,
            )
        _restore_project_from_file(live_db_path, source, project_id)
        prune_snapshots(data_dir, project_id, preserve=source.name)
    return {
        "restored": _snapshot_payload(source, project_id, parsed[1]),
        "safety": safety,
        "reload": True,
    }


def maybe_create_due_snapshots(
    live_db_path: Path,
    data_dir: Path,
    *,
    now: datetime | None = None,
    interval: timedelta | None = None,
) -> list[dict[str, Any]]:
    """Create a snapshot for each active project whose latest backup is due."""
    when = _utc(now or datetime.now(timezone.utc))
    gap = interval if interval is not None else timedelta(days=interval_days())
    created: list[dict[str, Any]] = []
    for project_id in _active_project_ids(live_db_path):
        latest = latest_snapshot_time(data_dir, project_id)
        if latest is not None and when - latest < gap:
            continue
        try:
            created.append(
                create_snapshot(live_db_path, data_dir, project_id, now=when)
            )
        except Exception:
            continue
    return created


def prune_snapshots(
    data_dir: Path,
    project_id: int,
    *,
    keep: int = KEEP_COUNT,
    preserve: str | None = None,
) -> list[str]:
    """Keep the newest ``keep`` snapshots. Returns deleted filenames."""
    items = list_snapshots(data_dir, project_id)
    preserve_name = Path(preserve).name if preserve else ""
    keep_n = max(1, int(keep))
    deleted: list[str] = []
    extra = items[keep_n:]
    for item in extra:
        name = str(item["filename"])
        if name == preserve_name:
            continue
        path = snapshots_dir(data_dir) / name
        try:
            path.unlink(missing_ok=True)
            deleted.append(name)
        except OSError:
            continue
    return deleted


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _snapshot_payload(path: Path, project_id: int, when: datetime) -> dict[str, Any]:
    stamp = _utc(when)
    size = path.stat().st_size if path.is_file() else 0
    return {
        "filename": path.name,
        "project_id": int(project_id),
        "created_at": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "created_at_label": stamp.strftime("%Y-%m-%d %H:%M"),
        "created_on": stamp.strftime("%Y-%m-%d"),
        "size_bytes": size,
    }


def _quote(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _assert_project_exists(live_db_path: Path, project_id: int) -> None:
    with _connect(live_db_path) as connection:
        row = connection.execute(
            "SELECT 1 FROM project WHERE id = ? AND deleted_at IS NULL",
            (project_id,),
        ).fetchone()
    if row is None:
        raise ValueError("소설을 찾을 수 없습니다.")


def _active_project_ids(live_db_path: Path) -> list[int]:
    if not Path(live_db_path).is_file():
        return []
    with _connect(live_db_path) as connection:
        rows = connection.execute(
            "SELECT id FROM project WHERE deleted_at IS NULL ORDER BY id"
        ).fetchall()
    return [int(row["id"]) for row in rows]


def _remove_sqlite_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        Path(str(path) + suffix).unlink(missing_ok=True)


def _backup_live_db(live_db_path: Path, dest_path: Path) -> None:
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(str(live_db_path))
    dest = sqlite3.connect(str(dest_path))
    try:
        try:
            source.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.Error:
            pass
        source.backup(dest)
        dest.commit()
        try:
            dest.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            dest.execute("PRAGMA journal_mode = DELETE")
        except sqlite3.Error:
            pass
    finally:
        dest.close()
        source.close()
    _remove_sqlite_sidecars(dest_path)


def _schema_version(connection: sqlite3.Connection) -> int:
    try:
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migration"
        ).fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0] if row else 0)


def _master_tables(connection: sqlite3.Connection, schema: str = "main") -> list[tuple[str, str]]:
    quoted = _quote(schema)
    rows = connection.execute(
        f"SELECT name, COALESCE(sql, '') AS sql FROM {quoted}.sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [(str(row[0]), str(row[1] or "")) for row in rows]


def _is_skipped_table(name: str, sql: str = "") -> bool:
    if name.startswith("sqlite_"):
        return True
    if "_fts" in name:
        return True
    if "VIRTUAL TABLE" in (sql or "").upper():
        return True
    return False


def _project_id_column(columns: list[str]) -> str | None:
    for name in PROJECT_ID_COLUMNS:
        if name in columns:
            return name
    return None


def _table_columns(connection: sqlite3.Connection, table: str, schema: str = "main") -> list[str]:
    rows = connection.execute(f"PRAGMA {_quote(schema)}.table_info({_quote(table)})").fetchall()
    return [str(row[1]) for row in rows]


def _iter_triggers(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    rows = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND sql IS NOT NULL"
    ).fetchall()
    return [(str(row[0]), str(row[1])) for row in rows]


def _drop_triggers(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    triggers = _iter_triggers(connection)
    for name, _sql in triggers:
        connection.execute(f"DROP TRIGGER IF EXISTS {_quote(name)}")
    return triggers


def _create_triggers(connection: sqlite3.Connection, triggers: list[tuple[str, str]]) -> None:
    for _name, sql in triggers:
        if sql:
            connection.execute(sql)


def _rebuild_fts(connection: sqlite3.Connection) -> None:
    names = {name for name, _sql in _master_tables(connection)}
    for fts in ("scene_fts", "character_fts"):
        if fts in names:
            try:
                connection.execute(f"INSERT INTO {_quote(fts)}({_quote(fts)}) VALUES('rebuild')")
            except sqlite3.Error:
                pass


def _delete_fk_violations(connection: sqlite3.Connection, rounds: int = 40) -> set[str]:
    touched: set[str] = set()
    for _ in range(rounds):
        rows = connection.execute("PRAGMA foreign_key_check").fetchall()
        if not rows:
            return touched
        by_table: dict[str, list[int]] = {}
        for row in rows:
            table = str(row[0])
            rowid = int(row[1])
            if _is_skipped_table(table):
                continue
            by_table.setdefault(table, []).append(rowid)
        if not by_table:
            return touched
        for table, ids in by_table.items():
            unique = sorted(set(ids))
            placeholders = ",".join("?" * len(unique))
            connection.execute(
                f"DELETE FROM {_quote(table)} WHERE rowid IN ({placeholders})",
                unique,
            )
            touched.add(table)
    return touched


def _prune_other_projects(snapshot_path: Path, project_id: int) -> None:
    connection = sqlite3.connect(str(snapshot_path))
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        triggers = _drop_triggers(connection)
        try:
            for name, sql in _master_tables(connection):
                if _is_skipped_table(name, sql) or name in GLOBAL_TABLES:
                    continue
                columns = _table_columns(connection, name)
                if name == "project":
                    connection.execute("DELETE FROM project WHERE id <> ?", (project_id,))
                    continue
                scope = _project_id_column(columns)
                if scope:
                    connection.execute(
                        f"DELETE FROM {_quote(name)} WHERE {_quote(scope)} <> ?",
                        (project_id,),
                    )
            _delete_fk_violations(connection)
        finally:
            _create_triggers(connection, triggers)
        connection.execute("PRAGMA foreign_keys = ON")
        _rebuild_fts(connection)
        connection.commit()
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        try:
            connection.execute("PRAGMA journal_mode = DELETE")
        except sqlite3.Error:
            pass
        try:
            connection.execute("VACUUM")
            connection.commit()
        except sqlite3.Error:
            pass
    finally:
        connection.close()
    _remove_sqlite_sidecars(snapshot_path)


def _restore_project_from_file(live_db_path: Path, snapshot_file: Path, project_id: int) -> None:
    live = sqlite3.connect(str(live_db_path))
    live.row_factory = sqlite3.Row
    try:
        live.execute("PRAGMA foreign_keys = OFF")
        live.execute("BEGIN IMMEDIATE")
        live.execute("ATTACH DATABASE ? AS snap", (str(Path(snapshot_file).resolve()),))
        try:
            live_version = _schema_version(live)
            snap_version = int(
                live.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM snap.schema_migration"
                ).fetchone()[0]
                or 0
            )
            if live_version != snap_version:
                raise ValueError(
                    "스냅샷 형식이 현재 앱과 달라요. 같은 버전에서 복구해 주세요."
                )
            triggers = _drop_triggers(live)
            try:
                live_tables = {
                    name: sql
                    for name, sql in _master_tables(live, "main")
                    if not _is_skipped_table(name, sql) and name not in GLOBAL_TABLES
                }
                snap_tables = {
                    name
                    for name, sql in _master_tables(live, "snap")
                    if not _is_skipped_table(name, sql) and name not in GLOBAL_TABLES
                }
                touched: set[str] = set()
                for name, sql in live_tables.items():
                    columns = _table_columns(live, name, "main")
                    if name == "project":
                        live.execute("DELETE FROM main.project WHERE id = ?", (project_id,))
                        touched.add(name)
                        continue
                    scope = _project_id_column(columns)
                    if scope:
                        live.execute(
                            f"DELETE FROM main.{_quote(name)} WHERE {_quote(scope)} = ?",
                            (project_id,),
                        )
                        touched.add(name)
                touched.update(_delete_fk_violations(live))

                for name in live_tables:
                    if name not in snap_tables:
                        continue
                    live_cols = _table_columns(live, name, "main")
                    if name != "project" and name not in touched and _project_id_column(live_cols) is None:
                        continue
                    snap_cols = _table_columns(live, name, "snap")
                    shared = [col for col in live_cols if col in snap_cols]
                    if not shared:
                        continue
                    col_sql = ", ".join(_quote(col) for col in shared)
                    live.execute(
                        f"INSERT INTO main.{_quote(name)} ({col_sql}) "
                        f"SELECT {col_sql} FROM snap.{_quote(name)}"
                    )
                _sync_sequences(live, list(touched | set(live_tables)))
            finally:
                _create_triggers(live, triggers)
            live.execute("PRAGMA foreign_keys = ON")
            _rebuild_fts(live)
            live.commit()
        except Exception:
            live.rollback()
            raise
        finally:
            try:
                live.execute("DETACH DATABASE snap")
            except sqlite3.Error:
                pass
            try:
                live.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
    finally:
        live.close()


def _sync_sequences(connection: sqlite3.Connection, tables: list[str]) -> None:
    has_sequence = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'"
    ).fetchone()
    if has_sequence is None:
        return
    for name in tables:
        columns = connection.execute(f"PRAGMA table_info({_quote(name)})").fetchall()
        pk = [row for row in columns if int(row[5] or 0) == 1]
        if len(pk) != 1:
            continue
        pk_name = str(pk[0][1])
        if pk_name.lower() != "id":
            continue
        row = connection.execute(
            f"SELECT MAX({_quote(pk_name)}) FROM {_quote(name)}"
        ).fetchone()
        max_id = row[0] if row else None
        if max_id is None:
            continue
        existing = connection.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = ?", (name,)
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)",
                (name, int(max_id)),
            )
        elif int(existing[0] or 0) < int(max_id):
            connection.execute(
                "UPDATE sqlite_sequence SET seq = ? WHERE name = ?",
                (int(max_id), name),
            )
