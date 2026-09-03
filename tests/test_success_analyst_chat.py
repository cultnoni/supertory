"""흥행요인 분석가 채팅 세션: 프롬프트 스코프 · 세션 분리 · API."""

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
import success_pattern

ROOT = Path(__file__).resolve().parents[1]


class SuccessAnalystChatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.orig_data = app.DATA_DIR
        self.orig_db = app.DATABASE_PATH
        app.DATA_DIR = Path(self.td.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        app.DATA_DIR = self.orig_data
        app.DATABASE_PATH = self.orig_db
        self.td.cleanup()

    def req(self, method: str, path: str, payload: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=120)
        body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        headers = {"Content-Type": "application/json"} if body is not None else {}
        conn.request(method, path, body, headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        conn.close()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = raw
        return resp.status, data

    def test_a_ui_gating_in_html_js(self) -> None:
        """(a) 버튼 id + 숨김 로직 + linked 시에만 노출 조건이 코드에 있는지."""
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="toryChatSuccessSummonButton"', html)
        self.assertIn("흥행요인 분석가 소환", html)
        self.assertIn("tory-chat-success-summon", html)
        self.assertIn("hidden", html[html.find("toryChatSuccessSummonButton") - 80 :])
        self.assertIn("updateToryChatSuccessUi", js)
        self.assertIn("getLinkedSuccessProfileId", js)
        self.assertIn("successAnalysis", js)
        # Summon hidden when !linked or already in success session
        compact = re.sub(r"\s+", "", js)
        self.assertTrue(
            "!linked||inSuccess" in compact or "!linked||inSuccess)" in compact,
            "updateToryChatSuccessUi should hide summon when !linked || inSuccess",
        )

    def test_b_storage_keys_isolated(self) -> None:
        """(b) general / successAnalysis 저장 키가 분리되어 있는지."""
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("supertory.toryChat.successAnalysis.", js)
        self.assertIn("supertory.toryChatArchive.successAnalysis.", js)
        self.assertIn("successAnalysis", js)
        # load/save accept chatMode parameter
        self.assertIn("function loadToryChatHistory(chatMode", js)
        self.assertIn("function saveToryChatHistory(list, chatMode", js)
        self.assertIn("function loadToryChatArchives(chatMode", js)
        self.assertIn("chatMode = getToryChatSessionKey()", js)
        self.assertIn("function archiveCurrentToryChat(", js)
        self.assertIn("function restoreToryChatArchive(archiveId, chatMode", js)

    def test_success_analyst_scope_prompt(self) -> None:
        profile = {
            "reader_popularity_factors": ["회차 말미 훅", "캐릭터 감정선"],
            "editor_popularity_factors": ["설정 일관성"],
            "must_follow_factors": ["캐릭터 말투 유지"],
            "hook_style": "궁금증 훅",
            "pacing_pattern": "빠른 전개",
            "dialogue_narration_balance": "대사 비중",
            "style_signature": "담백",
        }
        scope = success_pattern.build_success_analyst_chat_scope(profile)
        self.assertIn("흥행요인 분석가", scope)
        self.assertIn("회차 말미 훅", scope)
        self.assertIn("설정 일관성", scope)
        self.assertIn("캐릭터 말투 유지", scope)
        self.assertIn("궁금증 훅", scope)
        self.assertIn("Core Identity", scope)

    def test_c_d_live_chat_with_profile_and_persona(self) -> None:
        """(c)(d) 성공 요인 근거 답변 + 투덜이 톤 (Gemini 있을 때)."""
        if not gemini_client.is_configured():
            self.skipTest("GEMINI_API_KEY 없음")

        st, project = self.req(
            "POST",
            "/api/projects",
            {"title": "신작 채팅", "main_genre": "판타지"},
        )
        self.assertEqual(st, 201, project)
        pid = project["id"]

        # Build a profile with distinctive factors
        st, run = self.req(
            "POST",
            "/api/success-pattern/run",
            {
                "work_title": "전작 흥행",
                "total_chapters": 50,
                "dry_run": True,
                "sections": [
                    {
                        "key": "front",
                        "start_ep": 1,
                        "end_ep": 2,
                        "episodes": [
                            {"title": "1화", "text": ("훅으로 끝나는 회차. " * 40)},
                            {"title": "2화", "text": ("대사 위주 전개. " * 40)},
                        ],
                    }
                ],
            },
        )
        self.assertEqual(st, 200, run)
        profile_row = run["profile"]
        factors = profile_row.get("profile") or {}
        # ensure distinctive must-follow for assertion softness
        factors = {
            **factors,
            "reader_popularity_factors": ["회차 말미 궁금증 훅", "감정선 유지"],
            "editor_popularity_factors": ["설정 일관성", "전개 속도 균형"],
            "must_follow_factors": ["캐릭터 말투 일관성", "회차 단위 긴장"],
            "hook_style": "궁금증 훅으로 마무리",
            "pacing_pattern": "사건 중심 빠른 호흡",
            "dialogue_narration_balance": "대사 비중 높음",
            "style_signature": "담백하고 읽기 쉬운 문체",
        }

        st, ch = self.req("POST", f"/api/projects/{pid}/chapters", {"title": "1장"})
        st, sc = self.req("POST", f"/api/chapters/{ch['id']}/scenes", {"title": "1화"})
        st, detail = self.req("GET", f"/api/scenes/{sc['id']}")
        body_html = (
            "<p>주인공이 마을에 도착했다. 별다른 사건 없이 풍경만 길게 묘사한다. "
            "회차 끝이 잔잔하게 끝난다. 대사는 거의 없다.</p>" * 5
        )
        self.req(
            "PUT",
            f"/api/scenes/{sc['id']}",
            {
                "title": "1화",
                "status": "draft",
                "content_md": body_html,
                "row_version": detail["row_version"],
            },
        )

        question = "이거랑 비교해서 재미요소가 뭐가 부족해? 흥행 요인 기준으로 짚어 줘."

        # (c) default persona
        st, chat = self.req(
            "POST",
            "/api/ai/assist",
            {
                "mode": "chat",
                "chat_mode": "successAnalysis",
                "user_prompt": question,
                "history": [],
                "project_id": pid,
                "project_title": "신작 채팅",
                "main_genre": "판타지",
                "purpose": "general_novel",
                "scene_title": "1화",
                "scene_content": re.sub(r"<[^>]+>", "", body_html),
                "persona_mode": "default",
                "success_profile": factors,
            },
        )
        self.assertEqual(st, 200, chat)
        self.assertEqual(chat.get("chat_mode"), "successAnalysis")
        text = str(chat.get("text") or "")
        self.assertGreater(len(text), 40)
        # Should reference some success-related notions (hook / tension / dialogue etc.)
        lowered = text.lower()
        hits = sum(
            1
            for kw in ("훅", "긴장", "대사", "전개", "재미", "여운", "속도", "감정", "일관")
            if kw in text
        )
        self.assertGreaterEqual(hits, 1, f"프로파일 근거 답변으로 보기 어려움: {text[:200]}")

        # (d) grumbler persona — same question, tone should differ / still answer
        st, grumble = self.req(
            "POST",
            "/api/ai/assist",
            {
                "mode": "chat",
                "chat_mode": "successAnalysis",
                "user_prompt": question,
                "history": [],
                "project_id": pid,
                "project_title": "신작 채팅",
                "main_genre": "판타지",
                "purpose": "general_novel",
                "scene_title": "1화",
                "scene_content": re.sub(r"<[^>]+>", "", body_html),
                "persona_mode": "grumbler",
                "success_profile": factors,
            },
        )
        self.assertEqual(st, 200, grumble)
        gtext = str(grumble.get("text") or "")
        self.assertGreater(len(gtext), 40)
        # Not a strict string match — just ensure both ran and grumbler still uses profile frame
        self.assertTrue(
            any(k in gtext for k in ("훅", "긴장", "대사", "전개", "재미", "여운", "부족")),
            gtext[:240],
        )


if __name__ == "__main__":
    unittest.main()
