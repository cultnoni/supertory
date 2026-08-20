"""CLI wrapper for highlight episode_order recompute.

Startup already runs this once as schema migration 53. Use this script later
if the binder order changes and you want to refresh stored 화수 values.

Logic lives in db/053_recompute_highlight_episode_order.py.
"""
from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app  # noqa: E402

_MIGRATION_PATH = ROOT / "db" / "053_recompute_highlight_episode_order.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "recompute_highlight_episode_order", _MIGRATION_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"마이그레이션을 불러올 수 없습니다: {_MIGRATION_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mig = _load_migration()
EpisodeOrderChange = _mig.EpisodeOrderChange
plan_episode_order_updates = _mig.plan_episode_order_updates
apply_episode_order_updates = _mig.apply_episode_order_updates
format_change_log = _mig.format_change_log
write_change_log = _mig.write_change_log


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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
        description=(
            "Recompute glump_highlight_moments.episode_order from binder order. "
            "Migration 53 already applies this automatically on first startup."
        )
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
