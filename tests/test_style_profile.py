"""「내 문체」 프로필: 정규화·프롬프트 블록·자동 제안·HTTP·AI 요청 연결 (임시 DB).

설계: docs/design-ai-context-and-style-2026-11.md §3.
"""

from __future__ import annotations

import json
import re
import tempfile
import threading
import unittest
from pathlib import Path

import app
import style_profile

ROOT = Path(__file__).resolve().parents[1]


class NormalizeTests(unittest.TestCase):
    def test_unknown_and_out_of_range_values_are_cleaned(self) -> None:
        profile = style_profile.normalize_profile({
            "preset": "nope",
            "tempo": 9,
            "humor": -3,
            "dialogue_ratio": "55",
            "sentence_length": "huge",
            "preferred_words": "피식, 묵묵히, 피식,  ",
            "sample_text": "가" * 900,
            "extra": "버림",
        })
        self.assertEqual(profile["preset"], "")
        self.assertEqual(profile["tempo"], 5)
        self.assertEqual(profile["humor"], 0)
        self.assertEqual(profile["dialogue_ratio"], 55)
        self.assertIsNone(profile["sentence_length"])
        self.assertEqual(profile["preferred_words"], ["피식", "묵묵히"])
        self.assertEqual(len(profile["sample_text"]), 800)
        self.assertNotIn("extra", profile)

    def test_bad_json_is_empty(self) -> None:
        self.assertEqual(style_profile.normalize_profile("{깨짐"), style_profile.empty_profile())

    def test_choosing_preset_fills_its_defaults_but_keeps_explicit_values(self) -> None:
        merged = style_profile.merge_profile({}, {"preset": "speed"})
        self.assertEqual(merged["tempo"], 5)
        self.assertEqual(merged["sentence_length"], "short")
        merged = style_profile.merge_profile({}, {"preset": "speed", "tempo": 2})
        self.assertEqual(merged["tempo"], 2)

    def test_switching_to_custom_keeps_existing_dials(self) -> None:
        current = style_profile.merge_profile({}, {"preset": "speed"})
        merged = style_profile.merge_profile(current, {"preset": "custom", "humor": 4})
        self.assertEqual(merged["preset"], "custom")
        self.assertEqual(merged["tempo"], 5)
        self.assertEqual(merged["sentence_length"], "short")
        self.assertEqual(merged["humor"], 4)

    def test_patch_keeps_other_fields(self) -> None:
        current = style_profile.merge_profile({}, {"preset": "comic", "habits_md": "짧게 끊기"})
        merged = style_profile.merge_profile(current, {"humor": 3})
        self.assertEqual(merged["habits_md"], "짧게 끊기")
        self.assertEqual(merged["preset"], "comic")
        self.assertEqual(merged["humor"], 3)


class PromptBlockTests(unittest.TestCase):
    def test_empty_profile_gives_no_block(self) -> None:
        self.assertEqual(style_profile.build_prompt_block({}), "")

    def test_block_has_priority_rule_and_values(self) -> None:
        block = style_profile.build_prompt_block(
            {"preset": "hardboiled", "preferred_words": ["담배"], "sample_text": "그는 웃지 않았다."},
            viewpoint_person="first",
            viewpoint_tense="past",
            forbidden_words=["갑자기"],
        )
        self.assertTrue(block.startswith("[작가 문체 프로필]"))
        self.assertIn("이번 요청 > 이 문체 프로필 > 장르 기준", block)
        self.assertIn("건조한 하드보일드", block)
        self.assertIn("1인칭 · 과거형", block)
        self.assertIn("문장 길이: 짧게", block)
        self.assertIn("즐겨 쓰는 표현: 담배", block)
        self.assertIn("쓰지 않는 표현(본문에 넣지 말 것): 갑자기", block)
        self.assertIn("그는 웃지 않았다.", block)
        self.assertIn("내용·설정은 가져오지 말고", block)

    def test_off_preset_still_sends_words_and_viewpoint_but_not_dials(self) -> None:
        block = style_profile.build_prompt_block(
            {"preset": "", "tempo": 5, "habits_md": "문단 끝은 짧게"},
            viewpoint_person="third",
        )
        self.assertIn("3인칭", block)
        self.assertIn("문단 끝은 짧게", block)
        self.assertNotIn("전개 속도", block)

    def test_block_is_capped(self) -> None:
        block = style_profile.build_prompt_block(
            {"preset": "custom", "habits_md": "가" * 2000, "sample_text": "나" * 800,
             "preferred_words": [f"표현{i}" for i in range(30)]},
            forbidden_words=[f"금칙{i}" for i in range(30)],
        )
        self.assertLessEqual(len(block), style_profile.PROMPT_BLOCK_MAX_CHARS + 3)


class SuggestTests(unittest.TestCase):
    def test_short_manuscript_is_not_enough(self) -> None:
        result = style_profile.suggest_from_texts(["짧은 글."])
        self.assertFalse(result["enough"])

    def test_counts_sentence_length_and_dialogue(self) -> None:
        text = ("“가자.” 그가 말했다. 비가 왔다. 길은 젖어 있었다. " * 60)
        result = style_profile.suggest_from_texts([text])
        self.assertTrue(result["enough"])
        self.assertEqual(result["values"]["sentence_length"], "short")
        self.assertGreaterEqual(result["values"]["tempo"], 4)
        self.assertGreater(result["values"]["dialogue_ratio"], 0)
        self.assertLessEqual(result["values"]["dialogue_ratio"], 30)


class StyleProfileApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        status, project = self.request("POST", "/api/projects", {"title": "문체 검증", "main_genre": "판타지"})
        self.assertEqual(status, 201, project)
        self.project_id = int(project["id"])

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=30)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        return response.status, (json.loads(raw) if raw else {})

    def test_get_save_and_reload(self) -> None:
        status, data = self.request("GET", f"/api/projects/{self.project_id}/style-profile")
        self.assertEqual(status, 200, data)
        self.assertEqual(data["profile"]["preset"], "")
        self.assertIn("speed", data["presets"])

        status, data = self.request(
            "POST", f"/api/projects/{self.project_id}/style-profile",
            {"preset": "literary", "habits_md": "문단 끝은 짧게"},
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["profile"]["description_density"], 5)

        status, data = self.request(
            "POST", f"/api/projects/{self.project_id}/style-profile", {"humor": 4},
        )
        self.assertEqual(data["profile"]["preset"], "literary")
        self.assertEqual(data["profile"]["habits_md"], "문단 끝은 짧게")
        self.assertEqual(data["profile"]["humor"], 4)

    def test_unknown_project_is_error(self) -> None:
        status, _ = self.request("GET", "/api/projects/99999/style-profile")
        self.assertGreaterEqual(status, 400)

    def test_suggest_without_text_is_not_enough(self) -> None:
        status, data = self.request("GET", f"/api/projects/{self.project_id}/style-profile/suggest")
        self.assertEqual(status, 200, data)
        self.assertFalse(data["enough"])

    def test_ai_request_gets_block_only_in_writing_modes(self) -> None:
        self.request("POST", f"/api/projects/{self.project_id}/style-profile", {"preset": "comic"})
        status, _ = self.request(
            "PUT", f"/api/projects/{self.project_id}/tory-check",
            {"viewpoint_person": "first", "viewpoint_tense": "present", "forbidden_words": ["갑자기"]},
        )
        self.assertEqual(status, 200)
        build = app.SuperToryHandler._style_profile_system_prompt
        block = build("continue", self.project_id, {}, "")
        self.assertIn("[작가 문체 프로필]", block)
        self.assertIn("코믹 경쾌", block)
        self.assertIn("1인칭 · 현재형", block)
        self.assertIn("갑자기", block)
        for mode in ("rewrite", "worlddesc", "brainstorm", "brainstorm_next_exists"):
            self.assertIn("[작가 문체 프로필]", build(mode, self.project_id, {}, ""), mode)
        for mode in ("analyze", "summarize", "foreshadow", "chat", "dupcheck"):
            self.assertEqual(build(mode, self.project_id, {}, ""), "", mode)
        # 직접 요청은 넣고, 같은 free 모드를 쓰는 회차 요약 버튼은 넣지 않는다.
        self.assertIn("[작가 문체 프로필]", build("free", self.project_id, {"indexed_prompt": "x"}, ""))
        self.assertEqual(build("free", self.project_id, {}, "[요약] 이 회차를 요약해 주세요"), "")
        self.assertEqual(build("continue", 0, {}, ""), "")


class StyleProfileScreenTests(unittest.TestCase):
    def test_controls_and_locales(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        for element_id in (
            "styleProfileForm", "styleProfilePresets", "styleProfileDials", "styleProfileSuggestButton",
            "styleProfilePerson", "styleProfileTense", "styleProfilePreferred", "styleProfileForbidden",
            "styleProfileHabits", "styleProfileSample", "styleProfileSampleFromEditor", "styleLiteraryFields",
        ):
            self.assertIn(f'id="{element_id}"', html, element_id)
        for preset in style_profile.PRESETS:
            self.assertIn(f'data-style-preset="{preset}"', html, preset)
        keys = set(re.findall(r'data-i18n(?:-[a-z-]+)?="(style\.[^"]+)"', html))
        keys |= set(re.findall(r'i18n\.t\(["\'](style\.[^"\']+)["\']', app_js))
        self.assertGreater(len(keys), 30)
        for lang in ("ko", "en", "es"):
            data = json.loads((ROOT / "web" / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(k for k in keys if k not in data), [], lang)


if __name__ == "__main__":
    unittest.main()
