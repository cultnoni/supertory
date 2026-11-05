"""The backend build must not embed paid AI keys unless explicitly allowed."""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def _load_build_backend():
    spec = importlib.util.spec_from_file_location("build_backend_under_test", ROOT / "scripts" / "build_backend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildBackendGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.build = _load_build_backend()
        self.build.ROOT = self.root
        self.build.BUNDLED_ENV_PY = self.root / "bundled_env.py"
        clean = {k: "" for k in self.build.BUNDLE_KEYS}
        clean[self.build.ALLOW_AI_KEYS_FLAG] = ""
        self.env = mock.patch.dict(os.environ, clean)
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _write_env(self, text: str) -> None:
        (self.root / ".env").write_text(text, encoding="utf-8")

    def test_ai_key_in_env_blocks_build(self) -> None:
        self._write_env("GEMINI_API_KEY=real-looking-key\nSUPABASE_URL=https://x.supabase.co\n")
        with self.assertRaises(self.build.BundledSecretsBlocked) as caught:
            self.build.write_bundled_env()
        self.assertIn("GEMINI_API_KEY", str(caught.exception))
        self.assertFalse(self.build.BUNDLED_ENV_PY.exists())

    def test_without_ai_keys_build_writes_public_values(self) -> None:
        self._write_env("GEMINI_MODEL=gemini-3.5-flash-lite\nSUPABASE_URL=https://x.supabase.co\nSUPABASE_ANON_KEY=anon\n")
        selected = self.build.write_bundled_env()
        self.assertEqual(set(selected), {"GEMINI_MODEL", "SUPABASE_URL", "SUPABASE_ANON_KEY"})
        self.assertTrue(self.build.BUNDLED_ENV_PY.exists())

    def test_explicit_flag_allows_local_test_build(self) -> None:
        self._write_env("ANTHROPIC_API_KEY=real-looking-key\n")
        with mock.patch.dict(os.environ, {self.build.ALLOW_AI_KEYS_FLAG: "1"}):
            selected = self.build.write_bundled_env()
        self.assertIn("ANTHROPIC_API_KEY", selected)


if __name__ == "__main__":
    unittest.main()
