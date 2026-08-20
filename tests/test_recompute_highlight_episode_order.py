"""Tests for scripts/recompute_highlight_episode_order.py."""

from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

import app

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "recompute_highlight_episode_order.py"
_SPEC = importlib.util.spec_from_file_location(
    "recompute_highlight_episode_order", SCRIPT_PATH
)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
import sys

sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)


class RecomputeHighlightEpisodeOrderTests(unittest.TestCase):
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

    def test_plan_and_apply_updates_episode_order(self) -> None:
        with app.database() as conn:
            conn.row_factory = sqlite3.Row
            pid = int(
                conn.execute(
                    "INSERT INTO project(title) VALUES ('화수마이그레이션')"
                ).lastrowid
            )
            chapter_a = int(
                conn.execute(
                    "INSERT INTO chapter(project_id, title, sort_order) "
                    "VALUES (?, '추가확인', 0)",
                    (pid,),
                ).lastrowid
            )
            chapter_b = int(
                conn.execute(
                    "INSERT INTO chapter(project_id, title, sort_order) "
                    "VALUES (?, '1부', 1)",
                    (pid,),
                ).lastrowid
            )
            extra_id = int(
                conn.execute(
                    "INSERT INTO scene(project_id, chapter_id, title, sort_order) "
                    "VALUES (?, ?, '추가확인 회차', 0)",
                    (pid, chapter_a),
                ).lastrowid
            )
            part_id = int(
                conn.execute(
                    "INSERT INTO scene(project_id, chapter_id, title, sort_order) "
                    "VALUES (?, ?, '1부 회차', 0)",
                    (pid, chapter_b),
                ).lastrowid
            )
            for sid in (extra_id, part_id):
                conn.execute(
                    "INSERT INTO scene_revision"
                    "(scene_id, revision_no, content_md, is_current) "
                    "VALUES (?, 1, '<p>본문</p>', 1)",
                    (sid,),
                )
            # Stored orders follow stale chapter.sort_order: extra=1, part=2.
            conn.execute(
                "INSERT INTO glump_highlight_moments"
                "(id, work_id, episode_id, episode_order, moment_type, "
                "excerpt, reason, created_at) VALUES "
                "('m-extra', ?, ?, 1, 'dialogue', 'a', 'b', "
                "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')), "
                "('m-part', ?, ?, 2, 'scene', 'c', 'd', "
                "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')), "
                "('m-same', ?, ?, 2, 'description', 'e', 'f', "
                "strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                (
                    str(pid),
                    str(extra_id),
                    str(pid),
                    str(part_id),
                    str(pid),
                    str(extra_id),
                ),
            )
            root = int(
                conn.execute(
                    "INSERT INTO folder"
                    "(project_id, parent_id, title, is_box, sort_order, "
                    "source_kind, source_id) "
                    "VALUES (?, NULL, '1권', 1, 0, 'part', 1)",
                    (pid,),
                ).lastrowid
            )
            part_folder = int(
                conn.execute(
                    "INSERT INTO folder"
                    "(project_id, parent_id, title, is_box, sort_order, "
                    "source_kind, source_id) "
                    "VALUES (?, ?, '1부', 0, 0, 'chapter', ?)",
                    (pid, root, chapter_b),
                ).lastrowid
            )
            extra_folder = int(
                conn.execute(
                    "INSERT INTO folder"
                    "(project_id, parent_id, title, is_box, sort_order, "
                    "source_kind, source_id) "
                    "VALUES (?, ?, '추가확인', 0, 1, 'chapter', ?)",
                    (pid, root, chapter_a),
                ).lastrowid
            )
            conn.execute(
                "UPDATE scene SET folder_id = ? WHERE id = ?",
                (part_folder, part_id),
            )
            conn.execute(
                "UPDATE scene SET folder_id = ? WHERE id = ?",
                (extra_folder, extra_id),
            )

            changes, total = _MODULE.plan_episode_order_updates(conn)
            self.assertEqual(total, 3)
            moved = {item.moment_id: item for item in changes}
            self.assertIn("m-extra", moved)
            self.assertEqual(moved["m-extra"].old_order, 1)
            self.assertEqual(moved["m-extra"].new_order, 2)
            self.assertIn("m-part", moved)
            self.assertEqual(moved["m-part"].old_order, 2)
            self.assertEqual(moved["m-part"].new_order, 1)
            self.assertNotIn("m-same", moved)

            log = _MODULE.format_change_log(
                changes,
                total_rows=total,
                applied=False,
                database_path="test.db",
            )
            self.assertIn("mode: dry-run", log)
            self.assertIn("rows_changed: 2", log)
            self.assertIn("id=m-extra", log)
            self.assertIn("episode_order 1 -> 2", log)

            _MODULE.apply_episode_order_updates(conn, changes)
            rows = {
                row["id"]: int(row["episode_order"])
                for row in conn.execute(
                    "SELECT id, episode_order FROM glump_highlight_moments"
                )
            }
            self.assertEqual(rows["m-extra"], 2)
            self.assertEqual(rows["m-part"], 1)
            self.assertEqual(rows["m-same"], 2)


if __name__ == "__main__":
    unittest.main()
