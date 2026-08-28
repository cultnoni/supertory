"""Business rules for translation job creation and range settings."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Protocol

from services.translation_preparation_service import serialize_scene_context


CULTURE_LOCALIZATION_LEVELS = ("tight", "moderate", "as_is")
TRANSLATION_DEFAULT_SAMPLE_CHAPTERS = 3
TRANSLATION_SEGMENT_CONFIRM_THRESHOLD = 500
TRANSLATION_RANGE_REQUIRED_MESSAGE = (
    "번역할 회차 범위를 선택해 주세요. 전체를 번역할 때는 "
    "「전체 회차 번역」을 명시해야 해요."
)


class TranslationJobRepositoryContract(Protocol):
    def project_exists(self, project_id: int) -> bool: ...
    def create_job(
        self,
        project_id: int,
        target_language: str,
        culture_localization_level: str,
        start_chapter: int,
        end_chapter: int,
        translate_all_chapters: bool,
        *,
        style_guide_json: str | None = None,
    ) -> dict: ...
    def get_job(self, job_id: int) -> dict | None: ...
    def get_job_for_project(self, project_id: int) -> dict | None: ...
    def list_jobs_for_project(self, project_id: int) -> list[dict]: ...
    def update_job_settings(self, job_id: int, values: dict) -> dict: ...
    def update_job_status(self, job_id: int, status: str) -> dict: ...
    def delete_segments_outside_range(
        self, job_id: int, start_chapter: int, end_chapter: int
    ) -> int: ...
    def seed_segments_for_chapters(
        self, job_id: int, chapter_numbers: list[int]
    ) -> int: ...
    def manuscript_episode_catalog(self, project_id: int) -> list[dict]: ...
class TranslationPreparationRepositoryContract(Protocol):
    def delete_scene_contexts_outside_range(
        self, job_id: int, start_chapter: int, end_chapter: int
    ) -> int: ...
    def get_scene_contexts(self, job_id: int) -> list[dict]: ...
    def scene_context_chapter_numbers(self, job_id: int) -> set[int]: ...


class TranslationSegmentRepositoryContract(Protocol):
    def translation_progress(self, job_id: int) -> dict: ...
    def chapter_segment_counts(self, job_id: int) -> dict[int, int]: ...
    def existing_chapter_numbers(self, job_id: int) -> set[int]: ...
    def segment_deletion_stats(
        self, job_id: int, chapter_numbers: list[int]
    ) -> tuple[int, int]: ...


class TranslationExtrasRepositoryContract(Protocol):
    def get_all_qa_history(self, job_id: int) -> list[dict]: ...
    def get_submission_package(self, job_id: int) -> dict | None: ...


class TranslationSettingsConfirmRequired(ValueError):
    def __init__(self, preview: dict):
        removed = len(preview.get("removed_chapters") or [])
        deleted = int(preview.get("deleted_segments") or 0)
        super().__init__(f"{removed}개 회차, {deleted}개 문단이 삭제됩니다. 계속할까요?")
        self.preview = preview


def _payload_flag(value: object) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, (int, float)):
        return int(value) != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _optional_int(value: object, *, field_name: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} 값이 올바르지 않습니다.") from error


def _row_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _json_load_optional(raw: object) -> object | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def serialize_translation_job(
    row: dict,
    *,
    pipeline_wait_seconds: int = 0,
) -> dict:
    data = dict(row)
    status = data.get("status") or "draft"
    confirmed = data.get("proper_nouns_confirmed")
    if confirmed is None:
        proper_nouns_confirmed = status not in ("draft", "awaiting_review")
    else:
        proper_nouns_confirmed = bool(int(confirmed or 0))
    return {
        "id": int(data["id"]),
        "local_project_id": int(data["local_project_id"]),
        "target_language": data.get("target_language") or "en",
        "cliffhanger_chapter": data.get("cliffhanger_chapter"),
        "start_chapter": _row_int(data.get("start_chapter")),
        "end_chapter": _row_int(data.get("end_chapter")),
        "translate_all_chapters": bool(
            int(data.get("translate_all_chapters") or 0)
        ),
        "style_guide_json": _json_load_optional(data.get("style_guide_json")),
        "culture_localization_level": data.get("culture_localization_level"),
        "status": status,
        "narrative_formatting_rules": _json_load_optional(
            data.get("narrative_formatting_rules_json")
        ),
        "pipeline_failed_step": data.get("pipeline_failed_step") or "",
        "pipeline_error": data.get("pipeline_error") or "",
        "pipeline_wait_seconds": max(0, int(pipeline_wait_seconds or 0)),
        "proper_nouns_confirmed": proper_nouns_confirmed,
        "proper_nouns_extracted": bool(
            int(data.get("proper_nouns_extracted") or 0)
        ),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


def serialize_translation_chat_message(row: dict) -> dict:
    data = dict(row)
    dragged = data.get("dragged_text") or data.get("quoted_text") or ""
    return {
        "id": int(data["id"]),
        "translation_job_id": int(data["translation_job_id"]),
        "segment_id": data.get("segment_id"),
        "dragged_text": dragged,
        "quoted_text": dragged,
        "role": data.get("role") or "user",
        "message": data.get("message") or "",
        "created_at": data.get("created_at"),
    }


def serialize_translation_submission_package(row: dict) -> dict:
    data = dict(row)
    return {
        "id": int(data["id"]),
        "translation_job_id": int(data["translation_job_id"]),
        "synopsis_translated": data.get("synopsis_translated") or "",
        "logline_translated": data.get("logline_translated") or "",
        "sample_chapters_range": data.get("sample_chapters_range") or "",
        "generated_at": data.get("generated_at"),
    }


class TranslationJobService:
    def __init__(
        self,
        repository: TranslationJobRepositoryContract,
        preparation_repository: TranslationPreparationRepositoryContract,
        segment_repository: TranslationSegmentRepositoryContract,
        extras_repository: TranslationExtrasRepositoryContract,
        *,
        pipeline_wait_provider: Callable[[int], int] | None = None,
    ) -> None:
        self.repository = repository
        self.preparation_repository = preparation_repository
        self.segment_repository = segment_repository
        self.extras_repository = extras_repository
        self.pipeline_wait_provider = pipeline_wait_provider or (lambda _job_id: 0)

    def list_jobs_for_project(self, project_id: int) -> list[dict]:
        self._require_project(project_id)
        return [
            self._serialize(row)
            for row in self.repository.list_jobs_for_project(int(project_id))
        ]

    def get_job(self, job_id: int) -> dict:
        job = self.get_job_summary(job_id)
        job["chat_messages"] = [
            serialize_translation_chat_message(item)
            for item in self.extras_repository.get_all_qa_history(int(job_id))
        ]
        job["scene_contexts"] = [
            serialize_scene_context(item)
            for item in self.preparation_repository.get_scene_contexts(int(job_id))
        ]
        package = self.extras_repository.get_submission_package(int(job_id))
        if package:
            job["submission_package"] = serialize_translation_submission_package(
                package
            )
        return job

    def get_job_summary(self, job_id: int) -> dict:
        return self._decorate_job(self._require_job(job_id))

    def get_job_for_project(self, project_id: int) -> dict | None:
        self._require_project(project_id)
        row = self.repository.get_job_for_project(int(project_id))
        return self._decorate_job(row) if row else None

    def preview_create(self, project_id: int, payload: dict | None = None) -> dict:
        self._require_project(project_id)
        catalog = self.repository.manuscript_episode_catalog(int(project_id))
        episode_count = len(catalog)
        recommended_end = (
            min(TRANSLATION_DEFAULT_SAMPLE_CHAPTERS, episode_count)
            if episode_count else 0
        )
        data = payload if isinstance(payload, dict) else {}
        translate_all = _payload_flag(data.get("translate_all_chapters"))
        has_start = data.get("start_chapter") not in (None, "")
        has_end = data.get("end_chapter") not in (None, "")
        if episode_count < 1:
            start = end = None
            estimated = 0
            translate_all = False
        elif translate_all or (has_start and has_end):
            start, end, translate_all = self._resolve_range(data, episode_count)
            estimated = self._estimate_segments(catalog, start, end)
        else:
            start = 1
            end = recommended_end
            translate_all = False
            estimated = self._estimate_segments(catalog, start, end)
        return {
            "episode_count": episode_count,
            "episodes": catalog,
            "recommended_start_chapter": 1 if episode_count else None,
            "recommended_end_chapter": recommended_end or None,
            "start_chapter": start,
            "end_chapter": end,
            "translate_all_chapters": translate_all,
            "estimated_segments": estimated,
            "confirm_threshold": TRANSLATION_SEGMENT_CONFIRM_THRESHOLD,
        }

    def create_job(self, project_id: int, payload: dict | None = None) -> dict:
        self._require_project(project_id)
        data = payload if isinstance(payload, dict) else {}
        target_language = str(data.get("target_language") or "en").strip() or "en"
        culture = self._resolve_culture(
            data.get("culture_localization_level") or "moderate"
        )
        catalog = self.repository.manuscript_episode_catalog(int(project_id))
        start, end, translate_all = self._resolve_range(data, len(catalog))
        style_guide = data.get("style_guide_json")
        if isinstance(style_guide, (dict, list)):
            style_raw = json.dumps(style_guide, ensure_ascii=False)
        else:
            style_raw = str(style_guide).strip() if style_guide else None
        row = self.repository.create_job(
            int(project_id),
            target_language,
            culture,
            start,
            end,
            translate_all,
            style_guide_json=style_raw,
        )
        job_id = int(row["id"])
        seeded = self.repository.seed_segments_for_chapters(
            job_id,
            list(range(start, end + 1)),
        )
        job = self._decorate_job(self._require_job(job_id))
        job["seeded_segments"] = seeded
        job["estimated_segments"] = self._estimate_segments(catalog, start, end)
        job["confirm_threshold"] = TRANSLATION_SEGMENT_CONFIRM_THRESHOLD
        return job

    def preview_settings(self, job_id: int, payload: dict | None = None) -> dict:
        row = self._require_job(job_id)
        job = self._serialize(row)
        project_id = int(job["local_project_id"])
        catalog = self.repository.manuscript_episode_catalog(project_id)
        data = payload if isinstance(payload, dict) else {}
        start, end, translate_all = self._resolve_range(data, len(catalog))
        culture = self._resolve_culture(
            data.get("culture_localization_level")
            or job.get("culture_localization_level")
            or "moderate"
        )
        old_start = int(job.get("start_chapter") or 1)
        old_end = int(job.get("end_chapter") or old_start)
        old_all = bool(job.get("translate_all_chapters"))
        if old_all and catalog:
            old_start, old_end = 1, len(catalog)
        new_range = set(range(start, end + 1))
        old_range = set(range(old_start, old_end + 1))
        added = sorted(new_range - old_range)
        leftover = self.segment_repository.existing_chapter_numbers(int(job_id))
        leftover.update(
            self.preparation_repository.scene_context_chapter_numbers(int(job_id))
        )
        removed = sorted((old_range | leftover) - new_range)
        deleted_segments, translated_to_delete = (
            self.segment_repository.segment_deletion_stats(int(job_id), removed)
        )
        return {
            "start_chapter": start,
            "end_chapter": end,
            "translate_all_chapters": translate_all,
            "culture_localization_level": culture,
            "culture_changed": (
                culture != str(job.get("culture_localization_level") or "")
            ),
            "range_changed": (
                (start, end, translate_all) != (old_start, old_end, old_all)
            ),
            "added_chapters": added,
            "removed_chapters": removed,
            "deleted_segments": deleted_segments,
            "translated_segments_to_delete": translated_to_delete,
            "estimated_added_segments": self._estimate_segments(
                catalog,
                min(added) if added else 1,
                max(added) if added else 0,
                chapters=set(added),
            ),
            "confirm_threshold": TRANSLATION_SEGMENT_CONFIRM_THRESHOLD,
            "confirm_delete_required": translated_to_delete > 0,
            "episodes": catalog,
            "current_start_chapter": old_start,
            "current_end_chapter": old_end,
            "current_translate_all_chapters": old_all,
            "current_culture_localization_level": job.get(
                "culture_localization_level"
            ),
        }

    def update_settings(self, job_id: int, payload: dict | None = None) -> dict:
        data = payload if isinstance(payload, dict) else {}
        preview = self.preview_settings(job_id, data)
        if (
            preview["confirm_delete_required"]
            and not _payload_flag(data.get("confirm_delete"))
        ):
            raise TranslationSettingsConfirmRequired(preview)
        seeded = 0
        if preview["removed_chapters"]:
            self.repository.delete_segments_outside_range(
                int(job_id),
                int(preview["start_chapter"]),
                int(preview["end_chapter"]),
            )
            self.preparation_repository.delete_scene_contexts_outside_range(
                int(job_id),
                int(preview["start_chapter"]),
                int(preview["end_chapter"]),
            )
        if preview["added_chapters"]:
            seeded = self.repository.seed_segments_for_chapters(
                int(job_id),
                [int(number) for number in preview["added_chapters"]],
            )
        self.repository.update_job_settings(int(job_id), {
            "start_chapter": int(preview["start_chapter"]),
            "end_chapter": int(preview["end_chapter"]),
            "translate_all_chapters": (
                1 if preview["translate_all_chapters"] else 0
            ),
            "cliffhanger_chapter": int(preview["end_chapter"]),
            "culture_localization_level": preview["culture_localization_level"],
        })
        self._sync_status_from_progress(int(job_id))
        detail = self.get_job(int(job_id))
        detail["seeded_segments"] = seeded
        detail["deleted_segments"] = int(preview["deleted_segments"] or 0)
        detail["settings_preview"] = preview
        return detail

    def update_culture(self, job_id: int, level: object) -> dict:
        """Change only culture guidance; existing translations stay untouched."""
        culture = self._resolve_culture(level)
        self.repository.update_job_settings(
            int(job_id),
            {"culture_localization_level": culture},
        )
        return self.get_job(int(job_id))

    def _sync_status_from_progress(self, job_id: int) -> None:
        progress = self.segment_repository.translation_progress(int(job_id))
        row = self._require_job(job_id)
        status = str(row.get("status") or "draft")
        pending = int(progress["pending_segments"] or 0)
        translated = int(progress["translated_segments"] or 0)
        if pending > 0 and status in {"translated", "completed"}:
            self.repository.update_job_status(int(job_id), "in_progress")
        elif pending == 0 and translated > 0 and status == "in_progress":
            self.repository.update_job_status(int(job_id), "translated")

    def _decorate_job(self, row: dict) -> dict:
        job = self._serialize(row)
        job.update(self.segment_repository.translation_progress(int(job["id"])))
        job["chapters"] = self._chapter_catalog(
            int(job["id"]),
            int(job["local_project_id"]),
            job,
        )
        return job

    def _chapter_catalog(
        self,
        job_id: int,
        project_id: int,
        job: dict,
    ) -> list[dict]:
        scenes = self.repository.manuscript_episode_catalog(int(project_id))
        start = int(job.get("start_chapter") or 1)
        end = int(job.get("end_chapter") or start)
        if job.get("translate_all_chapters"):
            start, end = 1, len(scenes)
        counts = self.segment_repository.chapter_segment_counts(int(job_id))
        return [
            {
                "number": int(item["number"]),
                "title": item["title"],
                "segment_count": int(counts.get(int(item["number"])) or 0),
            }
            for item in scenes
            if start <= int(item["number"]) <= end
        ]

    def _serialize(self, row: dict) -> dict:
        job_id = int(row["id"])
        return serialize_translation_job(
            row,
            pipeline_wait_seconds=self.pipeline_wait_provider(job_id),
        )

    def _require_job(self, job_id: int) -> dict:
        row = self.repository.get_job(int(job_id))
        if row is None:
            raise LookupError("번역 작업을 찾을 수 없습니다.")
        return row

    def _require_project(self, project_id: int) -> None:
        if not self.repository.project_exists(int(project_id)):
            raise LookupError("작품을 찾을 수 없습니다.")

    @staticmethod
    def _resolve_culture(value: object) -> str:
        culture = str(value or "").strip()
        if culture not in CULTURE_LOCALIZATION_LEVELS:
            raise ValueError("문화반영범위 값이 올바르지 않습니다.")
        return culture

    @staticmethod
    def _resolve_range(
        payload: dict | None,
        episode_count: int,
    ) -> tuple[int, int, bool]:
        data = payload if isinstance(payload, dict) else {}
        if episode_count < 1:
            raise ValueError("번역할 회차가 없어요.")
        translate_all = _payload_flag(data.get("translate_all_chapters"))
        start = _optional_int(data.get("start_chapter"), field_name="시작 회차")
        end = _optional_int(data.get("end_chapter"), field_name="끝 회차")
        if not translate_all and (start is None or end is None):
            raise ValueError(TRANSLATION_RANGE_REQUIRED_MESSAGE)
        if translate_all:
            return 1, episode_count, True
        assert start is not None and end is not None
        if start < 1 or end < 1 or start > episode_count or end > episode_count:
            raise ValueError(
                f"회차 범위는 1화부터 {episode_count}화 사이여야 해요."
            )
        if start > end:
            raise ValueError("시작 회차가 끝 회차보다 클 수 없어요.")
        return start, end, False

    @staticmethod
    def _estimate_segments(
        catalog: list[dict],
        start: int,
        end: int,
        *,
        chapters: set[int] | None = None,
    ) -> int:
        if end < start:
            return 0
        return sum(
            int(item.get("segment_count") or 0)
            for item in catalog
            if (
                int(item.get("number") or 0) in chapters
                if chapters is not None
                else start <= int(item.get("number") or 0) <= end
            )
        )
