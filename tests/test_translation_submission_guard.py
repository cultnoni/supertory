"""Guards for the translation submission package (투고 패키지)."""

from __future__ import annotations

import unittest

from services.translation_extras_service import TranslationExtrasService


class _FakeRepository:
    def __init__(self, synopsis: str) -> None:
        self.synopsis = synopsis

    def get_job(self, job_id: int) -> dict:
        return {"id": job_id, "local_project_id": 1, "target_language": "en"}

    def get_submission_package(self, job_id: int) -> None:
        return None

    def get_completed_segments(self, job_id: int) -> list:
        return []

    def get_project_synopsis(self, project_id: int) -> str:
        return self.synopsis


class SubmissionPackageGuardTests(unittest.TestCase):
    def test_empty_synopsis_is_rejected_before_calling_the_model(self) -> None:
        calls: list[str] = []

        def fake_generate(prompt: str, **_kwargs: object) -> str:
            calls.append(prompt)
            return "{}"

        service = TranslationExtrasService(
            _FakeRepository(synopsis="   "),
            preparation_repository=None,
            job_service=None,
            gemini_generate=fake_generate,
        )
        with self.assertRaises(ValueError):
            service.generate_submission_package(1)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
