"""Genre-specialist virtual readers must notice when the work is a different genre."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import app
import gemini_client

MISMATCH_MARKER = "장르 불일치 인지"


def _persona(connection, persona_id: str) -> dict:
    row = connection.execute(
        "SELECT * FROM virtual_reader_personas WHERE id = ?",
        (persona_id,),
    ).fetchone()
    assert row is not None, persona_id
    return app.serialize_reader_persona(row)


class ReaderGenreMismatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        self.original_gap = app.READER_DEBATE_GEMINI_GAP_SECONDS
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.READER_DEBATE_GEMINI_GAP_SECONDS = 0
        app.initialise_database()

    def tearDown(self) -> None:
        app.READER_DEBATE_GEMINI_GAP_SECONDS = self.original_gap
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def test_match_keeps_existing_comment_prompt(self) -> None:
        with app.database() as connection:
            cider = _persona(connection, "roppan_cider")
        shared = (
            "[작품 정보]\n작품 제목: 사이다\n메인 장르: 로판\n서브 장르: 하이루판\n"
        )
        system = app._reader_comment_system_prompt(
            cider,
            shared,
            main_genre="romfant",
            sub_genre="high",
        )
        self.assertNotIn(MISMATCH_MARKER, system)
        self.assertIn("웹소설 플랫폼 댓글창에 이 독자가 남길 법한 댓글", system)

    def test_mismatch_injects_nuance_not_fixed_lines(self) -> None:
        with app.database() as connection:
            academy = _persona(connection, "academy_immersive")
        system = app._reader_comment_system_prompt(
            academy,
            "[작품 정보]\n메인 장르: 무협\n서브 장르: 정통무협\n",
            main_genre="martial",
            sub_genre="classic",
        )
        self.assertIn(MISMATCH_MARKER, system)
        self.assertIn("아카데미물", system)
        self.assertIn("매번 다른 표현", system)
        self.assertIn("이전에 썼던 대사를 반복하지 마라", system)
        self.assertIn("가벼운 당황", system)
        self.assertIn("스스로 정정", system)
        self.assertIn("기대한 맛은 아닌데", system)
        self.assertIn("전문가인 척 장르 문법을 들이밀며 비평하지 마라", system)
        self.assertIn("클리셰가 안 나온다고 아쉬워하거나 그걸 요구하지 마라", system)
        self.assertNotIn("아카데미물인데 왜 학교생활 얘기가 안 나와요?", system)

    def test_non_genre_personas_never_get_mismatch_block(self) -> None:
        with app.database() as connection:
            ids = (
                "plausibility_absolutist",
                "healing_family_reader",
                "relationship_purist",
                "three_episode_nomad",
                "webnovel_newcomer",
                "character_charm_critic",
            )
            personas = [_persona(connection, persona_id) for persona_id in ids]
        for persona in personas:
            matched = app.reader_persona_genre_matches(
                persona,
                main_genre="martial",
                sub_genre="classic",
            )
            self.assertIsNone(matched, persona.get("id"))
            block = app._reader_genre_mismatch_block(
                persona,
                main_genre="martial",
                sub_genre="classic",
            )
            self.assertEqual(block, "", persona.get("id"))

    def test_match_and_mismatch_matrix(self) -> None:
        cases = [
            ("roppan_cider", "romfant", "high", "", True),
            ("roppan_cider", "martial", "classic", "", False),
            ("academy_immersive", "youth", "school", "", True),
            ("academy_immersive", "martial", "classic", "", False),
            ("wuxia_romantic", "martial", "classic", "", True),
            ("wuxia_romantic", "contemporary", "daily", "", False),
            ("sf_hardcore_critic", "sf", "space", "", True),
            ("sf_hardcore_critic", "romance", "romfant", "", False),
            ("hunter_speedrunner", "fantasy", "urban", "", True),
            ("sports_sim_fan", "youth", "school", "", False),
            ("alt_history_analyst", "fantasy", "male", "alt_history", True),
            ("alt_history_analyst", "martial", "classic", "", False),
        ]
        with app.database() as connection:
            for persona_id, main, sub, detail, expect in cases:
                persona = _persona(connection, persona_id)
                matched = app.reader_persona_genre_matches(
                    persona,
                    main_genre=main,
                    sub_genre=sub,
                    genre_detail=detail,
                )
                self.assertEqual(
                    matched,
                    expect,
                    f"{persona_id} vs {main}/{sub}/{detail}",
                )

    def test_modern_wuxia_does_not_count_as_romance(self) -> None:
        with app.database() as connection:
            cider = _persona(connection, "roppan_cider")
        self.assertIs(
            app.reader_persona_genre_matches(
                cider,
                main_genre="martial",
                sub_genre="modern_wu",
            ),
            False,
        )

    def test_missing_work_genre_does_not_inject_block(self) -> None:
        with app.database() as connection:
            cider = _persona(connection, "roppan_cider")
        self.assertIsNone(app.reader_persona_genre_matches(cider))
        self.assertEqual(app._reader_genre_mismatch_block(cider), "")

    def test_labels_in_shared_context_are_enough(self) -> None:
        with app.database() as connection:
            academy = _persona(connection, "academy_immersive")
        shared = "[작품 정보]\n메인 장르: 무협\n서브 장르: 정통무협\n"
        self.assertIs(
            app.reader_persona_genre_matches(academy, shared_context=shared),
            False,
        )
        self.assertIn(
            MISMATCH_MARKER,
            app._reader_genre_mismatch_block(academy, shared_context=shared),
        )

    def _make_scene(self, *, title: str, main_genre: str, sub_genre: str, body: str) -> int:
        with app.database() as connection:
            project_id = int(
                connection.execute(
                    "INSERT INTO project(title, main_genre, sub_genre) VALUES (?, ?, ?)",
                    (title, main_genre, sub_genre),
                ).lastrowid
            )
            chapter_id = int(
                connection.execute(
                    "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '장', 0)",
                    (project_id,),
                ).lastrowid
            )
            scene_id = int(
                connection.execute(
                    "INSERT INTO scene(project_id, chapter_id, title, sort_order) "
                    "VALUES (?, ?, '회차', 0)",
                    (project_id, chapter_id),
                ).lastrowid
            )
            connection.execute(
                "INSERT INTO scene_revision(scene_id, revision_no, content_md, word_count, is_current) "
                "VALUES (?, 1, ?, 1, 1)",
                (scene_id, body),
            )
        return scene_id

    def test_generate_injects_only_for_mismatched_specialists(self) -> None:
        original = gemini_client.generate_text
        captured: list[dict] = []

        def _fake(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            captured.append({"prompt": prompt, "system": system or ""})
            return "어라 분위기가 다르네. 무릎 꿇는 장면은 시원하긴 하다."

        gemini_client.generate_text = _fake  # type: ignore[method-assign]
        try:
            scene_id = self._make_scene(
                title="무협",
                main_genre="martial",
                sub_genre="classic",
                body="협객이 강호에 나섰다.",
            )
            with app.database() as connection:
                academy = _persona(connection, "academy_immersive")
                plot = _persona(connection, "plausibility_absolutist")
            app.generate_scene_reader_comments(
                scene_id, [academy, plot], batch_id="batch-a"
            )
        finally:
            gemini_client.generate_text = original  # type: ignore[method-assign]
        self.assertEqual(len(captured), 2)
        academy_system = captured[0]["system"]
        plot_system = captured[1]["system"]
        self.assertIn(MISMATCH_MARKER, academy_system)
        self.assertNotIn(MISMATCH_MARKER, plot_system)

    def test_generate_match_does_not_change_prompt(self) -> None:
        original = gemini_client.generate_text
        captured: list[str] = []

        def _fake(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            captured.append(system or "")
            return "사이다 터지네 ㅋㅋ"

        gemini_client.generate_text = _fake  # type: ignore[method-assign]
        try:
            scene_id = self._make_scene(
                title="로판",
                main_genre="romfant",
                sub_genre="high",
                body="황제가 무릎을 꿇었다.",
            )
            with app.database() as connection:
                cider = _persona(connection, "roppan_cider")
            app.generate_scene_reader_comments(scene_id, [cider], batch_id="batch-b")
        finally:
            gemini_client.generate_text = original  # type: ignore[method-assign]
        self.assertEqual(len(captured), 1)
        self.assertNotIn(MISMATCH_MARKER, captured[0])

    def test_debate_mismatch_block_is_persona_specific(self) -> None:
        with app.database() as connection:
            cider = _persona(connection, "roppan_cider")
            plot = _persona(connection, "plausibility_absolutist")
        shared = "[작품 정보]\n메인 장르: 무협\n서브 장르: 정통무협\n"
        cider_prompt = app._reader_debate_system_prompt(
            cider, shared, main_genre="martial", sub_genre="classic"
        )
        plot_prompt = app._reader_debate_system_prompt(
            plot, shared, main_genre="martial", sub_genre="classic"
        )
        self.assertIn(MISMATCH_MARKER, cider_prompt)
        self.assertNotIn(MISMATCH_MARKER, plot_prompt)


class ReaderGenreMismatchLiveTests(unittest.TestCase):
    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_mismatch_replies_notice_wrong_genre(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        original_data_dir = app.DATA_DIR
        original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        replies: dict[str, str] = {}
        try:
            with app.database() as connection:
                combos = [
                    ("academy_immersive", "martial", "classic"),
                    ("roppan_cider", "martial", "classic"),
                    ("wuxia_romantic", "contemporary", "daily"),
                    ("roppan_cider", "romfant", "high"),
                ]
                for persona_id, main, sub in combos:
                    persona = _persona(connection, persona_id)
                    shared = (
                        "[작품 정보]\n"
                        f"작품 제목: 테스트\n"
                        f"메인 장르: {app.SuperToryHandler._genre_display_label(None, main)}\n"
                        f"서브 장르: {app.SuperToryHandler._genre_display_label(None, sub)}\n"
                        "다음은 작가가 공유한 원고 내용입니다:\n"
                        "주인공이 문을 열고 나가자 비가 내리고 있었다.\n"
                    )
                    system = app._reader_comment_system_prompt(
                        persona,
                        shared,
                        main_genre=main,
                        sub_genre=sub,
                    )
                    prompt = app._reader_comment_user_prompt(
                        str(persona.get("name") or ""),
                        "1화",
                    )
                    replies[f"{persona_id}:{main}/{sub}"] = gemini_client.generate_text(
                        prompt,
                        system=system,
                        temperature=0.9,
                        max_output_tokens=256,
                    )
        finally:
            app.DATA_DIR = original_data_dir
            app.DATABASE_PATH = original_database_path
            temporary_directory.cleanup()
        mismatch_keys = [
            "academy_immersive:martial/classic",
            "roppan_cider:martial/classic",
            "wuxia_romantic:contemporary/daily",
        ]
        for key in mismatch_keys:
            text = replies[key]
            self.assertTrue(text.strip(), key)
            joined = text.replace(" ", "")
            # Should not claim the work *is* the specialty genre as a fact.
            self.assertNotIn("아카데미물인데", joined)
            self.assertNotIn("로판인데", joined)
            self.assertNotIn("무협인데왜", joined)
        match_text = replies["roppan_cider:romfant/high"]
        self.assertTrue(match_text.strip())
        self.assertNotIn("장르 착각", match_text)
        # Keep live samples visible in unittest output.
        for key, text in replies.items():
            print(f"\n[live {key}]\n{text}\n")
