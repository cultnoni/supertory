"""Orchestration and validation for translation preparation."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Protocol

import character_import_analysis
import translation_prompts


PROPER_NOUN_SOURCES = ("character_index", "ai_detected", "user_added")
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


_INDEX_ORG_HINT = re.compile(
    r"(제국|왕국|공국|공화국|기사단|왕가|길드|교단|함대|연합|부족)$"
)
DETECTED_PROPER_NOUN_MAX_CHARS = 15
_SENTENCE_LIKE_MIN_CHARS = 8
_PREDICATE_ENDING = re.compile(
    r"(이며|있으며|이고|였으며|었으며|였다|었다|했다|한다|된다|이다|"
    r"입니다|습니다|합니까|해요|했어요|했고|했는데|하면서|하지만|"
    r"하는데|습니까)$"
)
_PARTICLE_ENDING = re.compile(
    r"(은|는|이|가|을|를|에|에서|으로|로|와|과|도|만|부터|까지)$"
)
_MID_CLAUSE_PARTICLE = re.compile(r"[은는이가을를]\s")


def _proper_noun_key(value: object) -> str:
    return character_import_analysis.strip_tori_text(value).casefold()


def is_sentence_like_proper_noun(
    value: object, *, max_chars: int | None = None
) -> bool:
    """True when a candidate looks like a clause/sentence, not a noun phrase."""
    text = character_import_analysis.strip_tori_text(value)
    text = " ".join(text.split()).strip()
    if not text:
        return False
    if re.search(r"[.。!?]", text):
        return True
    if text.count("(") != text.count(")") or text.count("（") != text.count("）"):
        return True
    if max_chars is not None and len(text) >= int(max_chars):
        return True
    if text.count(" ") >= 3:
        return True
    if len(text) >= _SENTENCE_LIKE_MIN_CHARS and (
        _PREDICATE_ENDING.search(text)
        or _PARTICLE_ENDING.search(text)
        or _MID_CLAUSE_PARTICLE.search(text)
    ):
        return True
    return False


def _stored_term_type(value: object) -> str:
    mapped = _PROPER_NOUN_TERM_TYPE_MAP.get(
        str(value or "").strip().casefold(),
        "",
    )
    return mapped if mapped in PROPER_NOUN_TERM_TYPES else "item"


def _merged_stored_term_type(name: str, types: list[object]) -> str:
    kinds = [_stored_term_type(item) for item in types]
    if _INDEX_ORG_HINT.search(str(name or "").strip()) and (
        "place" in kinds or "organization" in kinds
    ):
        return "organization"
    rank = {"character": 3, "organization": 2, "place": 1, "item": 0}
    return max(kinds, key=lambda kind: rank.get(kind, 0))


def _proper_noun_keep_rank(row: dict) -> tuple:
    name = character_import_analysis.strip_tori_text(row.get("source_term"))
    stored = str(row.get("source_term") or "").strip()
    source = str(row.get("source") or row.get("origin") or "").strip()
    return (
        1 if source == "user_added" else 0,
        1 if str(row.get("user_decision") or "").strip() else 0,
        1 if str(row.get("final_term") or "").strip() else 0,
        1 if _stored_term_type(row.get("term_type")) == "organization" else 0,
        1 if stored == name else 0,
        -int(row.get("id") or 0),
    )


def _proper_noun_is_user_locked(row: dict) -> bool:
    """Keep user-added and already-chosen translations; refresh placeholders only."""
    source = str(row.get("source") or row.get("origin") or "").strip()
    if source == "user_added":
        return True
    decision = str(row.get("user_decision") or "").strip()
    if not decision:
        return False
    if decision == "rename":
        return True
    final_term = character_import_analysis.strip_tori_text(row.get("final_term"))
    source_term = character_import_analysis.strip_tori_text(row.get("source_term"))
    if not final_term:
        return False
    return final_term.casefold() != source_term.casefold()


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
        term_type: str | None = None,
        source_term: str | None = None,
    ) -> dict: ...
    def apply_proper_noun_ai_fields(
        self,
        job_id: int,
        noun_id: int,
        *,
        fit_judgment: str | None,
        judgment_reason: str,
        romanized: str,
        suggested_alternatives: list[str],
        term_type: str | None = None,
    ) -> dict: ...
    def delete_proper_noun(self, job_id: int, noun_id: int) -> None: ...
    def suppress_proper_noun_term(self, job_id: int, source_term: str) -> None: ...
    def unsuppress_proper_noun_term(self, job_id: int, source_term: str) -> None: ...
    def suppressed_proper_noun_keys(self, job_id: int) -> set[str]: ...
    def confirm_all_proper_nouns(self, job_id: int) -> None: ...
    def clear_proper_nouns_confirmed(self, job_id: int) -> None: ...
    def source_text(self, job_id: int, chapter_number: int | None = None) -> str: ...
    def chapter_numbers(self, job_id: int) -> list[int]: ...
    def chapter_segment_count(self, job_id: int, chapter_number: int) -> int: ...
    def chapter_has_scene_contexts(
        self, job_id: int, chapter_number: int
    ) -> bool: ...
    def delete_scene_contexts_outside_range(
        self, job_id: int, start_chapter: int, end_chapter: int
    ) -> int: ...
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


def job_chapter_range(job: dict) -> tuple[int, int]:
    start = int(job.get("start_chapter") or 1)
    end = int(job.get("end_chapter") or start)
    if start > end:
        start, end = end, start
    return start, end


def scene_context_in_job_range(job: dict, chapter_number: int) -> bool:
    start, end = job_chapter_range(job)
    try:
        number = int(chapter_number)
    except (TypeError, ValueError):
        return False
    return start <= number <= end


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
        "source_term": character_import_analysis.strip_tori_text(
            data.get("source_term")
        ),
        "term_type": data.get("term_type"),
        "fit_judgment": data.get("fit_judgment"),
        "judgment_reason": data.get("judgment_reason") or "",
        "suggested_alternatives_json": alternatives_raw,
        "suggested_alternatives": alternatives,
        "romanized": romanized,
        "user_decision": data.get("user_decision"),
        "final_term": data.get("final_term") or "",
        "needs_translation_term": not str(data.get("final_term") or "").strip()
        and not romanized,
        "source": source,
        "origin": source,
        "created_at": data.get("created_at"),
    }


def format_proper_noun_glossary(rows: list[dict]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for row in rows:
        source = character_import_analysis.strip_tori_text(row.get("source_term"))
        final = character_import_analysis.strip_tori_text(row.get("final_term"))
        if not source:
            continue
        key = source.casefold()
        if key in seen:
            continue
        seen.add(key)
        parts.append(f"{source}→{final}" if final else source)
    return ", ".join(parts)


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
        start, end = job_chapter_range(job)
        self.repository.delete_scene_contexts_outside_range(
            int(job_id), start, end
        )
        self.repository.commit()
        target_language = str(job.get("target_language") or "en")
        skipped_chapters: list[int] = []
        for chapter_number in self.repository.chapter_numbers(int(job_id)):
            if not scene_context_in_job_range(job, chapter_number):
                skipped_chapters.append(chapter_number)
                continue
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

    def refresh_proper_nouns(self, job_id: int) -> dict:
        return self.extract_proper_nouns(int(job_id), refresh=True)

    def extract_proper_nouns(self, job_id: int, *, refresh: bool = False) -> dict:
        job = self._require_job(job_id)
        project_id = int(job["local_project_id"])
        self._collapse_duplicate_proper_nouns(int(job_id))
        dropped_sentence_like = self._drop_sentence_like_proper_nouns(int(job_id))
        suppressed = self.repository.suppressed_proper_noun_keys(int(job_id))
        existing = {
            _proper_noun_key(item.get("source_term"))
            for item in self.repository.get_proper_nouns(int(job_id))
        }
        index_terms = self.index_terms_provider(project_id)
        index_nouns: list[dict] = []
        for item in index_terms:
            source_term = character_import_analysis.strip_tori_text(
                item.get("source_term")
            )
            key = _proper_noun_key(source_term)
            if not key or key in existing or key in suppressed:
                continue
            if is_sentence_like_proper_noun(source_term):
                continue
            index_nouns.append({
                "source_term": source_term,
                "term_type": item.get("term_type") or "item",
                "fit_judgment": None,
                "judgment_reason": "",
                "romanized": "",
                "suggested_alternatives": [],
                "user_decision": None,
                "final_term": None,
                "source": "character_index",
            })
            existing.add(key)
        self.repository.save_proper_nouns(int(job_id), index_nouns)

        source_text = self.repository.source_text(int(job_id))[:100_000]
        detected_nouns: list[dict] = []
        judged = 0
        if source_text.strip() or index_terms:
            if not self.gemini_is_configured():
                raise ValueError(
                    "Gemini API 키가 없습니다. .env 에 GEMINI_API_KEY 를 넣어 주세요."
                )
            prompt = translation_prompts.build_proper_noun_fit_prompt(
                source_text,
                existing_index_terms=[
                    character_import_analysis.strip_tori_text(item.get("source_term"))
                    for item in index_terms
                    if not is_sentence_like_proper_noun(item.get("source_term"))
                ],
                target_language=str(job.get("target_language") or "en"),
            )
            raw = self.gemini_generate(
                prompt,
                temperature=0.3,
                max_output_tokens=8192,
                job_id=int(job_id),
            )
            rows_by_key = {
                _proper_noun_key(row.get("source_term")): row
                for row in self.repository.get_proper_nouns(int(job_id))
            }
            for item in _parse_detected_proper_nouns(raw):
                source_term = character_import_analysis.strip_tori_text(
                    item.get("source_term")
                )
                key = _proper_noun_key(source_term)
                if not key or key in suppressed:
                    continue
                current = rows_by_key.get(key)
                if current is not None:
                    if _proper_noun_is_user_locked(current):
                        continue
                    if not refresh and _row_has_ai_judgment(current):
                        continue
                    source = str(
                        current.get("source") or current.get("origin") or ""
                    ).strip()
                    self.repository.apply_proper_noun_ai_fields(
                        int(job_id),
                        int(current["id"]),
                        fit_judgment=item.get("fit_judgment") or "fits",
                        judgment_reason=str(item.get("judgment_reason") or ""),
                        romanized=str(item.get("romanized") or ""),
                        suggested_alternatives=list(
                            item.get("suggested_alternatives") or []
                        ),
                        term_type=(
                            None
                            if source == "character_index"
                            else item.get("term_type")
                        ),
                    )
                    judged += 1
                    continue
                detected_nouns.append({
                    **item,
                    "source_term": source_term,
                    "user_decision": None,
                    "final_term": None,
                    "source": "ai_detected",
                })
                existing.add(key)
            if detected_nouns:
                self.repository.save_proper_nouns(int(job_id), detected_nouns)
            self._fallback_unjudged_index_nouns(int(job_id))
        if refresh:
            self.repository.clear_proper_nouns_confirmed(int(job_id))
        self.repository.mark_proper_nouns_extracted(int(job_id))
        self.repository.commit()
        payload = self.list_proper_nouns(int(job_id))
        payload.update({
            "job": self.job_service.get_job_summary(int(job_id)),
            "seeded_from_index": len(index_nouns),
            "detected_new": len(detected_nouns),
            "judged_existing": judged,
            "dropped_sentence_like": dropped_sentence_like,
        })
        return payload

    def _drop_sentence_like_proper_nouns(self, job_id: int) -> int:
        dropped = 0
        for row in list(self.repository.get_proper_nouns(int(job_id))):
            source = str(row.get("source") or row.get("origin") or "").strip()
            if source == "user_added":
                continue
            term = character_import_analysis.strip_tori_text(row.get("source_term"))
            max_chars = (
                DETECTED_PROPER_NOUN_MAX_CHARS if source == "ai_detected" else None
            )
            if not is_sentence_like_proper_noun(term, max_chars=max_chars):
                continue
            self.repository.suppress_proper_noun_term(int(job_id), term)
            self.repository.delete_proper_noun(int(job_id), int(row["id"]))
            dropped += 1
        if dropped:
            self.repository.commit()
        return dropped

    def _fallback_unjudged_index_nouns(self, job_id: int) -> None:
        """Leave unjudged index names empty so the user must set a translation form.

        Previously this copied the Korean source_term into final_term with
        keep_as_is, which made the review screen look 'done' without a romanized
        or translated form.
        """
        return

    def list_scene_contexts(self, job_id: int) -> list[dict]:
        job = self._require_job(job_id)
        return [
            serialize_scene_context(item)
            for item in self.repository.get_scene_contexts(int(job_id))
            if scene_context_in_job_range(job, int(item["chapter_number"]))
        ]

    def _collapse_duplicate_proper_nouns(self, job_id: int) -> None:
        rows = self.repository.get_proper_nouns(int(job_id))
        groups: dict[str, list[dict]] = {}
        changed = False
        for row in rows:
            name = character_import_analysis.strip_tori_text(row.get("source_term"))
            key = name.casefold()
            if not key:
                continue
            groups.setdefault(key, []).append(row)
            if name != str(row.get("source_term") or "").strip():
                changed = True
        if any(len(group) > 1 for group in groups.values()):
            changed = True
        if not changed:
            return
        for _key, group in groups.items():
            keeper = max(group, key=_proper_noun_keep_rank)
            name = character_import_analysis.strip_tori_text(
                keeper.get("source_term")
            )
            term_type = _merged_stored_term_type(
                name,
                [item.get("term_type") for item in group],
            )
            source = str(
                keeper.get("source") or keeper.get("origin") or "ai_detected"
            ).strip()
            if source not in PROPER_NOUN_SOURCES:
                source = "ai_detected"
            needs_update = (
                name != str(keeper.get("source_term") or "").strip()
                or _stored_term_type(keeper.get("term_type")) != term_type
            )
            if needs_update:
                self.repository.update_proper_noun(
                    int(job_id),
                    int(keeper["id"]),
                    str(keeper.get("final_term") or ""),
                    source,
                    term_type=term_type,
                    source_term=name,
                )
            for extra in group:
                if int(extra["id"]) == int(keeper["id"]):
                    continue
                self.repository.delete_proper_noun(int(job_id), int(extra["id"]))
        self.repository.commit()

    def list_proper_nouns(self, job_id: int) -> dict:
        self._require_job(job_id)
        self._collapse_duplicate_proper_nouns(int(job_id))
        self._drop_sentence_like_proper_nouns(int(job_id))
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
            final_term = str(current.get("romanized") or "").strip()
        elif not final_term and decision == "keep_as_is":
            final_term = str(current.get("source_term") or "").strip()
        if not final_term:
            if decision == "keep_romanized":
                raise ValueError(
                    "로마자 표기가 없어요. 최종 표기를 직접 입력해 주세요."
                )
            raise ValueError("최종 표기를 입력해 주세요.")
        term_type = None
        if data.get("term_type") not in (None, ""):
            term_type = _PROPER_NOUN_TERM_TYPE_MAP.get(
                str(data.get("term_type") or "").strip().casefold(),
                "",
            )
            if term_type not in PROPER_NOUN_TERM_TYPES:
                raise ValueError("고유명사 유형이 올바르지 않습니다.")
        updated = self.repository.update_proper_noun(
            int(row["translation_job_id"]),
            int(noun_id),
            final_term,
            str(current.get("source") or "ai_detected"),
            user_decision=decision,
            term_type=term_type,
        )
        self.repository.commit()
        return serialize_proper_noun(updated)

    def add_proper_noun(self, job_id: int, payload: dict | None) -> dict:
        self._require_job(job_id)
        data = payload if isinstance(payload, dict) else {}
        source_term = character_import_analysis.strip_tori_text(
            data.get("source_term")
        )
        final_term = character_import_analysis.strip_tori_text(
            data.get("final_term") or source_term
        )
        term_type = _PROPER_NOUN_TERM_TYPE_MAP.get(
            str(data.get("term_type") or "").strip().casefold(),
            "",
        )
        if not source_term:
            raise ValueError("고유명사를 입력해 주세요.")
        if not final_term:
            raise ValueError("최종 표기를 입력해 주세요.")
        if term_type not in PROPER_NOUN_TERM_TYPES:
            raise ValueError("고유명사 유형이 올바르지 않습니다.")
        existing = {
            _proper_noun_key(item.get("source_term"))
            for item in self.repository.get_proper_nouns(int(job_id))
        }
        if _proper_noun_key(source_term) in existing:
            raise ValueError("이미 같은 고유명사가 있어요.")
        decision = "keep_as_is" if final_term == source_term else "rename"
        self.repository.unsuppress_proper_noun_term(int(job_id), source_term)
        self.repository.save_proper_nouns(int(job_id), [{
            "source_term": source_term,
            "term_type": term_type,
            "fit_judgment": "fits",
            "judgment_reason": "",
            "romanized": "",
            "suggested_alternatives": [],
            "user_decision": decision,
            "final_term": final_term,
            "source": "user_added",
        }])
        self.repository.commit()
        created = next(
            (
                item
                for item in self.repository.get_proper_nouns(int(job_id))
                if str(item.get("source_term") or "").strip().casefold()
                == source_term.casefold()
            ),
            None,
        )
        if created is None:
            raise ValueError("고유명사를 추가하지 못했어요.")
        payload_out = self.list_proper_nouns(int(job_id))
        payload_out["proper_noun"] = serialize_proper_noun(created)
        return payload_out

    def delete_proper_noun(self, noun_id: int) -> dict:
        row = self.repository.get_proper_noun(int(noun_id))
        if row is None:
            raise LookupError("고유명사 항목을 찾을 수 없습니다.")
        job_id = int(row["translation_job_id"])
        self._require_job(job_id)
        source_term = character_import_analysis.strip_tori_text(row.get("source_term"))
        self.repository.suppress_proper_noun_term(job_id, source_term)
        self.repository.delete_proper_noun(job_id, int(noun_id))
        self.repository.commit()
        return self.list_proper_nouns(job_id)

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


def _row_has_ai_judgment(row: dict) -> bool:
    if str(row.get("judgment_reason") or "").strip():
        return True
    parsed = serialize_proper_noun(row)
    if str(parsed.get("romanized") or "").strip():
        return True
    if parsed.get("suggested_alternatives"):
        return True
    return False


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
        if is_sentence_like_proper_noun(
            source_term, max_chars=DETECTED_PROPER_NOUN_MAX_CHARS
        ):
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
