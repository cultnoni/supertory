"""`python app.py` must not load app.py a second time as a separate module.

Sibling modules (feedback_api, routes/*, db/053_*) reach back with `import app`
inside their functions. Without the alias in app.py that import would re-execute
the file, giving the request handler and the route modules two copies of
DATA_DIR / DATABASE_PATH and of the in-memory run bookkeeping.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The real entrypoint tail; cut it so the probe never starts a server.
ENTRYPOINT_MARKER = '\nif __name__ == "__main__":\n    if _is_frozen():'

PROBE = textwrap.dedent(
    """
    import sys, types
    from pathlib import Path

    root = Path(sys.argv[1])
    sys.path.insert(0, str(root))
    source = (root / "app.py").read_text(encoding="utf-8")
    body = source.split(sys.argv[2])[0]

    main_module = types.ModuleType("__main__")
    main_module.__file__ = str(root / "app.py")
    sys.modules["__main__"] = main_module
    exec(compile(body, str(root / "app.py"), "exec"), main_module.__dict__)

    import app

    print("SAME" if app is main_module else "DIFFERENT")
    """
)


class AppModuleAliasTests(unittest.TestCase):
    def test_entrypoint_tail_marker_still_matches(self) -> None:
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertEqual(source.count(ENTRYPOINT_MARKER), 1)

    def test_import_app_reuses_the_main_module(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", PROBE, str(ROOT), ENTRYPOINT_MARKER],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=ROOT,
            timeout=180,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SAME", result.stdout)


if __name__ == "__main__":
    unittest.main()
