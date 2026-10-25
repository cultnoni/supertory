"""조판(쪽쓰기) 서식 보존: 모듈 배선과 번역 키 점검. 변환 동작 자체는 브라우저 DOM이 필요해 화면에서 확인한다."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PageWriteModelWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        cls.app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        cls.model_js = (ROOT / "web" / "pagewrite_model.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    def test_model_loaded_before_app(self) -> None:
        self.assertIn('src="/pagewrite_model.js', self.html)
        self.assertLess(
            self.html.find('src="/pagewrite_model.js'),
            self.html.find('src="/app.js'),
        )

    def test_model_exports_used_by_app(self) -> None:
        for name in (
            "parseEditor",
            "serialize",
            "renderRuns",
            "readPageRuns",
            "splitRuns",
            "deleteRange",
            "roundTripCheck",
        ):
            self.assertIn(f"function {name}", self.model_js)
            self.assertIn(name, self.model_js.split("global.PageWriteModel = {", 1)[1])
        self.assertIn("roundTripCheck", self.app_js)
        self.assertIn("serialize(pageWrite.runs", self.app_js)

    def test_unsafe_gate_uses_round_trip_not_tag_scan(self) -> None:
        gate = self.app_js.split("function manuscriptHasPageWriteUnsafeMarkup", 1)[1][:500]
        self.assertIn("roundTripCheck", gate)
        self.assertNotIn('el.tagName !== "BR"', gate)

    def test_pages_do_not_flatten_manuscript_to_plain_text(self) -> None:
        sync = self.app_js.split("function syncPageWriteToManuscript", 1)[1][:400]
        self.assertNotIn("escapeHtml(text)", sync)
        self.assertIn("page-write-embed", self.css)

    def test_paste_into_pages_is_plain_text(self) -> None:
        setup = self.app_js.split("function setupPageWrite", 1)[1][:2500]
        self.assertIn('addEventListener("paste"', setup)
        self.assertIn("text/plain", setup)

    def test_locale_keys_in_sync(self) -> None:
        keys = (
            "index.쪽쓰기_서식있는_회차는_열_수_없습니다",
            "index.쪽쓰기_삽입물_이미지",
            "index.쪽쓰기_삽입물_각주",
            "index.쪽쓰기_삽입물_작가의_말",
            "index.쪽쓰기_삽입물_기타",
        )
        for lang in ("ko", "en", "es"):
            locale = json.loads((ROOT / "web" / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
            for key in keys:
                self.assertTrue(locale.get(key), f"{lang}:{key}")
                self.assertNotRegex(
                    locale[key] if lang != "ko" else "", r"[가-힣]", f"{lang}:{key} 에 한글이 남음"
                )


if __name__ == "__main__":
    unittest.main()
