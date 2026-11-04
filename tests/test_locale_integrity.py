"""Locale files stay usable: no duplicate keys, same keys in ko/en/es,
every key the UI asks for exists, and ${...} names match across languages.

A missing key renders as the raw key (e.g. "app.서랍_크기_조절") even for
Korean users, and a ${...} name that differs from ko is never filled in.
"""

from __future__ import annotations

import json
import re
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
LANGS = ("ko", "en", "es")
PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")


def load_pairs(lang: str) -> list[tuple[str, object]]:
    text = (WEB / "locales" / f"{lang}.json").read_text(encoding="utf-8")
    return json.loads(text, object_pairs_hook=lambda pairs: pairs)


class LocaleIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pairs = {lang: load_pairs(lang) for lang in LANGS}
        cls.locales = {lang: dict(pairs) for lang, pairs in cls.pairs.items()}

    def test_no_duplicate_keys(self) -> None:
        for lang, pairs in self.pairs.items():
            dups = [k for k, n in Counter(k for k, _ in pairs).items() if n > 1]
            self.assertEqual(dups, [], f"{lang}.json 중복 키")

    def test_same_keys_in_every_language(self) -> None:
        ko = set(self.locales["ko"])
        for lang in ("en", "es"):
            other = set(self.locales[lang])
            self.assertEqual(sorted(ko - other), [], f"{lang}.json에 없는 키")
            self.assertEqual(sorted(other - ko), [], f"ko.json에 없는 키({lang}에만 있음)")

    def test_keys_used_by_the_ui_exist(self) -> None:
        used: set[str] = set()
        html = (WEB / "index.html").read_text(encoding="utf-8")
        used.update(re.findall(r'data-i18n(?:-html|-placeholder|-title|-aria-label|-label)?="([^"]+)"', html))
        call = re.compile(r"""i18n\.t\(\s*(['"])([^'"]+)\1""")
        for path in [*WEB.glob("*.js"), *(WEB / "js").glob("*.js")]:
            used.update(m.group(2) for m in call.finditer(path.read_text(encoding="utf-8", errors="replace")))
        missing = sorted(k for k in used if k not in self.locales["ko"])
        self.assertEqual(missing, [], "화면이 쓰는데 언어 파일에 없는 키")

    def test_placeholder_names_match_ko(self) -> None:
        ko = self.locales["ko"]
        bad = []
        for lang in ("en", "es"):
            for key, value in self.locales[lang].items():
                if key not in ko:
                    continue
                want = set(PLACEHOLDER.findall(str(ko[key])))
                got = set(PLACEHOLDER.findall(str(value)))
                if want != got:
                    bad.append((lang, key, sorted(got - want)))
        self.assertEqual(bad, [], "자리표시자 이름이 ko와 다르면 값이 채워지지 않는다")


if __name__ == "__main__":
    unittest.main()
