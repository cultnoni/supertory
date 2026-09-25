"""장편 기반(5-A): 분석 단위, 요약 캐시, 실패 안내, 충돌 알림. 모델은 가짜만."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import feedback_api
import feedback_store
import literary_form
import tory_notifications
from feedback_pipeline.claude_client import ClaudeError, FakeClaude
from feedback_pipeline.literature_runner import prepare_literature_run, run_literature_short
from feedback_pipeline.literature_summary import (
    content_hash,
    mark_summary_for_content,
    refresh_queued_summaries,
)
from feedback_pipeline.literature_units import literary_units, load_literary_units
from feedback_pipeline.run_errors import PUBLIC_RUN_FAILURE
from feedback_pipeline.runner import run_feedback


CREDIT = ClaudeError(
    "Your credit balance is too low to access the Anthropic API.",
    code="credit",
)


def _no_internal(blob: str) -> None:
    lowered = blob.lower()
    if "credit balance" in lowered or "anthropic" in lowered:
        raise AssertionError(blob)


class LiteraryUnitTests(unittest.TestCase):
    def test_chapters_combine_scenes_and_loose_scenes_stay_separate(self) -> None:
        chapters = literary_units(
            [
                {"id": 1, "title": "1장", "sort_order": 0},
                {"id": 2, "title": "2장", "sort_order": 1},
            ],
            [
                {"id": 11, "chapter_id": 1, "title": "가", "sort_order": 0},
                {"id": 12, "chapter_id": 1, "title": "나", "sort_order": 1},
                {"id": 21, "chapter_id": 2, "title": "다", "sort_order": 0},
            ],
        )
        self.assertEqual(
            [(unit["kind"], unit["id"], unit["scene_ids"]) for unit in chapters],
            [("chapter", 1, [11, 12]), ("chapter", 2, [21])],
        )
        scenes_only = literary_units(
            [{"id": 1, "title": "본편", "sort_order": 0, "transparent": True}],
            [
                {"id": 11, "chapter_id": 1, "title": "첫째", "sort_order": 0},
                {"id": 12, "chapter_id": 1, "title": "둘째", "sort_order": 1},
            ],
        )
        self.assertEqual(
            [(unit["kind"], unit["id"]) for unit in scenes_only],
            [("scene", 11), ("scene", 12)],
        )

    def test_one_real_chapter_stays_a_chapter_and_only_loose_scenes_split(self) -> None:
        several = literary_units(
            [{"id": 1, "title": "1장", "sort_order": 0}],
            [
                {"id": 11, "chapter_id": 1, "title": "가", "sort_order": 0},
                {"id": 12, "chapter_id": 1, "title": "나", "sort_order": 1},
                {"id": 13, "chapter_id": 1, "title": "다", "sort_order": 2},
            ],
        )
        self.assertEqual(
            [(unit["kind"], unit["id"], unit["scene_ids"]) for unit in several],
            [("chapter", 1, [11, 12, 13])],
        )
        single = literary_units(
            [{"id": 7, "title": "1장", "sort_order": 0}],
            [{"id": 70, "chapter_id": 7, "title": "본문", "sort_order": 0}],
        )
        self.assertEqual(
            [(unit["kind"], unit["id"], unit["scene_ids"]) for unit in single],
            [("chapter", 7, [70])],
        )
        episodes = literary_units(
            [{"id": 1, "title": "본편", "sort_order": 0, "transparent": True}],
            [
                {"id": 21, "chapter_id": 1, "title": "첫째", "sort_order": 0},
                {"id": 22, "chapter_id": 1, "title": "둘째", "sort_order": 1},
            ],
        )
        self.assertEqual(
            [(unit["kind"], unit["id"], unit["scene_ids"]) for unit in episodes],
            [("scene", 21, [21]), ("scene", 22, [22])],
        )


class LongFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.connection = app.connect()

    def tearDown(self) -> None:
        self.connection.close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def _project(self, form: str, cluster: str = "general_literature") -> int:
        main = "general_lit" if cluster == "general_literature" else "fantasy"
        sub = "mid" if cluster == "general_literature" else "regression"
        return int(
            self.connection.execute(
                "INSERT INTO project(title, cluster_id, main_genre, sub_genre, literary_form, "
                "style_narration, style_sentence, style_dialogue, style_lexicon) "
                "VALUES (?, ?, ?, ?, ?, '1인칭', '짧은 문장', '따옴표', '감각')",
                (form, cluster, main, sub, form if cluster == "general_literature" else None),
            ).lastrowid
        )

    def _scene(self, project_id: int, chapter_id: int, order: int, title: str, html: str) -> int:
        scene_id = int(
            self.connection.execute(
                "INSERT INTO scene(project_id, chapter_id, title, sort_order) VALUES (?, ?, ?, ?)",
                (project_id, chapter_id, title, order),
            ).lastrowid
        )
        self.connection.execute(
            "INSERT INTO scene_revision(scene_id, revision_no, content_md, word_count, is_current) "
            "VALUES (?, 1, ?, 20, 1)",
            (scene_id, html),
        )
        return scene_id

    def test_schema_101_and_summary_queue(self) -> None:
        name = self.connection.execute(
            "SELECT name FROM schema_migration WHERE version = 101"
        ).fetchone()[0]
        self.assertEqual(name, "literary_long_foundation")
        project_id = self._project("short")
        chapter_id = int(
            self.connection.execute(
                "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '1장', 0)",
                (project_id,),
            ).lastrowid
        )
        scene_id = self._scene(project_id, chapter_id, 0, "하나", "<p>그는 문을 열고 밤길을 걸었다.</p>")
        self.assertEqual(mark_summary_for_content(self.connection, scene_id, "처음"), "missing")
        digest = content_hash("처음")
        self.connection.execute(
            "UPDATE scene_summary SET summary = '{\"events\":[\"처음\"]}', stale = 0 WHERE scene_id = ?",
            (scene_id,),
        )
        self.assertEqual(mark_summary_for_content(self.connection, scene_id, "처음"), "fresh")
        self.assertEqual(mark_summary_for_content(self.connection, scene_id, "바뀐 본문"), "stale")
        row = self.connection.execute(
            "SELECT stale, content_hash FROM scene_summary WHERE scene_id = ?",
            (scene_id,),
        ).fetchone()
        self.assertEqual(int(row["stale"]), 1)
        self.assertNotEqual(row["content_hash"], digest)

        def fake_index(scene, body):
            self.assertIn("content_md", body)
            return {"summary": {"events": ["가짜"]}}

        done = refresh_queued_summaries(self.connection, project_id, fake_index)
        self.assertEqual(done, [scene_id])
        fresh = self.connection.execute(
            "SELECT summary, stale FROM scene_summary WHERE scene_id = ?",
            (scene_id,),
        ).fetchone()
        self.assertEqual(int(fresh["stale"]), 0)
        self.assertIn("가짜", fresh["summary"])

        queued = literary_form.on_literary_form_changed(
            "short", "long", connection=self.connection, project_id=project_id
        )
        self.assertEqual(queued, [])
        self.connection.execute("DELETE FROM scene_summary WHERE scene_id = ?", (scene_id,))
        queued = literary_form.on_literary_form_changed(
            "short", "long", connection=self.connection, project_id=project_id
        )
        self.assertEqual(queued, [scene_id])
        self.connection.execute(
            "INSERT INTO bait(id, project_id, kind, quote) VALUES ('bait-1', ?, 'plant', '청자')",
            (project_id,),
        )
        literary_form.on_literary_form_changed(
            "long", "short", connection=self.connection, project_id=project_id
        )
        kept = self.connection.execute("SELECT quote FROM bait WHERE id = 'bait-1'").fetchone()
        self.assertEqual(kept["quote"], "청자")

    def test_loaded_units_follow_real_chapters(self) -> None:
        project_id = self._project("long")
        first = int(
            self.connection.execute(
                "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '1장', 0)",
                (project_id,),
            ).lastrowid
        )
        second = int(
            self.connection.execute(
                "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '2장', 1)",
                (project_id,),
            ).lastrowid
        )
        self._scene(project_id, first, 0, "가", "<p>가</p>")
        self._scene(project_id, first, 1, "나", "<p>나</p>")
        self._scene(project_id, second, 0, "다", "<p>다</p>")
        units = load_literary_units(self.connection, project_id)
        self.assertEqual([unit["kind"] for unit in units], ["chapter", "chapter"])
        self.assertEqual(len(units[0]["scene_ids"]), 2)

        loose_id = self._project("long")
        folder = int(
            self.connection.execute(
                "INSERT INTO chapter(project_id, title, notes_md, sort_order) "
                "VALUES (?, '본편', 'supertory:transparent_volume', 0)",
                (loose_id,),
            ).lastrowid
        )
        self._scene(loose_id, folder, 0, "첫째", "<p>하나</p>")
        self._scene(loose_id, folder, 1, "둘째", "<p>둘</p>")
        loose = load_literary_units(self.connection, loose_id)
        self.assertEqual([unit["kind"] for unit in loose], ["scene", "scene"])

    def test_one_chapter_work_stays_chapter_units(self) -> None:
        many_id = self._project("long")
        many_chapter = int(
            self.connection.execute(
                "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '1장', 0)",
                (many_id,),
            ).lastrowid
        )
        self._scene(many_id, many_chapter, 0, "가", "<p>가</p>")
        self._scene(many_id, many_chapter, 1, "나", "<p>나</p>")
        many = load_literary_units(self.connection, many_id)
        self.assertEqual([(unit["kind"], unit["id"]) for unit in many], [("chapter", many_chapter)])
        self.assertEqual(len(many[0]["scene_ids"]), 2)

        one_id = self._project("long")
        one_chapter = int(
            self.connection.execute(
                "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '1장', 0)",
                (one_id,),
            ).lastrowid
        )
        scene_id = self._scene(one_id, one_chapter, 0, "본문", "<p>하나</p>")
        one = load_literary_units(self.connection, one_id)
        self.assertEqual(
            [(unit["kind"], unit["id"], unit["scene_ids"]) for unit in one],
            [("chapter", one_chapter, [scene_id])],
        )

        loose_id = self._project("long")
        folder = int(
            self.connection.execute(
                "INSERT INTO chapter(project_id, title, notes_md, sort_order) "
                "VALUES (?, '본편', 'supertory:transparent_volume', 0)",
                (loose_id,),
            ).lastrowid
        )
        first = self._scene(loose_id, folder, 0, "첫째", "<p>하나</p>")
        second = self._scene(loose_id, folder, 1, "둘째", "<p>둘</p>")
        loose = load_literary_units(self.connection, loose_id)
        self.assertEqual(
            [(unit["kind"], unit["id"], unit["scene_ids"]) for unit in loose],
            [("scene", first, [first]), ("scene", second, [second])],
        )

    def test_finale_mark_is_long_only(self) -> None:
        project_id = self._project("long")
        chapter_id = int(
            self.connection.execute(
                "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '끝', 0)",
                (project_id,),
            ).lastrowid
        )
        scene_id = self._scene(project_id, chapter_id, 0, "마지막", "<p>끝</p>")
        self.connection.commit()
        handler = app.SuperToryHandler.__new__(app.SuperToryHandler)
        saved = app.SuperToryHandler.set_literary_finale(
            handler, project_id, {"scene_id": scene_id}
        )
        self.assertEqual(saved["literary_finale_kind"], "chapter")
        self.assertEqual(saved["literary_finale_id"], chapter_id)
        short_id = self._project("short")
        with self.assertRaises(ValueError):
            app.SuperToryHandler.set_literary_finale(handler, short_id, {"scene_id": scene_id})

    def test_credit_failure_closes_literature_and_webnovel(self) -> None:
        project_id = self._project("short")
        chapter_id = int(
            self.connection.execute(
                "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '1장', 0)",
                (project_id,),
            ).lastrowid
        )
        scene_id = self._scene(project_id, chapter_id, 0, "앞", "<p>그는 푸른 눈을 깜빡였다.</p>")
        prepared = prepare_literature_run(
            self.connection,
            project_id,
            model="claude-sonnet-5",
            prompt_version="literature-short-v0.1",
            params={"usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "stages": []}},
        )
        self.connection.commit()
        client = FakeClaude([CREDIT])
        with patch("feedback_api.get_client", return_value=client), patch(
            "feedback_pipeline.literature_runner.spell_error_count", return_value=0
        ):
            feedback_api._worker(prepared["run_id"], project_id, scene_id, [], {"pipeline": "literature_short"})
        failed = feedback_store.get_run(self.connection, prepared["run_id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["params"]["public_error"], PUBLIC_RUN_FAILURE)
        _no_internal(json.dumps(failed["params"], ensure_ascii=False))
        self.assertNotIn("credit balance", str(failed.get("raw_output") or "").lower())
        self.assertFalse(feedback_api._literature_running(self.connection, project_id))

        quote = "그는 푸른 눈을 깜빡였다."
        full_rubric = {
            "items": [
                {"key": k, "verdict": "works", "intentional": False, "note": "ok", "evidence": [{"scene_no": 1, "quote": quote[:10]}]}
                for k in (
                    "opening", "pov", "scene_summary", "character", "motif",
                    "implication", "ending", "economy", "style", "title",
                )
            ],
            "strengths": [],
        }
        partial_client = FakeClaude([
            {
                "scenes": [{"id": 1, "start_para": 1, "end_para": 1, "pov": "그", "summary": "눈", "mode": "scene"}],
                "first_sentence": quote,
                "last_sentence": quote,
                "motifs": [],
            },
            {"items": [], "strengths": []},
            full_rubric,
            {
                "overview": {"reader": "몰입이 있습니다.", "editor": "단정합니다.", "critic": "눈이 남습니다.", "judge": ""},
                "reading": {"body": "눈에 관한 이야기입니다.", "intent_gap": ""},
                "strengths": [],
                "diagnoses": [],
                "tasks": ["하나", "둘", "셋"],
                "comparison": None,
            },
            CREDIT,
        ])
        prepared_ok = prepare_literature_run(
            self.connection,
            project_id,
            model="claude-sonnet-5",
            prompt_version="literature-short-v0.1",
            params={"usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "stages": []}},
        )
        self.connection.commit()
        with patch("feedback_pipeline.literature_runner.spell_error_count", return_value=0):
            run_literature_short(
                self.connection,
                project_id,
                prepared_ok["run_id"],
                {"max_cost": 2, "autocommit": True},
                claude=partial_client,
            )
        partial = feedback_store.get_run(self.connection, prepared_ok["run_id"])
        self.assertEqual(partial["status"], "partial")
        self.assertTrue(partial["report"])
        self.assertEqual(partial["cards"], [])
        self.assertTrue(partial["params"]["cards_failed"])
        self.assertEqual(partial["params"]["public_error"], PUBLIC_RUN_FAILURE)
        _no_internal(json.dumps(partial["params"], ensure_ascii=False))

        web_id = self._project("short", cluster="webnovel")
        web_chapter = int(
            self.connection.execute(
                "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '1', 0)",
                (web_id,),
            ).lastrowid
        )
        web_scene = self._scene(web_id, web_chapter, 0, "회차", "<p>주인공은 문을 열었다.</p>")
        web_run = feedback_store.create_run(
            self.connection, web_id, "analyze", "claude-sonnet-5", "pipeline-v0.5", {}
        )
        feedback_store.add_run_scene(
            self.connection, web_run, web_id, web_scene, 0, "회차", 1, "hash", [{"i": 1, "text": "주인공은 문을 열었다.", "type": "text"}]
        )
        self.connection.commit()
        web_client = FakeClaude([CREDIT])
        with patch("feedback_api.get_client", return_value=web_client):
            feedback_api._worker(
                web_run,
                web_id,
                web_scene,
                [{"i": 1, "text": "주인공은 문을 열었다.", "type": "text"}],
                {},
            )
        web = feedback_store.get_run(self.connection, web_run)
        self.assertEqual(web["status"], "failed")
        self.assertEqual(web["params"]["public_error"], PUBLIC_RUN_FAILURE)
        _no_internal(json.dumps(web["params"], ensure_ascii=False))
        self.assertFalse(feedback_api._scene_has_running(self.connection, web_scene))

    def test_settings_conflict_stays_open_until_the_next_run(self) -> None:
        project_id = self._project("long")
        character_id = int(
            self.connection.execute(
                "INSERT INTO character(project_id, name, profile_md, sort_order) "
                "VALUES (?, '이형식', '갈색 눈', 0)",
                (project_id,),
            ).lastrowid
        )
        created = tory_notifications.create_settings_conflict_notification(
            self.connection,
            project_id,
            character_id=character_id,
            field="profile",
            chapter_id=9,
            title="설정과 원고가 다릅니다",
            body="눈 색깔",
            scene_id=3,
            quote="푸른 눈",
            settings_section="characters",
        )
        again = tory_notifications.create_settings_conflict_notification(
            self.connection,
            project_id,
            character_id=character_id,
            field="profile",
            chapter_id=9,
            title="설정과 원고가 다릅니다",
            scene_id=3,
            quote="푸른 눈",
        )
        self.assertEqual(created["notification"]["id"], again["notification"]["id"])
        self.assertFalse(again["created"])
        key = tory_notifications.settings_conflict_dedupe_key(
            character_id=character_id, field="profile", chapter_id=9
        )
        self.assertEqual(created["notification"]["dedupe_key"], key)
        labels = [item["label"] for item in created["notification"]["actions"]]
        self.assertEqual(labels, ["원고를 고친다", "설정집을 고친다"])
        from character_import_analysis import _upsert_pending

        cid = created["notification"]["payload"]["conflict_id"]
        _upsert_pending(
            self.connection,
            character_id,
            "profile_md",
            "〔토리〕 푸른 눈",
            conflict_id=cid,
        )
        moved = tory_notifications.execute_action(
            self.connection, project_id, created["notification"]["id"], "fix-manuscript"
        )
        self.assertEqual(moved["effect"]["type"], "navigate")
        self.assertEqual(moved["notification"]["status"], "resolved")
        pending = self.connection.execute(
            "SELECT COALESCE(status, 'pending') AS status FROM character_tori_analysis "
            "WHERE character_id = ? AND field_name = 'profile_md'",
            (character_id,),
        ).fetchone()
        self.assertEqual(pending["status"], "reclaimed")
        self.assertEqual(
            tory_notifications.resolve_settings_conflicts(self.connection, project_id, set()),
            0,
        )

    def test_intentionally_open_is_stored(self) -> None:
        project_id = self._project("long")
        self.connection.commit()
        handler = app.SuperToryHandler.__new__(app.SuperToryHandler)
        created = app.SuperToryHandler.create_bait(
            handler, project_id, {"quote": "청자 접시", "kind": "plant"}
        )
        updated = app.SuperToryHandler.update_bait(
            handler, created["id"], {"intentionally_open": 1}
        )
        self.assertTrue(updated["intentionallyOpen"])
        self.assertTrue(updated["intentionally_open"])


class LongPipelineUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.connection = app.connect()

    def tearDown(self) -> None:
        self.connection.close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def _long_project(self) -> int:
        return int(
            self.connection.execute(
                "INSERT INTO project(title, cluster_id, main_genre, sub_genre, literary_form) "
                "VALUES ('무정', 'general_literature', 'general_lit', 'mid', 'long')"
            ).lastrowid
        )

    def test_schema_102_bundle_table(self) -> None:
        name = self.connection.execute(
            "SELECT name FROM schema_migration WHERE version = 102"
        ).fetchone()[0]
        self.assertEqual(name, "literary_summary_bundle")
        cols = {
            row[1]
            for row in self.connection.execute("PRAGMA table_info(literary_summary_bundle)")
        }
        self.assertIn("content_hash", cols)
        self.assertIn("summary_md", cols)

    def test_compress_bundle_reuses_cache_until_member_changes(self) -> None:
        from feedback_pipeline.literature_context import load_bundle, save_bundle, _bundle_hash

        project_id = self._long_project()
        parts = ["1장: 이형식이 학교에 간다", "2장: 영채가 온다"]
        digest = _bundle_hash(parts)
        save_bundle(self.connection, project_id, 0, 1, digest, "압축된 두 장")
        self.assertEqual(load_bundle(self.connection, project_id, 0, 1, digest), "압축된 두 장")
        changed = _bundle_hash(["1장: 이형식이 학교에 간다", "2장: 영채가 떠난다"])
        self.assertIsNone(load_bundle(self.connection, project_id, 0, 1, changed))

    def test_past_claim_becomes_a_question(self) -> None:
        from feedback_pipeline.literature_past_facts import soften_unverified_past_claims

        text, log = soften_unverified_past_claims(
            "2장에서 왼손잡이로 나온 이형식이 여기서는 오른손을 쓴다.",
            prior_context="1장 요약: 이형식이 학교에 간다.",
            settings_text="",
            current_unit_no=3,
        )
        self.assertIn("맞다면", text)
        self.assertTrue(log)
        self.assertEqual(log[0]["action"], "soften_past_claim")

    def test_past_claim_skips_current_unit_and_scene_refs(self) -> None:
        from feedback_pipeline.literature_past_facts import soften_unverified_past_claims

        text, log = soften_unverified_past_claims(
            "5장면의 부인 내력 서술은 형식의 내면과 분리된다. 이 3장은 독신을 유지한다.",
            prior_context="1장 요약: 독신.",
            settings_text="독신",
            current_unit_no=3,
        )
        self.assertEqual(log, [])
        self.assertIn("5장면", text)
        self.assertIn("3장", text)

    def test_past_claim_keeps_supported_prior_with_neighbor(self) -> None:
        from feedback_pipeline.literature_past_facts import soften_unverified_past_claims

        text, log = soften_unverified_past_claims(
            "2장 말미에서 영채가 울다 이야기를 재개한 뒤 3장이 이어진다.",
            prior_context="1장 요약.",
            neighbor_text="직전 단위 마지막 장면:\n영채가 울음을 그치고 이야기를 다시 시작한다.",
            current_unit_no=3,
        )
        self.assertEqual(log, [])
        self.assertNotIn("맞다면", text)

    def test_character_match_and_unit_scoped_conflict_close(self) -> None:
        from feedback_pipeline.literature_settings_write import (
            apply_character_fill,
            apply_settings_writes,
            match_character,
            resolve_character_field,
            resolve_unit_conflicts,
        )

        project_id = self._long_project()
        chapter_id = int(
            self.connection.execute(
                "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '3장', 2)",
                (project_id,),
            ).lastrowid
        )
        scene_id = int(
            self.connection.execute(
                "INSERT INTO scene(project_id, chapter_id, title, sort_order) VALUES (?, ?, '본문', 0)",
                (project_id, chapter_id),
            ).lastrowid
        )
        self.connection.execute(
            "INSERT INTO scene_revision(scene_id, revision_no, content_md, word_count, is_current) "
            "VALUES (?, 1, '<p>왼손으로 붓을 잡았다.</p>', 1, 1)",
            (scene_id,),
        )
        character_id = int(
            self.connection.execute(
                "INSERT INTO character(project_id, name, profile_md, sort_order) "
                "VALUES (?, '이형식', '오른손잡이', 0)",
                (project_id,),
            ).lastrowid
        )
        self.connection.execute(
            "INSERT INTO character_alias(character_id, project_id, alias) VALUES (?, ?, '형식')",
            (character_id, project_id),
        )
        characters = [
            {"id": character_id, "name": "이형식", "aliases": ["형식"], "profile_md": "오른손잡이"}
        ]
        self.assertEqual(match_character(characters, "이형식")[0], "exact")
        self.assertEqual(match_character(characters, "형식")[0], "exact")
        self.assertEqual(match_character(characters, "이")[0], "ambiguous")
        self.assertEqual(match_character(characters, "김선형")[0], "new")

        self.assertEqual(resolve_character_field("소개")[:2], ("short_description", "alias"))
        self.assertEqual(resolve_character_field("필기 손")[:2], ("author_notes_md", "memo"))
        outcome = apply_character_fill(
            self.connection,
            project_id=project_id,
            character_id=character_id,
            field="필기 손",
            content="이 장에서 왼손으로 붓을 잡음",
            scene_id=scene_id,
        )
        self.assertEqual(outcome["result"], "filled")
        self.assertEqual(outcome["field"], "author_notes_md")
        self.assertEqual(outcome["routing"], "memo")
        notes = self.connection.execute(
            "SELECT author_notes_md FROM character WHERE id = ?",
            (character_id,),
        ).fetchone()[0]
        self.assertIn("〔토리〕", notes)
        self.assertIn("필기 손", notes)

        unit3 = {"kind": "chapter", "id": chapter_id, "ord": 2, "title": "3장", "scene_ids": [scene_id]}
        unit1 = {"kind": "chapter", "id": 10, "ord": 0, "title": "1장", "scene_ids": [1]}
        conflict = {
            "conflict_id": "c-hand",
            "title": "손잡이 모순",
            "note": "설정집은 오른손, 원고는 왼손",
            "quote": "왼손으로 붓을 잡았다",
            "settings_section": "characters",
            "field": "profile_md",
            "character_id": character_id,
            "bait_id": "",
            "draft": "〔토리〕 왼손잡이",
            "scene_id": scene_id,
        }
        first = apply_settings_writes(
            self.connection,
            project_id,
            unit=unit3,
            scene_id=scene_id,
            discoveries=[],
            conflicts=[conflict],
        )
        self.assertEqual(len(first["conflicts"]), 1)
        key = first["active_keys"][0]
        open_row = self.connection.execute(
            "SELECT status, payload_json FROM tory_notification WHERE dedupe_key = ?",
            (key,),
        ).fetchone()
        self.assertEqual(open_row["status"], "unread")
        payload = json.loads(open_row["payload_json"])
        self.assertEqual(payload["unit_id"], chapter_id)

        # 1장을 다시 돌려도 3장 충돌은 열린 채
        apply_settings_writes(
            self.connection,
            project_id,
            unit=unit1,
            scene_id=1,
            discoveries=[],
            conflicts=[],
        )
        still = self.connection.execute(
            "SELECT status FROM tory_notification WHERE dedupe_key = ?",
            (key,),
        ).fetchone()
        self.assertEqual(still["status"], "unread")

        # 같은 3장에서 충돌이 사라지면 닫힘 + pending 회수
        closed = apply_settings_writes(
            self.connection,
            project_id,
            unit=unit3,
            scene_id=scene_id,
            discoveries=[],
            conflicts=[],
        )
        self.assertEqual(closed["closed_notifications"], 1)
        self.assertGreaterEqual(closed["reclaimed_pendings"], 1)
        done = self.connection.execute(
            "SELECT status FROM tory_notification WHERE dedupe_key = ?",
            (key,),
        ).fetchone()
        self.assertEqual(done["status"], "resolved")
        row = self.connection.execute(
            "SELECT COALESCE(status, 'pending') AS status, field_name, conflict_id "
            "FROM character_tori_analysis "
            "WHERE character_id = ? AND conflict_id = 'c-hand'",
            (character_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "reclaimed")

    def test_rubric_fills_missing_chapter_edges(self) -> None:
        from feedback_pipeline.literature_rubric_complete import ensure_long_rubric

        rubric = {
            "internal": [
                {"key": "pov", "verdict": "works", "intentional": False, "note": "ok", "evidence": []},
                {"key": "scene_summary", "verdict": "works", "intentional": False, "note": "ok", "evidence": []},
                {"key": "character_interior", "verdict": "works", "intentional": False, "note": "ok", "evidence": []},
                {"key": "implication", "verdict": "works", "intentional": False, "note": "ok", "evidence": []},
                {"key": "style", "verdict": "works", "intentional": False, "note": "ok", "evidence": []},
            ],
            "in_work": [
                {"key": "continuity", "verdict": "works", "intentional": False, "note": "ok", "evidence": []},
                {"key": "role", "verdict": "works", "intentional": False, "note": "ok", "evidence": []},
                {"key": "character_consistency", "verdict": "works", "intentional": False, "note": "ok", "evidence": []},
                {"key": "character_arc", "verdict": "works", "intentional": False, "note": "ok", "evidence": []},
                {"key": "motif", "verdict": "works", "intentional": False, "note": "ok", "evidence": []},
            ],
            "strengths": [],
        }
        log: list = []
        filled, absent = ensure_long_rubric(
            rubric, first=False, last=False, contest_on=False, log=log
        )
        self.assertIn("chapter_edges", absent)
        keys = [item["key"] for item in filled["in_work"]]
        self.assertIn("chapter_edges", keys)
        edge = next(item for item in filled["in_work"] if item["key"] == "chapter_edges")
        self.assertEqual(edge["verdict"], "none")
        self.assertEqual(edge["note"], "판정 없음")
        self.assertTrue(any(item.get("action") == "rubric_missing_filled" for item in log))

    def test_conflict_card_shares_conflict_id(self) -> None:
        from feedback_pipeline.literature_long_runner import conflict_cards

        assembled = {
            "paragraphs": [{"n": 1, "local": 1, "scene_id": 7, "text": "왼손으로 붓을 잡았다."}],
            "plain": "왼손으로 붓을 잡았다.",
        }
        conflicts = [
            {
                "conflict_id": "shared-1",
                "title": "손잡이",
                "note": "설정과 다름",
                "quote": "왼손으로 붓을 잡았다.",
                "scene_id": 7,
            }
        ]
        cards = conflict_cards(conflicts, assembled)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["conflict_id"], "shared-1")
        self.assertEqual(cards[0]["priority"], "high")
        self.assertEqual(cards[0]["form"], "note")
        self.assertIn("settings_conflict", cards[0]["tags"])
