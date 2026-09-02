"""Focused retry contracts for the translation batch service."""

from __future__ import annotations

import json
import unittest
from unittest import mock

import gemini_client
from services import translation_batch_service


class TranslationBatchServiceRetryTests(unittest.TestCase):
    def _service(self, generate):
        return translation_batch_service.TranslationBatchService(
            None,
            None,
            None,
            None,
            gemini_generate=generate,
            sleep_provider=lambda _seconds: None,
            empty_retry_delay_provider=lambda: 0,
        )

    def test_empty_text_retries_three_times_then_keeps_source(self) -> None:
        calls = []

        def generate(*_args, **_kwargs):
            calls.append(1)
            return json.dumps({
                "translated_text": "",
                "translation_notes": [],
            })

        text, notes, fallback = self._service(
            generate
        ).translate_paragraph_with_retries(
            segment_id=1,
            source_text="원문",
            prompt="translate",
            job_id=7,
        )
        self.assertEqual(len(calls), 3)
        self.assertEqual(text, "원문")
        self.assertTrue(fallback)
        self.assertTrue(notes[-1]["needs_manual_review"])

    def test_separator_bypasses_gemini(self) -> None:
        generate = mock.Mock(side_effect=AssertionError("should not call"))
        text, notes, fallback = self._service(
            generate
        ).translate_paragraph_with_retries(
            segment_id=1,
            source_text="====",
            prompt="translate",
        )
        self.assertEqual(text, "====")
        self.assertEqual(notes, [])
        self.assertFalse(fallback)
        generate.assert_not_called()

    def test_rate_limit_retries_five_times_with_parsed_wait(self) -> None:
        errors = [
            gemini_client.GeminiError(
                "retry in 1.25s",
                code="rate_limit",
                http_status=429,
            )
            for _ in range(5)
        ]
        waits = []
        with (
            mock.patch.object(gemini_client, "is_configured", return_value=True),
            mock.patch.object(
                gemini_client,
                "generate_text",
                side_effect=[*errors, "ok"],
            ) as generate,
        ):
            result = translation_batch_service.generate_translation_text(
                "prompt",
                temperature=0.4,
                max_output_tokens=100,
                job_id=9,
                sleep_provider=waits.append,
            )
        self.assertEqual(result, "ok")
        self.assertEqual(generate.call_count, 6)
        self.assertEqual(waits, [1.25] * 5)
        self.assertEqual(
            generate.call_args.kwargs.get("timeout"),
            translation_batch_service.TRANSLATION_GEMINI_TIMEOUT_SECONDS,
        )

    def test_timeout_error_becomes_api_failure_message(self) -> None:
        with (
            mock.patch.object(gemini_client, "is_configured", return_value=True),
            mock.patch.object(
                gemini_client,
                "generate_text",
                side_effect=gemini_client.GeminiError(
                    gemini_client.API_USER_MESSAGE, code="timeout"
                ),
            ),
        ):
            with self.assertRaises(ValueError) as ctx:
                translation_batch_service.generate_translation_text(
                    "prompt",
                    temperature=0.35,
                    max_output_tokens=8192,
                    job_id=3,
                )
        self.assertEqual(str(ctx.exception), gemini_client.API_USER_MESSAGE)
        self.assertNotIn("인터넷 연결이 필요", str(ctx.exception))

    def test_parser_keeps_hidden_nested_and_truncated_json_support(self) -> None:
        nested, _notes = translation_batch_service._parse_paragraph_output(
            'prefix {"result":{"translation":"Nested"}} suffix'
        )
        truncated, _notes = translation_batch_service._parse_paragraph_output(
            '{"translated_text":"Line one\\n\\"Line two\\"","notes":['
        )
        self.assertEqual(nested, "Nested")
        self.assertEqual(truncated, 'Line one\n"Line two"')


if __name__ == "__main__":
    unittest.main()
