"""AI 중계 창구 클라이언트: 가짜 창구 서버로 relay 모드를 시험한다. 실제 AI 키는 쓰지 않는다."""

from __future__ import annotations

import ast
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

import ai_relay
import gemini_client
from feedback_pipeline import claude_client
from feedback_pipeline.config import FEEDBACK_MODEL, HAIKU_MODEL

SEEN: list[dict] = []
BEHAVIOR = {"mode": "ok", "quota": {"remaining_usd": 1.5}}


class FakeRelay(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # noqa: D401
        pass

    def _send(self, status: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        SEEN.append({"path": self.path, "auth": self.headers.get("authorization")})
        if BEHAVIOR["mode"] == "login_required":
            self._send(401, json.dumps({"error": "login_required"}).encode())
            return
        self._send(200, json.dumps(BEHAVIOR["quota"]).encode())

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        SEEN.append({"path": self.path, "auth": self.headers.get("authorization"), "body": body})
        mode = BEHAVIOR["mode"]
        if mode in {"daily_limit", "no_access", "login_required"}:
            status = {"daily_limit": 429, "no_access": 403, "login_required": 401}[mode]
            self._send(status, json.dumps({"error": mode}).encode())
            return
        if mode == "provider_429":
            self._send(502, json.dumps({"error": "provider_error", "provider_status": 429,
                                        "detail": "{}"}).encode())
            return
        if self.path.endswith("/gemini"):
            reply = {"candidates": [{"content": {"parts": [{"text": "안녕하세요"}]}}]}
            self._send(200, json.dumps(reply).encode())
            return
        events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 12, "output_tokens": 1}}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "{\"ok\":"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " true}"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 7}},
        ]
        sse = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
        self._send(200, sse.encode(), "text/event-stream")


class RelayClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = HTTPServer(("127.0.0.1", 0), FakeRelay)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.env = mock.patch.dict(os.environ, {
            "SUPERTORY_AI_MODE": "relay",
            "SUPABASE_URL": f"http://127.0.0.1:{cls.server.server_port}",
        })
        cls.env.start()
        cls.token = mock.patch.object(ai_relay, "_access_token", return_value="tok123")
        cls.token.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.token.stop()
        cls.env.stop()
        cls.server.shutdown()

    def setUp(self) -> None:
        SEEN.clear()
        BEHAVIOR["mode"] = "ok"
        BEHAVIOR["quota"] = {"has_access": True, "enabled": True, "remaining_usd": 1.5}

    def test_mode_default_is_relay_only_when_frozen(self) -> None:
        with mock.patch.dict(os.environ, {"SUPERTORY_AI_MODE": ""}):
            self.assertEqual(ai_relay.mode(), "direct")
            with mock.patch.object(ai_relay.sys, "frozen", True, create=True):
                self.assertEqual(ai_relay.mode(), "relay")
        with mock.patch.dict(os.environ, {"SUPERTORY_AI_MODE": "relay"}):
            self.assertEqual(ai_relay.mode(), "relay")

    def test_gemini_goes_through_relay_with_feature_and_logical_model(self) -> None:
        text = gemini_client.generate_text("이어 써줘", feature="tory.continue")
        self.assertEqual(text, "안녕하세요")
        sent = SEEN[0]
        self.assertTrue(sent["path"].endswith("/functions/v1/ai/gemini"))
        self.assertEqual(sent["auth"], "Bearer tok123")
        self.assertEqual(sent["body"]["feature"], "tory.continue")
        self.assertEqual(sent["body"]["model"], "gemini:default")
        self.assertNotIn("key", json.dumps(sent["body"]["request"]).lower().replace("generationconfig", ""))

    def test_gemini_relay_errors_map_to_codes_without_http_status(self) -> None:
        for code in ("daily_limit", "no_access", "login_required"):
            BEHAVIOR["mode"] = code
            with self.assertRaises(gemini_client.GeminiError) as caught:
                gemini_client.generate_text("hi", feature="tory.continue")
            self.assertEqual(caught.exception.code, code)
            self.assertIsNone(caught.exception.http_status)
            self.assertTrue(str(caught.exception))

    def test_provider_429_keeps_existing_classification(self) -> None:
        BEHAVIOR["mode"] = "provider_429"
        with self.assertRaises(gemini_client.GeminiError) as caught:
            gemini_client.generate_text("hi")
        self.assertEqual(caught.exception.code, "rate_limit")

    def test_claude_stream_is_collected_with_usage(self) -> None:
        result = claude_client.generate(
            "요약해", model=HAIKU_MODEL, feature="feedback.test"
        )
        self.assertEqual(result["parsed"], {"ok": True})
        self.assertEqual(result["usage"]["input_tokens"], 12)
        self.assertEqual(result["usage"]["output_tokens"], 7)
        body = SEEN[0]["body"]
        self.assertEqual(body["model"], "claude:light")
        self.assertEqual(body["feature"], "feedback.test")

    def test_claude_limit_does_not_retry(self) -> None:
        BEHAVIOR["mode"] = "daily_limit"
        with self.assertRaises(claude_client.ClaudeError) as caught:
            claude_client.generate("hi", model=FEEDBACK_MODEL)
        self.assertEqual(caught.exception.code, "daily_limit")
        self.assertEqual(len(SEEN), 1)

    def test_quota(self) -> None:
        self.assertEqual(ai_relay.get_quota()["remaining_usd"], 1.5)

    def test_logged_out_raises_login_required(self) -> None:
        with mock.patch.object(ai_relay, "_access_token", return_value=None):
            with self.assertRaises(ai_relay.RelayError) as caught:
                ai_relay.get_quota()
        self.assertEqual(caught.exception.code, "login_required")

    def test_feedback_start_checks_quota_reserve(self) -> None:
        import feedback_api

        # 웹소설 여유분($0.5) ≤ 남은 $1.5 → 시작한다.
        feedback_api._ensure_ai_quota("webnovel", 2.0)
        self.assertTrue(SEEN[-1]["path"].endswith("/functions/v1/ai/quota"))
        # 중간 점검 여유분($2) > 남은 $1.5 → 시작하지 않는다.
        with self.assertRaises(ValueError) as caught:
            feedback_api._ensure_ai_quota("literature_midcheck", 6.0)
        self.assertEqual(str(caught.exception), feedback_api.QUOTA_SHORT_MESSAGE)
        # 여유분은 실행 상한을 넘지 않는다.
        feedback_api._ensure_ai_quota("literature_midcheck", 1.0)

    def test_feedback_start_reports_access_and_login(self) -> None:
        import feedback_api

        BEHAVIOR["quota"] = {"has_access": False, "enabled": True, "remaining_usd": 0}
        with self.assertRaises(ValueError) as caught:
            feedback_api._ensure_ai_quota("webnovel", 2.0)
        self.assertEqual(str(caught.exception), ai_relay.RELAY_ERROR_MESSAGES["no_access"])
        BEHAVIOR["mode"] = "login_required"
        with self.assertRaises(ValueError) as caught:
            feedback_api._ensure_ai_quota("webnovel", 2.0)
        self.assertEqual(str(caught.exception), ai_relay.RELAY_ERROR_MESSAGES["login_required"])

    def test_feedback_quota_skipped_in_direct_mode(self) -> None:
        import feedback_api

        with mock.patch.dict(os.environ, {"SUPERTORY_AI_MODE": "direct"}):
            feedback_api._ensure_ai_quota("literature_midcheck", 6.0)
        self.assertEqual(SEEN, [])


class FeatureLabelTests(unittest.TestCase):
    """모든 AI 호출에 사용 기록용 이름표(feature)가 붙어 있어야 한다(설계안 D8)."""

    ROOT = Path(__file__).resolve().parents[1]
    SKIP_DIRS = {"tests", "node_modules", "dist", "backend-dist", "build", ".git", "scripts"}

    def _sources(self):
        for path in self.ROOT.rglob("*.py"):
            rel = path.relative_to(self.ROOT)
            if rel.parts[0] in self.SKIP_DIRS or rel.parts[0].startswith("."):
                continue
            yield rel, ast.parse(path.read_text(encoding="utf-8"))

    @staticmethod
    def _labelled(call: ast.Call) -> bool:
        return any(k.arg == "feature" or k.arg is None for k in call.keywords)

    def test_every_gemini_call_has_feature(self) -> None:
        missing = []
        for rel, tree in self._sources():
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "generate_text" and not self._labelled(node)):
                    missing.append(f"{rel}:{node.lineno}")
        self.assertEqual(missing, [])

    def test_every_claude_call_has_feature(self) -> None:
        missing = []
        for rel, tree in self._sources():
            if not (rel.parts[0] == "feedback_pipeline" or rel.name == "feedback_api.py"):
                continue
            if rel.name == "claude_client.py":
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name == "generate" and not self._labelled(node):
                    missing.append(f"{rel}:{node.lineno}")
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
