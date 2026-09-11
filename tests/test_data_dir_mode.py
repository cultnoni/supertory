"""SUPERTORY_DB_MODE data-dir switch (app.py + launchers). Matches electron/main.js."""

from __future__ import annotations

import os
import sqlite3
import unittest
from pathlib import Path

import app

ROOT = Path(__file__).resolve().parents[1]


class WritableDataDirModeTests(unittest.TestCase):
    def test_explicit_data_dir_env_wins(self) -> None:
        path = app.resolve_writable_data_dir(
            environ={"SUPERTORY_DATA_DIR": r"C:\tmp\custom-data", "APPDATA": r"C:\Users\x\AppData\Roaming"},
            frozen=False,
            root=ROOT,
        )
        self.assertEqual(path, Path(r"C:\tmp\custom-data"))

    def test_isolated_uses_repo_data(self) -> None:
        path = app.resolve_writable_data_dir(
            environ={"SUPERTORY_DB_MODE": "isolated", "APPDATA": r"C:\Users\x\AppData\Roaming"},
            frozen=False,
            root=ROOT,
        )
        self.assertEqual(path, ROOT / "data")

    def test_shared_default_uses_appdata_supertory(self) -> None:
        appdata = r"C:\Users\x\AppData\Roaming"
        for mode in ("", "shared", "SHARED"):
            with self.subTest(mode=mode):
                env = {"APPDATA": appdata}
                if mode:
                    env["SUPERTORY_DB_MODE"] = mode
                path = app.resolve_writable_data_dir(environ=env, frozen=False, root=ROOT)
                self.assertEqual(path, Path(appdata) / "supertory" / "data")

    def test_frozen_keeps_executable_sidecar_data(self) -> None:
        path = app.resolve_writable_data_dir(
            environ={"APPDATA": r"C:\Users\x\AppData\Roaming", "SUPERTORY_DB_MODE": "isolated"},
            frozen=True,
            executable=Path(r"C:\Program Files\SuperTory\resources\supertory-server\supertory-server.exe"),
        )
        self.assertEqual(
            path,
            Path(r"C:\Program Files\SuperTory\resources\supertory-server\data"),
        )

    def test_start_bats_follow_the_same_switch(self) -> None:
        shared = (ROOT / "start_supertory.bat").read_text(encoding="utf-8")
        isolated = (ROOT / "start_supertory_isolated.bat").read_text(encoding="utf-8")
        self.assertNotIn("SUPERTORY_DB_MODE=shared", shared)
        self.assertNotIn("SUPERTORY_DB_MODE=isolated", shared)
        self.assertIn("app.py", shared)
        self.assertIn("SUPERTORY_DB_MODE=isolated", isolated)
        self.assertIn("start_supertory.bat", isolated)


class SharedAppDataDbPresenceTests(unittest.TestCase):
    def test_appdata_db_has_merged_project_count(self) -> None:
        appdata = os.environ.get("APPDATA", "").strip()
        if not appdata:
            self.skipTest("APPDATA not set")
        db_path = Path(appdata) / "supertory" / "data" / "supertory.sqlite3"
        if not db_path.is_file():
            self.skipTest("shared AppData DB missing")
        uri = db_path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            connection.execute("PRAGMA query_only = ON")
            count = connection.execute("SELECT COUNT(*) FROM project").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 44)
