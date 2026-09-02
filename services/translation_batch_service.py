"""First-pass batch translation, retries, and chapter polish."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from threading import Lock
from typing import Protocol

import gemini_client
import translation_prompts
from services.translation_preparation_service import (
    format_proper_noun_glossary,
    serialize_scene_context,
)


TRANSLATION_PARAGRAPH_BATCH_SIZE = 36
TRANSLATION_BATCH_STRUCTURE_ATTEMPTS = 3
PARAGRAPH_EMPTY_ATTEMPTS = 3
PARAGRAPH_FALLBACK_NOTE = (
    "자동번역 실패로 원문이 유지되었습니다. 수동 확인이 필요합니다"
)
CHAPTER_POLISH_RESPONSE_ATTEMPTS = 3
CHAPTER_POLISH_OUTPUT_BATCH_SIZE = 40
TRANSLATION_GEMINI_GAP_SECONDS = 4.5
TRANSLATION_GEMINI_TIMEOUT_SECONDS = 90.0
TRANSLATION_RATE_LIMIT_RETRIES = 5
TRANSLATION_RATE_LIMIT_DEFAULT_WAIT = 10.0
TRANSLATION_SEPARATOR_PARAGRAPH_RE = re.compile(r"^[=*~—\-]{2,}$")

_wait_lock = Lock()
_wait_until: dict[int, float] = {}


def is_translation_separator_paragraph(text: str) -> bool:
    return bool(
        TRANSLATION_SEPARATOR_PARAGRAPH_RE.fullmatch(str(text or "").strip())
    )


def _translation_sleep(seconds: float) -> None:
    delay = float(seconds or 0)
    if delay > 0:
        time.sleep(delay)


def is_translation_rate_limit_error(error: BaseException) -> bool:
    if isinstance(error, gemini_client.GeminiError):
        if int(error.http_status or 0) == 429:
            return True
        return str(error.code or "") in {"rate_limit", "quota"}
    return False


def translation_rate_limit_wait_seconds(error: BaseException) -> float:
    retry_after = getattr(error, "retry_after", None)
    try:
        if retry_after is not None and float(retry_after) >= 0:
            return float(retry_after)
    except (TypeError, ValueError):
        pass
    match = re.search(r"retry in\s+([\d.]+)\s*s", str(error or ""), re.I)
    if match:
        try:
            return float(match.group(1))
        except (TypeError, ValueError):
            pass
    return float(TRANSLATION_RATE_LIMIT_DEFAULT_WAIT)


def set_translation_pipeline_wait(job_id: int | None, seconds: float) -> None:
    if job_id is None:
        return
    with _wait_lock:
        _wait_until[int(job_id)] = time.monotonic() + max(
            0.0, float(seconds or 0)
        )


def clear_translation_pipeline_wait(job_id: int | None) -> None:
    if job_id is None:
        return
    with _wait_lock:
        _wait_until.pop(int(job_id), None)


def translation_pipeline_wait_seconds(job_id: int | None) -> float:
    if job_id is None:
        return 0.0
    with _wait_lock:
        until = _wait_until.get(int(job_id))
    if until is None:
        return 0.0
    return max(0.0, until - time.monotonic())


def generate_translation_text(
    prompt: str,
    *,
    temperature: float,
    max_output_tokens: int,
    job_id: int | None = None,
    sleep_provider: Callable[[float], None] | None = None,
) -> str:
    if not gemini_client.is_configured():
        raise ValueError(
            "Gemini API 키가 없습니다. .env 에 GEMINI_API_KEY 를 넣어 주세요."
        )
    rate_limit_tries = 0
    while True:
        try:
            return gemini_client.generate_text(
                prompt,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                timeout=TRANSLATION_GEMINI_TIMEOUT_SECONDS,
            )
        except gemini_client.GeminiError as error:
            visible = gemini_client.user_visible_message(error)
            if not is_translation_rate_limit_error(error):
                raise ValueError(visible) from error
            if rate_limit_tries >= TRANSLATION_RATE_LIMIT_RETRIES:
                raise ValueError(visible) from error
            rate_limit_tries += 1
            wait = translation_rate_limit_wait_seconds(error)
            print(
                f"[translation-rate-limit] job={job_id} wait={wait:.1f}s "
                f"attempt={rate_limit_tries}/{TRANSLATION_RATE_LIMIT_RETRIES} "
                f"code={error.code}",
                flush=True,
            )
            if wait > 0:
                set_translation_pipeline_wait(job_id, wait)
            try:
                (sleep_provider or _translation_sleep)(wait)
            finally:
                clear_translation_pipeline_wait(job_id)


class SegmentRepositoryContract(Protocol):
    def get_pending_segments(self, job_id: int, limit: int) -> list[dict]: ...
    def save_translated_batch(
        self, job_id: int, segment_results: list[dict]
    ) -> int: ...
    def save_translated_segment(
        self,
        job_id: int,
        segment_id: int,
        translated_text: str,
        notes: object,
        *,
        needs_manual_review: bool,
    ) -> dict: ...
    def save_separator_passthrough(self, segment_id: int) -> dict: ...
    def get_segment(self, segment_id: int) -> dict | None: ...
    def list_segments(
        self, job_id: int, chapter_number: int | None = None
    ) -> list[dict]: ...
    def get_segments_for_chapter(
        self, job_id: int, chapter_number: int
    ) -> list[dict]: ...
    def set_segment_approval(self, segment_id: int, approved: bool) -> dict: ...
    def get_approved_segments_for_chapter(
        self, job_id: int, chapter_number: int
    ) -> list[dict]: ...
    def save_polish_suggestions(
        self, job_id: int, chapter_number: int, suggestions: list[dict]
    ) -> None: ...
    def apply_polish_selection(
        self, segment_id: int, use_polished: bool, edited_text: str | None
    ) -> dict: ...
    def apply_all_chapter_polish(
        self, job_id: int, chapter_number: int
    ) -> int: ...
    def previous_translated_context(
        self,
        job_id: int,
        *,
        before_chapter_number: int,
        before_segment_order: int,
        limit: int = 3,
    ) -> str: ...
    def commit(self) -> None: ...


class JobRepositoryContract(Protocol):
    def get_job(self, job_id: int) -> dict | None: ...
    def update_job_status(self, job_id: int, status: str) -> dict: ...


class JobServiceContract(Protocol):
    def get_job(self, job_id: int) -> dict: ...
    def get_job_summary(self, job_id: int) -> dict: ...


class PreparationRepositoryContract(Protocol):
    def get_proper_nouns(self, job_id: int) -> list[dict]: ...
    def get_scene_context(self, scene_context_id: int) -> dict | None: ...
    def get_scene_contexts(self, job_id: int) -> list[dict]: ...
    def record_pipeline_failure(
        self, job_id: int, step: str, message: str
    ) -> None: ...
    def clear_pipeline_failure(self, job_id: int) -> None: ...
    def commit(self) -> None: ...


def serialize_translation_segment(row: dict) -> dict:
    data = dict(row)
    return {
        "id": int(data["id"]),
        "translation_job_id": int(data["translation_job_id"]),
        "scene_context_id": data.get("scene_context_id"),
        "chapter_number": int(data["chapter_number"]),
        "segment_order": int(data["segment_order"]),
        "source_text": data.get("source_text") or "",
        "translated_text": data.get("translated_text") or "",
        "translation_notes_json": _json_load_optional(
            data.get("translation_notes_json")
        ),
        "polished_text": data.get("polish_text")
        or data.get("polished_text")
        or "",
        "polish_proposal_text": data.get("polish_proposal_text") or "",
        "polish_choice": str(data.get("polish_choice") or "").strip(),
        "is_approved": bool(int(data.get("is_approved") or 0)),
        "needs_manual_review": bool(int(data.get("needs_manual_review") or 0)),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


def chapter_polish_state(rows: list[dict]) -> dict:
    count = len(rows)
    approved = sum(1 for row in rows if int(row.get("is_approved") or 0))
    translated = sum(
        1 for row in rows if str(row.get("translated_text") or "").strip()
    )
    proposed = sum(
        1 for row in rows if str(row.get("polish_proposal_text") or "").strip()
    )
    decided = sum(
        1 for row in rows if str(row.get("polish_choice") or "").strip()
    )
    return {
        "chapter_segment_count": count,
        "chapter_approved_count": approved,
        "chapter_polish_ready": count > 0
        and approved == count
        and translated == count,
        "chapter_polish_proposed": count > 0 and proposed == count,
        "chapter_polish_decided_count": decided,
    }


class TranslationBatchService:
    def __init__(
        self,
        repository: SegmentRepositoryContract,
        job_repository: JobRepositoryContract,
        job_service: JobServiceContract,
        preparation_repository: PreparationRepositoryContract,
        *,
        gemini_generate: Callable[..., str] = generate_translation_text,
        sleep_provider: Callable[[float], None] = _translation_sleep,
        empty_retry_delay_provider: Callable[[], float] | None = None,
        debug_log_path: Path | None = None,
    ) -> None:
        self.repository = repository
        self.job_repository = job_repository
        self.job_service = job_service
        self.preparation_repository = preparation_repository
        self.gemini_generate = gemini_generate
        self.sleep_provider = sleep_provider
        self.empty_retry_delay_provider = (
            empty_retry_delay_provider or (lambda: 0.5)
        )
        self.debug_log_path = debug_log_path

    def run_batch_translation(self, job_id: int) -> dict:
        job = self._require_job(job_id)
        if not job.get("proper_nouns_confirmed"):
            raise ValueError(
                "고유명사를 먼저 확정해야 번역을 진행할 수 있어요."
            )
        rules = job.get("narrative_formatting_rules")
        if not rules:
            raise ValueError("번역 준비를 먼저 실행해 주세요.")
        style = _style_guide_mapping(job.get("style_guide_json"))
        glossary = self._confirmed_glossary(int(job_id))
        formatting = _format_narrative_rules_for_prompt(rules)
        segments = self.repository.list_segments(int(job_id))
        pending = sum(
            1 for row in segments
            if not str(row.get("translated_text") or "").strip()
        )
        print(
            f"[translation-proceed] job={int(job_id)} "
            f"total_segments={len(segments)} pending={pending} "
            f"already_translated={len(segments) - pending} "
            f"start_chapter={job.get('start_chapter')} "
            f"end_chapter={job.get('end_chapter')} "
            f"translate_all_chapters="
            f"{int(bool(job.get('translate_all_chapters')))}",
            flush=True,
        )
        translated_count = 0
        skipped_count = 0
        completed_batches = 0
        try:
            for row in segments:
                if is_translation_separator_paragraph(row.get("source_text") or ""):
                    self.repository.save_separator_passthrough(int(row["id"]))
                    self.repository.commit()
                    skipped_count += 1
                elif str(row.get("translated_text") or "").strip():
                    skipped_count += 1
            while True:
                batch = self.repository.get_pending_segments(
                    int(job_id),
                    TRANSLATION_PARAGRAPH_BATCH_SIZE,
                )
                if not batch:
                    break
                if completed_batches > 0:
                    self.sleep_provider(TRANSLATION_GEMINI_GAP_SECONDS)
                count, successful_batches = self._translate_batch_recursive(
                    batch,
                    job,
                    style=style,
                    glossary=glossary,
                    formatting=formatting,
                )
                translated_count += count
                completed_batches += max(1, successful_batches)
        except Exception as error:
            clear_translation_pipeline_wait(job_id)
            self.preparation_repository.record_pipeline_failure(
                int(job_id),
                "paragraph_translation",
                str(error).strip() or "paragraph_translation",
            )
            self.preparation_repository.commit()
            raise
        clear_translation_pipeline_wait(job_id)
        self.preparation_repository.clear_pipeline_failure(int(job_id))
        self.job_repository.update_job_status(int(job_id), "translated")
        self.repository.commit()
        detail = self.job_service.get_job(int(job_id))
        detail["segments"] = [
            serialize_translation_segment(row)
            for row in self.repository.list_segments(int(job_id))
        ]
        detail["translated_count"] = translated_count
        detail["skipped_segments"] = skipped_count
        return detail

    def retranslate_segment(self, segment_id: int) -> dict:
        row = self.repository.get_segment(int(segment_id))
        if row is None:
            raise LookupError("번역 문단을 찾을 수 없습니다.")
        if is_translation_separator_paragraph(row.get("source_text") or ""):
            updated = self.repository.save_separator_passthrough(int(segment_id))
            self.repository.commit()
            return serialize_translation_segment(updated)
        job = self._require_job(int(row["translation_job_id"]))
        if not job.get("proper_nouns_confirmed"):
            raise ValueError(
                "고유명사를 먼저 확정해야 번역을 진행할 수 있어요."
            )
        rules = job.get("narrative_formatting_rules")
        if not rules:
            raise ValueError("번역 준비를 먼저 실행해 주세요.")
        self._translate_one(
            row,
            job,
            style=_style_guide_mapping(job.get("style_guide_json")),
            glossary=self._confirmed_glossary(int(job["id"])),
            formatting=_format_narrative_rules_for_prompt(rules),
        )
        return serialize_translation_segment(
            self.repository.get_segment(int(segment_id)) or {}
        )

    def run_chapter_polish(
        self,
        job_id: int,
        chapter_number: int,
    ) -> dict:
        job = self._require_job(job_id)
        rows = self.repository.get_segments_for_chapter(
            int(job_id), int(chapter_number)
        )
        if not rows:
            raise LookupError("윤문할 회차를 찾을 수 없습니다.")
        approved = self.repository.get_approved_segments_for_chapter(
            int(job_id), int(chapter_number)
        )
        if (
            len(approved) != len(rows)
            or any(
                not str(row.get("translated_text") or "").strip()
                for row in approved
            )
        ):
            raise ValueError(
                "1차 번역의 모든 문단을 승인한 뒤 윤문할 수 있어요."
            )
        paragraphs = [
            str(row.get("translated_text") or "").strip() for row in rows
        ]
        settings = _style_guide_mapping(job.get("style_guide_json"))
        proposals: list[dict] = []
        for batch_start in range(
            1, len(rows) + 1, CHAPTER_POLISH_OUTPUT_BATCH_SIZE
        ):
            batch_end = min(
                len(rows),
                batch_start + CHAPTER_POLISH_OUTPUT_BATCH_SIZE - 1,
            )
            expected_indices = list(range(batch_start, batch_end + 1))
            prompt = translation_prompts.build_chapter_polish_prompt(
                paragraphs,
                settings,
                target_start=batch_start,
                target_end=batch_end,
                target_language=job.get("target_language") or "en",
            )
            batch_proposals: list[dict] = []
            for attempt in range(1, CHAPTER_POLISH_RESPONSE_ATTEMPTS + 1):
                raw = self.gemini_generate(
                    prompt,
                    temperature=0.35,
                    max_output_tokens=8192,
                    job_id=int(job_id),
                )
                batch_proposals = _parse_chapter_polish_output(raw)
                indices = [int(item["index"]) for item in batch_proposals]
                separators_match = (
                    len(batch_proposals) == len(expected_indices)
                    and all(
                        not is_translation_separator_paragraph(
                            paragraphs[index - 1]
                        )
                        or batch_proposals[offset]["polished_text"]
                        == paragraphs[index - 1]
                        for offset, index in enumerate(expected_indices)
                    )
                )
                if indices == expected_indices and separators_match:
                    break
                batch_proposals = []
                if attempt < CHAPTER_POLISH_RESPONSE_ATTEMPTS:
                    prompt += (
                        "\n\n[재시도 지시]\n"
                        "직전 응답 형식이 잘못되었습니다. 이번 paragraphs는 "
                        f"정확히 {len(expected_indices)}개이며 index는 "
                        f"{batch_start}부터 {batch_end}까지 빠짐없이 "
                        "순서대로 반환하세요."
                    )
            if not batch_proposals:
                raise ValueError(
                    "윤문 응답의 문단 개수 또는 순서가 맞지 않습니다 "
                    f"(index {batch_start}~{batch_end}). "
                    f"{CHAPTER_POLISH_RESPONSE_ATTEMPTS}회 재시도했어요."
                )
            proposals.extend(batch_proposals)
        self.repository.save_polish_suggestions(
            int(job_id), int(chapter_number), proposals
        )
        self.repository.commit()
        return self.list_segments(int(job_id), int(chapter_number))

    def choose_polish(
        self,
        segment_id: int,
        payload: dict | None,
    ) -> dict:
        data = payload if isinstance(payload, dict) else {}
        choice = str(data.get("choice") or "").strip()
        if choice not in {"apply", "keep"}:
            raise ValueError("윤문 선택 값이 올바르지 않습니다.")
        row = self.repository.get_segment(int(segment_id))
        if row is None:
            raise LookupError("번역 문단을 찾을 수 없습니다.")
        if not int(row.get("is_approved") or 0):
            raise ValueError("승인된 1차 번역만 윤문을 선택할 수 있어요.")
        updated = self.repository.apply_polish_selection(
            int(segment_id),
            choice == "apply",
            str(data.get("polished_text") or "") if choice == "apply" else None,
        )
        self.repository.commit()
        return serialize_translation_segment(updated)

    def replace_translated_text(
        self,
        segment_id: int,
        payload: dict | None,
    ) -> dict:
        data = payload if isinstance(payload, dict) else {}
        text = str(
            data.get("translated_text") or data.get("text") or ""
        ).strip()
        if not text:
            raise ValueError("바꿀 번역문을 입력해 주세요.")
        row = self.repository.get_segment(int(segment_id))
        if row is None:
            raise LookupError("번역 문단을 찾을 수 없습니다.")
        updated = self.repository.save_translated_segment(
            int(row["translation_job_id"]),
            int(segment_id),
            text,
            row.get("translation_notes_json") or [],
            needs_manual_review=False,
        )
        self.repository.commit()
        return serialize_translation_segment(updated)

    def apply_all_polish(
        self,
        job_id: int,
        chapter_number: int,
    ) -> dict:
        rows = self.repository.get_segments_for_chapter(
            int(job_id), int(chapter_number)
        )
        if not rows:
            raise LookupError("윤문할 회차를 찾을 수 없습니다.")
        if any(
            not int(row.get("is_approved") or 0)
            or not str(row.get("polish_proposal_text") or "").strip()
            for row in rows
        ):
            raise ValueError(
                "회차 전체 윤문 제안을 먼저 만든 뒤 적용해 주세요."
            )
        self.repository.apply_all_chapter_polish(
            int(job_id), int(chapter_number)
        )
        self.repository.commit()
        return self.list_segments(int(job_id), int(chapter_number))

    def approve_segment(
        self,
        segment_id: int,
        payload: dict | None,
    ) -> dict:
        data = payload if isinstance(payload, dict) else {}
        row = self.repository.get_segment(int(segment_id))
        if row is None:
            raise LookupError("번역 문단을 찾을 수 없습니다.")
        approved = (
            bool(data.get("is_approved"))
            if "is_approved" in data
            else not bool(int(row.get("is_approved") or 0))
        )
        updated = self.repository.set_segment_approval(
            int(segment_id), approved
        )
        self.repository.commit()
        return serialize_translation_segment(updated)

    def approve_chapter_segments(
        self,
        job_id: int,
        chapter_number: int,
    ) -> dict:
        counts = self.repository.approve_unapproved_chapter_segments(
            int(job_id), int(chapter_number)
        )
        self.repository.commit()
        payload = self.list_segments(int(job_id), int(chapter_number))
        payload.update(counts)
        return payload

    def list_segments(
        self,
        job_id: int,
        chapter_number: int | None,
    ) -> dict:
        job = self.job_service.get_job_summary(int(job_id))
        rows = self.repository.list_segments(int(job_id), chapter_number)
        scenes = [
            serialize_scene_context(row)
            for row in self.preparation_repository.get_scene_contexts(int(job_id))
        ]
        job["scene_contexts"] = scenes
        payload = {
            "job": job,
            "chapter": chapter_number,
            "chapters": job.get("chapters") or [],
            "segments": [serialize_translation_segment(row) for row in rows],
            "scene_contexts": scenes,
        }
        payload.update(chapter_polish_state(rows))
        return payload

    def _translate_batch_recursive(
        self,
        rows: list[dict],
        job: dict,
        *,
        style: dict,
        glossary: object,
        formatting: object,
        depth: int = 0,
    ) -> tuple[int, int]:
        if not rows:
            return 0, 0
        paragraphs, settings = self._batch_settings(
            rows,
            job,
            style=style,
            glossary=glossary,
            formatting=formatting,
        )
        expected_ids = [int(row["id"]) for row in rows]
        prompt = translation_prompts.build_paragraph_translation_batch_prompt(
            paragraphs,
            settings,
            target_language=job.get("target_language") or "en",
        )
        for attempt in range(1, TRANSLATION_BATCH_STRUCTURE_ATTEMPTS + 1):
            print(
                f"[translation-batch] job={int(job['id'])} "
                f"chapter={int(rows[0]['chapter_number'])} size={len(rows)} "
                f"first_id={expected_ids[0]} last_id={expected_ids[-1]} "
                f"attempt={attempt}/{TRANSLATION_BATCH_STRUCTURE_ATTEMPTS} "
                f"depth={depth}",
                flush=True,
            )
            raw = self.gemini_generate(
                prompt,
                temperature=0.4,
                max_output_tokens=8192,
                job_id=int(job["id"]),
            )
            translated_items = _parse_batch_output(raw)
            returned_ids = [int(item["id"]) for item in translated_items]
            if (
                len(translated_items) == len(rows)
                and returned_ids == expected_ids
            ):
                self.repository.save_translated_batch(
                    int(job["id"]), translated_items
                )
                self.repository.commit()
                by_id = {
                    int(item["id"]): item for item in translated_items
                }
                for row in rows:
                    if not str(
                        by_id[int(row["id"])].get("translated_text") or ""
                    ).strip():
                        self._translate_one(
                            row,
                            job,
                            style=style,
                            glossary=glossary,
                            formatting=formatting,
                        )
                return len(rows), 1
            if attempt < TRANSLATION_BATCH_STRUCTURE_ATTEMPTS:
                prompt += (
                    "\n\n[재시도 지시]\n"
                    "직전 응답의 id·개수·순서가 잘못되었습니다. 정확히 "
                    f"{len(rows)}개를 다음 id 순서 그대로 반환하세요: "
                    f"{expected_ids}"
                )
        if len(rows) == 1:
            self._translate_one(
                rows[0],
                job,
                style=style,
                glossary=glossary,
                formatting=formatting,
            )
            return 1, 0
        midpoint = len(rows) // 2
        print(
            f"[translation-batch-split] job={int(job['id'])} "
            f"chapter={int(rows[0]['chapter_number'])} size={len(rows)} "
            f"into={midpoint}+{len(rows) - midpoint}",
            flush=True,
        )
        left_count, left_batches = self._translate_batch_recursive(
            rows[:midpoint],
            job,
            style=style,
            glossary=glossary,
            formatting=formatting,
            depth=depth + 1,
        )
        self.sleep_provider(TRANSLATION_GEMINI_GAP_SECONDS)
        right_count, right_batches = self._translate_batch_recursive(
            rows[midpoint:],
            job,
            style=style,
            glossary=glossary,
            formatting=formatting,
            depth=depth + 1,
        )
        return left_count + right_count, left_batches + right_batches

    def _batch_settings(
        self,
        rows: list[dict],
        job: dict,
        *,
        style: dict,
        glossary: object,
        formatting: object,
    ) -> tuple[list[dict], dict]:
        first = rows[0]
        previous = self.repository.previous_translated_context(
            int(job["id"]),
            before_chapter_number=int(first["chapter_number"]),
            before_segment_order=int(first["segment_order"]),
        )
        paragraphs = []
        for row in rows:
            relationship, mood = self._scene_tags(row.get("scene_context_id"))
            paragraphs.append({
                "id": int(row["id"]),
                "source_text": str(row.get("source_text") or ""),
                "relationship_tag": relationship,
                "mood_tag": mood,
            })
        return paragraphs, {
            "target_language": job.get("target_language") or "en",
            "tense": style.get("tense") or "",
            "character_voices": style.get("character_voices")
            or style.get("voices")
            or "",
            "proper_nouns_confirmed": glossary,
            "culture_localization_level": job.get(
                "culture_localization_level"
            )
            or "moderate",
            "narrative_formatting_rules": formatting,
            "previous_context_summary": previous,
        }

    def _translate_one(
        self,
        row: dict,
        job: dict,
        *,
        style: dict,
        glossary: object,
        formatting: object,
    ) -> None:
        relationship, mood = self._scene_tags(row.get("scene_context_id"))
        previous = self.repository.previous_translated_context(
            int(job["id"]),
            before_chapter_number=int(row["chapter_number"]),
            before_segment_order=int(row["segment_order"]),
        )
        settings = {
            "target_language": job.get("target_language") or "en",
            "tense": style.get("tense") or "",
            "character_voices": style.get("character_voices")
            or style.get("voices")
            or "",
            "proper_nouns_confirmed": glossary,
            "culture_localization_level": job.get(
                "culture_localization_level"
            )
            or "moderate",
            "relationship_tag": relationship,
            "mood_tag": mood,
            "narrative_formatting_rules": formatting,
            "previous_context_summary": previous,
        }
        source = str(row.get("source_text") or "")
        prompt = translation_prompts.build_paragraph_translation_prompt(
            source,
            settings,
            target_language=job.get("target_language") or "en",
        )
        translated, notes, used_fallback = (
            self.translate_paragraph_with_retries(
                segment_id=int(row["id"]),
                source_text=source,
                prompt=prompt,
                job_id=int(job["id"]),
            )
        )
        self.repository.save_translated_segment(
            int(job["id"]),
            int(row["id"]),
            translated,
            notes,
            needs_manual_review=used_fallback,
        )
        self.repository.commit()

    def translate_paragraph_with_retries(
        self,
        *,
        segment_id: int,
        source_text: str,
        prompt: str,
        job_id: int | None = None,
    ) -> tuple[str, object, bool]:
        if is_translation_separator_paragraph(source_text):
            return str(source_text or "").strip(), [], False
        last_notes: object = []
        last_raw = ""
        for attempt in range(1, PARAGRAPH_EMPTY_ATTEMPTS + 1):
            raw = self.gemini_generate(
                prompt,
                temperature=0.4,
                max_output_tokens=4096,
                job_id=job_id,
            )
            last_raw = raw
            translated, notes = _parse_paragraph_output(raw)
            last_notes = notes
            if str(translated or "").strip():
                if attempt > 1:
                    self._debug_log(
                        segment_id,
                        source_text,
                        raw,
                        attempt=attempt,
                    )
                return translated, notes, False
            if attempt < PARAGRAPH_EMPTY_ATTEMPTS:
                self._debug_log(
                    segment_id,
                    source_text,
                    raw,
                    attempt=attempt,
                    retrying=True,
                )
                delay = self.empty_retry_delay_provider()
                if delay > 0:
                    self.sleep_provider(delay)
        notes = _translation_notes_as_list(last_notes)
        notes.append({
            "note": PARAGRAPH_FALLBACK_NOTE,
            "needs_manual_review": True,
        })
        self._debug_log(
            segment_id,
            source_text,
            last_raw,
            attempt=PARAGRAPH_EMPTY_ATTEMPTS,
            used_fallback=True,
        )
        return str(source_text or ""), notes, True

    def _confirmed_glossary(self, job_id: int) -> str:
        return format_proper_noun_glossary(
            self.preparation_repository.get_proper_nouns(int(job_id))
        )

    def _scene_tags(self, scene_context_id: object) -> tuple[str, str]:
        if not scene_context_id:
            return "", ""
        row = self.preparation_repository.get_scene_context(
            int(scene_context_id)
        )
        if row is None:
            return "", ""
        return (
            str(row.get("relationship_tag") or ""),
            str(row.get("mood_tag") or ""),
        )

    def _require_job(self, job_id: int) -> dict:
        row = self.job_repository.get_job(int(job_id))
        if row is None:
            raise LookupError("번역 작업을 찾을 수 없습니다.")
        return self.job_service.get_job_summary(int(job_id))

    def _debug_log(
        self,
        segment_id: int,
        source_text: str,
        raw: str,
        *,
        attempt: int,
        retrying: bool = False,
        used_fallback: bool = False,
    ) -> None:
        flags = []
        if retrying:
            flags.append("retrying=True")
        if used_fallback:
            flags.append("used_fallback=True")
        header = (
            f"[translation-paragraph-debug] segment={segment_id} "
            f"source_len={len(source_text)} attempt={attempt} "
            + " ".join(flags)
        )
        print(header, flush=True)
        if self.debug_log_path is None:
            return
        try:
            self.debug_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.debug_log_path.open("a", encoding="utf-8") as handle:
                handle.write(header + "\n")
                handle.write(str(raw or "") + "\n\n")
        except OSError:
            pass


def _extract_json_object(raw: str) -> dict:
    text = str(raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        loaded = json.loads(text)
        if isinstance(loaded, dict):
            return loaded
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start >= 0:
        loaded, _end = json.JSONDecoder().raw_decode(text, start)
        if isinstance(loaded, dict):
            return loaded
    raise ValueError("JSON 응답을 읽지 못했습니다.")


def _parse_batch_output(raw: str) -> list[dict]:
    try:
        parsed = _extract_json_object(raw)
    except (ValueError, json.JSONDecodeError, TypeError):
        return []
    items = parsed.get("paragraphs")
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        if not isinstance(item, dict):
            return []
        try:
            segment_id = int(item.get("id"))
        except (TypeError, ValueError):
            return []
        notes = item.get("translation_notes") or item.get("notes") or []
        if not isinstance(notes, (list, str)):
            notes = []
        result.append({
            "id": segment_id,
            "translated_text": str(item.get("translated_text") or "").strip(),
            "translation_notes": notes,
        })
    return result


def _parse_paragraph_output(raw: str) -> tuple[str, object]:
    text = str(raw or "").strip()
    parsed: dict = {}
    for candidate in _candidate_json_objects(text):
        if _translated_text_from_mapping(candidate):
            parsed = candidate
            break
        if not parsed:
            parsed = candidate
    translated = _translated_text_from_mapping(parsed)
    notes = parsed.get("translation_notes") or parsed.get("notes") or []
    if not translated:
        for key in _PARAGRAPH_TRANSLATION_TEXT_KEYS:
            translated = _json_string_field(text, key)
            if translated:
                break
    if not translated:
        prose = re.sub(
            r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE
        ).strip()
        if prose and prose[0] not in "{[":
            translated = prose
    if not translated and not parsed:
        raise ValueError(
            "문단 번역 결과를 읽지 못했어요. 다시 「번역 진행」을 눌러 주세요."
        )
    return translated, notes


_PARAGRAPH_TRANSLATION_TEXT_KEYS = (
    "translated_text",
    "translation",
    "translated",
    "text",
)


def _json_string_field(text: str, field: str) -> str:
    marker = f'"{field}"'
    start = str(text or "").find(marker)
    if start < 0:
        return ""
    colon = str(text).find(":", start + len(marker))
    if colon < 0:
        return ""
    rest = str(text)[colon + 1 :].lstrip()
    if not rest.startswith('"'):
        return ""
    try:
        value, _end = json.JSONDecoder().raw_decode(rest)
    except json.JSONDecodeError:
        return ""
    return str(value).strip() if isinstance(value, str) else ""


def _candidate_json_objects(raw: str) -> list[dict]:
    cleaned = str(raw or "").strip()
    cleaned = re.sub(
        r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE
    )
    cleaned = re.sub(r"\s*```$", "", cleaned)
    found: list[dict] = []
    try:
        loaded = json.loads(cleaned)
        if isinstance(loaded, dict):
            found.append(loaded)
        elif isinstance(loaded, list):
            found.extend(item for item in loaded if isinstance(item, dict))
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    index = 0
    while index < len(cleaned):
        start = cleaned.find("{", index)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(cleaned, start)
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(obj, dict):
            found.append(obj)
        index = max(end, start + 1)
    return found


def _translated_text_from_mapping(data: dict) -> str:
    for key in _PARAGRAPH_TRANSLATION_TEXT_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for nest_key in ("result", "data", "output"):
        nested = data.get(nest_key)
        if isinstance(nested, dict):
            found = _translated_text_from_mapping(nested)
            if found:
                return found
    return ""


def _parse_chapter_polish_output(raw: str) -> list[dict]:
    try:
        parsed = _extract_json_object(raw)
    except (ValueError, json.JSONDecodeError, TypeError):
        return []
    items = parsed.get("paragraphs")
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        if not isinstance(item, dict):
            return []
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            return []
        text = str(item.get("polished_text") or "").strip()
        if not text:
            return []
        result.append({"index": index, "polished_text": text})
    return result


def _translation_notes_as_list(notes: object) -> list:
    if isinstance(notes, list):
        return [item for item in notes if item not in (None, "")]
    if isinstance(notes, str) and notes.strip():
        return [{"note": notes.strip()}]
    return []


def _style_guide_mapping(raw: object) -> dict[str, object]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _format_narrative_rules_for_prompt(rules: object) -> str:
    if not isinstance(rules, dict):
        return str(rules or "").strip()
    lines = []
    handling = str(rules.get("recommended_handling") or "").strip()
    reason = str(rules.get("recommendation_reason") or "").strip()
    if handling:
        lines.append(f"권장 처리: {handling}")
    if reason:
        lines.append(reason)
    conventions = rules.get("detected_conventions") or []
    if isinstance(conventions, list):
        for item in conventions:
            if isinstance(item, dict):
                marker = str(item.get("marker") or "").strip()
                meaning = str(item.get("meaning") or "").strip()
                if marker or meaning:
                    lines.append(f"- {marker}: {meaning}".strip())
    return "\n".join(lines)


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
