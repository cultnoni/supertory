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

    def test_dev_electron_uses_repo_data_dir(self) -> None:
        self.assertIn("const isDev = !app.isPackaged;", self.main)
        self.assertIn("function userDataDir()", self.main)
        self.assertIn("function userProjectsDir()", self.main)
        self.assertIn('path.join(projectRoot(), "data")', self.main)
        self.assertIn('path.join(projectRoot(), "projects")', self.main)
        self.assertIn('path.join(app.getPath("userData"), "data")', self.main)
        self.assertIn('path.join(app.getPath("userData"), "projects")', self.main)
        data_fn = self.main.split("function userDataDir()", 1)[1].split(
            "function userProjectsDir()", 1
        )[0]
        self.assertIn("if (isDev)", data_fn)
        self.assertIn('path.join(projectRoot(), "data")', data_fn)
        projects_fn = self.main.split("function userProjectsDir()", 1)[1].split(
            "function resolveBackendLaunch()", 1
        )[0]
        self.assertIn("if (isDev)", projects_fn)
        self.assertIn('path.join(projectRoot(), "projects")', projects_fn)

    def test_backend_crash_log_wired_without_changing_exit_dialog(self) -> None:
        self.assertIn('BACKEND_CRASH_LOG_NAME = "backend_crash.log"', self.main)
        self.assertIn("function backendCrashLogPath()", self.main)
        self.assertIn("function beginBackendCrashLog(launch)", self.main)
        self.assertIn("rememberBackendLogChunk", self.main)
        self.assertIn("----- last captured stdout/stderr -----", self.main)
        self.assertIn("exit code:", self.main)
        self.assertIn('stdio: ["ignore", "pipe", "pipe"]', self.main)
        self.assertIn(
            '`alert("백엔드 서버가 종료되었습니다. 앱을 다시 시작해 주세요. (code=${code})")`',
            self.main,
        )
        spawn_block = self.main.split("function startBackendServer()", 1)[1].split(
            "function stopBackendServer()", 1
        )[0]
        self.assertIn("beginBackendCrashLog(launch)", spawn_block)
        self.assertIn("appendBackendCrashLog(rememberBackendLogChunk(chunk))", spawn_block)
        self.assertIn("backendProcess.on(\"exit\"", spawn_block)
        self.assertIn("isQuitting: ${isQuitting}", spawn_block)

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
