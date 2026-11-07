"""Haiku 5.5 switch: sampling lock, tiered price, refusal fallback to Sonnet."""

from __future__ import annotations

import unittest
from unittest import mock

from feedback_pipeline import claude_client
from feedback_pipeline.config import FEEDBACK_MODEL, HAIKU_MODEL


class HaikuModelTests(unittest.TestCase):
    def test_haiku_is_55_and_sampling_locked(self) -> None:
        self.assertEqual(HAIKU_MODEL, "claude-haiku-5-5")
        # 5.5 rejects non-default temperature, so it must never be sent.
        self.assertTrue(claude_client.is_sampling_locked_model(HAIKU_MODEL))

    def test_price_is_tiered_above_100k_prompt(self) -> None:
        long_prompt = claude_client.estimate_cost_usd(HAIKU_MODEL, 1_000_000, 0)
        self.assertAlmostEqual(long_prompt, 0.50, places=6)
        small = claude_client.estimate_cost_usd(HAIKU_MODEL, 50_000, 10_000)
        self.assertAlmostEqual(small, 50_000 / 1e6 * 0.10 + 10_000 / 1e6 * 0.50, places=6)

    def test_sonnet_5_price_matches_published_rate(self) -> None:
        self.assertEqual(claude_client.PRICE_PER_MILLION_USD["claude-sonnet-5"], (2.0, 10.0))


class HaikuRefusalFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        # 직접 호출 경로를 시험한다(키가 비어 있어도 relay 모드로 바뀌지 않게 고정).
        patcher = mock.patch.dict("os.environ", {"SUPERTORY_AI_MODE": "direct"})
        patcher.start()
        self.addCleanup(patcher.stop)


    def _body(self, stop_reason: str) -> dict:
        return {"stop_reason": stop_reason, "usage": {"input_tokens": 10, "output_tokens": 2}}

    def test_refusal_retries_once_on_sonnet(self) -> None:
        calls: list[dict] = []

        def fake_once(prompt, **kwargs):
            calls.append(kwargs)
            if kwargs["model"] == HAIKU_MODEL:
                return "", self._body("refusal")
            return '{"ok": true}', self._body("end_turn")

        with mock.patch.object(claude_client, "is_configured", return_value=True), \
                mock.patch.object(claude_client, "_generate_once", side_effect=fake_once):
            result = claude_client.generate("요약해", model=HAIKU_MODEL)

        self.assertEqual([c["model"] for c in calls], [HAIKU_MODEL, FEEDBACK_MODEL])
        self.assertTrue(calls[0]["omit_temperature"])
        self.assertTrue(calls[0]["thinking_disabled"])
        self.assertEqual(result["model"], FEEDBACK_MODEL)
        self.assertEqual(result["parsed"], {"ok": True})

    def test_normal_answer_stays_on_haiku(self) -> None:
        with mock.patch.object(claude_client, "is_configured", return_value=True), \
                mock.patch.object(
                    claude_client, "_generate_once",
                    return_value=('{"ok": 1}', {"stop_reason": "end_turn", "usage": {}}),
                ) as once:
            result = claude_client.generate("요약해", model=HAIKU_MODEL)
        self.assertEqual(once.call_count, 1)
        self.assertEqual(result["model"], HAIKU_MODEL)


class ClaudeAuthErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        # 직접 호출 경로를 시험한다(키가 비어 있어도 relay 모드로 바뀌지 않게 고정).
        patcher = mock.patch.dict("os.environ", {"SUPERTORY_AI_MODE": "direct"})
        patcher.start()
        self.addCleanup(patcher.stop)


    def test_raw_http_401_is_auth(self) -> None:
        import io
        import urllib.error

        error = urllib.error.HTTPError(
            "https://api.anthropic.com/v1/messages", 401, "Unauthorized", {},
            io.BytesIO(b'{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}'),
        )
        with mock.patch.object(claude_client, "is_configured", return_value=True), \
                mock.patch.dict("sys.modules", {"anthropic": None}), \
                mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(claude_client.ClaudeError) as caught:
                claude_client.generate("hi", model=HAIKU_MODEL)
        self.assertEqual(caught.exception.code, "auth")


if __name__ == "__main__":
    unittest.main()
