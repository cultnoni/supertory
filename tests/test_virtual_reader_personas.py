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
        self.assertEqual(len(expected_ids), 24)
        with app.database() as connection:
            rows = connection.execute(
                "SELECT id, category, name, criteria, sample_responses, created_at "
                "FROM virtual_reader_personas ORDER BY display_order, id"
            ).fetchall()
            self.assertEqual(len(rows), 24)
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

    def test_migration_44_records_version_and_adds_two_personas(self) -> None:
        expected_new = {
            "game_system_maniac": "게임물 시스템 매니아",
            "alt_history_analyst": "대체역사 개연성 분석가",
        }
        with app.database() as connection:
            row = connection.execute(
                "SELECT 1 FROM schema_migration WHERE version = 44"
            ).fetchone()
            self.assertIsNotNone(row)
            rows = connection.execute(
                """
                SELECT id, name, category, identity, sample_responses, display_order
                FROM virtual_reader_personas
                WHERE id IN (?, ?)
                ORDER BY display_order
                """,
                tuple(expected_new),
            ).fetchall()
            self.assertEqual(len(rows), 2)
            seed = {persona["id"]: persona for persona in _PERSONAS}
            by_id = {row["id"]: row for row in rows}
            for persona_id, name in expected_new.items():
                row = by_id[persona_id]
                persona = seed[persona_id]
                self.assertEqual(row["name"], name)
                self.assertEqual(row["category"], persona["category"])
                self.assertEqual(row["identity"], persona["identity"])
                self.assertEqual(
                    json.loads(row["sample_responses"]), persona["sample_responses"]
                )
                self.assertEqual(row["display_order"], persona["display_order"])
            critic = connection.execute(
                "SELECT identity, sample_responses FROM virtual_reader_personas "
                "WHERE id = 'sf_hardcore_critic'"
            ).fetchone()
            self.assertEqual(critic["identity"], seed["sf_hardcore_critic"]["identity"])
            self.assertIn("SF·판타지의 마법이든 과학이든", critic["identity"])
            self.assertEqual(
                json.loads(critic["sample_responses"]),
                seed["sf_hardcore_critic"]["sample_responses"],
            )

    def test_migration_44_is_idempotent_and_leaves_other_columns(self) -> None:
        migration = app._load_py_migration(app.MIGRATION_044_PATH)
        with app.database() as connection:
            before = connection.execute(
                """
                SELECT id, category, tone, forbidden, discussion_attitude, display_order
                FROM virtual_reader_personas
                WHERE id = 'wuxia_romantic'
                """
            ).fetchone()
            before_critic = connection.execute(
                """
                SELECT category, name, tone, forbidden, discussion_attitude, display_order
                FROM virtual_reader_personas
                WHERE id = 'sf_hardcore_critic'
                """
            ).fetchone()
            connection.execute(
                "UPDATE virtual_reader_personas SET identity = '옛문장' "
                "WHERE id = 'sf_hardcore_critic'"
            )
            migration.apply(connection)
            after_critic = connection.execute(
                "SELECT identity, category, name, tone, forbidden, "
                "discussion_attitude, display_order "
                "FROM virtual_reader_personas WHERE id = 'sf_hardcore_critic'"
            ).fetchone()
            after_other = connection.execute(
                """
                SELECT id, category, tone, forbidden, discussion_attitude, display_order
                FROM virtual_reader_personas
                WHERE id = 'wuxia_romantic'
                """
            ).fetchone()
            versions = connection.execute(
                "SELECT COUNT(*) FROM schema_migration WHERE version = 44"
            ).fetchone()[0]
            count = connection.execute(
                "SELECT COUNT(*) FROM virtual_reader_personas"
            ).fetchone()[0]
        self.assertIn("SF·판타지의 마법이든 과학이든", after_critic["identity"])
        self.assertEqual(after_critic["category"], before_critic["category"])
        self.assertEqual(after_critic["name"], before_critic["name"])
        self.assertEqual(after_critic["tone"], before_critic["tone"])
        self.assertEqual(after_critic["forbidden"], before_critic["forbidden"])
        self.assertEqual(
            after_critic["discussion_attitude"], before_critic["discussion_attitude"]
        )
        self.assertEqual(after_critic["display_order"], before_critic["display_order"])
        self.assertEqual(dict(after_other), dict(before))
        self.assertEqual(versions, 1)
        self.assertEqual(count, 24)

    def test_migration_45_records_version_and_adds_adventurer(self) -> None:
        with app.database() as connection:
            row = connection.execute(
                "SELECT 1 FROM schema_migration WHERE version = 45"
            ).fetchone()
            self.assertIsNotNone(row)
            persona = connection.execute(
                """
                SELECT id, name, category, identity, sample_responses, display_order
                FROM virtual_reader_personas
                WHERE id = 'high_fantasy_adventurer'
                """
            ).fetchone()
            self.assertIsNotNone(persona)
            seed = next(item for item in _PERSONAS if item["id"] == "high_fantasy_adventurer")
            self.assertEqual(persona["name"], "정통 판타지 모험가")
            self.assertEqual(persona["category"], seed["category"])
            self.assertEqual(persona["identity"], seed["identity"])
            self.assertEqual(
                json.loads(persona["sample_responses"]), seed["sample_responses"]
            )
            self.assertEqual(persona["display_order"], seed["display_order"])
            count = connection.execute(
                "SELECT COUNT(*) FROM virtual_reader_personas"
            ).fetchone()[0]
            self.assertEqual(count, 24)

    def test_migration_45_is_idempotent_and_leaves_other_rows(self) -> None:
        migration = app._load_py_migration(app.MIGRATION_045_PATH)
        with app.database() as connection:
            before = connection.execute(
                """
                SELECT id, category, name, tone, forbidden, discussion_attitude, display_order
                FROM virtual_reader_personas
                WHERE id = 'wuxia_romantic'
                """
            ).fetchone()
            migration.apply(connection)
            after_other = connection.execute(
                """
                SELECT id, category, name, tone, forbidden, discussion_attitude, display_order
                FROM virtual_reader_personas
                WHERE id = 'wuxia_romantic'
                """
            ).fetchone()
            versions = connection.execute(
                "SELECT COUNT(*) FROM schema_migration WHERE version = 45"
            ).fetchone()[0]
            count = connection.execute(
                "SELECT COUNT(*) FROM virtual_reader_personas"
            ).fetchone()[0]
        self.assertEqual(dict(after_other), dict(before))
        self.assertEqual(versions, 1)
        self.assertEqual(count, 24)

    def test_migration_46_reorders_genre_specialist_after_tension(self) -> None:
        expected = [
            "roppan_cider",
            "roppan_narrative",
            "modern_romance_flutter",
            "modern_romance_tension",
            "modern_fantasy_pro",
            "hunter_speedrunner",
            "game_system_maniac",
            "high_fantasy_adventurer",
            "wuxia_romantic",
        ]
        with app.database() as connection:
            row = connection.execute(
                "SELECT 1 FROM schema_migration WHERE version = 46"
            ).fetchone()
            self.assertIsNotNone(row)
            rows = connection.execute(
                """
                SELECT id, name, display_order
                FROM virtual_reader_personas
                ORDER BY display_order, id
                """
            ).fetchall()
            self.assertEqual([row["id"] for row in rows[:9]], expected)
            self.assertEqual(
                [row["name"] for row in rows[4:9]],
                [
                    "현판 전문직·재벌물 실용파",
                    "헌터물 스피드러너",
                    "게임물 시스템 매니아",
                    "정통 판타지 모험가",
                    "정통 무협 낭만파",
                ],
            )
            seed_ids = [persona["id"] for persona in _PERSONAS]
            self.assertEqual([row["id"] for row in rows], seed_ids)
            for row, persona in zip(rows, _PERSONAS):
                self.assertEqual(row["display_order"], persona["display_order"])

    def test_migration_81_updates_identity_and_criteria_only(self) -> None:
        migration = app._load_py_migration(app.MIGRATION_081_PATH)
        seed = {persona["id"]: persona for persona in _PERSONAS}
        with app.database() as connection:
            before_other = connection.execute(
                """
                SELECT id, name, identity, criteria, tone, display_order
                FROM virtual_reader_personas
                WHERE id = 'modern_fantasy_pro'
                """
            ).fetchone()
            connection.execute(
                "UPDATE virtual_reader_personas SET identity = '옛문장' "
                "WHERE id = 'roppan_cider'"
            )
            connection.execute(
                "UPDATE virtual_reader_personas SET criteria = '[]' "
                "WHERE id = 'hunter_speedrunner'"
            )
            migration.apply(connection)
            cider = connection.execute(
                "SELECT identity, criteria FROM virtual_reader_personas "
                "WHERE id = 'roppan_cider'"
            ).fetchone()
            narrative = connection.execute(
                "SELECT identity, criteria FROM virtual_reader_personas "
                "WHERE id = 'roppan_narrative'"
            ).fetchone()
            hunter = connection.execute(
                "SELECT identity, criteria FROM virtual_reader_personas "
                "WHERE id = 'hunter_speedrunner'"
            ).fetchone()
            after_other = connection.execute(
                """
                SELECT id, name, identity, criteria, tone, display_order
                FROM virtual_reader_personas
                WHERE id = 'modern_fantasy_pro'
                """
            ).fetchone()
            versions = connection.execute(
                "SELECT COUNT(*) FROM schema_migration WHERE version = 81"
            ).fetchone()[0]
        self.assertEqual(
            cider["identity"],
            "억울함이 쌓일수록 좋다, 대신 터질 땐 확실하게 터져야 한다 — "
            "속도가 아니라 카타르시스의 완성도가 기준",
        )
        self.assertNotIn("배경이 이세계든 현대든", cider["identity"])
        self.assertEqual(cider["identity"], seed["roppan_cider"]["identity"])
        self.assertEqual(json.loads(cider["criteria"]), seed["roppan_cider"]["criteria"])
        self.assertEqual(narrative["identity"], seed["roppan_narrative"]["identity"])
        self.assertIn("설정(세계관·현실 배경) 몰입도", json.loads(narrative["criteria"]))
        self.assertNotIn(
            "세계관(이세계) 또는 현실 설정(현대)에 대한 몰입도",
            json.loads(narrative["criteria"]),
        )
        self.assertEqual(hunter["identity"], seed["hunter_speedrunner"]["identity"])
        self.assertEqual(
            json.loads(hunter["criteria"]),
            seed["hunter_speedrunner"]["criteria"],
        )
        self.assertNotIn("초반 후킹 속도", json.loads(hunter["criteria"]))
        self.assertEqual(dict(after_other), dict(before_other))
        self.assertEqual(versions, 1)




