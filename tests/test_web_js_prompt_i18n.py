"""프런트 JS의 템플릿 문자열 안에 `i18n.t(...)` 코드가 글자 그대로 남지 않게 막는다.

2026-08 번역 이관 때 AI 프롬프트 속 한국어 낱말이 `i18n.t('app.…')`로 바뀌었는데,
템플릿 문자열의 `${}` 바깥이라 번역되지 않고 코드 모양 그대로 AI에 전송됐다
(이어쓰기·요약·설정 붕괴 검사 등 44곳, 112개). 같은 일이 다시 생기지 않게
JS 구문 분석기로 템플릿 문자열의 글자 부분만 검사한다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACORN = ROOT / "node_modules" / "acorn"
WEB_FILES = [
    "web/app.js",
    "web/app-boot.js",
    "web/feedback_panel.js",
    "web/pagewrite_model.js",
    "web/smart-punctuation.js",
    "web/tory-check.js",
    "web/typeset_metrics.js",
    "web/js/gitsi.js",
]

# 템플릿 문자열의 글자 부분(TemplateElement)에서 `i18n.t(`가 있는 줄 번호를 찾는다.
_SCRIPT = r"""
const acorn = require(process.argv[1]);
const payload = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const hits = [];
function walk(node, name) {
  if (!node || typeof node.type !== 'string') return;
  if (node.type === 'TemplateElement' && node.value.raw.includes('i18n.t(')) {
    hits.push({ file: name, line: node.loc.start.line });
  }
  for (const key of Object.keys(node)) {
    if (key === 'type' || key === 'loc') continue;
    const value = node[key];
    if (Array.isArray(value)) value.forEach((c) => c && typeof c.type === 'string' && walk(c, name));
    else if (value && typeof value.type === 'string') walk(value, name);
  }
}
for (const item of payload) {
  const ast = acorn.parse(item.source, {
    ecmaVersion: 'latest', sourceType: 'script', locations: true,
    allowAwaitOutsideFunction: true, allowReturnOutsideFunction: true,
  });
  walk(ast, item.name);
}
process.stdout.write(JSON.stringify(hits));
"""


def _find_hits(sources: list[tuple[str, str]]) -> list[dict]:
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        raise unittest.SkipTest("node 없음 — Node.js가 필요합니다")
    if not ACORN.exists():
        raise unittest.SkipTest("node_modules/acorn 없음 — npm install이 필요합니다")
    payload = json.dumps([{"name": n, "source": s} for n, s in sources])
    result = subprocess.run(
        [node, "-e", _SCRIPT, str(ACORN)],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"구문 분석 실패:\n{result.stderr}")
    return json.loads(result.stdout or "[]")


class PromptI18nLeakTests(unittest.TestCase):
    def test_web_scripts_have_no_i18n_call_text_in_templates(self) -> None:
        sources = [
            (rel, (ROOT / rel).read_text(encoding="utf-8"))
            for rel in WEB_FILES
            if (ROOT / rel).exists()
        ]
        hits = _find_hits(sources)
        self.assertEqual(
            hits,
            [],
            "템플릿 문자열 안에 i18n.t(...) 코드가 글자 그대로 있습니다. "
            "프롬프트라면 한국어 원문으로, 화면 문구라면 ${i18n.t(...)}로 바꾸세요: "
            f"{hits[:10]}",
        )

    def test_checker_detects_a_leak_and_ignores_real_calls(self) -> None:
        """검사기가 실제로 동작하는지(오염은 잡고, 정상 호출은 통과시키는지)."""
        leaked = "const p = `다시 i18n.t('app.이어서_쓰기')를 누른다`;\n"
        fine = "const p = `다시 ${i18n.t('app.이어서_쓰기')}를 누른다`;\n"
        self.assertEqual(
            [h["line"] for h in _find_hits([("leak.js", leaked)])],
            [1],
        )
        self.assertEqual(_find_hits([("fine.js", fine)]), [])


if __name__ == "__main__":
    unittest.main()
