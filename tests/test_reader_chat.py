"""Virtual reader 1:1 chat: prompts, list/history APIs, POST /api/reader-chat."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app
import gemini_client


class ReaderChatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        self.original_generate = gemini_client.generate_text
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.captured: dict[str, object] = {}

        def _fake_generate(prompt: str, *, system: str | None = None, **kwargs: object) -> str:
            self.captured["prompt"] = prompt
            self.captured["system"] = system
            return "이렇게 쌓아놓고 이 정도로 끝나면 섭섭하지. 여주가 직접 되갚는 장면이 더 필요해."

        gemini_client.generate_text = _fake_generate  # type: ignore[method-assign]

    def tearDown(self) -> None:
        gemini_client.generate_text = self.original_generate  # type: ignore[method-assign]
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
            "127.0.0.1", self.server.server_port, timeout=30
        )
        body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = raw
        return response.status, data

    def _make_project(self) -> int:
        status, project = self.request(
            "POST",
            "/api/projects",
            {"title": "사이다 테스트", "main_genre": "romfant", "sub_genre": "high"},
        )
        self.assertEqual(status, 201, project)
        return int(project["id"])

    def test_persona_system_prompt_is_only_this_reader(self) -> None:
        with app.database() as connection:
            row = connection.execute(
                "SELECT * FROM virtual_reader_personas WHERE id = ?",
                ("roppan_cider",),
            ).fetchone()
        prompt = app._reader_persona_system_prompt(dict(row))
        self.assertIn("당신은 '로맨스·로판 사이다파'이라는 이름의 가상 독자입니다.", prompt)
        self.assertIn("[정체성]", prompt)
        self.assertIn("카타르시스", prompt)
        self.assertIn("[말투]", prompt)
        self.assertIn("[평가 우선순위]", prompt)
        self.assertIn("1. ", prompt)
        self.assertIn("주인공이 그 사이다를 스스로 만들어내는가", prompt)
        self.assertIn("[금지사항]", prompt)
        self.assertIn("[말투 예시]", prompt)
        self.assertIn("그대로 따라 말하지 말고", prompt)
        self.assertIn("1:1로 대화 중입니다", prompt)
        self.assertIn("AI라는 사실을 언급하지 말고", prompt)
        self.assertIn("이유를 함께 설명하세요", prompt)
        self.assertIn("회빙환", prompt)
        self.assertIn("임의로 가정해서 언급하지 마라", prompt)
        self.assertIn("본문/설정에 안 나와서 모르겠다", prompt)
        self.assertNotIn("토리 Core Identity", prompt)
        self.assertNotIn("당신은 '토리'입니다", prompt)
        self.assertNotIn("로판 서사파", prompt)
        self.assertNotIn("현대로맨스 설렘파", prompt)
        self.assertNotIn("discussion_attitude", prompt)

    def test_dynamic_context_includes_genre_and_optional_manuscript(self) -> None:
        pid = self._make_project()
        status, _ = self.request(
            "PUT",
            f"/api/projects/{pid}/settings",
            {
                "synopsis_md": (
                    "재벌가 계약결혼 로맨스. 여주는 위장 이혼을 추진하고, "
                    "남주는 가문의 후계 분쟁에 휘말린다. 회귀·빙의·환생은 없다."
                ),
                "worldbuilding_md": (
                    "## 1. 무대 및 시대 (Where & When)\n"
                    "작품의 기본 바탕이 되는 공간과 시간선입니다.\n\n"
                    "### 현실 / 가상 구분\n"
                    "현실\n\n"
                    "### 시대 배경\n"
                    "현대 서울\n\n"
                    "### 주요 배경\n"
                    "재벌 본사와 한남동 저택\n\n"
                    "## 2. 세계의 특이점 (Unique Concept)\n"
                    "이 세계를 다른 세계관과 다르게 만드는 단 하나의 핵심 규칙입니다.\n\n"
                    "### 특수 요소\n"
                    "\n\n"
                    "### 작동 규칙\n"
                    "\n\n"
                    "### 한계와 대가\n"
                    "\n\n"
                ),
            },
        )
        self.assertEqual(status, 200)
        bare = app._reader_dynamic_context(pid)
        self.assertIn("메인 장르: 로판", bare)
        self.assertIn("서브 장르:", bare)
        self.assertIn("[작품 설정 요약", bare)
        self.assertIn("설정집 정보이며 실제 원고 문장이 아님", bare)
        self.assertIn("시놉시스 요약:", bare)
        self.assertIn("계약결혼", bare)
        self.assertIn("세계관 설정 요약", bare)
        self.assertIn("현대 서울", bare)
        self.assertNotIn("### 특수 요소", bare)
        self.assertIn("[원고 미첨부]", bare)
        self.assertIn("시놉시스/세계관·누적 인덱스 정보만 가지고 대화하는 상황", bare)
        self.assertNotIn("작가가 공유한 원고", bare)
        self.assertNotIn("[프로젝트 누적 정보]", bare)
        with_ms = app._reader_dynamic_context(pid, "빌런이 사과하고 끝났다.")
        self.assertIn("다음은 작가가 공유한 원고 내용입니다:", with_ms)
        self.assertIn("빌런이 사과하고 끝났다.", with_ms)
        self.assertNotIn("[원고 미첨부]", with_ms)
        self.assertNotIn("[Tory Core Identity]", with_ms)

    def test_dynamic_context_includes_project_index_when_present(self) -> None:
        pid = self._make_project()
        with app.database() as connection:
            connection.execute(
                "INSERT INTO project_index("
                "project_id, characters_json, world_rules_json, timeline_json, "
                "open_threads_json, tracked_facts_json, index_dirty, pending_scene_ids_json"
                ") VALUES (?, ?, ?, ?, ?, ?, 0, '[]') "
                "ON CONFLICT(project_id) DO UPDATE SET "
                "characters_json=excluded.characters_json, "
                "world_rules_json=excluded.world_rules_json, "
                "timeline_json=excluded.timeline_json, "
                "open_threads_json=excluded.open_threads_json, "
                "tracked_facts_json=excluded.tracked_facts_json",
                (
                    pid,
                    '["송혜아", "신재결"]',
                    '["BNG그룹은 호텔을 운영한다."]',
                    '["26화: 공항에서 혜아가 긴급 고용된다."]',
                    '["파격적인 고용 조건", "식중독 사건의 원인"]',
                    '[{"category":"관계","subject":"송혜아","attribute":"신재결","value":"재회","since_scene":"26"}]',
                ),
            )
        ctx = app._reader_dynamic_context(pid)
        self.assertIn("[프로젝트 누적 정보]", ctx)
        self.assertIn("[지금까지 전개 - 실제 원고 문장 아님, 요약임]", ctx)
        self.assertIn("공항에서 혜아가 긴급 고용", ctx)
        self.assertIn("[아직 안 풀린 떡밥/복선 목록]", ctx)
        self.assertIn("파격적인 고용 조건", ctx)
        self.assertIn("식중독 사건의 원인", ctx)
        self.assertIn("[추적 중인 설정/인물 사실]", ctx)
        self.assertIn("송혜아", ctx)
        self.assertIn("BNG그룹은 호텔을 운영한다.", ctx)
        # settings block still before index, manuscript tip after
        self.assertLess(ctx.find("[작품 정보]"), ctx.find("[프로젝트 누적 정보]"))
        self.assertLess(ctx.find("[프로젝트 누적 정보]"), ctx.find("[원고 미첨부]"))

    def test_plot_analyst_gets_thread_focus_instruction(self) -> None:
        with app.database() as connection:
            plot = connection.execute(
                "SELECT * FROM virtual_reader_personas WHERE id = ?",
                ("plausibility_absolutist",),
            ).fetchone()
            cider = connection.execute(
                "SELECT * FROM virtual_reader_personas WHERE id = ?",
                ("roppan_cider",),
            ).fetchone()
        plot_prompt = app._reader_persona_system_prompt(dict(plot))
        cider_prompt = app._reader_persona_system_prompt(dict(cider))
        focus = "너무 오래 방치된 떡밥은 없는지 짚어라"
        self.assertIn(focus, plot_prompt)
        self.assertIn("[아직 안 풀린 떡밥/복선 목록]", plot_prompt)
        self.assertNotIn(focus, cider_prompt)
        self.assertIn("개연성·복선 설계 분석가", plot_prompt)

    def test_dynamic_context_omits_empty_settings_quietly(self) -> None:
        pid = self._make_project()
        bare = app._reader_dynamic_context(pid)
        self.assertIn("메인 장르: 로판", bare)
        self.assertIn("[원고 미첨부]", bare)
        self.assertNotIn("시놉시스 요약:", bare)
        self.assertNotIn("세계관 설정 요약", bare)
        self.assertNotIn("[프로젝트 누적 정보]", bare)

    def test_list_personas_grouped_by_category(self) -> None:
        status, grouped = self.request("GET", "/api/reader-personas")
        self.assertEqual(status, 200, grouped)
        self.assertEqual(
            list(grouped.keys()),
            list(app.READER_PERSONA_CATEGORIES),
        )
        total = sum(len(grouped[key]) for key in grouped)
        self.assertEqual(total, 24)
        first = grouped["genre_specialist"][0]
        self.assertEqual(first["id"], "roppan_cider")
        self.assertIsInstance(first["criteria"], list)
        self.assertGreaterEqual(len(first["criteria"]), 1)
        self.assertEqual(
            first["identity"],
            "억울함이 쌓일수록 좋다, 대신 터질 땐 확실하게 터져야 한다 — "
            "속도가 아니라 카타르시스의 완성도가 기준",
        )
        self.assertNotIn("배경이 이세계든 현대든", first["identity"])
        by_id = {
            person["id"]: person
            for people in grouped.values()
            for person in people
        }
        self.assertEqual(
            by_id["roppan_narrative"]["identity"],
            "사이다 없어도 주인공이 왜 그런 선택을 하는지 납득되면 된다, 개연성과 독창성을 봄",
        )
        self.assertIn(
            "설정(세계관·현실 배경) 몰입도",
            by_id["roppan_narrative"]["criteria"],
        )
        self.assertEqual(
            by_id["modern_romance_flutter"]["identity"],
            "두근거림이 생명, 직접적인 다정함과 츤데레의 숨은 마음 둘 다 좋음",
        )
        self.assertEqual(
            by_id["modern_romance_tension"]["identity"],
            "밀당 없는 로맨스는 밍밍하다, 긴장감이 심장을 뛰게 해야 함",
        )
        hunter = by_id["hunter_speedrunner"]
        self.assertEqual(
            hunter["identity"],
            "각성부터 랭크업까지 성장 곡선이 납득 가능해야 함, "
            "등급 체계와 힘의 논리가 헐거우면 감점",
        )
        self.assertEqual(
            hunter["criteria"],
            [
                "각성·랭크업 서사의 개연성",
                "랭크·등급 체계의 논리적 일관성",
                "성장 속도와 보상(레벨업, 스킬 습득)의 밸런스",
            ],
        )
        self.assertNotIn("초반 후킹 속도", hunter["criteria"])

    def test_missing_persona_is_404(self) -> None:
        pid = self._make_project()
        status, result = self.request(
            "POST",
            "/api/reader-chat",
            {
                "work_id": str(pid),
                "persona_id": "not_a_real_reader",
                "user_message": "이 장면 어때?",
            },
        )
        self.assertEqual(status, 404, result)

    def test_post_reader_chat_saves_history(self) -> None:
        pid = self._make_project()
        status, chat = self.request(
            "POST",
            "/api/reader-chat",
            {
                "work_id": str(pid),
                "persona_id": "roppan_cider",
                "user_message": "이 장면 어때?",
                "episode_content": "빌런이 울면서 사과하고 끝났다.",
            },
        )
        self.assertEqual(status, 200, chat)
        self.assertEqual(chat.get("persona_name"), "로맨스·로판 사이다파")
        self.assertTrue(chat.get("session_id"))
        self.assertIn("섭섭하지", chat.get("reply") or "")
        system = str(self.captured.get("system") or "")
        self.assertIn("로맨스·로판 사이다파", system)
        self.assertIn("작가가 공유한 원고", system)
        self.assertIn("임의로 가정해서 언급하지 마라", system)
        self.assertNotIn("당신은 '토리'입니다", system)
        self.assertNotIn("로판 서사파", system)
        self.assertNotIn("장르 불일치 인지", system)

        status, history = self.request(
            "GET",
            f"/api/reader-chat/history?work_id={pid}&persona_id=roppan_cider",
        )
        self.assertEqual(status, 200, history)
        self.assertEqual(history.get("session_id"), chat["session_id"])
        messages = history.get("messages") or []
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[0]["content"], "이 장면 어때?")
        self.assertEqual(messages[1]["role"], "assistant")
        self.assertRegex(
            messages[0]["created_at"],
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$",
        )

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_reader_chat_example(self) -> None:
        gemini_client.generate_text = self.original_generate  # type: ignore[method-assign]
        pid = self._make_project()
        status, chat = self.request(
            "POST",
            "/api/reader-chat",
            {
                "work_id": str(pid),
                "persona_id": "roppan_cider",
                "user_message": "이 장면 어때?",
                "episode_content": "빌런이 울면서 사과하고 끝났다. 여주는 아무 말도 하지 않았다.",
            },
        )
        self.assertEqual(status, 200, chat)
        self.assertEqual(chat.get("persona_name"), "로맨스·로판 사이다파")
        self.assertGreater(len(str(chat.get("reply") or "")), 10)

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_reader_no_regression_stereotype_without_manuscript(self) -> None:
        """로판이라도 시놉시스에 없는 회빙환을 임의로 꺼내지 않는지 확인."""
        gemini_client.generate_text = self.original_generate  # type: ignore[method-assign]
        pid = self._make_project()
        status, _ = self.request(
            "PUT",
            f"/api/projects/{pid}/settings",
            {
                "synopsis_md": (
                    "현대 재벌가 계약결혼물. 여주 서연은 위장 이혼으로 자유를 얻으려 하고, "
                    "남주 도현은 이사회 쿠데타를 막기 위해 혼인 유지를 원한다. "
                    "회귀·빙의·환생·숨겨진 황족 혈통 같은 장치는 없다."
                ),
                "worldbuilding_md": (
                    "## 1. 무대 및 시대 (Where & When)\n\n"
                    "### 현실 / 가상 구분\n현실\n\n"
                    "### 시대 배경\n2020년대 서울\n\n"
                    "### 주요 배경\n한남동 저택, 여의도 본사\n\n"
                ),
            },
        )
        self.assertEqual(status, 200)
        status, chat = self.request(
            "POST",
            "/api/reader-chat",
            {
                "work_id": str(pid),
                "persona_id": "roppan_cider",
                "user_message": "이 작품 어때요?",
            },
        )
        self.assertEqual(status, 200, chat)
        reply = str(chat.get("reply") or "")
        self.assertGreater(len(reply), 10, reply)
        lowered = reply.lower()
        for banned in ("회빙환", "회귀", "빙의", "환생", "전생"):
            self.assertNotIn(banned, reply, f"unexpected {banned!r} in: {reply}")
            self.assertNotIn(banned, lowered, f"unexpected {banned!r} in: {reply}")
        # Keep the reply visible in unittest -v output for manual review.
        print("\n[live no-manuscript reply]\n", reply)

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_reader_with_manuscript_is_more_specific(self) -> None:
        gemini_client.generate_text = self.original_generate  # type: ignore[method-assign]
        pid = self._make_project()
        status, _ = self.request(
            "PUT",
            f"/api/projects/{pid}/settings",
            {
                "synopsis_md": (
                    "현대 재벌가 계약결혼물. 여주 서연은 위장 이혼으로 자유를 얻으려 하고, "
                    "남주 도현은 이사회 쿠데타를 막기 위해 혼인 유지를 원한다."
                ),
            },
        )
        self.assertEqual(status, 200)
        episode = (
            "회의실 유리창 너머로 석양이 기울었다. 서연은 도현에게 이혼 합의서를 내밀며 "
            "말했다. \"이번엔 진짜예요. 가문 이미지도, 주가도, 더 이상 제 몫이 아니니까.\" "
            "도현은 서류를 받지 않은 채, 낮게 웃었다. \"이사회가 내일 열리는데, "
            "당신이 없으면 난 끝장이야. 한 달만. 그 뒤엔 당신이 원하는 대로 할게.\""
        )
        status, chat = self.request(
            "POST",
            "/api/reader-chat",
            {
                "work_id": str(pid),
                "persona_id": "roppan_cider",
                "user_message": "이 장면 어때요?",
                "episode_content": episode,
            },
        )
        self.assertEqual(status, 200, chat)
        reply = str(chat.get("reply") or "")
        self.assertGreater(len(reply), 10, reply)
        for banned in ("회빙환", "회귀", "빙의", "환생", "전생"):
            self.assertNotIn(banned, reply, f"unexpected {banned!r} in: {reply}")
        print("\n[live with-manuscript reply]\n", reply)

    def test_avatar_pngs_are_served_for_every_persona(self) -> None:
        status, grouped = self.request("GET", "/api/reader-personas")
        self.assertEqual(status, 200, grouped)
        ids = []
        for key in app.READER_PERSONA_CATEGORIES:
            ids.extend(item["id"] for item in grouped[key])
        self.assertEqual(len(ids), 24)
        missing = []
        for persona_id in ids:
            connection = http.client.HTTPConnection(
                "127.0.0.1", self.server.server_port, timeout=10
            )
            connection.request("GET", f"/assets/reader_avatars/{persona_id}.png")
            response = connection.getresponse()
            code = response.status
            body = response.read()
            connection.close()
            if code != 200 or not body.startswith(b"\x89PNG"):
                missing.append(f"{persona_id}:{code}")
        self.assertEqual(missing, [])

    def test_reader_ui_is_wired_in_html_js(self) -> None:
        root = Path(__file__).resolve().parents[1]
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        js = (root / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="readerPersonaGrid"', html)
        self.assertIn('id="readerChatStartButton"', html)
        self.assertIn("대화 시작하기", html)
        self.assertIn("startReaderChatFromPicker", js)
        self.assertIn('id="readerPersonaAllButton"', html)
        self.assertIn('id="readerPersonaAllModal"', html)
        self.assertIn("openReaderPersonaAllModal", js)
        all_modal = html[html.find('id="readerPersonaAllModal"'):html.find('id="toryChatCharacterAllModal"')]
        self.assertIn("modal-close", all_modal)
        self.assertNotIn("modal-actions", all_modal)
        self.assertIn('id="readerChatForm"', html)
        self.assertIn('id="readerChatAttachButton"', html)
        self.assertIn("listExportEpisodes", js)
        self.assertIn("openReaderChatWithPersona", js)
        self.assertIn("setupReaderDebateUi", js)
        self.assertIn("data-reader-list-mode", html)
        self.assertIn('id="readerDebatePane"', html)
        self.assertIn("app.p_class_reader_debate_s", js)
        self.assertIn("app.최소_3명을_골라주세요", js)
        self.assertIn("/api/reader-chat", js)
        self.assertNotIn("가상 독자 대화는 다음 안내에서 이어서 만들어요.", html)
