"""initialise_database() discovers db/NNN_*.sql instead of a hand-written if-chain.

Guards the two ways the table can silently drift: a migration file that no loop
iteration reaches, and a .py migration that the generic executescript path would
try to run as SQL.
"""

from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

import app

VERSION_RE = re.compile(r"(\d{3})_.+")


class MigrationTableTests(unittest.TestCase):
    def test_versions_are_contiguous_from_the_base_schema(self) -> None:
        versions = sorted(app.MIGRATION_FILES)
        self.assertEqual(versions, list(range(1, app.LATEST_SCHEMA_VERSION + 1)))

    def test_every_python_migration_has_an_override(self) -> None:
        for version, files in sorted(app.MIGRATION_FILES.items()):
            if version == 1 or ".sql" in files:
                continue
            self.assertIn(
                version,
                app.MIGRATION_OVERRIDES,
                f"db/{files['.py'].name} needs an apply_migration_{version:03d} entry",
            )

    def test_overrides_only_name_versions_that_exist(self) -> None:
        for version in app.MIGRATION_OVERRIDES:
            self.assertIn(version, app.MIGRATION_FILES)

    def test_migration_path_prefers_python_over_sql(self) -> None:
        for version, files in app.MIGRATION_FILES.items():
            expected = files.get(".py") or files.get(".sql")
            self.assertEqual(app.migration_path(version), expected)

    def test_fresh_database_records_every_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            original_data_dir = app.DATA_DIR
            original_database_path = app.DATABASE_PATH
            app.DATA_DIR = Path(directory) / "data"
            app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
            try:
                app.initialise_database()
                with app.database() as connection:
                    applied = {
                        int(row[0])
                        for row in connection.execute(
                            "SELECT version FROM schema_migration"
                        ).fetchall()
                    }
            finally:
                app.DATA_DIR = original_data_dir
                app.DATABASE_PATH = original_database_path

        missing = set(range(1, app.LATEST_SCHEMA_VERSION + 1)) - applied
        self.assertEqual(missing, set(), f"versions never recorded: {sorted(missing)}")

    def test_old_database_upgrades_to_the_latest_version(self) -> None:
        """A database stuck at the base schema must walk the whole table."""
        with tempfile.TemporaryDirectory() as directory:
            original_data_dir = app.DATA_DIR
            original_database_path = app.DATABASE_PATH
            app.DATA_DIR = Path(directory) / "data"
            app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
            try:
                app.DATA_DIR.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(app.DATABASE_PATH)
                try:
                    connection.executescript(
                        app.SCHEMA_PATH.read_text(encoding="utf-8")
                    )
                    connection.execute("DELETE FROM schema_migration WHERE version > 1")
                    connection.commit()
                finally:
                    connection.close()

                app.initialise_database()
                with app.database() as connection:
                    applied = {
                        int(row[0])
                        for row in connection.execute(
                            "SELECT version FROM schema_migration"
                        ).fetchall()
                    }
            finally:
                app.DATA_DIR = original_data_dir
                app.DATABASE_PATH = original_database_path

        missing = set(range(1, app.LATEST_SCHEMA_VERSION + 1)) - applied
        self.assertEqual(missing, set(), f"versions never recorded: {sorted(missing)}")


if __name__ == "__main__":
    unittest.main()
