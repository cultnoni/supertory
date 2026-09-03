"""흥행 공식 분석: ranges, dual budgets, parse, dry-run profile save."""

from __future__ import annotations

import base64
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import success_pattern


def _make_txt_episodes(start: int, count: int, chars_per: int = 200) -> bytes:
    """Build a plain text file with heading-style episodes."""
    parts = []
    filler = ("가나다라마바사아자차카타파하 " * 20)[: max(20, chars_per - 20)]
    for i in range(start, start + count):
        body = (filler + " ") * max(1, chars_per // max(1, len(filler) + 1))
        body = body[:chars_per]
        parts.append(f"# {i}화\n\n{body}\n")
    return "\n".join(parts).encode("utf-8")


class SuccessPatternUnitTests(unittest.TestCase):
    def test_recommend_ranges_300(self) -> None:
        r = success_pattern.recommend_ranges(300, 10)
        self.assertEqual(r["front"]["start"], 1)
        self.assertEqual(r["front"]["end"], 10)
        self.assertEqual(r["ending"]["start"], 291)
        self.assertEqual(r["ending"]["end"], 300)
        mid = r["middle"]
        self.assertEqual(mid["end"] - mid["start"] + 1, 10)
        self.assertTrue(140 <= mid["start"] <= 160)

    def test_character_budget_blocked_under_50_eps(self) -> None:
        # 40 episodes * 8000 chars = 320_000 > 300_000
        sections = [
            success_pattern.UploadedSection(
                key="front",
                start_ep=1,
                end_ep=40,
                episodes=[
                    success_pattern.EpisodeUnit(title=f"{i}화", text="가" * 8000, index=i)
                    for i in range(1, 41)
                ],
            )
        ]
        ep_budget = success_pattern.check_episode_budget(40)
        # 40 ≤ 50 max (may warn vs 30 recommended) — not blocked
        self.assertIn(ep_budget["status"], {"ok", "warning"})
        self.assertNotEqual(ep_budget["status"], "blocked")
        char_budget = success_pattern.check_character_budget(sections)
        self.assertEqual(char_budget["status"], "blocked")
        self.assertGreater(char_budget["totalChars"], success_pattern.MAX_TOTAL_CHARS)
        self.assertGreaterEqual(char_budget["suggestedRemoval"], 1)
        # Suggested removal roughly excess / avg
        excess = char_budget["totalChars"] - success_pattern.MAX_TOTAL_CHARS
        self.assertAlmostEqual(
            char_budget["suggestedRemoval"],
            int(max(1, (excess / 8000) + 0.999)),
            delta=1,
        )

    def test_prompts_include_copyright_and_json(self) -> None:
        p1 = success_pattern.build_structural_observation_prompt("본문 샘플")
        self.assertIn("저작권", p1)
        self.assertIn("ending_hook", p1)
        p2 = success_pattern.build_success_pattern_merge_prompt({"total": 1}, [])
        self.assertIn("저작권", p2)
        self.assertIn("hook_style", p2)

    def test_mock_profile_matches_live_merge_schema(self) -> None:
        profile = success_pattern.mock_merge_profile(
            {"total_episodes": 3, "total_chars": 1200},
            [{"section_key": "front"}],
        )
        self.assertEqual(
            set(profile),
            set(success_pattern.PROFILE_FIELDS),
        )
        normalized_live = success_pattern.normalize_merge_profile({
            **profile,
            "unexpected_model_field": "제거 대상",
        })
        self.assertEqual(set(normalized_live), set(success_pattern.PROFILE_FIELDS))
        self.assertNotIn("unexpected_model_field", normalized_live)

    def test_legacy_mock_factor_arrays_remain_readable(self) -> None:
        factors = success_pattern._profile_factor_lists({
            "profile": {
                "hook_style": "훅",
                "reader_popularity_factors": ["독자 요인"],
                "editor_popularity_factors": ["편집 요인"],
                "must_follow_factors": ["필수 요인"],
            },
        })
        self.assertEqual(factors["reader"], ["독자 요인"])
        self.assertEqual(factors["editor"], ["편집 요인"])
        self.assertEqual(factors["must"], ["필수 요인"])

    def test_frontend_sends_cluster_and_reports_auto_link_failure(self) -> None:
        js = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(
            encoding="utf-8"
        )
        run_block = js.split("async function runSuccessPatternAnalysis()", 1)[1].split(
            "function setupSuccessPatternWizard()", 1
        )[0]
        self.assertIn("cluster_id: getProjectClusterId()", run_block)
        self.assertIn('console.error("Success profile auto-link failed"', run_block)
        self.assertIn('toast(i18n.t("app.흥행_프로파일_자동_연결_실패"))', run_block)
        self.assertNotIn("spOpenWizardButton", js)

    def test_frontend_supports_text_episodes_and_mixed_payload(self) -> None:
        root = Path(__file__).resolve().parents[1]
        js = (root / "web" / "app.js").read_text(encoding="utf-8")
        css = (root / "web" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("function getSpSectionEpisodes(", js)
        self.assertIn("function addSpTextEpisode(", js)
        self.assertIn("function updateSpTextEpisode(", js)
        self.assertIn('sourceType: "text"', js)
        self.assertIn('data-sp-input-mode="file"', js)
        self.assertIn('data-sp-input-mode="text"', js)
        self.assertIn('data-sp-text-add="${key}"', js)
        self.assertIn('data-sp-text-body="${key}"', js)
        run_block = js.split("async function runSuccessPatternAnalysis()", 1)[1].split(
            "function setupSuccessPatternWizard()", 1
        )[0]
        self.assertIn("episodes: getSpSectionEpisodes(key).map((ep)", run_block)
        file_append = js.split("async function parseSpSectionFile(", 1)[1].split(
            "function renderSuccessPatternStep()", 1
        )[0]
        self.assertIn("const episodes = [...prev, ...incoming]", file_append)
        self.assertIn(".sp-text-episode textarea", css)
        self.assertIn(".sp-input-mode-toggle", css)
        for language in ("ko", "en", "es"):
            locale = json.loads(
                (root / "web" / "locales" / f"{language}.json").read_text(
                    encoding="utf-8"
                )
            )
            for key in (
                "app.파일_업로드",
                "app.텍스트_직접_입력",
                "app.화_추가",
                "app.회차_본문을_붙여넣어_주세요",
                "app.직접_입력_글자수_Gemini_전달",
                "app.입력_합계_n화_chars자",
            ):
                self.assertIn(key, locale)

    def test_success_feedback_entry_requires_linked_profile(self) -> None:
        root = Path(__file__).resolve().parents[1]
        js = (root / "web" / "app.js").read_text(encoding="utf-8")
        guide_block = js.split("async function updateSuccessFeedbackGuideUi()", 1)[1].split(
            "function closeAiModePicker()", 1
        )[0]
        self.assertIn("Boolean(getLinkedSuccessProfileId())", guide_block)
        self.assertNotIn("listSuccessProfiles", guide_block)
        setter_block = js.split("function setAiModeValue(", 1)[1].split(
            "function installAiModeValueSync(", 1
        )[0]
        self.assertIn(
            'next === "successfeedback" && !getLinkedSuccessProfileId()',
            setter_block,
        )
        run_block = js.split("async function runSuccessFormulaFeedback()", 1)[1].split(
            "async function runSuccessPatternAnalysis()", 1
        )[0]
        self.assertIn("!getLinkedSuccessProfileId()", run_block)
        self.assertIn(
            'i18n.t("app.먼저_설정집에서_흥행작_프로파일을_연결해_주세요")',
            run_block,
        )
        for language in ("ko", "en", "es"):
            locale = json.loads(
                (root / "web" / "locales" / f"{language}.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn(
                "app.먼저_설정집에서_흥행작_프로파일을_연결해_주세요",
                locale,
            )


class SuccessPatternApiTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        return response.status, json.loads(raw)

    def test_migration_025(self) -> None:
        with app.database() as connection:
            row = connection.execute(
                "SELECT 1 FROM schema_migration WHERE version = 25"
            ).fetchone()
            self.assertIsNotNone(row)
            tables = {
                r[0]
                for r in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertIn("success_pattern_profile", tables)

    def test_migration_083_profile_link_foreign_key(self) -> None:
        with app.database() as connection:
            migration = connection.execute(
                "SELECT 1 FROM schema_migration WHERE version = 83"
            ).fetchone()
            self.assertIsNotNone(migration)
            foreign_keys = connection.execute(
                "PRAGMA foreign_key_list(project)"
            ).fetchall()
            link_fk = next(
                (row for row in foreign_keys if row["from"] == "linked_success_profile_id"),
                None,
            )
            self.assertIsNotNone(link_fk)
            self.assertEqual(link_fk["table"], "success_pattern_profile")
            self.assertEqual(str(link_fk["on_delete"]).upper(), "SET NULL")

            profile_id = connection.execute(
                "INSERT INTO success_pattern_profile(work_title) VALUES (?)",
                ("삭제 검증 프로파일",),
            ).lastrowid
            project_id = connection.execute(
                "INSERT INTO project(title, linked_success_profile_id) VALUES (?, ?)",
                ("외래키 검증 작품", profile_id),
            ).lastrowid
            connection.execute(
                "DELETE FROM success_pattern_profile WHERE id = ?",
                (profile_id,),
            )
            linked = connection.execute(
                "SELECT linked_success_profile_id FROM project WHERE id = ?",
                (project_id,),
            ).fetchone()
            self.assertIsNone(linked["linked_success_profile_id"])

    def test_migration_084_chapter_notes_table_and_cascade(self) -> None:
        with app.database() as connection:
            migration = connection.execute(
                "SELECT 1 FROM schema_migration WHERE version = 84"
            ).fetchone()
            self.assertIsNotNone(migration)
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(success_pattern_chapter_notes)"
                ).fetchall()
            }
            self.assertTrue({
                "profile_id",
                "note_order",
                "section_key",
                "episode_title",
                "episode_index",
                "observation_json",
                "used_mock",
            }.issubset(columns))
            foreign_keys = connection.execute(
                "PRAGMA foreign_key_list(success_pattern_chapter_notes)"
            ).fetchall()
            profile_fk = next(
                (row for row in foreign_keys if row["from"] == "profile_id"),
                None,
            )
            self.assertIsNotNone(profile_fk)
            self.assertEqual(str(profile_fk["on_delete"]).upper(), "CASCADE")

            profile_id = connection.execute(
                "INSERT INTO success_pattern_profile(work_title) VALUES (?)",
                ("회차 기록 삭제 검증",),
            ).lastrowid
            connection.execute(
                "INSERT INTO success_pattern_chapter_notes("
                "profile_id, note_order, section_key, observation_json"
                ") VALUES (?, 0, 'front', '{}')",
                (profile_id,),
            )
            connection.execute(
                "DELETE FROM success_pattern_profile WHERE id = ?",
                (profile_id,),
            )
            count = connection.execute(
                "SELECT COUNT(*) FROM success_pattern_chapter_notes WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()[0]
            self.assertEqual(count, 0)

    def test_a_front_and_ending_profile(self) -> None:
        """(a) 300화 가상 작품 — 앞 10 + 끝 10 업로드, 프로파일 생성·화수 합계."""
        status, rec = self.request(
            "POST",
            "/api/success-pattern/recommend-ranges",
            {"total_chapters": 300},
        )
        self.assertEqual(status, 200, rec)
        ranges = rec["ranges"]
        self.assertEqual(ranges["front"]["start"], 1)
        self.assertEqual(ranges["front"]["end"], 10)
        self.assertEqual(ranges["ending"]["start"], 291)
        self.assertEqual(ranges["ending"]["end"], 300)

        front_bytes = _make_txt_episodes(1, 10, chars_per=300)
        end_bytes = _make_txt_episodes(291, 10, chars_per=300)

        status, front_parsed = self.request(
            "POST",
            "/api/success-pattern/parse",
            {
                "filename": "front.txt",
                "content_base64": base64.b64encode(front_bytes).decode("ascii"),
                "split_mode": "headings",
            },
        )
        self.assertEqual(status, 200, front_parsed)
        self.assertEqual(front_parsed["episode_count"], 10)

        status, end_parsed = self.request(
            "POST",
            "/api/success-pattern/parse",
            {
                "filename": "ending.txt",
                "content_base64": base64.b64encode(end_bytes).decode("ascii"),
                "split_mode": "headings",
            },
        )
        self.assertEqual(status, 200, end_parsed)
        self.assertEqual(end_parsed["episode_count"], 10)

        sections = [
            {
                "key": "front",
                "start_ep": 1,
                "end_ep": 10,
                "episodes": front_parsed["episodes"],
            },
            {
                "key": "ending",
                "start_ep": 291,
                "end_ep": 300,
                "episodes": end_parsed["episodes"],
            },
        ]
        status, budget = self.request(
            "POST",
            "/api/success-pattern/check-budget",
            {"sections": sections},
        )
        self.assertEqual(status, 200, budget)
        self.assertEqual(budget["stats"]["total_episodes"], 20)
        self.assertIn(budget["status"], {"ok", "warning"})

        status, result = self.request(
            "POST",
            "/api/success-pattern/run",
            {
                "work_title": "가상 히트작 300화",
                "total_chapters": 300,
                "cluster_id": "genre_literature",
                "sections": sections,
                "dry_run": True,
            },
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("cluster_id"), "genre_literature")
        profile = result["profile"]
        self.assertTrue(profile.get("id"))
        self.assertEqual(profile.get("work_title"), "가상 히트작 300화")
        self.assertEqual(profile.get("total_chapters"), 300)
        self.assertEqual(len(profile.get("analyzed_sections") or []), 2)
        self.assertIn("summary", profile.get("profile") or {})
        self.assertEqual(profile.get("quantitative", {}).get("total_episodes"), 20)
        self.assertEqual(len(result.get("chapter_notes") or []), 20)
        with app.database() as connection:
            rows = connection.execute(
                "SELECT note_order, section_key, episode_title, episode_index, "
                "char_count, observation_json, used_mock "
                "FROM success_pattern_chapter_notes WHERE profile_id = ? "
                "ORDER BY note_order",
                (profile["id"],),
            ).fetchall()
        self.assertEqual(len(rows), 20)
        self.assertEqual([row["note_order"] for row in rows], list(range(20)))
        self.assertEqual(rows[0]["section_key"], "front")
        self.assertEqual(rows[-1]["section_key"], "ending")
        self.assertTrue(rows[0]["episode_title"])
        self.assertGreater(rows[0]["char_count"], 0)
        self.assertEqual(rows[0]["used_mock"], 1)
        self.assertIsInstance(json.loads(rows[0]["observation_json"]), dict)

        status, listed = self.request("GET", "/api/success-pattern/profiles")
        self.assertEqual(status, 200)
        self.assertTrue(any(p.get("id") == profile["id"] for p in listed))

    def test_b_char_blocked_even_if_episodes_ok(self) -> None:
        """(b) 화수 ≤50 이어도 30만자 초과 시 blocked + 회차 줄이기 제안."""
        # 25 eps * 13000 chars = 325000
        heavy = [
            success_pattern.EpisodeUnit(
                title=f"{i}화",
                text="나" * 13000,
                index=i,
            )
            for i in range(1, 26)
        ]
        sections_payload = [
            {
                "key": "front",
                "start_ep": 1,
                "end_ep": 25,
                "episodes": [
                    {"title": ep.title, "text": ep.text, "length": ep.length}
                    for ep in heavy
                ],
            }
        ]
        status, budget = self.request(
            "POST",
            "/api/success-pattern/check-budget",
            {"sections": sections_payload},
        )
        self.assertEqual(status, 200, budget)
        self.assertEqual(budget["episode_budget"]["status"], "ok")
        self.assertEqual(budget["character_budget"]["status"], "blocked")
        self.assertEqual(budget["status"], "blocked")
        suggested = budget["character_budget"]["suggestedRemoval"]
        self.assertGreaterEqual(suggested, 1)
        # ~25000 excess / 13000 ≈ 2
        self.assertLessEqual(suggested, 5)

        status, result = self.request(
            "POST",
            "/api/success-pattern/run",
            {
                "work_title": "과다 글자",
                "total_chapters": 100,
                "sections": sections_payload,
                "dry_run": True,
            },
        )
        self.assertEqual(status, 400, result)
        self.assertIn("글자", str(result.get("error") or result))


if __name__ == "__main__":
    unittest.main()
