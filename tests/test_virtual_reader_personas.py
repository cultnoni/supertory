"""Contract tests for migration 35 (virtual reader personas + reader chat)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import app

_MIGRATION = app._load_py_migration(app.MIGRATION_035_PATH)
_PERSONAS = _MIGRATION.load_personas()


class VirtualReaderPersonasMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()

    def tearDown(self) -> None:
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def test_migration_creates_tables_and_records_version(self) -> None:
        with app.database() as connection:
            row = connection.execute(
                "SELECT 1 FROM schema_migration WHERE version = 35"
            ).fetchone()
            self.assertIsNotNone(row)
            tables = {
                name
                for (name,) in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertIn("virtual_reader_personas", tables)
            self.assertIn("reader_chat_sessions", tables)
            self.assertIn("reader_chat_messages", tables)

    def test_seed_matches_json_personas(self) -> None:
        expected_ids = [persona["id"] for persona in _PERSONAS]
        self.assertEqual(len(expected_ids), 21)
        with app.database() as connection:
            rows = connection.execute(
                "SELECT id, category, name, criteria, sample_responses, created_at "
                "FROM virtual_reader_personas ORDER BY display_order, id"
            ).fetchall()
            self.assertEqual(len(rows), 21)
            self.assertEqual([row["id"] for row in rows], expected_ids)
            for row, persona in zip(rows, _PERSONAS):
                self.assertEqual(row["category"], persona["category"])
                self.assertEqual(row["name"], persona["name"])
                self.assertEqual(json.loads(row["criteria"]), persona["criteria"])
                self.assertEqual(
                    json.loads(row["sample_responses"]), persona["sample_responses"]
                )
                self.assertRegex(
                    row["created_at"],
                    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$",
                )

    def test_session_and_message_constraints(self) -> None:
        stamp = app.utc_timestamp_now()
        with app.database() as connection:
            connection.execute("INSERT INTO project(id, title) VALUES (1, '작품')")
            connection.execute(
                """
                INSERT INTO reader_chat_sessions
                    (id, work_id, persona_id, session_key, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "sess-1",
                    "1",
                    "roppan_cider",
                    "reader_chat_1_roppan_cider",
                    stamp,
                    stamp,
                ),
            )
            connection.execute(
                """
                INSERT INTO reader_chat_messages
                    (id, session_id, role, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("msg-1", "sess-1", "user", "안녕", stamp),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO reader_chat_messages
                        (id, session_id, role, content, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    ("msg-bad", "sess-1", "system", "nope", stamp),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO reader_chat_sessions
                        (id, work_id, persona_id, session_key, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "sess-2",
                        "1",
                        "roppan_cider",
                        "reader_chat_1_roppan_cider",
                        stamp,
                        stamp,
                    ),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO virtual_reader_personas (
                        id, category, name, identity, tone, criteria, forbidden,
                        sample_responses, discussion_attitude, display_order, created_at
                    ) VALUES (?, ?, ?, '', '', '[]', '', '[]', '', 99, ?)
                    """,
                    ("bad_cat", "not_a_category", "잘못된 분류", stamp),
                )

    def test_migration_36_records_version_and_updates_four_names(self) -> None:
        expected = {
            "roppan_cider": "로맨스·로판 사이다파",
            "roppan_narrative": "로맨스·로판 서사파",
            "modern_romance_flutter": "로맨스·로판 설렘파",
            "modern_romance_tension": "로맨스·로판 텐션파",
        }
        with app.database() as connection:
            row = connection.execute(
                "SELECT 1 FROM schema_migration WHERE version = 36"
            ).fetchone()
            self.assertIsNotNone(row)
            rows = connection.execute(
                """
                SELECT id, name, identity, category, tone, forbidden,
                       discussion_attitude, display_order
                FROM virtual_reader_personas
                WHERE id IN (?, ?, ?, ?)
                ORDER BY display_order
                """,
                tuple(expected),
            ).fetchall()
            self.assertEqual(len(rows), 4)
            by_id = {row["id"]: row for row in rows}
            seed = {
                persona["id"]: persona
                for persona in _PERSONAS
                if persona["id"] in expected
            }
            for persona_id, name in expected.items():
                row = by_id[persona_id]
                persona = seed[persona_id]
                self.assertEqual(row["name"], name)
                self.assertEqual(row["identity"], persona["identity"])
                self.assertEqual(row["category"], persona["category"])
                self.assertEqual(row["tone"], persona["tone"])
                self.assertEqual(row["forbidden"], persona["forbidden"])
                self.assertEqual(
                    row["discussion_attitude"], persona["discussion_attitude"]
                )
                self.assertEqual(row["display_order"], persona["display_order"])

    def test_migration_36_is_idempotent_and_leaves_other_columns(self) -> None:
        migration = app._load_py_migration(app.MIGRATION_036_PATH)
        with app.database() as connection:
            before = connection.execute(
                """
                SELECT id, category, tone, forbidden, discussion_attitude, display_order
                FROM virtual_reader_personas
                WHERE id = 'wuxia_romantic'
                """
            ).fetchone()
            connection.execute(
                "UPDATE virtual_reader_personas SET name = '옛이름' WHERE id = 'roppan_cider'"
            )
            migration.apply(connection)
            after_cider = connection.execute(
                "SELECT name FROM virtual_reader_personas WHERE id = 'roppan_cider'"
            ).fetchone()
            after_other = connection.execute(
                """
                SELECT id, category, tone, forbidden, discussion_attitude, display_order
                FROM virtual_reader_personas
                WHERE id = 'wuxia_romantic'
                """
            ).fetchone()
            versions = connection.execute(
                "SELECT COUNT(*) FROM schema_migration WHERE version = 36"
            ).fetchone()[0]
        self.assertEqual(after_cider["name"], "로맨스·로판 사이다파")
        self.assertEqual(dict(after_other), dict(before))
        self.assertEqual(versions, 1)

