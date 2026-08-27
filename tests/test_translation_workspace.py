"""HTTP checks for the submission-translation workspace APIs."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import gemini_client
import translation_prompts


class TranslationWorkspaceApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.calls: list[dict] = []
        self.fail_on_step: str | None = None
        self.paragraph_calls = 0
        self._orig_generate = gemini_client.generate_text
        self._orig_configured = gemini_client.is_configured
        gemini_client.is_configured = lambda: True  # type: ignore[method-assign]

        def _prompt_step(prompt: str, blob: str) -> str:
            if "detected_conventions" in prompt:
                return "narrative_formatting"
            if "start_paragraph_index" in prompt:
                return "scene_split"
            if "사용자가 클릭한 단어:" in prompt:
                return "word_context"
            if '"proper_nouns"' in prompt or "fit_judgment" in prompt:
                return "proper_nouns"
            if "polished_text" in prompt or "change_log" in prompt:
                return "polish"
            if '"logline"' in prompt:
                return "submission_package"
            if '"translated_text"' in prompt and "translation_notes" in prompt:
                return "paragraph_translation"
            if "suggested_revision" in prompt or '당신은 "토리"입니다' in blob:
                return "chat"
            return "other"

        def _fake(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            self.calls.append({"prompt": prompt, "system": system or ""})
            blob = f"{system or ''}\n{prompt}"
            step = _prompt_step(prompt, blob)
            if step == "paragraph_translation":
                self.paragraph_calls += 1
            if self.fail_on_step == step:
                raise gemini_client.GeminiError(f"{step} failed")
            if self.fail_on_step == "paragraph_translation_second" and self.paragraph_calls >= 2:
                raise gemini_client.GeminiError("paragraph_translation failed")
            if step == "narrative_formatting":
                return json.dumps(
                    {
                        "detected_conventions": [
                            {
                                "marker": "—",
                                "meaning": "텔레파시",
                                "confidence": "high",
                            }
                        ],
                        "recommended_handling": "preserve_with_note",
                        "recommendation_reason": "언어 구분이 세계관에 중요합니다.",
                    },
                    ensure_ascii=False,
                )
            if step == "scene_split":
                return json.dumps(
                    {
                        "scenes": [
                            {
                                "scene_order": 1,
                                "start_paragraph_index": 0,
                                "end_paragraph_index": 1,
                                "relationship_tag": "초면-설렘",
                                "mood_tag": "설렘",
                                "situation_note": "비 오는 첫 만남",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            if step == "proper_nouns":
                return json.dumps(
                    {
                        "proper_nouns": [
                            {
                                "source_term": "우산골",
                                "term_type": "place",
                                "romanized": "Usangol",
                                "fit_judgment": "does_not_fit",
                                "judgment_reason": "발음이 어색하고 지명처럼 들리지 않습니다.",
                                "suggested_alternatives": ["Rainvale", "Umberwick"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            if step == "polish":
                return json.dumps(
                    {
                        "polished_text": "The rain had already started when they met.",
                        "change_log": [
                            {
                                "before": "It was raining.",
                                "after": "The rain had already started.",
                                "reason": "리듬을 다듬었습니다.",
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
            if step == "submission_package":
                return json.dumps(
                    {
                        "logline": "Two strangers share one umbrella in the rain.",
                        "synopsis": "On a rainy day they meet for the first time and share an umbrella.",
                    },
                    ensure_ascii=False,
                )
            if step == "word_context":
                return json.dumps(
                    {"explanation": "원문의 서두름을 hurried로 담았어요."},
                    ensure_ascii=False,
                )
            if step == "paragraph_translation":
                return json.dumps(
                    {
                        "translated_text": "It was raining the day they first met.",
                        "translation_notes": [],
                    },
                    ensure_ascii=False,
                )
            if step == "chat":
                return json.dumps(
                    {
                        "response": "조금 더 부드럽게 바꿔 봤어요. 첫 만남의 설렘을 잃지 않으면서 비의 정적을 살렸어요.",
                        "suggested_revision": "The rain had already begun when they first met.",
                    },
                    ensure_ascii=False,
                )
            return "이 문장은 주인공의 첫인상을 강조하는 번역입니다."

        gemini_client.generate_text = _fake  # type: ignore[method-assign]
        self._orig_dictionary = app.fetch_free_dictionary_payload

        def _fake_dictionary(word: str) -> tuple[int, object]:
            token = str(word or "").strip().lower()
            if token in {"xyzzy", "notaword"}:
                return 404, {"title": "No Definitions Found"}
            return 200, [
                {
                    "word": word,
                    "phonetic": "/ˈhʌrid/",
                    "meanings": [
                        {
                            "partOfSpeech": "verb",
                            "definitions": [{"definition": "moved or acted with haste"}],
                        }
                    ],
                }
            ]

        app.fetch_free_dictionary_payload = _fake_dictionary  # type: ignore[method-assign]

    def tearDown(self) -> None:
        gemini_client.generate_text = self._orig_generate  # type: ignore[method-assign]
        gemini_client.is_configured = self._orig_configured  # type: ignore[method-assign]
        app.fetch_free_dictionary_payload = self._orig_dictionary  # type: ignore[method-assign]
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def request(
        self, method: str, path: str, payload: dict | None = None
    ) -> tuple[int, object]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=15
        )
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if body else {}
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return response.status, parsed

    def _make_story(self) -> tuple[int, int]:
        status, project = self.request(
            "POST", "/api/projects", {"title": "비의 도시", "main_genre": "판타지"}
        )
        self.assertEqual(status, 201)
        project_id = int(project["id"])
        status, chapter = self.request(
            "POST", f"/api/projects/{project_id}/chapters", {"title": "첫 장"}
        )
        self.assertEqual(status, 201)
        status, scene = self.request(
            "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "첫 만남"}
        )
        self.assertEqual(status, 201)
        status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        self.assertEqual(status, 200)
        status, _ = self.request(
            "PUT",
            f"/api/scenes/{scene['id']}",
            {
                "title": "첫 만남",
                "status": "draft",
                "synopsis_md": "",
                "notes_md": "",
                "content_md": "비가 내리던 날, 두 사람은 처음 만났다.\n\n우산 하나가 둘을 가렸다.",
                "row_version": detail["row_version"],
            },
        )
        self.assertEqual(status, 200)
        return project_id, int(scene["id"])

    def test_create_job_seeds_segments_and_lists_by_chapter(self) -> None:
        project_id, _ = self._make_story()
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/translation/jobs",
            {"target_language": "en", "culture_localization_level": "moderate"},
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        self.assertGreaterEqual(int(created.get("seeded_segments") or 0), 2)

        status, listing = self.request("GET", f"/api/projects/{project_id}/translation/jobs")
        self.assertEqual(status, 200)
        self.assertEqual(len(listing["jobs"]), 1)

        status, payload = self.request(
            "GET", f"/api/translation/jobs/{job_id}/segments?chapter=1"
        )
        self.assertEqual(status, 200)
        segments = payload["segments"]
        self.assertGreaterEqual(len(segments), 2)
        self.assertEqual(segments[0]["segment_order"], 1)
        self.assertIn("비가 내리던 날", segments[0]["source_text"])

        status, nouns = self.request("GET", f"/api/translation/jobs/{job_id}/proper_nouns")
        self.assertEqual(status, 200)
        self.assertEqual(nouns["proper_nouns"], [])

    def test_approve_toggle_and_culture_reset(self) -> None:
        project_id, _ = self._make_story()
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", {}
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        status, payload = self.request(
            "GET", f"/api/translation/jobs/{job_id}/segments?chapter=1"
        )
        segment_id = int(payload["segments"][0]["id"])
        status, approved = self.request(
            "POST",
            f"/api/translation/segments/{segment_id}/approve",
            {"is_approved": True},
        )
        self.assertEqual(status, 200)
        self.assertTrue(approved["is_approved"])

        status, job = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/culture",
            {"culture_localization_level": "tight"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(job["culture_localization_level"], "tight")

    def test_polish_and_chat_persist_messages(self) -> None:
        project_id, _ = self._make_story()
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", {}
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        status, payload = self.request(
            "GET", f"/api/translation/jobs/{job_id}/segments?chapter=1"
        )
        segment_id = int(payload["segments"][0]["id"])
        other_id = int(payload["segments"][1]["id"])
        with app.database() as connection:
            connection.execute(
                "UPDATE translation_segments SET translated_text = ? WHERE id = ?",
                ("It was raining when they first met.", segment_id),
            )
            connection.execute(
                "INSERT INTO translation_chat_messages"
                "(translation_job_id, segment_id, role, message) "
                "VALUES (?, ?, 'user', ?)",
                (job_id, other_id, "다른 문단 바나나 질문"),
            )

        status, polished = self.request(
            "POST", f"/api/translation/segments/{segment_id}/polish", {}
        )
        self.assertEqual(status, 200)
        self.assertIn("rain", (polished.get("polished_text") or "").lower())
        self.assertTrue(any("change_log" in call["prompt"] or True for call in self.calls))

        status, chat = self.request(
            "POST",
            "/api/translation/chat",
            {
                "job_id": job_id,
                "segment_id": segment_id,
                "dragged_text": "It was raining when they first met.",
                "message": "이 문장, 더 부드럽게 바꿀 수 있을까요?",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(chat["user"]["role"], "user")
        self.assertEqual(chat["tori"]["role"], "tori")
        self.assertIn("부드럽게", chat["tori"]["message"])
        self.assertIn("The rain had already begun", chat["tori"]["message"])
        self.assertEqual(
            chat["tori"].get("suggested_revision"),
            "The rain had already begun when they first met.",
        )
        first_chat_prompt = next(
            call["prompt"]
            for call in reversed(self.calls)
            if '당신은 "토리"입니다' in call["prompt"]
        )
        self.assertIn("비가 내리던 날", first_chat_prompt)
        self.assertIn("It was raining when they first met.", first_chat_prompt)
        self.assertNotIn("바나나", first_chat_prompt)

        status, follow = self.request(
            "POST",
            "/api/translation/chat",
            {
                "job_id": job_id,
                "segment_id": segment_id,
                "message": "이름은 빼 줘.",
            },
        )
        self.assertEqual(status, 201)
        follow_prompt = next(
            call["prompt"]
            for call in reversed(self.calls)
            if '당신은 "토리"입니다' in call["prompt"]
        )
        self.assertIn("이 문장, 더 부드럽게 바꿀 수 있을까요?", follow_prompt)
        self.assertNotIn("바나나", follow_prompt)

        status, detail = self.request("GET", f"/api/translation/jobs/{job_id}")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(detail["chat_messages"]), 2)

    def test_chat_route_uses_chat_prompt_with_segment_context(self) -> None:
        project_id, _ = self._make_story()
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/translation/jobs",
            {
                "culture_localization_level": "as_is",
                "style_guide_json": {
                    "tense": "past",
                    "character_voices": "이오나=캐주얼",
                },
            },
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        status, payload = self.request(
            "GET", f"/api/translation/jobs/{job_id}/segments?chapter=1"
        )
        self.assertEqual(status, 200)
        segment_id = int(payload["segments"][0]["id"])
        other_id = int(payload["segments"][1]["id"])
        with app.database() as connection:
            cursor = connection.execute(
                "INSERT INTO translation_scene_contexts"
                "(translation_job_id, chapter_number, scene_order, "
                "relationship_tag, mood_tag) VALUES (?, 1, 1, ?, ?)",
                (job_id, "연인-걱정", "불안"),
            )
            context_id = int(cursor.lastrowid)
            connection.execute(
                "UPDATE translation_segments SET translated_text = ?, "
                "scene_context_id = ? WHERE id = ?",
                ("Have you eaten, babe?", context_id, segment_id),
            )
            connection.execute(
                "INSERT INTO translation_chat_messages"
                "(translation_job_id, segment_id, role, message) "
                "VALUES (?, ?, 'user', ?)",
                (job_id, other_id, "다른 문단 바나나 질문"),
            )
            for index in range(13):
                connection.execute(
                    "INSERT INTO translation_chat_messages"
                    "(translation_job_id, segment_id, role, message) "
                    "VALUES (?, ?, 'user', ?)",
                    (job_id, segment_id, f"이전 질문 {index}"),
                )

        captured: list[dict] = []
        original = translation_prompts.build_translation_chat_prompt

        def _spy(question, settings=None):
            captured.append({"question": question, "settings": dict(settings or {})})
            return original(question, settings)

        translation_prompts.build_translation_chat_prompt = _spy  # type: ignore[method-assign]
        try:
            status, chat = self.request(
                "POST",
                "/api/translation/chat",
                {
                    "job_id": job_id,
                    "segment_id": segment_id,
                    "dragged_text": "Have you eaten, babe?",
                    "message": "이 문장 조금 더 슬프게 바꿔 줘",
                },
            )
        finally:
            translation_prompts.build_translation_chat_prompt = original  # type: ignore[method-assign]

        self.assertEqual(status, 201)
        self.assertEqual(chat["user"]["role"], "user")
        self.assertEqual(len(captured), 1)
        settings = captured[0]["settings"]
        self.assertEqual(captured[0]["question"], "이 문장 조금 더 슬프게 바꿔 줘")
        self.assertIn("비가 내리던 날", settings["source_text"])
        self.assertEqual(settings["translated_text"], "Have you eaten, babe?")
        self.assertEqual(settings["dragged_text"], "Have you eaten, babe?")
        self.assertEqual(settings["tense"], "past")
        self.assertEqual(settings["character_voices"], "이오나=캐주얼")
        self.assertEqual(settings["relationship_tag"], "연인-걱정")
        self.assertEqual(settings["mood_tag"], "불안")
        self.assertEqual(settings["culture_localization_level"], "as_is")
        history = settings["chat_history"]
        self.assertIn("이전 질문 12", history)
        self.assertIn("이전 질문 1", history)
        self.assertNotIn("이전 질문 0", history)
        self.assertNotIn("바나나", history)
        self.assertNotIn("이 문장 조금 더 슬프게 바꿔 줘", history)
        prompt = next(
            call["prompt"]
            for call in reversed(self.calls)
            if '당신은 "토리"입니다' in call["prompt"]
        )
        self.assertIn("[스타일가이드]\n- 시제: past\n", prompt)
        self.assertIn("- 인물별 어조: 이오나=캐주얼\n", prompt)
        self.assertIn(
            "[씬 컨텍스트]: relationship_tag=연인-걱정, mood_tag=불안\n",
            prompt,
        )
        self.assertIn("[문화반영범위]: as_is\n", prompt)

    def test_extract_decide_and_confirm_proper_nouns(self) -> None:
        project_id, _ = self._make_story()
        status, _ = self.request(
            "POST",
            f"/api/projects/{project_id}/characters",
            {"name": "이오나"},
        )
        self.assertEqual(status, 201)
        with app.database() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO project_index(project_id) VALUES (?)",
                (project_id,),
            )
            cols = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(project_index)").fetchall()
            }
            char_col = "characters_json" if "characters_json" in cols else "characters_json"
            connection.execute(
                f"UPDATE project_index SET {char_col} = ? WHERE project_id = ?",
                (json.dumps(["세리나"], ensure_ascii=False), project_id),
            )
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", {}
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        self.assertEqual(created.get("status"), "draft")
        self.assertFalse(created.get("proper_nouns_confirmed"))

        status, extracted = self.request(
            "POST", f"/api/translation/jobs/{job_id}/extract_proper_nouns", {}
        )
        self.assertEqual(status, 200)
        nouns = extracted["proper_nouns"]
        names = {item["source_term"] for item in nouns}
        self.assertIn("이오나", names)
        self.assertIn("세리나", names)
        self.assertIn("우산골", names)
        index_item = next(item for item in nouns if item["source_term"] == "이오나")
        self.assertEqual(index_item["source"], "character_index")
        self.assertEqual(index_item["final_term"], "이오나")
        detected = next(item for item in nouns if item["source_term"] == "우산골")
        self.assertEqual(detected["source"], "ai_detected")
        self.assertEqual(detected["fit_judgment"], "does_not_fit")
        self.assertFalse(str(detected.get("final_term") or "").strip())
        self.assertTrue(
            any("건너뛰" in call["prompt"] for call in self.calls),
        )

        status, _ = self.request(
            "POST", f"/api/translation/jobs/{job_id}/confirm_proper_nouns", {}
        )
        self.assertEqual(status, 400)

        status, decided = self.request(
            "POST",
            f"/api/translation/proper_nouns/{detected['id']}/decide",
            {"user_decision": "rename", "final_term": "Rainvale"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(decided["final_term"], "Rainvale")
        self.assertEqual(decided["user_decision"], "rename")

        status, confirmed = self.request(
            "POST", f"/api/translation/jobs/{job_id}/confirm_proper_nouns", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(confirmed["status"], "draft")
        self.assertTrue(confirmed.get("proper_nouns_confirmed"))

    def _count_steps(self, *needles: str) -> int:
        return sum(
            1
            for call in self.calls
            if any(needle in call["prompt"] for needle in needles)
        )

    def test_start_pipeline_skips_completed_steps_on_rerun(self) -> None:
        project_id, _ = self._make_story()
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", {}
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])

        status, started = self.request(
            "POST", f"/api/translation/jobs/{job_id}/start", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(started["status"], "awaiting_review")
        self.assertTrue(started.get("narrative_formatting_rules"))
        self.assertTrue(started.get("scene_contexts"))
        self.assertTrue(started.get("proper_nouns"))
        formatting_calls = self._count_steps("detected_conventions")
        scene_calls = self._count_steps("start_paragraph_index")
        noun_calls = self._count_steps('"proper_nouns"')
        self.assertGreaterEqual(formatting_calls, 1)
        self.assertGreaterEqual(scene_calls, 1)
        self.assertGreaterEqual(noun_calls, 1)

        status, again = self.request(
            "POST", f"/api/translation/jobs/{job_id}/start", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(again["status"], "awaiting_review")
        self.assertIn("narrative_formatting", again.get("skipped_steps") or [])
        self.assertIn("scene_split", again.get("skipped_steps") or [])
        self.assertIn("proper_nouns", again.get("skipped_steps") or [])
        self.assertEqual(self._count_steps("detected_conventions"), formatting_calls)
        self.assertEqual(self._count_steps("start_paragraph_index"), scene_calls)
        self.assertEqual(self._count_steps('"proper_nouns"'), noun_calls)

    def test_start_pipeline_resumes_after_mid_failure(self) -> None:
        project_id, _ = self._make_story()
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", {}
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        self.fail_on_step = "scene_split"

        status, failed = self.request(
            "POST", f"/api/translation/jobs/{job_id}/start", {}
        )
        self.assertEqual(status, 400)
        status, job = self.request("GET", f"/api/translation/jobs/{job_id}")
        self.assertEqual(status, 200)
        self.assertEqual(job.get("pipeline_failed_step"), "scene_split")
        self.assertTrue(job.get("narrative_formatting_rules"))
        self.assertFalse(job.get("scene_contexts"))
        formatting_calls = self._count_steps("detected_conventions")
        scene_calls = self._count_steps("start_paragraph_index")
        self.assertEqual(formatting_calls, 1)
        self.assertEqual(scene_calls, 1)

        self.fail_on_step = None
        status, started = self.request(
            "POST", f"/api/translation/jobs/{job_id}/start", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(started["status"], "awaiting_review")
        self.assertIn("narrative_formatting", started.get("skipped_steps") or [])
        self.assertNotIn("scene_split", started.get("skipped_steps") or [])
        self.assertEqual(self._count_steps("detected_conventions"), formatting_calls)
        self.assertEqual(self._count_steps("start_paragraph_index"), scene_calls + 1)
        self.assertTrue(started.get("scene_contexts"))
        self.assertFalse(started.get("pipeline_failed_step"))

    def test_proceed_requires_confirmed_nouns_and_resumes_segments(self) -> None:
        project_id, _ = self._make_story()
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", {}
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        status, _ = self.request("POST", f"/api/translation/jobs/{job_id}/start", {})
        self.assertEqual(status, 200)

        status, blocked = self.request(
            "POST", f"/api/translation/jobs/{job_id}/proceed_to_translation", {}
        )
        self.assertEqual(status, 400)
        self.assertIn("고유명사", str(blocked))

        status, extracted = self.request(
            "GET", f"/api/translation/jobs/{job_id}/proper_nouns"
        )
        self.assertEqual(status, 200)
        detected = next(
            item
            for item in extracted["proper_nouns"]
            if item["source_term"] == "우산골"
        )
        status, _ = self.request(
            "POST",
            f"/api/translation/proper_nouns/{detected['id']}/decide",
            {"user_decision": "rename", "final_term": "Rainvale"},
        )
        self.assertEqual(status, 200)
        status, confirmed = self.request(
            "POST", f"/api/translation/jobs/{job_id}/confirm_proper_nouns", {}
        )
        self.assertEqual(status, 200)
        self.assertTrue(confirmed.get("proper_nouns_confirmed"))
        self.assertEqual(confirmed["status"], "awaiting_review")

        self.fail_on_step = "paragraph_translation_second"
        status, failed = self.request(
            "POST", f"/api/translation/jobs/{job_id}/proceed_to_translation", {}
        )
        self.assertEqual(status, 400)
        status, job = self.request("GET", f"/api/translation/jobs/{job_id}")
        self.assertEqual(job.get("pipeline_failed_step"), "paragraph_translation")
        status, listing = self.request(
            "GET", f"/api/translation/jobs/{job_id}/segments"
        )
        texts = [str(item.get("translated_text") or "") for item in listing["segments"]]
        self.assertTrue(any(texts))
        self.assertTrue(any(not text.strip() for text in texts))
        first_pass = self.paragraph_calls

        self.fail_on_step = None
        status, done = self.request(
            "POST", f"/api/translation/jobs/{job_id}/proceed_to_translation", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(done["status"], "translated")
        self.assertGreaterEqual(done.get("skipped_segments") or 0, 1)
        self.assertEqual(self.paragraph_calls, first_pass + 1)
        status, listing = self.request(
            "GET", f"/api/translation/jobs/{job_id}/segments"
        )
        self.assertTrue(
            all(str(item.get("translated_text") or "").strip() for item in listing["segments"])
        )

        status, package = self.request(
            "POST", f"/api/translation/jobs/{job_id}/generate_submission_package", {}
        )
        self.assertEqual(status, 200)
        self.assertTrue(package.get("submission_package", {}).get("logline_translated"))
        package_calls = self._count_steps('"logline"')
        status, again = self.request(
            "POST", f"/api/translation/jobs/{job_id}/generate_submission_package", {}
        )
        self.assertEqual(status, 200)
        self.assertIn("submission_package", again.get("skipped_steps") or [])
        self.assertEqual(self._count_steps('"logline"'), package_calls)

    def test_dictionary_lookup_found_and_missing(self) -> None:
        status, found = self.request("GET", "/api/translation/dictionary?word=hurried")
        self.assertEqual(status, 200)
        self.assertTrue(found.get("found"))
        self.assertEqual(found.get("phonetic"), "/ˈhʌrid/")
        self.assertEqual(found["meanings"][0]["part_of_speech"], "verb")
        status, missing = self.request("GET", "/api/translation/dictionary?word=xyzzy")
        self.assertEqual(status, 200)
        self.assertFalse(missing.get("found"))
        self.assertEqual(missing.get("word"), "xyzzy")

    def test_word_context_uses_notes_then_cache_then_gemini(self) -> None:
        project_id, _ = self._make_story()
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", {}
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        status, listing = self.request(
            "GET", f"/api/translation/jobs/{job_id}/segments"
        )
        self.assertEqual(status, 200)
        segment_id = int(listing["segments"][0]["id"])
        notes = [
            {
                "source_phrase": "발길을 옮겼다",
                "translated_as": "hurried",
                "note": "문학적 표현을 서두름으로 압축했어요.",
            }
        ]
        with app.database() as connection:
            connection.execute(
                "UPDATE translation_segments SET translated_text = ?, translation_notes_json = ? "
                "WHERE id = ?",
                (
                    "Iona hurried home.",
                    json.dumps(notes, ensure_ascii=False),
                    segment_id,
                ),
            )
        before = len(self.calls)
        status, from_notes = self.request(
            "POST",
            "/api/translation/word_context",
            {"segment_id": segment_id, "word": "hurried"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(from_notes.get("source"), "translation_notes")
        self.assertIn("압축", from_notes.get("explanation") or "")
        self.assertEqual(len(self.calls), before)

        status, cached = self.request(
            "POST",
            "/api/translation/word_context",
            {"segment_id": segment_id, "word": "hurried"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(cached.get("source"), "cache")
        self.assertEqual(cached.get("explanation"), from_notes.get("explanation"))
        self.assertEqual(len(self.calls), before)

        status, generated = self.request(
            "POST",
            "/api/translation/word_context",
            {"segment_id": segment_id, "word": "home"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(generated.get("source"), "gemini")
        self.assertIn("hurried", generated.get("explanation") or "")
        self.assertEqual(len(self.calls), before + 1)
        gemini_again = len(self.calls)
        status, cached_home = self.request(
            "POST",
            "/api/translation/word_context",
            {"segment_id": segment_id, "word": "home"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(cached_home.get("source"), "cache")
        self.assertEqual(len(self.calls), gemini_again)


class ParagraphTranslationParseTests(unittest.TestCase):
    def test_reads_standard_json(self) -> None:
        text, notes = app._parse_paragraph_translation_output(
            '{"translated_text": "Iona sighed.", "translation_notes": []}'
        )
        self.assertEqual(text, "Iona sighed.")
        self.assertEqual(notes, [])

    def test_reads_json_hidden_in_prose_and_alt_keys(self) -> None:
        raw = 'Here you go:\n{"translation": "She looked up.", "notes": [{"note": "ok"}]}\nThanks.'
        text, notes = app._parse_paragraph_translation_output(raw)
        self.assertEqual(text, "She looked up.")
        self.assertEqual(notes, [{"note": "ok"}])

    def test_recovers_truncated_json_string_field(self) -> None:
        raw = '{"translated_text": "The bakery was closed.\\n\\"Ahh...\\"", "translation_notes": ['
        text, _notes = app._parse_paragraph_translation_output(raw)
        self.assertEqual(text, 'The bakery was closed.\n"Ahh..."')

    def test_korean_error_when_empty(self) -> None:
        with self.assertRaises(ValueError) as raised:
            app._parse_paragraph_translation_output("{")
        self.assertIn("문단 번역 결과를 읽지 못했어요", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
