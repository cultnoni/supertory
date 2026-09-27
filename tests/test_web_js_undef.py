"""프론트엔드 JS no-undef (ESLint) — 정의되지 않은 식별자 참조를 막는다."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "web" / "app.js"
LINT_FILES = [
    "web/typeset_metrics.js",
    "web/tory-check.js",
    "web/smart-punctuation.js",
    "web/app.js",
    "web/feedback_panel.js",
]


def _npx() -> str:
    found = shutil.which("npx") or shutil.which("npx.cmd")
    if not found:
        raise unittest.SkipTest("npx 없음 — Node.js/npm이 필요합니다")
    return found


def _run_eslint(paths: list[str], *, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # npm이 주입하는 비표준 설정 경고를 테스트 출력에서 줄인다.
    env.pop("npm_config_devdir", None)
    return subprocess.run(
        [_npx(), "eslint", *paths, "-f", "stylish"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )


class WebJsUndefLintTests(unittest.TestCase):
    def test_web_scripts_have_no_undef(self) -> None:
        result = _run_eslint(LINT_FILES)
        if result.returncode != 0:
            self.fail(
                "ESLint no-undef 실패 (정의되지 않은 식별자):\n"
                f"{result.stdout}\n{result.stderr}"
            )

    def test_missing_is_markup_feedback_mode_is_caught(self) -> None:
        """isMarkupFeedbackMode 삭제가 도우미 클릭을 깨뜨리던 회귀를 감시한다."""
        original = APP_JS.read_text(encoding="utf-8")
        marker = "function isMarkupFeedbackMode(value) {"
        self.assertIn(marker, original)
        # 정의를 주석 처리해도 호출부는 남긴다 → no-undef 실패여야 한다.
        sabotaged = original.replace(
            marker,
            "function __removed_isMarkupFeedbackMode(value) {",
            1,
        )
        self.assertNotEqual(sabotaged, original)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            dest = Path(tmp) / "web"
            dest.mkdir()
            # ESLint flat config는 저장소 루트의 설정을 쓰므로, 파일만 바꿔서
            # 동일 경로에 덮어쓴 뒤 복원한다(원자적 교체).
            backup = APP_JS.read_bytes()
            try:
                APP_JS.write_text(sabotaged, encoding="utf-8")
                result = _run_eslint(["web/app.js"])
            finally:
                APP_JS.write_bytes(backup)
        self.assertNotEqual(
            result.returncode,
            0,
            "isMarkupFeedbackMode 정의를 제거해도 ESLint가 통과하면 안 됩니다.\n"
            f"{result.stdout}\n{result.stderr}",
        )
        self.assertIn(
            "isMarkupFeedbackMode",
            result.stdout + result.stderr,
        )


if __name__ == "__main__":
    unittest.main()
