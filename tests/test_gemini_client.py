"""Gemini HTTP error classification."""

from __future__ import annotations

import unittest

import app
import gemini_client


DAILY_QUOTA_BODY = """{
  "error": {
    "code": 429,
    "message": "Resource exhausted. Please retry in 17.59s.",
    "status": "RESOURCE_EXHAUSTED",
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "RATE_LIMIT_EXCEEDED",
        "metadata": {
          "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
          "quota_limit_value": "500"
        }
      },
      {
        "@type": "type.googleapis.com/google.rpc.RetryInfo",
        "retryDelay": "17s"
      }
    ]
  }
}"""

PER_MINUTE_BODY = """{
  "error": {
    "code": 429,
    "message": "Resource exhausted.",
    "status": "RESOURCE_EXHAUSTED",
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "metadata": {
          "quota_id": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
        }
      }
    ]
  }
}"""

CAPTURED_FREE_TIER_PER_DAY_BODY = """{
  "error": {
    "code": 429,
    "message": "You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \\n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 500, model: gemini-3.5-flash-lite\\nPlease retry in 21.497994s.",
    "status": "RESOURCE_EXHAUSTED",
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.Help",
        "links": [
          {
            "description": "Learn more about Gemini API quotas",
            "url": "https://ai.google.dev/gemini-api/docs/rate-limits"
          }
        ]
      },
      {
        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [
          {
            "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
            "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
            "quotaDimensions": {
              "location": "global",
              "model": "gemini-3.5-flash-lite"
            },
            "quotaValue": "500"
          }
        ]
      },
      {
        "@type": "type.googleapis.com/google.rpc.RetryInfo",
        "retryDelay": "21s"
      }
    ]
  }
}"""


class GeminiErrorClassificationTests(unittest.TestCase):
    def test_429_per_day_quota_id_is_quota(self) -> None:
        code, retry_after, message = gemini_client.classify_gemini_http_error(
            429, DAILY_QUOTA_BODY
        )
        self.assertEqual(code, "quota")
        self.assertEqual(retry_after, 17.0)
        self.assertIn("Resource exhausted", message)

    def test_429_other_quota_id_is_rate_limit(self) -> None:
        code, retry_after, message = gemini_client.classify_gemini_http_error(
            429, PER_MINUTE_BODY
        )
        self.assertEqual(code, "rate_limit")
        self.assertIsNone(retry_after)
        self.assertTrue(message)

    def test_429_without_details_is_rate_limit(self) -> None:
        code, retry_after, message = gemini_client.classify_gemini_http_error(
            429, '{"error":{"message":"busy"}}'
        )
        self.assertEqual(code, "rate_limit")
        self.assertIsNone(retry_after)
        self.assertEqual(message, "busy")

    def test_401_and_403_are_auth(self) -> None:
        code, _, _ = gemini_client.classify_gemini_http_error(
            401, '{"error":{"message":"no key"}}'
        )
        self.assertEqual(code, "auth")
        code, _, _ = gemini_client.classify_gemini_http_error(
            403, '{"error":{"message":"denied"}}'
        )
        self.assertEqual(code, "auth")

    def test_other_status_is_unknown(self) -> None:
        code, _, message = gemini_client.classify_gemini_http_error(
            500, '{"error":{"message":"boom"}}'
        )
        self.assertEqual(code, "unknown")
        self.assertEqual(message, "boom")

    def test_error_object_carries_fields(self) -> None:
        error = gemini_client.GeminiError(
            "daily",
            code="quota",
            http_status=429,
            retry_after=17,
        )
        self.assertEqual(error.code, "quota")
        self.assertEqual(error.http_status, 429)
        self.assertEqual(error.retry_after, 17.0)
        self.assertTrue(app.is_gemini_quota_error(error))

    def test_captured_429_reads_quota_id_from_violations(self) -> None:
        payload = gemini_client._parse_error_payload(CAPTURED_FREE_TIER_PER_DAY_BODY)
        quota_ids = gemini_client._collect_quota_ids(payload)
        self.assertIn(
            "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
            quota_ids,
        )
        code, retry_after, message = gemini_client.classify_gemini_http_error(
            429, CAPTURED_FREE_TIER_PER_DAY_BODY
        )
        self.assertEqual(code, "quota")
        self.assertEqual(retry_after, 21.0)
        self.assertIn("exceeded your current quota", message)


if __name__ == "__main__":
    unittest.main()
