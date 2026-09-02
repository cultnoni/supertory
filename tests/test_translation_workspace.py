"""HTTP checks for the submission-translation workspace APIs."""

from __future__ import annotations

import http.client
import json
import re
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
        self.batch_calls = 0
        self.batch_structure_mismatch_left = 0
        self.batch_order_mismatch_left = 0
        self.empty_paragraph_mode = None
        self.rate_limit_left = 0
        self.rate_limit_raises = 0
        self.dictionary_languages: list[str] = []
        self.polish_mismatch_left = 0
        self.proper_noun_round = 0
        self._orig_generate = gemini_client.generate_text
        self._orig_configured = gemini_client.is_configured
        self._orig_retry_delay = app._paragraph_empty_retry_delay
        self._orig_gap = app.TRANSLATION_GEMINI_GAP_SECONDS
        self._orig_sleep = app._translation_sleep
        app._paragraph_empty_retry_delay = lambda: 0.0  # type: ignore[assignment]
        app.TRANSLATION_GEMINI_GAP_SECONDS = 0
        app._translation_sleep = lambda _seconds: None  # type: ignore[assignment]
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
            if "<<<SEGMENT id=" in prompt and '"paragraphs"' in prompt:
                return "paragraph_translation_batch"
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
            if step in {"paragraph_translation", "paragraph_translation_batch"}:
                if self.rate_limit_left > 0:
                    self.rate_limit_left -= 1
                    self.rate_limit_raises += 1
                    raise gemini_client.GeminiError(
                        "Resource exhausted. Please retry in 8.4s.",
                        code="rate_limit",
                        http_status=429,
                        retry_after=0,
                    )
                if step == "paragraph_translation_batch":
                    self.batch_calls += 1
                else:
                    self.paragraph_calls += 1
            if self.fail_on_step == step or (
                self.fail_on_step == "paragraph_translation"
                and step == "paragraph_translation_batch"
            ):
                raise gemini_client.GeminiError(f"{step} failed")
            if (
                self.fail_on_step == "paragraph_translation_second"
                and step == "paragraph_translation"
            ):
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
                self.proper_noun_round += 1
                seen: set[str] = set()
                nouns: list[dict] = []
                round_suffix = (
                    f" {self.proper_noun_round}" if self.proper_noun_round > 1 else ""
                )

                def add_noun(source_term: str, payload: dict) -> None:
                    if source_term in seen:
                        return
                    seen.add(source_term)
                    nouns.append({"source_term": source_term, **payload})

                capturing = False
                for line in prompt.splitlines():
                    if "설정집에 이미 있는 고유명사" in line:
                        capturing = True
                        continue
                    if not capturing:
                        continue
                    if line.startswith("- "):
                        name = line[2:].strip()
                        romanized = {
                            "이오나": "Iona",
                            "안테레스": "Anteres",
                            "세리나": "Serena",
                        }.get(name, name)
                        romanized = f"{romanized}{round_suffix}"
                        add_noun(
                            name,
                            {
                                "term_type": "character",
                                "romanized": romanized,
                                "fit_judgment": "fits",
                                "judgment_reason": (
                                    f"'{romanized}' 표기가 대상 언어권에서 자연스럽습니다."
                                ),
                                "suggested_alternatives": [],
                            },
                        )
                    elif line.startswith("[") or line.startswith("그 다음"):
                        capturing = False
                add_noun(
                    "우산골",
                    {
                        "term_type": "place",
                        "romanized": f"Usangol{round_suffix}",
                        "fit_judgment": "does_not_fit",
                        "judgment_reason": "발음이 어색하고 지명처럼 들리지 않습니다.",
                        "suggested_alternatives": ["Rainvale", "Umberwick"],
                    },
                )
                return json.dumps({"proper_nouns": nouns}, ensure_ascii=False)
            if step == "polish":
                spanish = "[대상 언어]: 스페인어" in prompt
                french = "[대상 언어]: 프랑스어" in prompt
                chapter_body = prompt.rsplit(
                    "이제 아래 회차를 같은 문단 개수와 순서로 윤문하세요.", 1
                )[-1]
                paragraph_count = chapter_body.count("<<<PARAGRAPH ")
                if self.polish_mismatch_left > 0:
                    self.polish_mismatch_left -= 1
                    paragraph_count = max(0, paragraph_count - 1)
                return json.dumps(
                    {
                        "paragraphs": [
                            {
                                "index": index,
                                "polished_text": (
                                    (
                                        "La lluvia ya había comenzado cuando se conocieron."
                                        if index == 1
                                        else "Un solo paraguas los cobijó a ambos."
                                    )
                                    if spanish
                                    else (
                                        (
                                            "La pluie avait déjà commencé quand ils se sont rencontrés."
                                            if index == 1
                                            else "Un seul parapluie les abritait tous les deux."
                                        )
                                        if french
                                        else (
                                            "The rain had already started when they met."
                                            if index == 1
                                            else "One umbrella sheltered them both."
                                        )
                                    )
                                ),
                            }
                            for index in range(1, paragraph_count + 1)
                        ]
                    },
                    ensure_ascii=False,
                )
            if step == "submission_package":
                spanish = "[대상 언어]: 스페인어" in prompt
                french = "[대상 언어]: 프랑스어" in prompt
                return json.dumps(
                    {
                        "logline": (
                            "Dos desconocidos comparten un paraguas bajo la lluvia."
                            if spanish
                            else (
                                "Deux inconnus partagent un parapluie sous la pluie."
                                if french
                                else "Two strangers share one umbrella in the rain."
                            )
                        ),
                        "synopsis": (
                            "En un día lluvioso se conocen y comparten un paraguas."
                            if spanish
                            else (
                                "Un jour de pluie, ils se rencontrent et partagent un parapluie."
                                if french
                                else "On a rainy day they meet for the first time and share an umbrella."
                            )
                        ),
                    },
                    ensure_ascii=False,
                )
            if step == "word_context":
                return json.dumps(
                    {"explanation": "원문의 서두름을 hurried로 담았어요."},
                    ensure_ascii=False,
                )
            if step == "paragraph_translation_batch":
                spanish = "[대상 언어]: 스페인어" in prompt
                french = "[대상 언어]: 프랑스어" in prompt
                ids = [
                    int(value)
                    for value in re.findall(r"<<<SEGMENT id=(\d+)>>>", prompt)
                ]
                if self.batch_structure_mismatch_left > 0:
                    self.batch_structure_mismatch_left -= 1
                    ids = ids[:-1]
                elif self.batch_order_mismatch_left > 0:
                    self.batch_order_mismatch_left -= 1
                    ids = list(reversed(ids))
                paragraphs = []
                for index, segment_id in enumerate(ids):
                    empty = self.empty_paragraph_mode in {"always", "twice_then_ok"}
                    if self.fail_on_step == "paragraph_translation_second" and index == 1:
                        empty = True
                    paragraphs.append(
                        {
                            "id": segment_id,
                            "translated_text": (
                                ""
                                if empty
                                else (
                                    f"Traducción por lotes {segment_id}."
                                    if spanish
                                    else (
                                        f"Traduction groupée {segment_id}."
                                        if french
                                        else f"Batch translation {segment_id}."
                                    )
                                )
                            ),
                            "translation_notes": [],
                        }
                    )
                return json.dumps({"paragraphs": paragraphs}, ensure_ascii=False)
            if step == "paragraph_translation":
                spanish = "[대상 언어]: 스페인어" in prompt
                french = "[대상 언어]: 프랑스어" in prompt
                if self.empty_paragraph_mode == "always" or (
                    self.empty_paragraph_mode == "twice_then_ok"
                    and self.paragraph_calls <= 2
                ):
                    return (
                        '```json\n{\n  "translated_text": "",\n'
                        '  "translation_notes": []\n}\n```'
                    )
                return json.dumps(
                    {
                        "translated_text": (
                            "Llovía el día que se conocieron."
                            if spanish
                            else (
                                "Il pleuvait le jour de leur rencontre."
                                if french
                                else "It was raining the day they first met."
                            )
                        ),
                        "translation_notes": [],
                    },
                    ensure_ascii=False,
                )
            if step == "chat":
                spanish = "[대상 언어]: 스페인어" in prompt
                french = "[대상 언어]: 프랑스어" in prompt
                return json.dumps(
                    {
                        "response": (
                            "스페인어 문장을 조금 더 자연스럽게 다듬었어요."
                            if spanish
                            else (
                                "프랑스어 문장을 조금 더 자연스럽게 다듬었어요."
                                if french
                                else "조금 더 부드럽게 바꿔 봤어요. 첫 만남의 설렘을 잃지 않으면서 비의 정적을 살렸어요."
                            )
                        ),
                        "suggested_revision": (
                            "La lluvia ya había comenzado cuando se conocieron."
                            if spanish
                            else (
                                "La pluie avait déjà commencé quand ils se sont rencontrés."
                                if french
                                else "The rain had already begun when they first met."
                            )
                        ),
                    },
                    ensure_ascii=False,
                )
            return "이 문장은 주인공의 첫인상을 강조하는 번역입니다."

        gemini_client.generate_text = _fake  # type: ignore[method-assign]
        self._orig_dictionary = app.fetch_free_dictionary_payload

        def _fake_dictionary(
            word: str, target_language: object = "en"
        ) -> tuple[int, object]:
            self.dictionary_languages.append(str(target_language))
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
        app._paragraph_empty_retry_delay = self._orig_retry_delay  # type: ignore[assignment]
        app.TRANSLATION_GEMINI_GAP_SECONDS = self._orig_gap
        app._translation_sleep = self._orig_sleep  # type: ignore[assignment]
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

    def request_bytes(
        self, method: str, path: str, payload: dict | None = None
    ) -> tuple[int, bytes, str]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=15
        )
        body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        headers = {"Content-Type": "application/json"} if body else {}
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        raw = response.read()
        content_type = response.getheader("Content-Type") or ""
        connection.close()
        return response.status, raw, content_type

    def _decide_empty_nouns(self, job_id: int, overrides: dict | None = None) -> None:
        overrides = overrides or {}
        status, listing = self.request(
            "GET", f"/api/translation/jobs/{job_id}/proper_nouns"
        )
        self.assertEqual(status, 200)
        for item in listing["proper_nouns"]:
            name = str(item.get("source_term") or "")
            if name in overrides:
                payload = overrides[name]
            elif str(item.get("final_term") or "").strip():
                continue
            else:
                payload = {
                    "user_decision": "keep_romanized",
                    "final_term": item.get("romanized") or name,
                }
            status, _ = self.request(
                "POST",
                f"/api/translation/proper_nouns/{item['id']}/decide",
                payload,
            )
            self.assertEqual(status, 200)

    def _make_story(self, content_md: str | None = None) -> tuple[int, int]:
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
                "content_md": content_md
                or "비가 내리던 날, 두 사람은 처음 만났다.\n\n우산 하나가 둘을 가렸다.",
                "row_version": detail["row_version"],
            },
        )
        self.assertEqual(status, 200)
        return project_id, int(scene["id"])

    def _translation_job_body(self, **extra: object) -> dict:
        body: dict = {
            "target_language": "en",
            "culture_localization_level": "moderate",
            "translate_all_chapters": True,
        }
        body.update(extra)
        return body

    def _make_story_with_episodes(self, episode_texts: list[str]) -> int:
        status, project = self.request(
            "POST", "/api/projects", {"title": "범위 검증", "main_genre": "판타지"}
        )
        self.assertEqual(status, 201)
        project_id = int(project["id"])
        for index, text in enumerate(episode_texts, start=1):
            status, chapter = self.request(
                "POST",
                f"/api/projects/{project_id}/chapters",
                {"title": f"{index}장"},
            )
            self.assertEqual(status, 201)
            status, scene = self.request(
                "POST",
                f"/api/chapters/{chapter['id']}/scenes",
                {"title": f"{index}화"},
            )
            self.assertEqual(status, 201)
            status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
            self.assertEqual(status, 200)
            status, _ = self.request(
                "PUT",
                f"/api/scenes/{scene['id']}",
                {
                    "title": f"{index}화",
                    "status": "draft",
                    "synopsis_md": "",
                    "notes_md": "",
                    "content_md": text,
                    "row_version": detail["row_version"],
                },
            )
            self.assertEqual(status, 200)
        return project_id

    def test_create_job_seeds_segments_and_lists_by_chapter(self) -> None:
        project_id, _ = self._make_story()
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/translation/jobs",
            {"target_language": "en", "culture_localization_level": "moderate", "translate_all_chapters": True},
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        self.assertGreaterEqual(int(created.get("seeded_segments") or 0), 2)
        self.assertTrue(created.get("translate_all_chapters"))
        self.assertEqual(int(created.get("start_chapter") or 0), 1)
        self.assertEqual(int(created.get("end_chapter") or 0), 1)
        self.assertEqual(int(created.get("cliffhanger_chapter") or 0), 1)

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
            "POST", f"/api/projects/{project_id}/translation/jobs", self._translation_job_body()
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

    def test_chapter_polish_and_chat_persist_messages(self) -> None:
        project_id, _ = self._make_story()
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", self._translation_job_body()
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
                "UPDATE translation_segments SET translated_text = ? WHERE id = ?",
                ("One umbrella covered the two people.", other_id),
            )
            connection.execute(
                "INSERT INTO translation_chat_messages"
                "(translation_job_id, segment_id, role, message) "
                "VALUES (?, ?, 'user', ?)",
                (job_id, other_id, "다른 문단 바나나 질문"),
            )

        status, blocked = self.request(
            "POST", f"/api/translation/jobs/{job_id}/chapters/1/polish", {}
        )
        self.assertEqual(status, 400)
        self.assertIn("모든 문단", str(blocked.get("error") or blocked))
        for current_id in (segment_id, other_id):
            status, approved = self.request(
                "POST",
                f"/api/translation/segments/{current_id}/approve",
                {"is_approved": True},
            )
            self.assertEqual(status, 200)
            self.assertTrue(approved["is_approved"])

        status, polished = self.request(
            "POST", f"/api/translation/jobs/{job_id}/chapters/1/polish", {}
        )
        self.assertEqual(status, 200)
        self.assertTrue(polished["chapter_polish_proposed"])
        self.assertEqual(len(polished["segments"]), 2)
        self.assertIn(
            "rain",
            str(polished["segments"][0].get("polish_proposal_text") or "").lower(),
        )
        self.assertFalse(polished["segments"][0].get("polished_text"))
        polish_prompt = next(
            call["prompt"] for call in reversed(self.calls)
            if "[핵심 임무 — 반드시 수행" in call["prompt"]
        )
        self.assertNotIn("비가 내리던 날", polish_prompt)
        self.assertIn("It was raining when they first met.", polish_prompt)
        self.assertIn("One umbrella covered the two people.", polish_prompt)

        status, applied = self.request(
            "POST",
            f"/api/translation/segments/{segment_id}/polish_choice",
            {"choice": "apply", "polished_text": "Rain had begun when they met."},
        )
        self.assertEqual(status, 200)
        self.assertEqual(applied["polished_text"], "Rain had begun when they met.")
        self.assertEqual(applied["polish_choice"], "apply")
        status, kept = self.request(
            "POST",
            f"/api/translation/segments/{other_id}/polish_choice",
            {"choice": "keep"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(kept["polished_text"], "One umbrella covered the two people.")
        self.assertEqual(kept["polish_choice"], "keep")

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
        self.assertNotIn("⟦수정 제안⟧", chat["tori"]["message"])
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
        tori_messages = [
            item for item in detail["chat_messages"] if item.get("role") == "tori"
        ]
        self.assertTrue(
            any(
                item.get("suggested_revision")
                == "The rain had already begun when they first met."
                for item in tori_messages
            )
        )

        status, replaced = self.request(
            "POST",
            f"/api/translation/segments/{segment_id}/text",
            {
                "translated_text": (
                    "The rain had already begun when they first met."
                )
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            replaced["translated_text"],
            "The rain had already begun when they first met.",
        )
        self.assertFalse(replaced.get("polished_text"))
        self.assertFalse(replaced.get("is_approved"))

        status, edited = self.request(
            "POST",
            f"/api/translation/segments/{segment_id}/text",
            {"translated_text": "Rain was already falling when they first met."},
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            edited["translated_text"],
            "Rain was already falling when they first met.",
        )
        status, empty = self.request(
            "POST",
            f"/api/translation/segments/{segment_id}/text",
            {"translated_text": "   "},
        )
        self.assertEqual(status, 400)
        self.assertIn("바꿀 번역문", str(empty.get("error") or empty))
        status, listing = self.request(
            "GET", f"/api/translation/jobs/{job_id}/segments?chapter=1"
        )
        self.assertEqual(status, 200)
        saved = next(
            item
            for item in listing["segments"]
            if int(item["id"]) == int(segment_id)
        )
        self.assertEqual(
            saved["translated_text"],
            "Rain was already falling when they first met.",
        )

    def test_qa_revision_and_chat_expand_ui_are_wired(self) -> None:
        root = Path(__file__).resolve().parents[1]
        js = (root / "web" / "app.js").read_text(encoding="utf-8")
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        css = (root / "web" / "styles.css").read_text(encoding="utf-8")
        ko = (root / "web" / "locales" / "ko.json").read_text(encoding="utf-8")
        en = (root / "web" / "locales" / "en.json").read_text(encoding="utf-8")
        es = (root / "web" / "locales" / "es.json").read_text(encoding="utf-8")
        self.assertIn('id="translationChatResizer"', html)
        self.assertIn('id="translationWorkspaceSplit"', html)
        self.assertNotIn('id="translationChatExpand"', html)
        self.assertIn("data-revision-apply", js)
        self.assertIn("data-revision-edit", js)
        self.assertIn("data-revision-save", js)
        self.assertIn("applyTranslationChatRevision", js)
        self.assertIn("setupTranslationChatResizer", js)
        self.assertIn("translation-chat-select-hint", js)
        self.assertIn("/segments/${id}/text", js)
        self.assertIn("translation-workspace-split", css)
        self.assertIn("translation-chat-resizer", css)
        self.assertNotIn("is-chat-expanded", css)
        collapsed_toggle = css.split(
            ".translation-chat-dock.is-collapsed .translation-chat-toggle"
        )[1].split(".translation-chat-dock.is-collapsed .translation-chat-chevron")[0]
        self.assertIn("writing-mode: vertical-rl", collapsed_toggle)
        self.assertNotIn("rotate(180deg)", collapsed_toggle)
        for locale in (ko, en, es):
            self.assertIn("index.이_문장으로_바꾸기", locale)
            self.assertIn("index.직접_수정", locale)
            self.assertIn("index.특정_문장을_드래그해서_선택한_뒤_질문하면", locale)
            self.assertIn("index.이_문장을_드래그해서_다시_질문하면", locale)
            self.assertIn("index.번역문에_반영했어요", locale)

    def test_submission_result_ui_is_wired(self) -> None:
        root = Path(__file__).resolve().parents[1]
        js = (root / "web" / "app.js").read_text(encoding="utf-8")
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        css = (root / "web" / "styles.css").read_text(encoding="utf-8")
        ko = (root / "web" / "locales" / "ko.json").read_text(encoding="utf-8")
        en = (root / "web" / "locales" / "en.json").read_text(encoding="utf-8")
        es = (root / "web" / "locales" / "es.json").read_text(encoding="utf-8")
        self.assertIn('id="translationCheerViewResult"', html)
        self.assertIn('id="translationSubmissionResultModal"', html)
        self.assertIn('id="translationResultButton"', html)
        self.assertIn("openTranslationSubmissionResultModal", js)
        self.assertIn("downloadTranslationSubmissionExport", js)
        self.assertIn("/submission_result", js)
        self.assertIn("/export_submission", js)
        self.assertIn("translation-result-modal-card", css)
        self.assertIn('id="translationResultPdfPreview"', html)
        self.assertIn('id="viewerTranslationExportTxt"', html)
        self.assertIn("openTranslationSubmissionPdfPreview", js)
        self.assertIn("buildTranslationSubmissionViewerHtml", js)
        self.assertIn("is-translation-preview", js)
        self.assertIn("is-translation-preview", css)
        self.assertIn("layoutDevicePages", js)
        self.assertIn("wrapViewerEpisode", js)
        self.assertIn("openViewerMode(\"pdf\"", js)
        for locale in (ko, en, es):
            self.assertIn("index.번역_결과_보기", locale)
            self.assertIn("index.텍스트로_내보내기", locale)
            self.assertIn("index.PDF로_보기", locale)
            self.assertIn("index.번역_원고", locale)
            self.assertIn("index.투고_패키지를_아직_만들지_않았어요", locale)

    def test_submission_result_and_export_use_saved_package_and_segments(self) -> None:
        project_id, _ = self._make_story()
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/translation/jobs",
            self._translation_job_body(),
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        status, missing = self.request(
            "GET", f"/api/translation/jobs/{job_id}/submission_result"
        )
        self.assertEqual(status, 404)

        status, listing = self.request(
            "GET", f"/api/translation/jobs/{job_id}/segments?chapter=1"
        )
        self.assertEqual(status, 200)
        segments = listing["segments"]
        self.assertGreaterEqual(len(segments), 2)
        first_id = int(segments[0]["id"])
        second_id = int(segments[1]["id"])
        with app.database() as connection:
            connection.execute(
                "UPDATE translation_segments "
                "SET translated_text = ?, polish_text = ? WHERE id = ?",
                ("First draft.", "Polished rain.", first_id),
            )
            connection.execute(
                "UPDATE translation_segments SET translated_text = ? WHERE id = ?",
                ("One umbrella covered them both.", second_id),
            )
            app.get_translation_extras_repository(connection).save_submission_package(
                job_id,
                "Two strangers share an umbrella.",
                "A rainy city brings two people together.",
            )

        status, result = self.request(
            "GET", f"/api/translation/jobs/{job_id}/submission_result"
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            result["logline_translated"], "Two strangers share an umbrella."
        )
        self.assertIn("rainy city", result["synopsis_translated"])
        self.assertEqual(result["project_title"], "비의 도시")
        self.assertTrue(result["chapters"])
        manuscript = result["chapters"][0]["text"]
        self.assertIn("Polished rain.", manuscript)
        self.assertNotIn("First draft.", manuscript)
        self.assertIn("One umbrella covered them both.", manuscript)

        status, exported = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/export_submission",
            {"format": "txt"},
        )
        self.assertEqual(status, 200)
        raw = str(exported.get("raw") or "")
        self.assertIn("Two strangers share an umbrella.", raw)
        self.assertIn("A rainy city brings two people together.", raw)
        self.assertIn("Polished rain.", raw)

        status, docx, content_type = self.request_bytes(
            "POST",
            f"/api/translation/jobs/{job_id}/export_submission",
            {"format": "docx"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(docx.startswith(b"PK"))
        self.assertIn("wordprocessingml", content_type)

        status, bad = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/export_submission",
            {"format": "pdf"},
        )
        self.assertEqual(status, 400)

    def test_approve_all_skips_manual_review_and_already_approved(self) -> None:
        project_id, _ = self._make_story(
            "첫 문단입니다.\n\n둘째 문단입니다.\n\n셋째 문단입니다."
        )
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/translation/jobs",
            self._translation_job_body(),
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        status, payload = self.request(
            "GET", f"/api/translation/jobs/{job_id}/segments?chapter=1"
        )
        self.assertEqual(status, 200)
        segments = payload["segments"]
        self.assertGreaterEqual(len(segments), 3)
        first_id = int(segments[0]["id"])
        review_id = int(segments[1]["id"])
        pending_id = int(segments[2]["id"])
        with app.database() as connection:
            connection.execute(
                "UPDATE translation_segments SET translated_text = ? WHERE id = ?",
                ("First paragraph.", first_id),
            )
            connection.execute(
                "UPDATE translation_segments SET translated_text = ?, "
                "needs_manual_review = 1 WHERE id = ?",
                ("Needs a check.", review_id),
            )
            connection.execute(
                "UPDATE translation_segments SET translated_text = ? WHERE id = ?",
                ("Pending paragraph.", pending_id),
            )
            connection.commit()
        status, approved = self.request(
            "POST",
            f"/api/translation/segments/{first_id}/approve",
            {"is_approved": True},
        )
        self.assertEqual(status, 200)
        self.assertTrue(approved["is_approved"])

        status, bulk = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/chapters/1/approve_all",
            {},
        )
        self.assertEqual(status, 200)
        self.assertEqual(int(bulk["approved_count"]), 1)
        self.assertEqual(int(bulk["already_approved"]), 1)
        self.assertEqual(int(bulk["skipped_manual_review"]), 1)
        by_id = {int(item["id"]): item for item in bulk["segments"]}
        self.assertTrue(by_id[first_id]["is_approved"])
        self.assertTrue(by_id[pending_id]["is_approved"])
        self.assertFalse(by_id[review_id]["is_approved"])
        self.assertTrue(by_id[review_id]["needs_manual_review"])

        status, again = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/chapters/1/approve_all",
            {},
        )
        self.assertEqual(status, 200)
        self.assertEqual(int(again["approved_count"]), 0)
        self.assertEqual(int(again["already_approved"]), 2)
        self.assertEqual(int(again["skipped_manual_review"]), 1)
        again_by_id = {int(item["id"]): item for item in again["segments"]}
        self.assertFalse(again_by_id[review_id]["is_approved"])

    def test_approve_all_ui_is_wired(self) -> None:
        root = Path(__file__).resolve().parents[1]
        js = (root / "web" / "app.js").read_text(encoding="utf-8")
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        css = (root / "web" / "styles.css").read_text(encoding="utf-8")
        ko = (root / "web" / "locales" / "ko.json").read_text(encoding="utf-8")
        en = (root / "web" / "locales" / "en.json").read_text(encoding="utf-8")
        es = (root / "web" / "locales" / "es.json").read_text(encoding="utf-8")
        self.assertIn('id="translationApproveAllButton"', html)
        self.assertIn("translationApproveToolbar", html)
        self.assertIn("approveAllTranslationSegments", js)
        self.assertIn("window.confirm", js)
        self.assertIn("/chapters/${chapter}/approve_all", js)
        self.assertIn("needs_manual_review", js)
        self.assertIn("translation-approve-toolbar", css)
        for locale in (ko, en, es):
            self.assertIn("index.전체_승인", locale)
            self.assertIn("index.n개_문단을_한_번에_승인할까요", locale)
            self.assertIn("index.n개는_확인이_필요해서_제외됐어요", locale)

    def test_chapter_polish_retries_count_mismatch_and_applies_all(self) -> None:
        project_id, _ = self._make_story()
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/translation/jobs",
            self._translation_job_body(),
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        with app.database() as connection:
            connection.execute(
                """
                UPDATE translation_segments
                SET translated_text = CASE segment_order
                    WHEN 1 THEN 'She walked toward the door. She opened the door.'
                    ELSE 'She stepped through it.'
                END,
                is_approved = 1
                WHERE translation_job_id = ? AND chapter_number = 1
                """,
                (job_id,),
            )
        self.polish_mismatch_left = 1
        status, polished = self.request(
            "POST", f"/api/translation/jobs/{job_id}/chapters/1/polish", {}
        )
        self.assertEqual(status, 200)
        polish_calls = [
            call for call in self.calls
            if "[핵심 임무 — 반드시 수행" in call["prompt"]
        ]
        self.assertEqual(len(polish_calls), 2)
        self.assertEqual(
            [int(item["segment_order"]) for item in polished["segments"]],
            [1, 2],
        )
        status, applied = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/chapters/1/polish/apply_all",
            {},
        )
        self.assertEqual(status, 200)
        self.assertTrue(
            all(item["polish_choice"] == "apply" for item in applied["segments"])
        )
        self.assertTrue(
            all(item["polished_text"] for item in applied["segments"])
        )
        self.assertEqual(applied["chapter_polish_decided_count"], 2)

    def test_spanish_job_uses_spanish_across_translation_pipeline(self) -> None:
        project_id, _ = self._make_story()
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/translation/jobs",
            self._translation_job_body(
                target_language="es",
                culture_localization_level="moderate",
            ),
        )
        self.assertEqual(status, 201)
        self.assertEqual(created.get("target_language"), "es")
        job_id = int(created["id"])
        call_start = len(self.calls)
        self._start_and_confirm(job_id)

        status, translated = self.request(
            "POST", f"/api/translation/jobs/{job_id}/proceed_to_translation", {}
        )
        self.assertEqual(status, 200)
        segments = translated.get("segments") or []
        self.assertTrue(segments)
        self.assertTrue(
            all(
                "Traducción por lotes" in str(item.get("translated_text") or "")
                for item in segments
            )
        )
        status, retrans = self.request(
            "POST",
            f"/api/translation/segments/{int(segments[0]['id'])}/retranslate",
            {},
        )
        self.assertEqual(status, 200)
        self.assertIn("Llovía", retrans.get("translated_text") or "")
        for segment in segments:
            status, _ = self.request(
                "POST",
                f"/api/translation/segments/{int(segment['id'])}/approve",
                {"is_approved": True},
            )
            self.assertEqual(status, 200)

        status, polished = self.request(
            "POST", f"/api/translation/jobs/{job_id}/chapters/1/polish", {}
        )
        self.assertEqual(status, 200)
        self.assertIn(
            "La lluvia",
            str(polished["segments"][0].get("polish_proposal_text") or ""),
        )
        segment_id = int(segments[0]["id"])
        status, _ = self.request(
            "POST",
            "/api/translation/word_context",
            {"segment_id": segment_id, "word": "lluvia"},
        )
        self.assertEqual(status, 200)
        status, _ = self.request(
            "POST",
            "/api/translation/chat",
            {
                "job_id": job_id,
                "segment_id": segment_id,
                "message": "더 자연스럽게 바꿔줘.",
            },
        )
        self.assertEqual(status, 201)
        status, package = self.request(
            "POST", f"/api/translation/jobs/{job_id}/generate_submission_package", {}
        )
        self.assertEqual(status, 200)
        self.assertIn(
            "Dos desconocidos",
            package["submission_package"]["logline_translated"],
        )

        prompts = [
            str(call["prompt"])
            for call in self.calls[call_start:]
            if str(call.get("prompt") or "").strip()
        ]
        spanish_prompts = [
            prompt
            for prompt in prompts
            if "스페인어" in prompt or "스페인어권" in prompt
        ]
        self.assertGreaterEqual(len(spanish_prompts), 8)
        self.assertTrue(all("영어권" not in prompt for prompt in spanish_prompts))
        self.assertTrue(any("스페인어 철자·발음" in prompt for prompt in prompts))
        self.assertTrue(any("[문화반영범위]: moderate" in prompt for prompt in prompts))

        status, dictionary = self.request(
            "GET",
            "/api/translation/dictionary?word=canci%C3%B3n&target_language=es",
        )
        self.assertEqual(status, 200)
        self.assertTrue(dictionary.get("found"))
        self.assertEqual(self.dictionary_languages[-1], "es")

    def test_french_job_uses_french_across_translation_pipeline(self) -> None:
        project_id, _ = self._make_story()
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/translation/jobs",
            self._translation_job_body(
                target_language="fr",
                culture_localization_level="moderate",
            ),
        )
        self.assertEqual(status, 201)
        self.assertEqual(created.get("target_language"), "fr")
        job_id = int(created["id"])
        call_start = len(self.calls)
        self._start_and_confirm(job_id)

        status, translated = self.request(
            "POST", f"/api/translation/jobs/{job_id}/proceed_to_translation", {}
        )
        self.assertEqual(status, 200)
        segments = translated.get("segments") or []
        self.assertTrue(segments)
        self.assertTrue(
            all(
                "Traduction groupée" in str(item.get("translated_text") or "")
                for item in segments
            )
        )
        status, retrans = self.request(
            "POST",
            f"/api/translation/segments/{int(segments[0]['id'])}/retranslate",
            {},
        )
        self.assertEqual(status, 200)
        self.assertIn("Il pleuvait", retrans.get("translated_text") or "")
        for segment in segments:
            status, _ = self.request(
                "POST",
                f"/api/translation/segments/{int(segment['id'])}/approve",
                {"is_approved": True},
            )
            self.assertEqual(status, 200)

        status, polished = self.request(
            "POST", f"/api/translation/jobs/{job_id}/chapters/1/polish", {}
        )
        self.assertEqual(status, 200)
        self.assertIn(
            "La pluie",
            str(polished["segments"][0].get("polish_proposal_text") or ""),
        )
        segment_id = int(segments[0]["id"])
        status, _ = self.request(
            "POST",
            "/api/translation/word_context",
            {"segment_id": segment_id, "word": "pluie"},
        )
        self.assertEqual(status, 200)
        status, _ = self.request(
            "POST",
            "/api/translation/chat",
            {
                "job_id": job_id,
                "segment_id": segment_id,
                "message": "더 자연스럽게 바꿔줘.",
            },
        )
        self.assertEqual(status, 201)
        status, package = self.request(
            "POST", f"/api/translation/jobs/{job_id}/generate_submission_package", {}
        )
        self.assertEqual(status, 200)
        self.assertIn(
            "Deux inconnus",
            package["submission_package"]["logline_translated"],
        )

        prompts = [
            str(call["prompt"])
            for call in self.calls[call_start:]
            if str(call.get("prompt") or "").strip()
        ]
        french_prompts = [
            prompt
            for prompt in prompts
            if "프랑스어" in prompt or "프랑스어권" in prompt
        ]
        self.assertGreaterEqual(len(french_prompts), 8)
        self.assertTrue(all("영어권" not in prompt for prompt in french_prompts))
        self.assertTrue(any("기메 따옴표(« … »)" in prompt for prompt in prompts))
        self.assertTrue(any("프랑스어 발음" in prompt for prompt in prompts))
        self.assertTrue(any("[문화반영범위]: moderate" in prompt for prompt in prompts))

        status, dictionary = self.request(
            "GET",
            "/api/translation/dictionary?word=%C3%A9t%C3%A9&target_language=fr",
        )
        self.assertEqual(status, 200)
        self.assertTrue(dictionary.get("found"))
        self.assertEqual(self.dictionary_languages[-1], "fr")

    def test_chat_route_uses_chat_prompt_with_segment_context(self) -> None:
        project_id, _ = self._make_story()
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/translation/jobs",
            {
                "culture_localization_level": "as_is",
                "translate_all_chapters": True,
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

        def _spy(question, settings=None, target_language="en"):
            captured.append(
                {
                    "question": question,
                    "settings": dict(settings or {}),
                    "target_language": target_language,
                }
            )
            return original(question, settings, target_language=target_language)

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
        status, _ = self.request(
            "POST",
            f"/api/projects/{project_id}/characters",
            {"name": "안테레스"},
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
            "POST", f"/api/projects/{project_id}/translation/jobs", self._translation_job_body()
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
        self.assertIn("안테레스", names)
        self.assertIn("세리나", names)
        self.assertIn("우산골", names)
        index_item = next(item for item in nouns if item["source_term"] == "이오나")
        self.assertEqual(index_item["source"], "character_index")
        self.assertEqual(index_item["romanized"], "Iona")
        self.assertEqual(index_item["fit_judgment"], "fits")
        self.assertTrue(str(index_item.get("judgment_reason") or "").strip())
        self.assertFalse(str(index_item.get("final_term") or "").strip())
        anteres = next(item for item in nouns if item["source_term"] == "안테레스")
        self.assertEqual(anteres["source"], "character_index")
        self.assertEqual(anteres["romanized"], "Anteres")
        self.assertTrue(str(anteres.get("judgment_reason") or "").strip())
        detected = next(item for item in nouns if item["source_term"] == "우산골")
        self.assertEqual(detected["source"], "ai_detected")
        self.assertEqual(detected["fit_judgment"], "does_not_fit")
        self.assertFalse(str(detected.get("final_term") or "").strip())
        noun_prompt = next(
            call["prompt"]
            for call in reversed(self.calls)
            if '"proper_nouns"' in call["prompt"] or "fit_judgment" in call["prompt"]
        )
        self.assertIn("반드시 같은 기준으로 판정하세요", noun_prompt)
        self.assertNotIn("건너뛰세요", noun_prompt)

        status, _ = self.request(
            "POST", f"/api/translation/jobs/{job_id}/confirm_proper_nouns", {}
        )
        self.assertEqual(status, 400)

        self._decide_empty_nouns(
            job_id,
            {
                "우산골": {
                    "user_decision": "rename",
                    "final_term": "Rainvale",
                }
            },
        )

        status, confirmed = self.request(
            "POST", f"/api/translation/jobs/{job_id}/confirm_proper_nouns", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(confirmed["status"], "draft")
        self.assertTrue(confirmed.get("proper_nouns_confirmed"))

    def test_add_and_delete_proper_nouns_update_translation_glossary(self) -> None:
        project_id, _ = self._make_story()
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", self._translation_job_body()
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        status, started = self.request(
            "POST", f"/api/translation/jobs/{job_id}/start", {}
        )
        self.assertEqual(status, 200)
        nouns = started.get("proper_nouns") or []
        if not nouns:
            status, listing = self.request(
                "GET", f"/api/translation/jobs/{job_id}/proper_nouns"
            )
            self.assertEqual(status, 200)
            nouns = listing["proper_nouns"]
        detected = next(
            item for item in nouns if item["source_term"] == "우산골"
        )
        status, after_delete = self.request(
            "DELETE", f"/api/translation/proper_nouns/{detected['id']}"
        )
        self.assertEqual(status, 200)
        names = {item["source_term"] for item in after_delete["proper_nouns"]}
        self.assertNotIn("우산골", names)

        status, added = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/proper_nouns",
            {
                "source_term": "뮤온",
                "term_type": "item",
                "final_term": "Muon",
            },
        )
        self.assertEqual(status, 201)
        created_noun = added["proper_noun"]
        self.assertEqual(created_noun["source_term"], "뮤온")
        self.assertEqual(created_noun["final_term"], "Muon")
        self.assertEqual(created_noun["term_type"], "item")
        self.assertEqual(created_noun["user_decision"], "rename")
        self.assertEqual(created_noun["source"], "user_added")
        names = {item["source_term"] for item in added["proper_nouns"]}
        self.assertIn("뮤온", names)
        self.assertNotIn("우산골", names)

        status, edited = self.request(
            "POST",
            f"/api/translation/proper_nouns/{created_noun['id']}/decide",
            {"user_decision": "rename", "final_term": "Myuon"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(edited["final_term"], "Myuon")

        status, duplicate = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/proper_nouns",
            {"source_term": "뮤온", "term_type": "item", "final_term": "Muon"},
        )
        self.assertEqual(status, 400)

        for item in added["proper_nouns"]:
            if item["source_term"] == "뮤온":
                continue
            if str(item.get("final_term") or "").strip():
                continue
            status, _ = self.request(
                "POST",
                f"/api/translation/proper_nouns/{item['id']}/decide",
                {"user_decision": "keep_as_is", "final_term": item["source_term"]},
            )
            self.assertEqual(status, 200)

        status, confirmed = self.request(
            "POST", f"/api/translation/jobs/{job_id}/confirm_proper_nouns", {}
        )
        self.assertEqual(status, 200)
        self.assertTrue(confirmed.get("proper_nouns_confirmed"))

        status, done = self.request(
            "POST", f"/api/translation/jobs/{job_id}/proceed_to_translation", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(done["status"], "translated")
        paragraph_prompt = next(
            call["prompt"]
            for call in reversed(self.calls)
            if "[확정된 고유명사 — 반드시 이 표기를 그대로 사용하세요]" in str(call.get("prompt") or "")
        )
        glossary = paragraph_prompt.split(
            "[확정된 고유명사 — 반드시 이 표기를 그대로 사용하세요]", 1
        )[1].split("[문화반영범위]", 1)[0]
        self.assertIn("뮤온→Myuon", glossary)
        self.assertNotIn("우산골", glossary)

    def test_refresh_proper_nouns_keeps_locked_and_user_edits(self) -> None:
        project_id, _ = self._make_story()
        status, _ = self.request(
            "POST",
            f"/api/projects/{project_id}/characters",
            {"name": "이오나"},
        )
        self.assertEqual(status, 201)
        status, _ = self.request(
            "POST",
            f"/api/projects/{project_id}/characters",
            {"name": "안테레스"},
        )
        self.assertEqual(status, 201)
        with app.database() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO project_index(project_id) VALUES (?)",
                (project_id,),
            )
            connection.execute(
                "UPDATE project_index SET characters_json = ? WHERE project_id = ?",
                (json.dumps(["세리나"], ensure_ascii=False), project_id),
            )
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", self._translation_job_body()
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        status, extracted = self.request(
            "POST", f"/api/translation/jobs/{job_id}/extract_proper_nouns", {}
        )
        self.assertEqual(status, 200)
        nouns = {item["source_term"]: item for item in extracted["proper_nouns"]}
        self.assertEqual(nouns["이오나"]["romanized"], "Iona")
        self.assertEqual(nouns["안테레스"]["romanized"], "Anteres")
        self.assertEqual(nouns["세리나"]["romanized"], "Serena")
        self.assertIn("우산골", nouns)

        status, _ = self.request(
            "POST",
            f"/api/translation/proper_nouns/{nouns['이오나']['id']}/decide",
            {"user_decision": "keep_romanized", "final_term": "Iona"},
        )
        self.assertEqual(status, 200)
        status, _ = self.request(
            "POST",
            f"/api/translation/proper_nouns/{nouns['세리나']['id']}/decide",
            {"user_decision": "keep_as_is", "final_term": "세리나"},
        )
        self.assertEqual(status, 200)
        status, _ = self.request(
            "DELETE", f"/api/translation/proper_nouns/{nouns['우산골']['id']}"
        )
        self.assertEqual(status, 200)
        status, added = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/proper_nouns",
            {
                "source_term": "뮤온",
                "term_type": "item",
                "final_term": "Muon",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(added["proper_noun"]["source"], "user_added")
        muon_id = int(added["proper_noun"]["id"])

        status, refreshed = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/extract_proper_nouns",
            {"refresh": True},
        )
        self.assertEqual(status, 200)
        after = {item["source_term"]: item for item in refreshed["proper_nouns"]}
        self.assertEqual(after["이오나"]["romanized"], "Iona")
        self.assertEqual(after["이오나"]["final_term"], "Iona")
        self.assertEqual(after["이오나"]["user_decision"], "keep_romanized")
        self.assertEqual(after["안테레스"]["romanized"], "Anteres 2")
        self.assertFalse(str(after["안테레스"].get("final_term") or "").strip())
        self.assertIsNone(after["안테레스"].get("user_decision"))
        self.assertEqual(after["세리나"]["romanized"], "Serena 2")
        self.assertFalse(str(after["세리나"].get("final_term") or "").strip())
        self.assertEqual(after["뮤온"]["final_term"], "Muon")
        self.assertEqual(after["뮤온"]["source"], "user_added")
        self.assertEqual(int(after["뮤온"]["id"]), muon_id)
        self.assertNotIn("우산골", after)
        job = refreshed.get("job") or {}
        self.assertFalse(job.get("proper_nouns_confirmed"))

        status, extracted_again = self.request(
            "POST", f"/api/translation/jobs/{job_id}/extract_proper_nouns", {}
        )
        self.assertEqual(status, 200)
        again_names = {
            item["source_term"] for item in extracted_again["proper_nouns"]
        }
        self.assertNotIn("우산골", again_names)
        self.assertEqual(
            next(
                item["romanized"]
                for item in extracted_again["proper_nouns"]
                if item["source_term"] == "이오나"
            ),
            "Iona",
        )

    def test_refresh_proper_nouns_ui_is_wired(self) -> None:
        root = Path(__file__).resolve().parents[1]
        js = (root / "web" / "app.js").read_text(encoding="utf-8")
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        css = (root / "web" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('id="translationRefreshNounsButton"', html)
        self.assertIn("translation-nouns-toolbar", html)
        self.assertIn('data-i18n="index.전체_새로고침"', html)
        self.assertIn("refreshTranslationProperNouns", js)
        self.assertIn("refresh: true", js)
        self.assertIn("/extract_proper_nouns", js)
        self.assertIn("translation-nouns-toolbar", css)
        ko = (root / "web" / "locales" / "ko.json").read_text(encoding="utf-8")
        en = (root / "web" / "locales" / "en.json").read_text(encoding="utf-8")
        es = (root / "web" / "locales" / "es.json").read_text(encoding="utf-8")
        self.assertIn("index.전체_새로고침", ko)
        self.assertIn("index.전체_새로고침", en)
        self.assertIn("index.전체_새로고침", es)
        self.assertIn("index.고유명사를_다시_확인하고_있어요", ko)
        self.assertIn("index.고유명사_목록을_새로_확인했어요", ko)
        self.assertIn("index.고유명사를_다시_확인하고_있어요", js)
        self.assertIn("index.고유명사_목록을_새로_확인했어요", js)

    def _count_steps(self, *needles: str) -> int:
        return sum(
            1
            for call in self.calls
            if any(needle in call["prompt"] for needle in needles)
        )

    def test_start_pipeline_skips_completed_steps_on_rerun(self) -> None:
        project_id, _ = self._make_story()
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", self._translation_job_body()
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
            "POST", f"/api/projects/{project_id}/translation/jobs", self._translation_job_body()
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
            "POST", f"/api/projects/{project_id}/translation/jobs", self._translation_job_body()
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
        self._decide_empty_nouns(
            job_id,
            {
                "우산골": {
                    "user_decision": "rename",
                    "final_term": "Rainvale",
                }
            },
        )
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
        first_pass_batches = self.batch_calls

        self.fail_on_step = None
        status, done = self.request(
            "POST", f"/api/translation/jobs/{job_id}/proceed_to_translation", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(done["status"], "translated")
        self.assertGreaterEqual(done.get("skipped_segments") or 0, 1)
        self.assertEqual(self.batch_calls, first_pass_batches + 1)
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
        package_prompt = next(
            call["prompt"]
            for call in reversed(self.calls)
            if '"logline"' in str(call.get("prompt") or "")
        )
        self.assertIn("[완료된 번역 샘플", package_prompt)
        self.assertIn("Batch translation", package_prompt)
        package_calls = self._count_steps('"logline"')
        status, again = self.request(
            "POST", f"/api/translation/jobs/{job_id}/generate_submission_package", {}
        )
        self.assertEqual(status, 200)
        self.assertIn("submission_package", again.get("skipped_steps") or [])
        self.assertEqual(self._count_steps('"logline"'), package_calls)

    def _start_and_confirm(self, job_id: int) -> None:
        status, _ = self.request("POST", f"/api/translation/jobs/{job_id}/start", {})
        self.assertEqual(status, 200)
        status, extracted = self.request(
            "GET", f"/api/translation/jobs/{job_id}/proper_nouns"
        )
        self.assertEqual(status, 200)
        for item in extracted.get("proper_nouns") or []:
            status, _ = self.request(
                "POST",
                f"/api/translation/proper_nouns/{item['id']}/decide",
                {"user_decision": "keep_as_is", "final_term": item["source_term"]},
            )
            self.assertEqual(status, 200)
        status, confirmed = self.request(
            "POST", f"/api/translation/jobs/{job_id}/confirm_proper_nouns", {}
        )
        self.assertEqual(status, 200)
        self.assertTrue(confirmed.get("proper_nouns_confirmed"))

    def test_empty_translated_text_falls_back_and_flags_manual_review(self) -> None:
        project_id, _ = self._make_story("싱긋")
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", self._translation_job_body()
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        self._start_and_confirm(job_id)
        self.empty_paragraph_mode = "always"
        status, done = self.request(
            "POST", f"/api/translation/jobs/{job_id}/proceed_to_translation", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(done["status"], "translated")
        self.assertFalse(done.get("pipeline_failed_step"))
        self.assertGreaterEqual(self.paragraph_calls, 3)
        status, listing = self.request(
            "GET", f"/api/translation/jobs/{job_id}/segments"
        )
        self.assertEqual(status, 200)
        segments = listing["segments"]
        self.assertTrue(segments)
        self.assertTrue(all(item.get("needs_manual_review") for item in segments))
        self.assertTrue(
            all(str(item.get("translated_text") or "") == "싱긋" for item in segments)
        )
        notes = json.dumps(segments[0].get("translation_notes_json") or [], ensure_ascii=False)
        self.assertIn("자동번역 실패로 원문이 유지되었습니다", notes)

    def test_empty_translated_text_retries_then_succeeds(self) -> None:
        project_id, _ = self._make_story("싱긋")
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", self._translation_job_body()
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        self._start_and_confirm(job_id)
        self.empty_paragraph_mode = "twice_then_ok"
        status, done = self.request(
            "POST", f"/api/translation/jobs/{job_id}/proceed_to_translation", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.paragraph_calls, 3)
        status, listing = self.request(
            "GET", f"/api/translation/jobs/{job_id}/segments"
        )
        segment = listing["segments"][0]
        self.assertEqual(
            segment.get("translated_text"),
            "It was raining the day they first met.",
        )
        self.assertFalse(segment.get("needs_manual_review"))

    def test_empty_paragraph_fallback_repeats_without_stopping(self) -> None:
        project_id, _ = self._make_story("싱긋")
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", self._translation_job_body()
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        self._start_and_confirm(job_id)
        self.empty_paragraph_mode = "always"
        for _ in range(5):
            status, payload = self.request(
                "POST", f"/api/translation/jobs/{job_id}/proceed_to_translation", {}
            )
            self.assertEqual(status, 200)
            self.assertEqual(payload["status"], "translated")
            self.assertFalse(payload.get("pipeline_failed_step"))
        status, listing = self.request(
            "GET", f"/api/translation/jobs/{job_id}/segments"
        )
        self.assertTrue(all(item.get("needs_manual_review") for item in listing["segments"]))

    def test_review_ui_marks_manual_review_segments(self) -> None:
        root = Path(__file__).resolve().parents[1]
        js = (root / "web" / "app.js").read_text(encoding="utf-8")
        css = (root / "web" / "styles.css").read_text(encoding="utf-8")
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("is-manual-review", js)
        self.assertIn("translation-manual-review-flag", js)
        self.assertIn("is-manual-review", css)
        self.assertIn("startTranslationWaitPoll", js)
        self.assertIn("index.잠시_대기_중_초", js)
        self.assertIn("translationPipelineWaitHint", html)
        self.assertIn("pipeline_wait", js)
        self.assertIn("pipeline_wait_seconds", js)
        ko = (root / "web" / "locales" / "ko.json").read_text(encoding="utf-8")
        en = (root / "web" / "locales" / "en.json").read_text(encoding="utf-8")
        es = (root / "web" / "locales" / "es.json").read_text(encoding="utf-8")
        self.assertIn("index.잠시_대기_중_초", ko)
        self.assertIn("index.잠시_대기_중_초", en)
        self.assertIn("index.잠시_대기_중_초", es)

    def test_chapter_polish_ui_replaces_per_paragraph_ai_button(self) -> None:
        root = Path(__file__).resolve().parents[1]
        js = (root / "web" / "app.js").read_text(encoding="utf-8")
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("translationChapterPolishStart", html)
        self.assertIn("translationChapterPolishApplyAll", html)
        self.assertIn("data-polish-choice", js)
        self.assertIn("/chapters/${chapter}/polish", js)
        self.assertNotIn("data-translation-polish=", js)
        self.assertNotIn("/segments/${segmentId}/polish`", js)

    def test_spanish_language_option_and_unicode_dictionary_tokens_are_enabled(self) -> None:
        root = Path(__file__).resolve().parents[1]
        js = (root / "web" / "app.js").read_text(encoding="utf-8")
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn(
            '<option value="es" data-i18n="app.스페인어">스페인어</option>',
            html,
        )
        self.assertIn(
            '<option value="fr" data-i18n="app.프랑스어">프랑스어</option>',
            html,
        )
        self.assertNotIn('<option value="es" disabled', html)
        self.assertNotIn('<option value="fr" disabled', html)
        self.assertIn(r"\p{L}\p{N}", js)
        self.assertIn("target_language=${encodeURIComponent", js)
        self.assertIn("index.투고는_보통_로그라인_시놉시스_샘플_3화", html)
        self.assertEqual(
            app.FREE_DICTIONARY_API_URL.format(language="fr"),
            "https://api.dictionaryapi.dev/api/v2/entries/fr/",
        )

    def test_proper_noun_alt_chips_group_under_rename(self) -> None:
        root = Path(__file__).resolve().parents[1]
        js = (root / "web" / "app.js").read_text(encoding="utf-8")
        css = (root / "web" / "styles.css").read_text(encoding="utf-8")
        ko = (root / "web" / "locales" / "ko.json").read_text(encoding="utf-8")
        en = (root / "web" / "locales" / "en.json").read_text(encoding="utf-8")
        es = (root / "web" / "locales" / "es.json").read_text(encoding="utf-8")
        self.assertIn("index.추천_이름", js)
        self.assertIn("translation-noun-rename", js)
        self.assertIn("translation-noun-alts-label", js)
        self.assertIn('input[type=\'radio\'][value=\'rename\']', js)
        self.assertIn("setTranslationNounAltSelected", js)
        self.assertIn("translation-noun-alts[hidden]", css)
        self.assertIn("추천 이름", ko)
        self.assertIn("Suggested names", en)
        self.assertIn("Nombres sugeridos", es)
        self.assertIn("index.추천_이름", ko)
        self.assertIn("index.추천_이름", en)
        self.assertIn("index.추천_이름", es)

    def test_rate_limit_retries_without_consuming_empty_text_attempts(self) -> None:
        project_id, _ = self._make_story("싱긋")
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", self._translation_job_body()
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        self._start_and_confirm(job_id)
        self.rate_limit_left = 2
        self.empty_paragraph_mode = "always"
        status, done = self.request(
            "POST", f"/api/translation/jobs/{job_id}/proceed_to_translation", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(done["status"], "translated")
        self.assertFalse(done.get("pipeline_failed_step"))
        self.assertEqual(self.rate_limit_raises, 2)
        self.assertEqual(self.paragraph_calls, 3)
        status, listing = self.request(
            "GET", f"/api/translation/jobs/{job_id}/segments"
        )
        self.assertTrue(listing["segments"][0].get("needs_manual_review"))

    def test_rate_limit_recovers_and_translates_remaining_paragraphs(self) -> None:
        project_id, _ = self._make_story()
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", self._translation_job_body()
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        self._start_and_confirm(job_id)
        self.rate_limit_left = 2
        status, done = self.request(
            "POST", f"/api/translation/jobs/{job_id}/proceed_to_translation", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(done["status"], "translated")
        self.assertFalse(done.get("pipeline_failed_step"))
        self.assertEqual(self.rate_limit_raises, 2)
        self.assertEqual(self.batch_calls, 1)
        self.assertEqual(self.paragraph_calls, 0)
        status, listing = self.request(
            "GET", f"/api/translation/jobs/{job_id}/segments"
        )
        texts = [str(item.get("translated_text") or "") for item in listing["segments"]]
        self.assertTrue(all(text.strip() for text in texts))
        self.assertFalse(any(item.get("needs_manual_review") for item in listing["segments"]))

    def test_first_pass_translates_187_paragraphs_in_six_batches(self) -> None:
        content = "\n\n".join(f"문단 {index}입니다." for index in range(1, 188))
        project_id, _ = self._make_story(content)
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", self._translation_job_body()
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        self._start_and_confirm(job_id)

        status, done = self.request(
            "POST", f"/api/translation/jobs/{job_id}/proceed_to_translation", {}
        )

        self.assertEqual(status, 200)
        self.assertEqual(done.get("translated_count"), 187)
        self.assertEqual(self.batch_calls, 6)
        self.assertEqual(self.paragraph_calls, 0)

    def test_batch_structure_mismatch_retries_three_times_then_splits(self) -> None:
        content = "\n\n".join(f"문단 {index}입니다." for index in range(1, 5))
        project_id, _ = self._make_story(content)
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", self._translation_job_body()
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        self._start_and_confirm(job_id)
        self.batch_structure_mismatch_left = 3

        status, done = self.request(
            "POST", f"/api/translation/jobs/{job_id}/proceed_to_translation", {}
        )

        self.assertEqual(status, 200)
        self.assertEqual(done.get("translated_count"), 4)
        self.assertEqual(self.batch_calls, 5)
        self.assertEqual(self.paragraph_calls, 0)

    def test_batch_id_order_mismatch_retries_whole_batch(self) -> None:
        project_id, _ = self._make_story("첫 문단입니다.\n\n둘째 문단입니다.")
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", self._translation_job_body()
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        self._start_and_confirm(job_id)
        self.batch_order_mismatch_left = 1

        status, done = self.request(
            "POST", f"/api/translation/jobs/{job_id}/proceed_to_translation", {}
        )

        self.assertEqual(status, 200)
        self.assertEqual(done.get("translated_count"), 2)
        self.assertEqual(self.batch_calls, 2)

    def test_gemini_gap_constant_matches_free_tier_rpm(self) -> None:
        self.assertGreaterEqual(self._orig_gap, 4.0)
        self.assertEqual(self._orig_gap, 4.5)

    def test_pipeline_wait_endpoint_reads_in_memory_countdown(self) -> None:
        project_id, _ = self._make_story()
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", self._translation_job_body()
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        app._set_translation_pipeline_wait(job_id, 12)
        try:
            status, payload = self.request(
                "GET", f"/api/translation/jobs/{job_id}/pipeline_wait"
            )
            self.assertEqual(status, 200)
            self.assertGreater(payload.get("pipeline_wait_seconds") or 0, 0)
            status, job = self.request("GET", f"/api/translation/jobs/{job_id}")
            self.assertEqual(status, 200)
            self.assertGreater(job.get("pipeline_wait_seconds") or 0, 0)
        finally:
            app._clear_translation_pipeline_wait(job_id)
        status, cleared = self.request(
            "GET", f"/api/translation/jobs/{job_id}/pipeline_wait"
        )
        self.assertEqual(status, 200)
        self.assertEqual(cleared.get("pipeline_wait_seconds") or 0, 0)

    def test_dictionary_lookup_found_and_missing(self) -> None:
        status, found = self.request("GET", "/api/translation/dictionary?word=hurried")
        self.assertEqual(status, 200)
        self.assertTrue(found.get("found"))
        self.assertEqual(found.get("phonetic"), "/ˈhʌrid/")
        self.assertEqual(found["meanings"][0]["part_of_speech"], "verb")
        self.assertEqual(found.get("status"), "ok")
        status, missing = self.request("GET", "/api/translation/dictionary?word=xyzzy")
        self.assertEqual(status, 200)
        self.assertFalse(missing.get("found"))
        self.assertEqual(missing.get("word"), "xyzzy")
        self.assertEqual(missing.get("status"), "not_found")

    def test_dictionary_lemma_fallback_ui_is_wired(self) -> None:
        root = Path(__file__).resolve().parents[1]
        js = (root / "web" / "app.js").read_text(encoding="utf-8")
        css = (root / "web" / "styles.css").read_text(encoding="utf-8")
        ko = (root / "web" / "locales" / "ko.json").read_text(encoding="utf-8")
        en = (root / "web" / "locales" / "en.json").read_text(encoding="utf-8")
        es = (root / "web" / "locales" / "es.json").read_text(encoding="utf-8")
        self.assertIn("looked_up_as", js)
        self.assertIn("translation-word-lemma", js)
        self.assertIn("index.원래_형태_word", js)
        self.assertIn("translation-word-lemma", css)
        self.assertIn("원래 형태: ${word}", ko)
        self.assertIn("index.원래_형태_word", en)
        self.assertIn("index.원래_형태_word", es)

    def test_dictionary_timeout_seconds_are_short(self) -> None:
        self.assertGreaterEqual(app.FREE_DICTIONARY_TIMEOUT_SECONDS, 4)
        self.assertLessEqual(app.FREE_DICTIONARY_TIMEOUT_SECONDS, 5)
        self.assertEqual(app.FREE_DICTIONARY_MAX_ATTEMPTS, 2)

    def test_dictionary_lookup_timeout_returns_lookup_failed(self) -> None:
        calls = {"n": 0}

        def _timeout(_word: str, _target_language: object = "en") -> tuple[int, object]:
            calls["n"] += 1
            raise TimeoutError("timed out")

        app.fetch_free_dictionary_payload = _timeout  # type: ignore[method-assign]
        status, payload = self.request(
            "GET", "/api/translation/dictionary?word=chronological"
        )
        self.assertEqual(status, 200)
        self.assertFalse(payload.get("found"))
        self.assertEqual(payload.get("status"), "lookup_failed")
        self.assertEqual(payload.get("word"), "chronological")
        self.assertEqual(calls["n"], 2)

    def test_dictionary_lookup_retries_timeout_then_succeeds(self) -> None:
        calls = {"n": 0}

        def _flaky(_word: str, _target_language: object = "en") -> tuple[int, object]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("timed out")
            return 200, [
                {
                    "word": "chronological",
                    "phonetic": "/ˌkrɒnəˈlɒdʒɪkəl/",
                    "meanings": [
                        {
                            "partOfSpeech": "adjective",
                            "definitions": [
                                {"definition": "arranged in the order of time"}
                            ],
                        }
                    ],
                }
            ]

        app.fetch_free_dictionary_payload = _flaky  # type: ignore[method-assign]
        status, payload = self.request(
            "GET", "/api/translation/dictionary?word=chronological"
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("found"))
        self.assertEqual(payload.get("status"), "ok")
        self.assertEqual(payload.get("word"), "chronological")
        self.assertEqual(calls["n"], 2)

    def test_dictionary_lookup_rate_limit_returns_lookup_failed(self) -> None:
        calls = {"n": 0}

        def _rate_limit(_word: str, _target_language: object = "en") -> tuple[int, object]:
            calls["n"] += 1
            return 429, {"title": "Too Many Requests"}

        app.fetch_free_dictionary_payload = _rate_limit  # type: ignore[method-assign]
        status, payload = self.request(
            "GET", "/api/translation/dictionary?word=chronological"
        )
        self.assertEqual(status, 200)
        self.assertFalse(payload.get("found"))
        self.assertEqual(payload.get("status"), "lookup_failed")
        self.assertEqual(calls["n"], 2)

    def test_separator_paragraph_detector(self) -> None:
        self.assertTrue(app.is_translation_separator_paragraph("===="))
        self.assertTrue(app.is_translation_separator_paragraph("----"))
        self.assertTrue(app.is_translation_separator_paragraph("***"))
        self.assertTrue(app.is_translation_separator_paragraph("———"))
        self.assertTrue(app.is_translation_separator_paragraph("~~~~"))
        self.assertFalse(app.is_translation_separator_paragraph("="))
        self.assertFalse(app.is_translation_separator_paragraph("==== 끝"))
        self.assertFalse(app.is_translation_separator_paragraph("hello"))

    def test_separator_paragraph_passthrough_skips_gemini(self) -> None:
        project_id, _ = self._make_story("첫 문단입니다.\n\n====\n\n다음 문단입니다.")
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", self._translation_job_body()
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        status, listing = self.request("GET", f"/api/translation/jobs/{job_id}/segments")
        self.assertEqual(status, 200)
        segments = listing["segments"]
        self.assertEqual(len(segments), 3)
        separator = next(item for item in segments if item["source_text"] == "====")
        self.assertEqual(separator["translated_text"], "====")
        self.assertFalse(separator.get("needs_manual_review"))
        self._start_and_confirm(job_id)
        before_batches = self.batch_calls
        before_paragraphs = self.paragraph_calls
        status, done = self.request(
            "POST", f"/api/translation/jobs/{job_id}/proceed_to_translation", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.batch_calls, before_batches + 1)
        self.assertEqual(self.paragraph_calls, before_paragraphs)
        status, listing = self.request("GET", f"/api/translation/jobs/{job_id}/segments")
        separator = next(item for item in listing["segments"] if item["source_text"] == "====")
        self.assertEqual(separator["translated_text"], "====")
        self.assertEqual(separator.get("polished_text"), "====")
        with app.database() as connection:
            connection.execute(
                "UPDATE translation_segments SET translated_text = ? WHERE id = ?",
                ("Ssugi...", int(separator["id"])),
            )
        status, fixed = self.request(
            "POST",
            f"/api/translation/segments/{int(separator['id'])}/retranslate",
            {},
        )
        self.assertEqual(status, 200)
        self.assertEqual(fixed.get("translated_text"), "====")
        self.assertFalse(fixed.get("needs_manual_review"))
        self.assertEqual(self.batch_calls, before_batches + 1)
        self.assertEqual(self.paragraph_calls, before_paragraphs)

    def test_word_context_uses_notes_then_cache_then_gemini(self) -> None:
        project_id, _ = self._make_story()
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", self._translation_job_body()
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

    def test_create_job_without_range_is_rejected(self) -> None:
        project_id, _ = self._make_story()
        status, payload = self.request(
            "POST", f"/api/projects/{project_id}/translation/jobs", {}
        )
        self.assertEqual(status, 400)
        self.assertIn("회차 범위", str(payload.get("error") or payload))

    def test_preview_recommends_sample_range_and_estimates_segments(self) -> None:
        texts = [
            f"{index}화 문단 하나.\n\n{index}화 문단 둘."
            for index in range(1, 6)
        ]
        project_id = self._make_story_with_episodes(texts)
        status, preview = self.request(
            "GET", f"/api/projects/{project_id}/translation/preview"
        )
        self.assertEqual(status, 200)
        self.assertEqual(preview["episode_count"], 5)
        self.assertEqual(preview["recommended_start_chapter"], 1)
        self.assertEqual(preview["recommended_end_chapter"], 3)
        self.assertEqual(preview["estimated_segments"], 6)
        self.assertFalse(preview["translate_all_chapters"])
        status, ranged = self.request(
            "GET",
            f"/api/projects/{project_id}/translation/preview?start_chapter=1&end_chapter=3",
        )
        self.assertEqual(status, 200)
        self.assertEqual(ranged["estimated_segments"], 6)
        status, all_preview = self.request(
            "GET",
            f"/api/projects/{project_id}/translation/preview?translate_all_chapters=1",
        )
        self.assertEqual(status, 200)
        self.assertTrue(all_preview["translate_all_chapters"])
        self.assertEqual(all_preview["estimated_segments"], 10)

    def test_create_job_seeds_only_selected_chapter_range(self) -> None:
        texts = [
            f"{index}화 문단 하나.\n\n{index}화 문단 둘."
            for index in range(1, 6)
        ]
        project_id = self._make_story_with_episodes(texts)
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/translation/jobs",
            {
                "target_language": "en",
                "start_chapter": 1,
                "end_chapter": 3,
                "translate_all_chapters": False,
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(int(created.get("seeded_segments") or 0), 6)
        self.assertEqual(int(created.get("start_chapter") or 0), 1)
        self.assertEqual(int(created.get("end_chapter") or 0), 3)
        self.assertEqual(int(created.get("cliffhanger_chapter") or 0), 3)
        self.assertFalse(created.get("translate_all_chapters"))
        job_id = int(created["id"])
        status, payload = self.request(
            "GET", f"/api/translation/jobs/{job_id}/segments"
        )
        self.assertEqual(status, 200)
        chapters = {int(row["chapter_number"]) for row in payload["segments"]}
        self.assertEqual(chapters, {1, 2, 3})
        self.assertEqual(len(payload["segments"]), 6)

    def test_create_job_explicit_all_seeds_every_episode(self) -> None:
        texts = [
            f"{index}화 문단 하나.\n\n{index}화 문단 둘."
            for index in range(1, 6)
        ]
        project_id = self._make_story_with_episodes(texts)
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/translation/jobs",
            self._translation_job_body(),
        )
        self.assertEqual(status, 201)
        self.assertEqual(int(created.get("seeded_segments") or 0), 10)
        self.assertTrue(created.get("translate_all_chapters"))
        self.assertEqual(int(created.get("start_chapter") or 0), 1)
        self.assertEqual(int(created.get("end_chapter") or 0), 5)
        job_id = int(created["id"])
        status, payload = self.request(
            "GET", f"/api/translation/jobs/{job_id}/segments"
        )
        self.assertEqual(status, 200)
        chapters = {int(row["chapter_number"]) for row in payload["segments"]}
        self.assertEqual(chapters, {1, 2, 3, 4, 5})

    def test_create_job_rejects_inverted_chapter_range(self) -> None:
        texts = [
            f"{index}화 문단 하나.\n\n{index}화 문단 둘."
            for index in range(1, 4)
        ]
        project_id = self._make_story_with_episodes(texts)
        status, payload = self.request(
            "POST",
            f"/api/projects/{project_id}/translation/jobs",
            {"start_chapter": 3, "end_chapter": 1},
        )
        self.assertEqual(status, 400)
        self.assertIn("시작 회차", str(payload.get("error") or payload))

    def _mark_job_translated(self, job_id: int, marker: str = "EN:") -> dict[int, str]:
        kept: dict[int, str] = {}
        with app.database() as connection:
            rows = connection.execute(
                "SELECT id, chapter_number, source_text FROM translation_segments "
                "WHERE translation_job_id = ?",
                (int(job_id),),
            ).fetchall()
            for row in rows:
                text = f"{marker} {row['source_text']}"
                connection.execute(
                    "UPDATE translation_segments "
                    "SET translated_text = ?, polish_text = ? WHERE id = ?",
                    (text, f"PL: {row['source_text']}", int(row["id"])),
                )
                kept[int(row["id"])] = text
            connection.execute(
                "UPDATE translation_jobs SET status = 'translated', "
                "proper_nouns_confirmed = 1 WHERE id = ?",
                (int(job_id),),
            )
        return kept

    def test_job_settings_shrink_keeps_remaining_translations(self) -> None:
        texts = [
            f"{index}화 문단 하나.\n\n{index}화 문단 둘."
            for index in range(1, 6)
        ]
        project_id = self._make_story_with_episodes(texts)
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/translation/jobs",
            {
                "target_language": "en",
                "start_chapter": 2,
                "end_chapter": 4,
                "translate_all_chapters": False,
            },
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        kept = self._mark_job_translated(job_id)
        status, preview = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/settings_preview",
            {"start_chapter": 2, "end_chapter": 3, "translate_all_chapters": False},
        )
        self.assertEqual(status, 200)
        self.assertEqual(preview["removed_chapters"], [4])
        self.assertEqual(preview["added_chapters"], [])
        self.assertTrue(preview["confirm_delete_required"])
        self.assertEqual(int(preview["deleted_segments"] or 0), 2)
        status, blocked = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/settings",
            {"start_chapter": 2, "end_chapter": 3, "translate_all_chapters": False},
        )
        self.assertEqual(status, 409)
        self.assertEqual(blocked.get("code"), "confirm_delete_required")
        status, updated = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/settings",
            {
                "start_chapter": 2,
                "end_chapter": 3,
                "translate_all_chapters": False,
                "confirm_delete": True,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(int(updated.get("start_chapter") or 0), 2)
        self.assertEqual(int(updated.get("end_chapter") or 0), 3)
        self.assertEqual(updated.get("status"), "translated")
        status, payload = self.request("GET", f"/api/translation/jobs/{job_id}/segments")
        self.assertEqual(status, 200)
        chapters = {int(row["chapter_number"]) for row in payload["segments"]}
        self.assertEqual(chapters, {2, 3})
        for row in payload["segments"]:
            self.assertEqual(row["translated_text"], kept[int(row["id"])])

    def test_job_settings_expand_seeds_only_new_chapters(self) -> None:
        texts = [
            f"{index}화 문단 하나.\n\n{index}화 문단 둘."
            for index in range(1, 6)
        ]
        project_id = self._make_story_with_episodes(texts)
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/translation/jobs",
            {
                "target_language": "en",
                "start_chapter": 2,
                "end_chapter": 4,
                "translate_all_chapters": False,
            },
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        kept = self._mark_job_translated(job_id)
        status, updated = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/settings",
            {"start_chapter": 2, "end_chapter": 5, "translate_all_chapters": False},
        )
        self.assertEqual(status, 200)
        self.assertEqual(int(updated.get("end_chapter") or 0), 5)
        self.assertEqual(updated.get("status"), "in_progress")
        self.assertGreater(int(updated.get("pending_segments") or 0), 0)
        self.assertEqual(int(updated.get("seeded_segments") or 0), 2)
        status, payload = self.request("GET", f"/api/translation/jobs/{job_id}/segments")
        self.assertEqual(status, 200)
        by_chapter: dict[int, list[dict]] = {}
        for row in payload["segments"]:
            by_chapter.setdefault(int(row["chapter_number"]), []).append(row)
        self.assertEqual(set(by_chapter), {2, 3, 4, 5})
        for chapter in (2, 3, 4):
            for row in by_chapter[chapter]:
                self.assertEqual(row["translated_text"], kept[int(row["id"])])
        for row in by_chapter[5]:
            self.assertFalse(str(row.get("translated_text") or "").strip())
        self.assertGreater(int(payload["job"].get("pending_segments") or 0), 0)

    def test_job_settings_culture_only_keeps_translated_text(self) -> None:
        texts = [
            f"{index}화 문단 하나.\n\n{index}화 문단 둘."
            for index in range(1, 4)
        ]
        project_id = self._make_story_with_episodes(texts)
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/translation/jobs",
            {
                "target_language": "en",
                "start_chapter": 1,
                "end_chapter": 2,
                "translate_all_chapters": False,
                "culture_localization_level": "moderate",
            },
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        kept = self._mark_job_translated(job_id)
        status, updated = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/settings",
            {
                "start_chapter": 1,
                "end_chapter": 2,
                "translate_all_chapters": False,
                "culture_localization_level": "as_is",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated.get("culture_localization_level"), "as_is")
        self.assertEqual(int(updated.get("start_chapter") or 0), 1)
        self.assertEqual(int(updated.get("end_chapter") or 0), 2)
        self.assertEqual(updated.get("status"), "translated")
        status, payload = self.request("GET", f"/api/translation/jobs/{job_id}/segments")
        self.assertEqual(status, 200)
        for row in payload["segments"]:
            self.assertEqual(row["translated_text"], kept[int(row["id"])])
            self.assertTrue(str(row.get("polished_text") or "").startswith("PL:"))

    def test_job_settings_shrink_untranslated_does_not_require_confirm(self) -> None:
        texts = [
            f"{index}화 문단 하나.\n\n{index}화 문단 둘."
            for index in range(1, 4)
        ]
        project_id = self._make_story_with_episodes(texts)
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/translation/jobs",
            {
                "target_language": "en",
                "start_chapter": 1,
                "end_chapter": 3,
                "translate_all_chapters": False,
            },
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        status, preview = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/settings_preview",
            {"start_chapter": 1, "end_chapter": 2, "translate_all_chapters": False},
        )
        self.assertEqual(status, 200)
        self.assertFalse(preview["confirm_delete_required"])
        status, updated = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/settings",
            {"start_chapter": 1, "end_chapter": 2, "translate_all_chapters": False},
        )
        self.assertEqual(status, 200)
        status, payload = self.request("GET", f"/api/translation/jobs/{job_id}/segments")
        chapters = {int(row["chapter_number"]) for row in payload["segments"]}
        self.assertEqual(chapters, {1, 2})

    def test_job_settings_deletes_leftover_segments_outside_new_range(self) -> None:
        texts = [
            f"{index}화 문단 하나.\n\n{index}화 문단 둘."
            for index in range(1, 6)
        ]
        project_id = self._make_story_with_episodes(texts)
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/translation/jobs",
            {
                "target_language": "en",
                "start_chapter": 1,
                "end_chapter": 1,
                "translate_all_chapters": False,
            },
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        with app.database() as connection:
            for chapter in (4, 5):
                connection.execute(
                    """
                    INSERT INTO translation_segments(
                        translation_job_id, chapter_number, segment_order, source_text,
                        created_at, updated_at
                    ) VALUES (?, ?, 1, '범위 밖 문단', datetime('now'), datetime('now'))
                    """,
                    (job_id, chapter),
                )
        status, preview = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/settings_preview",
            {"start_chapter": 1, "end_chapter": 1, "translate_all_chapters": False},
        )
        self.assertEqual(status, 200)
        self.assertEqual(preview["removed_chapters"], [4, 5])
        self.assertFalse(preview["confirm_delete_required"])
        self.assertEqual(int(preview["deleted_segments"] or 0), 2)
        status, updated = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/settings",
            {"start_chapter": 1, "end_chapter": 1, "translate_all_chapters": False},
        )
        self.assertEqual(status, 200)
        status, payload = self.request("GET", f"/api/translation/jobs/{job_id}/segments")
        self.assertEqual(status, 200)
        chapters = {int(row["chapter_number"]) for row in payload["segments"]}
        self.assertEqual(chapters, {1})

    def test_job_settings_leftover_translated_requires_confirm(self) -> None:
        texts = [
            f"{index}화 문단 하나.\n\n{index}화 문단 둘."
            for index in range(1, 4)
        ]
        project_id = self._make_story_with_episodes(texts)
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/translation/jobs",
            {
                "target_language": "en",
                "start_chapter": 1,
                "end_chapter": 1,
                "translate_all_chapters": False,
            },
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        with app.database() as connection:
            connection.execute(
                """
                INSERT INTO translation_segments(
                    translation_job_id, chapter_number, segment_order, source_text,
                    translated_text, created_at, updated_at
                ) VALUES (?, 3, 1, '범위 밖', 'Outside', datetime('now'), datetime('now'))
                """,
                (job_id,),
            )
        status, preview = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/settings_preview",
            {"start_chapter": 1, "end_chapter": 1, "translate_all_chapters": False},
        )
        self.assertEqual(status, 200)
        self.assertEqual(preview["removed_chapters"], [3])
        self.assertTrue(preview["confirm_delete_required"])
        status, blocked = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/settings",
            {"start_chapter": 1, "end_chapter": 1, "translate_all_chapters": False},
        )
        self.assertEqual(status, 409)
        status, updated = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/settings",
            {
                "start_chapter": 1,
                "end_chapter": 1,
                "translate_all_chapters": False,
                "confirm_delete": True,
            },
        )
        self.assertEqual(status, 200)
        status, payload = self.request("GET", f"/api/translation/jobs/{job_id}/segments")
        chapters = {int(row["chapter_number"]) for row in payload["segments"]}
        self.assertEqual(chapters, {1})

    def test_job_chapter_catalog_follows_range_not_leftover_segments(self) -> None:
        texts = [
            f"{index}화 문단 하나.\n\n{index}화 문단 둘."
            for index in range(1, 6)
        ]
        project_id = self._make_story_with_episodes(texts)
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/translation/jobs",
            {
                "target_language": "en",
                "start_chapter": 1,
                "end_chapter": 1,
                "translate_all_chapters": False,
            },
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        status, detail = self.request("GET", f"/api/translation/jobs/{job_id}")
        self.assertEqual(status, 200)
        self.assertEqual(
            [int(item["number"]) for item in detail.get("chapters") or []],
            [1],
        )
        with app.database() as connection:
            connection.execute(
                """
                INSERT INTO translation_segments(
                    translation_job_id, chapter_number, segment_order, source_text,
                    created_at, updated_at
                ) VALUES (?, 5, 1, '범위 밖 문단', datetime('now'), datetime('now'))
                """,
                (job_id,),
            )
        status, listing = self.request("GET", f"/api/translation/jobs/{job_id}/segments")
        self.assertEqual(status, 200)
        self.assertEqual(
            [int(item["number"]) for item in listing.get("chapters") or []],
            [1],
        )
        status, widened = self.request(
            "POST",
            f"/api/translation/jobs/{job_id}/settings",
            {"start_chapter": 1, "end_chapter": 3, "translate_all_chapters": False},
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            [int(item["number"]) for item in widened.get("chapters") or []],
            [1, 2, 3],
        )

    def test_scene_contexts_follow_job_range_not_leftover_rows(self) -> None:
        texts = [
            f"{index}화 문단 하나.\n\n{index}화 문단 둘."
            for index in range(1, 8)
        ]
        project_id = self._make_story_with_episodes(texts)
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/translation/jobs",
            {
                "target_language": "en",
                "start_chapter": 5,
                "end_chapter": 6,
                "translate_all_chapters": False,
            },
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        status, started = self.request(
            "POST", f"/api/translation/jobs/{job_id}/start", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            {
                int(item["chapter_number"])
                for item in started.get("scene_contexts") or []
            },
            {5, 6},
        )
        with app.database() as connection:
            connection.execute(
                """
                INSERT INTO translation_scene_contexts
                (translation_job_id, chapter_number, scene_order,
                 relationship_tag, mood_tag, situation_note, created_at)
                VALUES (?, 7, 1, '범위밖', '분위기', '7화 잔여', datetime('now'))
                """,
                (job_id,),
            )
        status, detail = self.request("GET", f"/api/translation/jobs/{job_id}")
        self.assertEqual(status, 200)
        numbers = {
            int(item["chapter_number"])
            for item in detail.get("scene_contexts") or []
        }
        self.assertEqual(numbers, {5, 6})

    def test_split_scenes_skips_leftover_segments_outside_range(self) -> None:
        texts = [
            f"{index}화 문단 하나.\n\n{index}화 문단 둘."
            for index in range(1, 4)
        ]
        project_id = self._make_story_with_episodes(texts)
        status, created = self.request(
            "POST",
            f"/api/projects/{project_id}/translation/jobs",
            {
                "target_language": "en",
                "start_chapter": 1,
                "end_chapter": 1,
                "translate_all_chapters": False,
            },
        )
        self.assertEqual(status, 201)
        job_id = int(created["id"])
        with app.database() as connection:
            connection.execute(
                """
                INSERT INTO translation_segments(
                    translation_job_id, chapter_number, segment_order, source_text,
                    created_at, updated_at
                ) VALUES (?, 3, 1, '범위 밖 문단', datetime('now'), datetime('now'))
                """,
                (job_id,),
            )
        before_scene_calls = self._count_steps("start_paragraph_index")
        status, started = self.request(
            "POST", f"/api/translation/jobs/{job_id}/start", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            {
                int(item["chapter_number"])
                for item in started.get("scene_contexts") or []
            },
            {1},
        )
        self.assertEqual(
            self._count_steps("start_paragraph_index") - before_scene_calls,
            1,
        )

    def test_episode_catalog_includes_volume_folder_path(self) -> None:
        status, project = self.request(
            "POST", "/api/projects", {"title": "권부 라벨", "main_genre": "판타지"}
        )
        self.assertEqual(status, 201)
        pid = int(project["id"])
        status, volume = self.request(
            "POST", f"/api/projects/{pid}/parts", {"title": "1권"}
        )
        self.assertEqual(status, 201)
        status, chapter = self.request(
            "POST",
            f"/api/projects/{pid}/chapters",
            {"title": "2화. 파가몬 제국에 가다", "part_id": volume["id"]},
        )
        self.assertEqual(status, 201)
        status, scene = self.request(
            "POST",
            f"/api/chapters/{chapter['id']}/scenes",
            {"title": "2화. 파가몬 제국에 가다"},
        )
        self.assertEqual(status, 201)
        status, preview = self.request(
            "GET",
            f"/api/projects/{pid}/translation/preview?translate_all_chapters=1",
        )
        self.assertEqual(status, 200)
        episodes = preview.get("episodes") or []
        self.assertTrue(episodes)
        first = episodes[0]
        self.assertEqual(first.get("folder_path"), "1권")
        self.assertEqual(first.get("chapter_title"), "2화. 파가몬 제국에 가다")
        self.assertEqual(int(first["number"]), 1)


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

    def test_empty_translated_text_is_blank_not_error(self) -> None:
        raw = '```json\n{\n  "translated_text": "",\n  "translation_notes": []\n}\n```'
        text, notes = app._parse_paragraph_translation_output(raw)
        self.assertEqual(text, "")
        self.assertEqual(notes, [])

    def test_fallback_keeps_source_and_manual_review_note(self) -> None:
        text, notes = app._fallback_paragraph_translation("싱긋", [])
        self.assertEqual(text, "싱긋")
        self.assertEqual(notes[0]["needs_manual_review"], True)
        self.assertIn("원문이 유지", notes[0]["note"])

    def test_empty_output_retries_five_times_without_raising(self) -> None:
        calls = {"n": 0}

        def _fake_gemini(prompt: str, **kwargs: object) -> str:
            calls["n"] += 1
            return '{"translated_text": "", "translation_notes": []}'

        orig_gemini = app._translation_pipeline_gemini
        orig_delay = app._paragraph_empty_retry_delay
        app._translation_pipeline_gemini = _fake_gemini  # type: ignore[assignment]
        app._paragraph_empty_retry_delay = lambda: 0.0  # type: ignore[assignment]
        try:
            for _ in range(5):
                text, _notes, used = app._translate_paragraph_with_retries(
                    segment_id=1,
                    source_text="싱긋",
                    prompt="short",
                )
                self.assertTrue(used)
                self.assertEqual(text, "싱긋")
        finally:
            app._translation_pipeline_gemini = orig_gemini  # type: ignore[assignment]
            app._paragraph_empty_retry_delay = orig_delay  # type: ignore[assignment]
        self.assertEqual(calls["n"], 15)


class TranslationRateLimitHelperTests(unittest.TestCase):
    def test_detects_429_and_rate_limit_codes(self) -> None:
        limited = gemini_client.GeminiError(
            "Please retry in 8.4s.",
            code="rate_limit",
            http_status=429,
            retry_after=8.4,
        )
        quota = gemini_client.GeminiError("quota", code="quota", http_status=429)
        other = gemini_client.GeminiError("scene_split failed")
        self.assertTrue(app._is_translation_rate_limit_error(limited))
        self.assertTrue(app._is_translation_rate_limit_error(quota))
        self.assertFalse(app._is_translation_rate_limit_error(other))
        self.assertFalse(app._is_translation_rate_limit_error(ValueError("empty")))

    def test_parses_retry_in_seconds_and_defaults_to_ten(self) -> None:
        with_retry = gemini_client.GeminiError(
            "Resource exhausted. Please retry in 17.59s.",
            code="rate_limit",
            http_status=429,
        )
        self.assertAlmostEqual(
            app._translation_rate_limit_wait_seconds(with_retry), 17.59
        )
        explicit = gemini_client.GeminiError(
            "ignored",
            code="rate_limit",
            http_status=429,
            retry_after=3.5,
        )
        self.assertEqual(app._translation_rate_limit_wait_seconds(explicit), 3.5)
        missing = gemini_client.GeminiError(
            "You exceeded your current quota",
            code="quota",
            http_status=429,
        )
        self.assertEqual(app._translation_rate_limit_wait_seconds(missing), 10.0)

    def test_wrapper_retries_429_without_empty_text_loop(self) -> None:
        calls = {"n": 0, "sleeps": []}

        def _fake_generate(prompt: str, **kwargs: object) -> str:
            calls["n"] += 1
            if calls["n"] <= 2:
                raise gemini_client.GeminiError(
                    "Please retry in 8.4s.",
                    code="rate_limit",
                    http_status=429,
                    retry_after=8.4,
                )
            return '{"translated_text": "She smiled.", "translation_notes": []}'

        orig_generate = gemini_client.generate_text
        orig_configured = gemini_client.is_configured
        orig_sleep = app._translation_sleep
        orig_delay = app._paragraph_empty_retry_delay
        gemini_client.generate_text = _fake_generate  # type: ignore[method-assign]
        gemini_client.is_configured = lambda: True  # type: ignore[method-assign]
        app._translation_sleep = lambda seconds: calls["sleeps"].append(seconds)  # type: ignore[assignment]
        app._paragraph_empty_retry_delay = lambda: (_ for _ in ()).throw(  # type: ignore[assignment]
            AssertionError("empty-text retry must not run on 429")
        )
        try:
            text, _notes, used = app._translate_paragraph_with_retries(
                segment_id=1,
                source_text="싱긋",
                prompt='{"translated_text": true, "translation_notes": true}',
                job_id=9,
            )
        finally:
            gemini_client.generate_text = orig_generate  # type: ignore[method-assign]
            gemini_client.is_configured = orig_configured  # type: ignore[method-assign]
            app._translation_sleep = orig_sleep  # type: ignore[assignment]
            app._paragraph_empty_retry_delay = orig_delay  # type: ignore[assignment]
        self.assertFalse(used)
        self.assertEqual(text, "She smiled.")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(calls["sleeps"], [8.4, 8.4])
        self.assertEqual(app._translation_pipeline_wait_seconds(9), 0.0)


if __name__ == "__main__":
    unittest.main()
