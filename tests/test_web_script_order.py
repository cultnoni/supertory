"""index.html script order must match scripts/web_js_globals.mjs lintFiles."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "web" / "index.html"
GLOBALS_MJS = ROOT / "scripts" / "web_js_globals.mjs"

SCRIPT_SRC_RE = re.compile(
    r"""<script\s+[^>]*src=["'](/[^"']+)["']""",
    re.IGNORECASE,
)
LINT_FILES_RE = re.compile(
    r"export\s+const\s+lintFiles\s*=\s*\[([^\]]+)\]",
    re.DOTALL,
)


def _lint_files() -> list[str]:
    text = GLOBALS_MJS.read_text(encoding="utf-8")
    match = LINT_FILES_RE.search(text)
    if match is None:
        raise AssertionError("lintFiles array not found in web_js_globals.mjs")
    return [
        item.strip().strip("'\"")
        for item in match.group(1).split(",")
        if item.strip().strip("'\"")
    ]


def _index_app_scripts() -> list[str]:
    """Return web-relative paths for lint-relevant scripts in document order."""
    html = INDEX.read_text(encoding="utf-8")
    out: list[str] = []
    for match in SCRIPT_SRC_RE.finditer(html):
        src = match.group(1).split("?", 1)[0]
        if src.startswith("/vendor/"):
            continue
        # Map URL path to lintFiles style (web/...).
        rel = "web" + src
        out.append(rel)
    return out


class WebScriptOrderTests(unittest.TestCase):
    def test_lint_files_match_index_script_order(self) -> None:
        lint = _lint_files()
        index = _index_app_scripts()
        # lintFiles is a subsequence of index scripts (index may load extras).
        filtered = [path for path in index if path in lint]
        self.assertEqual(
            lint,
            filtered,
            "lintFiles order must match index.html script order for those files",
        )


if __name__ == "__main__":
    unittest.main()
