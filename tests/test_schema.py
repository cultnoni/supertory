"""Executable contract tests for db/001_initial_schema.sql.

Run with: python -m unittest tests.test_schema
"""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path


SCHEMA = Path(__file__).resolve().parents[1] / "db" / "001_initial_schema.sql"


class SuperTorySchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA.read_text(encoding="utf-8"))
        self.db.execute("INSERT INTO project(id, title) VALUES (1, 'Project A')")
        self.db.execute("INSERT INTO project(id, title) VALUES (2, 'Project B')")

    def tearDown(self) -> None:
        self.db.close()

    def assert_integrity_error(self, sql: str, parameters: tuple[object, ...] = ()) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(sql, parameters)

    def create_story(self) -> None:
        self.db.execute("INSERT INTO part(id, project_id, title, sort_order) VALUES (10, 1, 'Part', 0)")
        self.db.execute(
            "INSERT INTO chapter(id, project_id, part_id, title, sort_order) VALUES (20, 1, 10, 'Chapter', 0)"
        )
        self.db.execute(
            "INSERT INTO scene(id, project_id, chapter_id, title, sort_order) VALUES (30, 1, 20, 'Scene', 0)"
        )

    def create_character(self) -> None:
        self.db.execute(
            "INSERT INTO character(id, project_id, name, sort_order) VALUES (40, 1, 'Han', 0)"
        )

    def test_cross_project_links_and_active_order_are_rejected(self) -> None:
        self.db.execute("INSERT INTO part(id, project_id, title, sort_order) VALUES (10, 1, 'Part', 0)")
        self.assert_integrity_error(
            "INSERT INTO chapter(project_id, part_id, title, sort_order) VALUES (2, 10, 'Wrong', 0)"
        )
        self.db.execute("INSERT INTO chapter(id, project_id, title, sort_order) VALUES (20, 1, 'One', 0)")
        self.assert_integrity_error(
            "INSERT INTO chapter(project_id, title, sort_order) VALUES (1, 'Two', 0)"
        )
        self.db.execute("INSERT INTO chapter(id, project_id, title, sort_order) VALUES (21, 1, 'Three', 1)")
        self.assert_integrity_error(
            "INSERT INTO scene(project_id, chapter_id, title, sort_order) VALUES (2, 21, 'Wrong scene', 0)"
        )
        self.db.execute("INSERT INTO character(id, project_id, name, sort_order) VALUES (40, 1, 'Han', 0)")
        self.assert_integrity_error(
            "INSERT INTO character_relationship(project_id, from_character_id, to_character_id, relationship_type) VALUES (2, 40, 40, 'wrong')"
        )

    def test_revisions_are_ordered_immutable_and_current(self) -> None:
        self.create_story()
        self.assert_integrity_error(
            "INSERT INTO scene_revision(scene_id, revision_no, content_md) VALUES (30, 2, 'skip')"
        )
        self.db.execute(
            "INSERT INTO scene_revision(scene_id, revision_no, content_md, word_count) VALUES (30, 1, 'first text', 2)"
        )
        self.assert_integrity_error(
            "INSERT INTO scene_revision(scene_id, revision_no, content_md) VALUES (30, 2, 'second')"
        )
        self.db.execute("UPDATE scene_revision SET is_current = 0 WHERE scene_id = 30 AND revision_no = 1")
        self.db.execute(
            "INSERT INTO scene_revision(scene_id, revision_no, content_md, is_current) VALUES (30, 2, 'second text', 1)"
        )
        current = self.db.execute(
            "SELECT revision_no, content_md FROM v_current_scene_revision WHERE scene_id = 30"
        ).fetchone()
        self.assertEqual(current, (2, "second text"))
        self.assert_integrity_error("UPDATE scene_revision SET content_md = 'mutated' WHERE revision_no = 1")

    def test_scene_character_and_relationship_constraints(self) -> None:
        self.create_story()
        self.create_character()
        self.db.execute("INSERT INTO character(id, project_id, name, sort_order) VALUES (41, 1, 'Min', 1)")
        self.db.execute("INSERT INTO scene_character(scene_id, character_id, project_id, is_pov) VALUES (30, 40, 1, 1)")
        self.assert_integrity_error(
            "INSERT INTO scene_character(scene_id, character_id, project_id, is_pov) VALUES (30, 41, 1, 1)"
        )
        self.assert_integrity_error(
            "INSERT INTO character_relationship(project_id, from_character_id, to_character_id, relationship_type) VALUES (1, 40, 40, 'self')"
        )

    def test_custom_field_type_and_required_field_view(self) -> None:
        self.create_character()
        self.db.execute(
            "INSERT INTO character_field_definition(id, project_id, field_key, label, field_type, is_required, sort_order) VALUES (50, 1, 'age', 'Age', 'integer', 1, 0)"
        )
        self.assertEqual(
            self.db.execute("SELECT character_id FROM v_character_required_fields_missing").fetchall(), [(40,)]
        )
        self.assert_integrity_error(
            "INSERT INTO character_field_value(character_id, field_definition_id, project_id, text_value) VALUES (40, 50, 1, 'wrong')"
        )
        self.db.execute(
            "INSERT INTO character_field_value(character_id, field_definition_id, project_id, integer_value) VALUES (40, 50, 1, 30)"
        )
        self.assertEqual(self.db.execute("SELECT * FROM v_character_required_fields_missing").fetchall(), [])

    def test_all_custom_field_types_and_option_ownership(self) -> None:
        self.create_character()
        fields = [
            (50, "bio", "text", 0),
            (51, "notes", "markdown", 1),
            (52, "age", "integer", 2),
            (53, "height", "real", 3),
            (54, "alive", "boolean", 4),
            (55, "birth", "date", 5),
            (56, "rank", "single_select", 6),
            (57, "traits", "multi_select", 7),
        ]
        self.db.executemany(
            "INSERT INTO character_field_definition(id, project_id, field_key, label, field_type, sort_order) VALUES (?, 1, ?, ?, ?, ?)",
            [(field_id, key, key.title(), field_type, order) for field_id, key, field_type, order in fields],
        )
        self.db.execute(
            "INSERT INTO character_field_option(id, project_id, field_definition_id, option_key, label, sort_order) VALUES (60, 1, 56, 'captain', 'Captain', 0)"
        )
        self.db.execute(
            "INSERT INTO character_field_option(id, project_id, field_definition_id, option_key, label, sort_order) VALUES (61, 1, 57, 'brave', 'Brave', 0)"
        )
        values = [
            (50, "text_value", "detective"),
            (51, "text_value", "**private**"),
            (52, "integer_value", 30),
            (53, "real_value", 172.5),
            (54, "boolean_value", 1),
            (55, "date_value", "1990-01-02"),
            (56, "option_id", 60),
        ]
        for definition_id, column, value in values:
            self.db.execute(
                f"INSERT INTO character_field_value(character_id, field_definition_id, project_id, {column}) VALUES (40, ?, 1, ?)",
                (definition_id, value),
            )
        self.db.execute(
            "INSERT INTO character_field_multi_option(character_id, field_definition_id, option_id, project_id) VALUES (40, 57, 61, 1)"
        )
        self.assert_integrity_error(
            "INSERT INTO character_field_value(character_id, field_definition_id, project_id, option_id) VALUES (40, 57, 1, 61)"
        )
        self.assert_integrity_error(
            "INSERT INTO character_field_multi_option(character_id, field_definition_id, option_id, project_id) VALUES (40, 56, 60, 1)"
        )
        self.assert_integrity_error(
            "INSERT INTO character_field_option(project_id, field_definition_id, option_key, label, sort_order) VALUES (1, 50, 'bad', 'Bad', 0)"
        )
        self.assert_integrity_error(
            "UPDATE character_field_definition SET field_type = 'text' WHERE id = 56"
        )

    def test_fts_tracks_current_content_and_soft_delete(self) -> None:
        self.create_story()
        self.db.execute(
            "INSERT INTO scene_revision(scene_id, revision_no, content_md) VALUES (30, 1, 'moonlit harbor')"
        )
        self.assertEqual(
            self.db.execute("SELECT rowid FROM scene_fts WHERE scene_fts MATCH 'moonlit'").fetchall(), [(30,)]
        )
        self.db.execute("UPDATE scene SET deleted_at = '2026-08-04T00:00:00.000Z' WHERE id = 30")
        self.assertEqual(self.db.execute("SELECT rowid FROM scene_fts WHERE scene_fts MATCH 'moonlit'").fetchall(), [])
        self.db.execute("UPDATE scene SET deleted_at = NULL WHERE id = 30")
        self.assertEqual(
            self.db.execute("SELECT rowid FROM scene_fts WHERE scene_fts MATCH 'moonlit'").fetchall(), [(30,)]
        )

    def test_character_alias_fts_tracks_changes(self) -> None:
        self.create_character()
        self.db.execute("INSERT INTO character_alias(character_id, project_id, alias) VALUES (40, 1, 'Shadow')")
        self.assertEqual(
            self.db.execute("SELECT rowid FROM character_fts WHERE character_fts MATCH 'shadow'").fetchall(), [(40,)]
        )
        self.db.execute("UPDATE character SET deleted_at = '2026-08-04T00:00:00.000Z' WHERE id = 40")
        self.assertEqual(self.db.execute("SELECT rowid FROM character_fts WHERE character_fts MATCH 'shadow'").fetchall(), [])
        self.db.execute("UPDATE character SET deleted_at = NULL WHERE id = 40")
        self.assertEqual(
            self.db.execute("SELECT rowid FROM character_fts WHERE character_fts MATCH 'shadow'").fetchall(), [(40,)]
        )

    def test_soft_delete_cascades_downward_and_touch_advances_version(self) -> None:
        self.create_story()
        original_version = self.db.execute("SELECT row_version FROM chapter WHERE id = 20").fetchone()[0]
        self.db.execute("UPDATE part SET deleted_at = '2026-08-04T00:00:00.000Z' WHERE id = 10")
        self.assertIsNotNone(self.db.execute("SELECT deleted_at FROM chapter WHERE id = 20").fetchone()[0])
        self.assertIsNotNone(self.db.execute("SELECT deleted_at FROM scene WHERE id = 30").fetchone()[0])
        self.assertGreater(
            self.db.execute("SELECT row_version FROM chapter WHERE id = 20").fetchone()[0], original_version
        )
        self.assert_integrity_error("UPDATE chapter SET deleted_at = NULL WHERE id = 20")

    def test_project_index_and_scene_summary_tables(self) -> None:
        migration = Path(__file__).resolve().parents[1] / "db" / "019_project_index.sql"
        self.db.executescript(migration.read_text(encoding="utf-8"))
        self.create_story()
        self.db.execute(
            "INSERT INTO project_index(project_id, characters_json, open_threads_json) "
            "VALUES (1, '[\"Han\"]', '[\"편지\"]')"
        )
        self.db.execute(
            "INSERT INTO scene_summary(scene_id, summary) VALUES (30, '{\"event_summary\":\"만남\"}')"
        )
        row = self.db.execute(
            "SELECT characters_json, open_threads_json FROM project_index WHERE project_id = 1"
        ).fetchone()
        self.assertEqual(row[0], '["Han"]')
        summary = self.db.execute(
            "SELECT summary FROM scene_summary WHERE scene_id = 30"
        ).fetchone()[0]
        self.assertIn("만남", summary)
        # Cross-project / missing FK rejected
        self.assert_integrity_error(
            "INSERT INTO project_index(project_id) VALUES (999)"
        )
        self.assert_integrity_error(
            "INSERT INTO scene_summary(scene_id, summary) VALUES (999, '{}')"
        )

    def test_reader_comments_started_column(self) -> None:
        migration = Path(__file__).resolve().parents[1] / "db" / "047_scene_reader_comments_started.sql"
        self.db.executescript(migration.read_text(encoding="utf-8"))
        self.create_story()
        cols = {
            str(row[1])
            for row in self.db.execute("PRAGMA table_info(scene)").fetchall()
        }
        self.assertIn("reader_comments_started", cols)
        value = self.db.execute(
            "SELECT reader_comments_started FROM scene WHERE id = 30"
        ).fetchone()[0]
        self.assertEqual(value, 0)
        self.db.execute("UPDATE scene SET reader_comments_started = 1 WHERE id = 30")
        value = self.db.execute(
            "SELECT reader_comments_started FROM scene WHERE id = 30"
        ).fetchone()[0]
        self.assertEqual(value, 1)

    def test_tracked_facts_columns(self) -> None:
        self.db.executescript(
            (Path(__file__).resolve().parents[1] / "db" / "019_project_index.sql").read_text(encoding="utf-8")
        )
        self.db.executescript(
            (Path(__file__).resolve().parents[1] / "db" / "048_tracked_facts.sql").read_text(encoding="utf-8")
        )
        self.create_story()
        scene_cols = {
            str(row[1])
            for row in self.db.execute("PRAGMA table_info(scene_summary)").fetchall()
        }
        index_cols = {
            str(row[1])
            for row in self.db.execute("PRAGMA table_info(project_index)").fetchall()
        }
        self.assertIn("tracked_facts_json", scene_cols)
        self.assertIn("tracked_facts_json", index_cols)
        self.db.execute(
            "INSERT INTO scene_summary(scene_id, summary, tracked_facts_json) "
            "VALUES (30, '{}', '[{\"category\":\"신체상태\"}]')"
        )
        self.db.execute(
            "INSERT INTO project_index(project_id, tracked_facts_json) VALUES (1, '[]')"
        )
        scene_val = self.db.execute(
            "SELECT tracked_facts_json FROM scene_summary WHERE scene_id = 30"
        ).fetchone()[0]
        self.assertIn("신체상태", scene_val)
        version = self.db.execute(
            "SELECT name FROM schema_migration WHERE version = 48"
        ).fetchone()[0]
        self.assertEqual(version, "tracked_facts")

    def test_import_delimiter_config_column(self) -> None:
        migration = Path(__file__).resolve().parents[1] / "db" / "049_import_delimiter_config.sql"
        self.db.executescript(migration.read_text(encoding="utf-8"))
        cols = {
            str(row[1])
            for row in self.db.execute("PRAGMA table_info(project)").fetchall()
        }
        self.assertIn("import_delimiter_config", cols)
        self.db.execute(
            "UPDATE project SET import_delimiter_config = ? WHERE id = 1",
            ('{"presets":["blank"],"blank_line_threshold":2}',),
        )
        value = self.db.execute(
            "SELECT import_delimiter_config FROM project WHERE id = 1"
        ).fetchone()[0]
        self.assertIn("blank", value)
        version = self.db.execute(
            "SELECT name FROM schema_migration WHERE version = 49"
        ).fetchone()[0]
        self.assertEqual(version, "import_delimiter_config")

    def test_character_tori_analysis_table(self) -> None:
        self.create_character()
        migration = Path(__file__).resolve().parents[1] / "db" / "050_character_tori_analysis.sql"
        self.db.executescript(migration.read_text(encoding="utf-8"))
        tables = {
            str(row[0])
            for row in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        self.assertIn("character_tori_analysis", tables)
        self.db.execute(
            "INSERT INTO character_tori_analysis(character_id, field_name, analyzed_content) "
            "VALUES (40, 'profile_md', '새 분석')"
        )
        value = self.db.execute(
            "SELECT analyzed_content FROM character_tori_analysis WHERE character_id = 40"
        ).fetchone()[0]
        self.assertEqual(value, "새 분석")
        version = self.db.execute(
            "SELECT name FROM schema_migration WHERE version = 50"
        ).fetchone()[0]
        self.assertEqual(version, "character_tori_analysis")

    def test_world_tori_analysis_table(self) -> None:
        migration = Path(__file__).resolve().parents[1] / "db" / "051_world_tori_analysis.sql"
        self.db.executescript(migration.read_text(encoding="utf-8"))
        tables = {
            str(row[0])
            for row in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        self.assertIn("world_tori_analysis", tables)
        self.db.execute(
            "INSERT INTO world_tori_analysis(project_id, section_name, field_name, analyzed_content) "
            "VALUES (1, 'where_when', 'era', '새 분석')"
        )
        value = self.db.execute(
            "SELECT analyzed_content FROM world_tori_analysis WHERE project_id = 1 AND field_name = 'era'"
        ).fetchone()[0]
        self.assertEqual(value, "새 분석")
        self.assert_integrity_error(
            "INSERT INTO world_tori_analysis(project_id, section_name, field_name, analyzed_content) "
            "VALUES (1, 'where_when', 'era', '중복')"
        )
        version = self.db.execute(
            "SELECT name FROM schema_migration WHERE version = 51"
        ).fetchone()[0]
        self.assertEqual(version, "world_tori_analysis")

    def test_reader_debate_tables(self) -> None:
        migration = Path(__file__).resolve().parents[1] / "db" / "052_reader_debate.sql"
        self.db.executescript(migration.read_text(encoding="utf-8"))
        tables = {
            str(row[0])
            for row in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        self.assertIn("reader_debate_sessions", tables)
        self.assertIn("reader_debate_messages", tables)
        stamp = "2026-01-01T00:00:00.000000Z"
        self.db.execute(
            "INSERT INTO reader_debate_sessions"
            "(id, work_id, persona_ids_key, persona_order_json, created_at, updated_at) "
            "VALUES ('s1', '1', 'a,b,c', '[\"c\",\"a\",\"b\"]', ?, ?)",
            (stamp, stamp),
        )
        self.db.execute(
            "INSERT INTO reader_debate_messages"
            "(id, session_id, round_number, speaker_type, persona_id, message, turn_order, created_at) "
            "VALUES ('m1', 's1', 1, 'user', NULL, '질문', 0, ?)",
            (stamp,),
        )
        self.assert_integrity_error(
            "INSERT INTO reader_debate_sessions"
            "(id, work_id, persona_ids_key, persona_order_json, created_at, updated_at) "
            "VALUES ('s2', '1', 'a,b,c', '[\"a\",\"b\",\"c\"]', ?, ?)",
            (stamp, stamp),
        )
        self.assert_integrity_error(
            "INSERT INTO reader_debate_messages"
            "(id, session_id, round_number, speaker_type, persona_id, message, turn_order, created_at) "
            "VALUES ('m2', 's1', 1, 'user', 'x', 'bad', 1, ?)",
            (stamp,),
        )
        self.db.execute("DELETE FROM reader_debate_sessions WHERE id = 's1'")
        leftover = self.db.execute(
            "SELECT COUNT(*) FROM reader_debate_messages WHERE session_id = 's1'"
        ).fetchone()[0]
        self.assertEqual(leftover, 0)
        version = self.db.execute(
            "SELECT name FROM schema_migration WHERE version = 52"
        ).fetchone()[0]
        self.assertEqual(version, "reader_debate")

    def test_recompute_highlight_episode_order_migration(self) -> None:
        import importlib.util

        path = (
            Path(__file__).resolve().parents[1]
            / "db"
            / "053_recompute_highlight_episode_order.py"
        )
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        module.apply(self.db)
        version = self.db.execute(
            "SELECT name FROM schema_migration WHERE version = 53"
        ).fetchone()[0]
        self.assertEqual(version, "recompute_highlight_episode_order")


if __name__ == "__main__":
    unittest.main()
