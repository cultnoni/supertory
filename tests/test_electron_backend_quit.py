"""Electron backend quit sequencing contracts.

Live Electron click-to-close is documented in README.md (앱 닫기).
This module checks main.js wiring and runs the Node helper tests.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_JS = ROOT / "electron" / "main.js"
STOP_JS = ROOT / "electron" / "backend-stop.js"
NODE_TEST = ROOT / "tests" / "test_electron_backend_stop.js"


class ElectronBackendQuitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.main = MAIN_JS.read_text(encoding="utf-8")

    def test_quit_hooks_wait_for_backend_stop(self) -> None:
        self.assertIn('app.on("before-quit"', self.main)
        self.assertIn("event.preventDefault()", self.main)
        self.assertIn("backendQuitFinished", self.main)
        self.assertIn("quitAfterBackendStops", self.main)
        self.assertIn('app.on("window-all-closed"', self.main)
        self.assertIn("BACKEND_STOP_TIMEOUT_MS", self.main)
        self.assertIn("postBackendQuit", self.main)
        self.assertIn("TASKKILL_ATTEMPTS", self.main)
        stop = STOP_JS.read_text(encoding="utf-8")
        self.assertIn("/api/app/quit", stop)

    def test_taskkill_uses_process_tree_flag(self) -> None:
        stop = STOP_JS.read_text(encoding="utf-8")
        self.assertIn('["/pid"', stop)
        self.assertIn('"/t"', stop)
        self.assertIn("collectPidTree", stop)
        self.assertIn('once("close"', stop)

    def test_node_backend_stop_helpers(self) -> None:
        result = subprocess.run(
            ["node", str(NODE_TEST)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=result.stdout + "\n" + result.stderr,
        )


if __name__ == "__main__":
    raise SystemExit(unittest.main())
