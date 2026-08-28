"""Orchestration and validation for translation preparation."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Protocol

import translation_prompts


PROPER_NOUN_SOURCES = ("character_index", "ai_detected")
PROPER_NOUN_TERM_TYPES = ("character", "place", "item", "organization")
PROPER_NOUN_FIT_JUDGMENTS = ("fits", "does_not_fit")
PROPER_NOUN_USER_DECISIONS = ("keep_romanized", "rename", "keep_as_is")

_PROPER_NOUN_TERM_TYPE_MAP = {
    "character": "character",
    "place": "place",
    "location": "place",
    "item": "item",
    "organization": "organization",
    "org": "organization",
}
_PROPER_NOUN_FIT_MAP = {
    "fits": "fits",
    "does_not_fit": "does_not_fit",
}


class PreparationRepositoryContract(Protocol):
    def save_formatting_rules(self, job_id: int, rules_json: object) -> None: ...
    def get_formatting_rules(self, job_id: int) -> dict | None: ...
    def save_scene_contexts(self, job_id: int, scenes: list[dict]) -> None: ...
    def get_scene_contexts(self, job_id: int) -> list[dict]: ...
    def save_proper_nouns(self, job_id: int, nouns: list[dict]) -> None: ...
    def get_proper_nouns(self, job_id: int) -> list[dict]: ...
    def get_proper_noun(self, noun_id: int) -> dict | None: ...
    def update_proper_noun(
        self,
        job_id: int,
        noun_id: int,
        final_term: str,
        source: str,
        *,
        user_decision: str | None = None,
    ) -> dict: ...
    def confirm_all_proper_nouns(self, job_id: int) -> None: ...
    def source_text(self, job_id: int, chapter_number: int | None = None) -> str: ...
    def chapter_numbers(self, job_id: int) -> list[int]: ...
    def chapter_segment_count(self, job_id: int, chapter_number: int) -> int: ...
    def chapter_has_scene_contexts(
        self, job_id: int, chapter_number: int
    ) -> bool: ...
    def mark_proper_nouns_extracted(self, job_id: int) -> None: ...
    def set_preparation_status(self, job_id: int, status: str) -> None: ...
    def record_pipeline_failure(
        self, job_id: int, step: str, message: str
    ) -> None: ...
    def clear_pipeline_failure(self, job_id: int) -> None: ...
    def commit(self) -> None: ...


class JobRepositoryContract(Protocol):
    def get_job(self, job_id: int) -> dict | None: ...


class JobServiceContract(Protocol):
    def get_job(self, job_id: int) -> dict: ...
    def get_job_summary(self, job_id: int) -> dict: ...


def serialize_scene_context(row: dict) -> dict:
    data = dict(row)
    return {
        "id": int(data["id"]),
        "translation_job_id": int(data["translation_job_id"]),
        "chapter_number": int(data["chapter_number"]),
        "scene_order": int(data.get("scene_order") or 0),
        "relationship_tag": data.get("relationship_tag") or "",
        "mood_tag": data.get("mood_tag") or "",
        "situation_note": data.get("situation_note") or "",
        "created_at": data.get("created_at"),
    }


def serialize_proper_noun(row: dict) -> dict:
    data = dict(row)
    alternatives_raw = _json_load_optional(
        data.get("suggested_alternatives_json")
    )
    romanized = ""
    alternatives: list[str] = []
    if isinstance(alternatives_raw, dict):
        romanized = str(alternatives_raw.get("romanized") or "").strip()
        values = (
            alternatives_raw.get("alternatives")
            or alternatives_raw.get("suggested_alternatives")
            or []
        )
        if isinstance(values, list):
            alternatives = [
                str(item).strip() for item in values if str(item).strip()
            ]
    elif isinstance(alternatives_raw, list):
        alternatives = [
            str(item).strip()
            for item in alternatives_raw
            if str(item).strip()
        ]
    source = str(
        data.get("source") or data.get("origin") or "ai_detected"
    ).strip()
    if source not in PROPER_NOUN_SOURCES:
        source = "ai_detected"
    return {
        "id": int(data["id"]),
        "translation_job_id": int(data["translation_job_id"]),
        "source_term": data.get("source_term") or "",
        "term_type": data.get("term_type"),
        "fit_judgment": data.get("fit_judgment"),
        "judgment_reason": data.get("judgment_reason") or "",
        "suggested_alternatives_json": alternatives_raw,
        "suggested_alternatives": alternatives,
        "romanized": romanized,
        "user_decision": data.get("user_decision"),
        "final_term": data.get("final_term") or "",
        "source": source,
        "origin": source,
        "created_at": data.get("created_at"),
    }


class TranslationPreparationService:
    def __init__(
        self,
        repository: PreparationRepositoryContract,
        job_repository: JobRepositoryContract,
        job_service: JobServiceContract,
        *,
        index_terms_provider: Callable[[int], list[dict]],
        gemini_generate: Callable[..., str],
        gemini_is_configured: Callable[[], bool],
    ) -> None:
        self.repository = repository
        self.job_repository = job_repository
        self.job_service = job_service
        self.index_terms_provider = index_terms_provider
        self.gemini_generate = gemini_generate
        self.gemini_is_configured = gemini_is_configured

    def start_preparation(self, job_id: int) -> dict:
        self._require_job(job_id)
        skipped: list[str] = []
        try:
            _rules, was_skipped = self.detect_formatting_rules(int(job_id))
            if was_skipped:
                skipped.append("narrative_formatting")
        except Exception as error:
            self._record_failure(job_id, "narrative_formatting", error)
            raise
        try:
            _scenes, skipped_chapters = self.split_scenes(int(job_id))
            chapters = self.repository.chapter_numbers(int(job_id))
            if chapters and len(skipped_chapters) == len(chapters):
                skipped.append("scene_split")
        except Exception as error:
            self._record_failure(job_id, "scene_split", error)
            raise
        try:
            job = self._require_job(job_id)
            if bool(int(job.get("proper_nouns_extracted") or 0)):
                skipped.append("proper_nouns")
                nouns_payload = self.list_proper_nouns(int(job_id))
            else:
                nouns_payload = self.extract_proper_nouns(int(job_id))
        except Exception as error:
            self._record_failure(job_id, "proper_nouns", error)
            raise
        self.repository.clear_pipeline_failure(int(job_id))
        self.repository.set_preparation_status(int(job_id), "awaiting_review")
        self.repository.commit()
        detail = self.job_service.get_job(int(job_id))
        detail["skipped_steps"] = skipped
        detail["proper_nouns"] = nouns_payload["proper_nouns"]
        return detail

    def detect_formatting_rules(self, job_id: int) -> tuple[dict, bool]:
        job = self._require_job(job_id)
        existing = self.repository.get_formatting_rules(int(job_id))
        if existing is not None:
            return existing, True
        source = self.repository.source_text(int(job_id))[:40_000]
        prompt = translation_prompts.build_narrative_formatting_prompt(
            source,
            str(job.get("target_language") or "en"),
        )
        raw = self.gemini_generate(
            prompt,
            temperature=0.2,
            max_output_tokens=2048,
            job_id=int(job_id),
        )
        rules = _parse_narrative_formatting_output(raw)
        self.repository.save_formatting_rules(int(job_id), rules)
        self.repository.commit()
        return rules, False

    def split_scenes(self, job_id: int) -> tuple[list[dict], list[int]]:
        job = self._require_job(job_id)
        target_language = str(job.get("target_language") or "en")
        skipped_chapters: list[int] = []
        for chapter_number in self.repository.chapter_numbers(int(job_id)):
            if self.repository.chapter_has_scene_contexts(
                int(job_id), chapter_number
            ):
                skipped_chapters.append(chapter_number)
                continue
            chapter_text = self.repository.source_text(
                int(job_id),
                chapter_number,
            )
            if not chapter_text.strip():
                skipped_chapters.append(chapter_number)
                continue
            prompt = translation_prompts.build_scene_split_prompt(
                chapter_text,
                target_language,
            )
            raw = self.gemini_generate(
                prompt,
                temperature=0.2,
                max_output_tokens=4096,
                job_id=int(job_id),
            )
            scenes = _parse_scene_split_output(raw)
            if not scenes:
                scenes = [{
                    "scene_order": 1,
                    "start_paragraph_index": 0,
                    "end_paragraph_index": max(
                        0,
                        self.repository.chapter_segment_count(
                            int(job_id), chapter_number
                        ) - 1,
                    ),
                    "relationship_tag": "",
                    "mood_tag": "",
                    "situation_note": "",
                }]
            self.repository.save_scene_contexts(
                int(job_id),
                [
                    {**scene, "chapter_number": chapter_number}
                    for scene in scenes
                ],
            )
            self.repository.commit()
        return self.list_scene_contexts(int(job_id)), skipped_chapters

    def extract_proper_nouns(self, job_id: int) -> dict:
        job = self._require_job(job_id)
        project_id = int(job["local_project_id"])
        existing = {
            str(item.get("source_term") or "").strip().casefold()
            for item in self.repository.get_proper_nouns(int(job_id))
        }
        index_terms = self.index_terms_provider(project_id)
        index_nouns: list[dict] = []
        for item in index_terms:
            source_term = str(item.get("source_term") or "").strip()
            key = source_term.casefold()
            if not key or key in existing:
                continue
            index_nouns.append({
                "source_term": source_term,
                "term_type": item.get("term_type") or "item",
                "fit_judgment": "fits",
                "judgment_reason": "",
                "romanized": "",
                "suggested_alternatives": [],
                "user_decision": "keep_as_is",
                "final_term": source_term,
                "source": "character_index",
            })
            existing.add(key)
        self.repository.save_proper_nouns(int(job_id), index_nouns)

        source_text = self.repository.source_text(int(job_id))[:100_000]
        detected_nouns: list[dict] = []
        if source_text.strip():
            if not self.gemini_is_configured():
                raise ValueError(
                    "Gemini API 키가 없습니다. .env 에 GEMINI_API_KEY 를 넣어 주세요."
                )
            prompt = translation_prompts.build_proper_noun_fit_prompt(
                source_text,
                existing_index_terms=[
                    str(item.get("source_term") or "")
                    for item in index_terms
                ],
                target_language=str(job.get("target_language") or "en"),
            )
            raw = self.gemini_generate(
                prompt,
                temperature=0.3,
                max_output_tokens=4096,
                job_id=int(job_id),
            )
            for item in _parse_detected_proper_nouns(raw):
                key = item["source_term"].casefold()
                if key in existing:
                    continue
                detected_nouns.append({
                    **item,
                    "user_decision": None,
                    "final_term": None,
                    "source": "ai_detected",
                })
                existing.add(key)
            self.repository.save_proper_nouns(int(job_id), detected_nouns)
        self.repository.mark_proper_nouns_extracted(int(job_id))
        self.repository.commit()
        payload = self.list_proper_nouns(int(job_id))
        payload.update({
            "job": self.job_service.get_job_summary(int(job_id)),
            "seeded_from_index": len(index_nouns),
            "detected_new": len(detected_nouns),
        })
        return payload

    def list_scene_contexts(self, job_id: int) -> list[dict]:
        self._require_job(job_id)
        return [
            serialize_scene_context(item)
            for item in self.repository.get_scene_contexts(int(job_id))
        ]

    def list_proper_nouns(self, job_id: int) -> dict:
        self._require_job(job_id)
        return {
            "proper_nouns": [
                serialize_proper_noun(item)
                for item in self.repository.get_proper_nouns(int(job_id))
            ]
        }

    def decide_proper_noun(self, noun_id: int, payload: dict | None) -> dict:
        data = payload if isinstance(payload, dict) else {}
        row = self.repository.get_proper_noun(int(noun_id))
        if row is None:
            raise LookupError("고유명사 항목을 찾을 수 없습니다.")
        decision = str(data.get("user_decision") or "").strip()
        if decision not in PROPER_NOUN_USER_DECISIONS:
            raise ValueError("고유명사 결정 값이 올바르지 않습니다.")
        current = serialize_proper_noun(row)
        final_term = str(data.get("final_term") or "").strip()
        if not final_term and decision == "keep_romanized":
            final_term = str(
                current.get("romanized") or current.get("source_term") or ""
            ).strip()
        elif not final_term and decision == "keep_as_is":
            final_term = str(current.get("source_term") or "").strip()
        if not final_term:
            raise ValueError("최종 표기를 입력해 주세요.")
        updated = self.repository.update_proper_noun(
            int(row["translation_job_id"]),
            int(noun_id),
            final_term,
            str(current.get("source") or "ai_detected"),
            user_decision=decision,
        )
        self.repository.commit()
        return serialize_proper_noun(updated)

    def confirm_all_proper_nouns(self, job_id: int) -> dict:
        self._require_job(job_id)
        nouns = self.repository.get_proper_nouns(int(job_id))
        missing = [
            str(row.get("source_term") or "")
            for row in nouns
            if not str(row.get("final_term") or "").strip()
        ]
        if missing:
            raise ValueError(
                "아직 최종 표기가 없는 고유명사가 있어요: "
                + ", ".join(missing[:8])
            )
        self.repository.confirm_all_proper_nouns(int(job_id))
        self.repository.commit()
        detail = self.job_service.get_job(int(job_id))
        detail["proper_nouns"] = [
            serialize_proper_noun(item) for item in nouns
        ]
        return detail

    def _record_failure(
        self,
        job_id: int,
        step: str,
        error: BaseException,
    ) -> None:
        message = str(error).strip() or step
        self.repository.record_pipeline_failure(
            int(job_id),
            step,
            message,
        )
        self.repository.commit()

    def _require_job(self, job_id: int) -> dict:
        row = self.job_repository.get_job(int(job_id))
        if row is None:
            raise LookupError("번역 작업을 찾을 수 없습니다.")
        return row


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


def _parse_narrative_formatting_output(raw: str) -> dict:
    try:
        parsed = _extract_json_object(raw)
    except (ValueError, json.JSONDecodeError, TypeError):
        parsed = {}
    conventions = parsed.get("detected_conventions") or []
    if not isinstance(conventions, list):
        conventions = []
    return {
        "detected_conventions": conventions,
        "recommended_handling": str(
            parsed.get("recommended_handling") or ""
        ).strip(),
        "recommendation_reason": str(
            parsed.get("recommendation_reason") or ""
        ).strip(),
    }


def _parse_scene_split_output(raw: str) -> list[dict]:
    try:
        parsed = _extract_json_object(raw)
    except (ValueError, json.JSONDecodeError, TypeError):
        parsed = {}
    items = parsed.get("scenes") if isinstance(parsed, dict) else []
    if not isinstance(items, list):
        return []
    scenes: list[dict] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        try:
            start = int(item.get("start_paragraph_index", 0))
            end = int(item.get("end_paragraph_index", start))
        except (TypeError, ValueError):
            continue
        if end < start:
            start, end = end, start
        try:
            order = int(item.get("scene_order", index + 1))
        except (TypeError, ValueError):
            order = index + 1
        scenes.append({
            "scene_order": order,
            "start_paragraph_index": max(0, start),
            "end_paragraph_index": max(0, end),
            "relationship_tag": str(
                item.get("relationship_tag") or ""
            ).strip(),
            "mood_tag": str(item.get("mood_tag") or "").strip(),
            "situation_note": str(item.get("situation_note") or "").strip(),
        })
    return scenes


def _parse_detected_proper_nouns(raw: str) -> list[dict]:
    try:
        parsed = _extract_json_object(raw)
    except (ValueError, json.JSONDecodeError, TypeError):
        parsed = {}
    items = parsed.get("proper_nouns") or parsed.get("items") or []
    if not isinstance(items, list):
        return []
    results: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        source_term = str(
            item.get("source_term") or item.get("term") or ""
        ).strip()
        if not source_term:
            continue
        alternatives = item.get("suggested_alternatives") or []
        if not isinstance(alternatives, list):
            alternatives = []
        results.append({
            "source_term": source_term,
            "term_type": _PROPER_NOUN_TERM_TYPE_MAP.get(
                str(item.get("term_type") or "").strip().casefold(),
                "character",
            ),
            "fit_judgment": _PROPER_NOUN_FIT_MAP.get(
                str(item.get("fit_judgment") or "").strip().casefold(),
                "fits",
            ),
            "judgment_reason": str(
                item.get("judgment_reason") or ""
            ).strip(),
            "romanized": str(item.get("romanized") or "").strip(),
            "suggested_alternatives": [
                str(value).strip()
                for value in alternatives
                if str(value).strip()
            ],
        })
    return results
