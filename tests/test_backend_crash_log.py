"""Investigation-only backend crash log (sys.excepthook). Does not change crash behavior."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import app


class BackendCrashLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"

    def tearDown(self) -> None:
        app.DATA_DIR = self.original_data_dir
        self.temporary_directory.cleanup()

    def test_force_unbuffered_stdio_does_not_raise(self) -> None:
        app._force_unbuffered_stdio()

    def test_append_writes_under_data_dir(self) -> None:
        app._append_backend_crash_log("hello crash")
        path = app.backend_crash_log_path()
        self.assertEqual(path, app.DATA_DIR / "backend_crash.log")
        self.assertTrue(path.is_file())
        self.assertIn("hello crash", path.read_text(encoding="utf-8"))

    def test_record_uncaught_exception_writes_traceback(self) -> None:
        try:
            raise RuntimeError("idle-crash-probe")
        except RuntimeError as error:
            app._record_uncaught_exception(
                type(error), error, error.__traceback__, origin="sys.excepthook"
            )
        text = app.backend_crash_log_path().read_text(encoding="utf-8")
        self.assertIn("PYTHON UNCAUGHT", text)
        self.assertIn("origin=sys.excepthook", text)
        self.assertIn("idle-crash-probe", text)
        self.assertIn("RuntimeError", text)

    def test_excepthook_logs_then_calls_original(self) -> None:
        try:
            raise ValueError("must-still-propagate")
        except ValueError as error:
            with mock.patch.object(app.sys, "__excepthook__") as original:
                app.sys.excepthook(type(error), error, error.__traceback__)
        original.assert_called_once()
        text = app.backend_crash_log_path().read_text(encoding="utf-8")
        self.assertIn("must-still-propagate", text)

    def test_thread_excepthook_logs_without_replacing_behavior(self) -> None:
        self.assertTrue(hasattr(threading, "excepthook"))
        try:
            raise OSError("thread-idle-crash")
        except OSError as error:
            args = threading.ExceptHookArgs(
                (type(error), error, error.__traceback__, threading.current_thread())
            )
            from io import StringIO
            from contextlib import redirect_stderr

            with redirect_stderr(StringIO()):
                threading.excepthook(args)
        text = app.backend_crash_log_path().read_text(encoding="utf-8")
        self.assertIn("thread-idle-crash", text)
        self.assertIn("threading.excepthook", text)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
