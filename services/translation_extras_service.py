"""Dictionary, contextual QA, and submission-package orchestration."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Protocol
from urllib.parse import quote

import translation_prompts


FREE_DICTIONARY_API_URL = (
    "https://api.dictionaryapi.dev/api/v2/entries/{language}/"
)
FREE_DICTIONARY_TIMEOUT_SECONDS = 4
FREE_DICTIONARY_MAX_ATTEMPTS = 2
_DICTIONARY_EDGE_PUNCT = re.compile(r"^[^\w']+|[^\w']+$", re.UNICODE)


class ExtrasRepositoryContract(Protocol):
    def get_cached_word_lookup(
        self, segment_id: int, word: str
    ) -> dict | None: ...
    def save_word_lookup_cache(
        self, segment_id: int, word: str, result: dict
    ) -> None: ...
    def get_cached_word_context(
        self, segment_id: int, word: str
    ) -> dict | None: ...
    def save_word_context_cache(
        self, segment_id: int, word: str, explanation: str
    ) -> None: ...
    def save_qa_message(
        self,
        job_id: int,
        question: str,
        answer: str,
        *,
        segment_id: int | None = None,
        dragged_text: str | None = None,
    ) -> dict: ...
    def get_qa_history(
        self,
        job_id: int,
        *,
        segment_id: int | None = None,
        limit: int | None = None,
        scoped: bool = False,
    ) -> list[dict]: ...
    def get_submission_package(self, job_id: int) -> dict | None: ...
    def save_submission_package(
        self, job_id: int, logline: str, synopsis: str
    ) -> dict: ...
    def get_job(self, job_id: int) -> dict | None: ...
    def get_segment(self, segment_id: int) -> dict | None: ...
    def get_segment_for_job(
        self, segment_id: int, job_id: int
    ) -> dict | None: ...
    def get_project_synopsis(self, project_id: int) -> str: ...
    def get_completed_segments(self, job_id: int) -> list[dict]: ...
    def commit(self) -> None: ...


class PreparationRepositoryContract(Protocol):
    def get_proper_nouns(self, job_id: int) -> list[dict]: ...
    def get_scene_context(self, scene_context_id: int) -> dict | None: ...
    def record_pipeline_failure(
        self, job_id: int, step: str, message: str
    ) -> None: ...
    def clear_pipeline_failure(self, job_id: int) -> None: ...
    def commit(self) -> None: ...


class JobServiceContract(Protocol):
    def get_job(self, job_id: int) -> dict: ...


def normalize_dictionary_word(word: object) -> str:
    text = str(word or "").strip()
    return _DICTIONARY_EDGE_PUNCT.sub("", text) if text else ""


def fetch_free_dictionary_payload(
    word: str,
    target_language: object = "en",
) -> tuple[int, object]:
    clean = normalize_dictionary_word(word)
    language = translation_prompts.normalize_target_language(target_language)
    url = FREE_DICTIONARY_API_URL.format(
        language=quote(language, safe="")
    ) + quote(clean)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "SuperTory/1.0",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=FREE_DICTIONARY_TIMEOUT_SECONDS
        ) as response:
            raw = response.read().decode("utf-8") or "[]"
            status = int(getattr(response, "status", 200) or 200)
            return status, json.loads(raw)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8") if error.fp else ""
        parsed: object = {}
        if body:
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = {"title": body}
        return int(error.code), parsed
    except (
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
    ) as error:
        raise ValueError("사전 조회에 실패했어요.") from error


def serialize_qa_message(row: dict) -> dict:
    data = dict(row)
    return {
        "id": int(data["id"]),
        "translation_job_id": int(data["translation_job_id"]),
        "segment_id": data.get("segment_id"),
        "dragged_text": data.get("dragged_text")
        or data.get("quoted_text")
        or "",
        "quoted_text": data.get("dragged_text")
        or data.get("quoted_text")
        or "",
        "role": data.get("role") or "user",
        "message": data.get("message") or "",
        "created_at": data.get("created_at"),
    }


def serialize_submission_package(row: dict) -> dict:
    data = dict(row)
    return {
        "id": int(data["id"]),
        "translation_job_id": int(data["translation_job_id"]),
        "synopsis_translated": data.get("synopsis_translated") or "",
        "logline_translated": data.get("logline_translated") or "",
        "sample_chapters_range": data.get("sample_chapters_range") or "",
        "generated_at": data.get("generated_at"),
    }


class TranslationExtrasService:
    def __init__(
        self,
        repository: ExtrasRepositoryContract,
        preparation_repository: PreparationRepositoryContract,
        job_service: JobServiceContract,
        *,
        gemini_generate: Callable[..., str],
        dictionary_fetch: Callable[
            [str, object], tuple[int, object]
        ] = fetch_free_dictionary_payload,
    ) -> None:
        self.repository = repository
        self.preparation_repository = preparation_repository
        self.job_service = job_service
        self.gemini_generate = gemini_generate
        self.dictionary_fetch = dictionary_fetch

    def lookup_word(
        self,
        segment_id: int | None,
        word: object,
        target_language: object = "en",
    ) -> dict:
        clean = normalize_dictionary_word(word)
        if not clean:
            return _dictionary_not_found_payload("")
        language = translation_prompts.normalize_target_language(
            target_language
        )
        if segment_id is not None:
            cached = self.repository.get_cached_word_lookup(
                int(segment_id), clean.casefold()
            )
            if (
                cached is not None
                and cached.get("target_language") == language
            ):
                result = dict(cached)
                result.pop("target_language", None)
                result["source"] = "cache"
                return result
        last_status: int | None = None
        for attempt in range(1, FREE_DICTIONARY_MAX_ATTEMPTS + 1):
            try:
                status, payload = self.dictionary_fetch(clean, language)
            except (
                ValueError,
                TimeoutError,
                urllib.error.URLError,
                OSError,
            ):
                if attempt >= FREE_DICTIONARY_MAX_ATTEMPTS:
                    return _dictionary_lookup_failed_payload(clean)
                continue
            last_status = int(status or 0)
            if last_status == 404:
                result = _dictionary_not_found_payload(clean)
                return self._cache_dictionary_result(
                    segment_id, clean, language, result
                )
            if last_status != 200:
                if attempt >= FREE_DICTIONARY_MAX_ATTEMPTS:
                    return _dictionary_lookup_failed_payload(clean)
                continue
            result = _parse_dictionary_payload(clean, payload)
            return self._cache_dictionary_result(
                segment_id, clean, language, result
            )
        if last_status == 404:
            return _dictionary_not_found_payload(clean)
        return _dictionary_lookup_failed_payload(clean)

    def explain_word_context(self, body: dict | None) -> dict:
        payload = body if isinstance(body, dict) else {}
        try:
            segment_id = int(payload.get("segment_id"))
        except (TypeError, ValueError) as error:
            raise ValueError("문단을 찾을 수 없습니다.") from error
        word = normalize_dictionary_word(payload.get("word")).casefold()
        if not word:
            raise ValueError("단어를 선택해 주세요.")
        cached = self.repository.get_cached_word_context(segment_id, word)
        if cached:
            return cached
        row = self.repository.get_segment(segment_id)
        if row is None:
            raise LookupError("번역 문단을 찾을 수 없습니다.")
        notes = _parse_translation_notes_list(
            row.get("translation_notes_json")
        )
        from_note = _note_explanation_for_word(notes, word)
        if from_note:
            self.repository.save_word_context_cache(
                segment_id, word, from_note
            )
            self.repository.commit()
            return {
                "segment_id": segment_id,
                "word": word,
                "explanation": from_note,
                "source": "translation_notes",
            }
        translated = str(
            row.get("polish_text") or row.get("translated_text") or ""
        ).strip()
        job = self._require_job(int(row["translation_job_id"]))
        prompt = translation_prompts.build_word_context_prompt(
            {
                "source_text": str(row.get("source_text") or ""),
                "translated_text": translated,
            },
            word,
            _format_translation_notes_for_prompt(notes),
            target_language=job.get("target_language") or "en",
        )
        raw = self.gemini_generate(
            prompt,
            temperature=0.3,
            max_output_tokens=512,
            job_id=int(row["translation_job_id"]),
        )
        explanation = _parse_word_context_explanation(raw)
        self.repository.save_word_context_cache(
            segment_id, word, explanation
        )
        self.repository.commit()
        return {
            "segment_id": segment_id,
            "word": word,
            "explanation": explanation,
            "source": "gemini",
        }

    def answer_qa_question(
        self,
        job_id: int,
        question: str,
        *,
        segment_id: int | None = None,
        dragged_text: str = "",
    ) -> dict:
        job = self._require_job(int(job_id))
        message = str(question or "").strip()
        if not message:
            raise ValueError("질문 내용을 입력해 주세요.")
        segment = None
        if segment_id is not None:
            segment = self.repository.get_segment_for_job(
                int(segment_id), int(job_id)
            )
            if segment is None:
                raise LookupError("번역 문단을 찾을 수 없습니다.")
        history = self.repository.get_qa_history(
            int(job_id),
            segment_id=segment_id,
            limit=12,
            scoped=True,
        )
        history_lines = []
        for item in history:
            role = "작가" if item.get("role") == "user" else "토리"
            quote_bit = str(item.get("dragged_text") or "").strip()
            prefix = f"[인용] {quote_bit}\n" if quote_bit else ""
            history_lines.append(
                f"{role}: {prefix}{item.get('message') or ''}"
            )
        relationship = ""
        mood = ""
        if segment and segment.get("scene_context_id"):
            context = self.preparation_repository.get_scene_context(
                int(segment["scene_context_id"])
            )
            if context:
                relationship = str(context.get("relationship_tag") or "")
                mood = str(context.get("mood_tag") or "")
        style = _mapping(job.get("style_guide_json"))
        prompt = translation_prompts.build_translation_chat_prompt(
            message,
            {
                "source_text": str(
                    segment.get("source_text") if segment else ""
                ),
                "translated_text": str(
                    (
                        segment.get("polish_text")
                        or segment.get("translated_text")
                        or ""
                    )
                    if segment
                    else ""
                ),
                "dragged_text": str(dragged_text or "").strip(),
                "tense": style.get("tense") or "",
                "character_voices": style.get("character_voices") or "",
                "relationship_tag": relationship,
                "mood_tag": mood,
                "culture_localization_level": job.get(
                    "culture_localization_level"
                )
                or "",
                "chat_history": "\n".join(history_lines),
            },
            target_language=job.get("target_language") or "en",
        )
        reply = self.gemini_generate(
            prompt,
            temperature=0.5,
            max_output_tokens=1024,
            job_id=int(job_id),
        )
        response_text, suggested_revision = (
            translation_prompts.parse_translation_qa_output(reply)
        )
        reply_text = (
            response_text.strip()
            or "지금은 답을 만들지 못했어요. 조금 뒤에 다시 물어봐 주세요."
        )
        if suggested_revision:
            reply_text = f"{reply_text}\n\n{suggested_revision}"
        saved = self.repository.save_qa_message(
            int(job_id),
            message,
            reply_text,
            segment_id=segment_id,
            dragged_text=str(dragged_text or "").strip() or None,
        )
        self.repository.commit()
        user = serialize_qa_message(saved["user"])
        tori = serialize_qa_message(saved["tori"])
        tori["suggested_revision"] = suggested_revision
        return {"user": user, "tori": tori}

    def answer_qa_payload(self, body: dict | None) -> dict:
        payload = body if isinstance(body, dict) else {}
        try:
            job_id = int(payload.get("job_id") or 0)
        except (TypeError, ValueError) as error:
            raise ValueError("job_id 가 올바르지 않습니다.") from error
        if job_id <= 0:
            raise ValueError("job_id 가 필요합니다.")
        raw_segment = payload.get("segment_id")
        segment_id = None
        if raw_segment not in (None, ""):
            try:
                segment_id = int(raw_segment)
            except (TypeError, ValueError) as error:
                raise ValueError("segment_id 가 올바르지 않습니다.") from error
        return self.answer_qa_question(
            job_id,
            str(payload.get("message") or ""),
            segment_id=segment_id,
            dragged_text=str(
                payload.get("dragged_text")
                or payload.get("quoted_text")
                or ""
            ),
        )

    def generate_submission_package(self, job_id: int) -> dict:
        job = self._require_job(int(job_id))
        existing = self.repository.get_submission_package(int(job_id))
        if (
            existing
            and str(existing.get("logline_translated") or "").strip()
            and str(existing.get("synopsis_translated") or "").strip()
        ):
            detail = self.job_service.get_job(int(job_id))
            detail["skipped_steps"] = ["submission_package"]
            return detail
        segments = self.repository.get_completed_segments(int(job_id))
        translated_sample = "\n\n".join(
            str(row.get("polish_text") or row.get("translated_text") or "")
            for row in segments
        )
        korean_synopsis = self.repository.get_project_synopsis(
            int(job["local_project_id"])
        )
        source = korean_synopsis
        if translated_sample:
            source = (
                f"{korean_synopsis}\n\n"
                "[완료된 번역 샘플 — 문체와 고유명사 표기 참고]\n"
                f"{translated_sample}"
            ).strip()
        settings = {
            "proper_nouns_confirmed": self._confirmed_glossary(int(job_id)),
            "culture_localization_level": job.get(
                "culture_localization_level"
            )
            or "moderate",
        }
        prompt = translation_prompts.build_submission_query_prompt(
            source,
            settings,
            target_language=job.get("target_language") or "en",
        )
        try:
            raw = self.gemini_generate(
                prompt,
                temperature=0.4,
                max_output_tokens=4096,
                job_id=int(job_id),
            )
            parsed = _extract_json_object(raw)
            logline = str(parsed.get("logline") or "").strip()
            synopsis = str(parsed.get("synopsis") or "").strip()
            if not logline or not synopsis:
                raise ValueError(
                    "투고 패키지 결과에서 logline/synopsis 를 찾지 못했습니다."
                )
            self.repository.save_submission_package(
                int(job_id), logline, synopsis
            )
            self.preparation_repository.clear_pipeline_failure(int(job_id))
            self.repository.commit()
        except Exception as error:
            self.preparation_repository.record_pipeline_failure(
                int(job_id),
                "submission_package",
                str(error).strip() or "submission_package",
            )
            self.preparation_repository.commit()
            raise
        return self.job_service.get_job(int(job_id))

    def _cache_dictionary_result(
        self,
        segment_id: int | None,
        word: str,
        language: str,
        result: dict,
    ) -> dict:
        if segment_id is not None:
            stored = dict(result)
            stored["target_language"] = language
            self.repository.save_word_lookup_cache(
                int(segment_id), word.casefold(), stored
            )
            self.repository.commit()
        return result

    def _confirmed_glossary(self, job_id: int) -> str:
        parts = []
        for row in self.preparation_repository.get_proper_nouns(int(job_id)):
            source = str(row.get("source_term") or "").strip()
            final = str(row.get("final_term") or "").strip()
            if source and final:
                parts.append(f"{source}→{final}")
            elif source:
                parts.append(source)
        return ", ".join(parts)

    def _require_job(self, job_id: int) -> dict:
        row = self.repository.get_job(int(job_id))
        if row is None:
            raise LookupError("번역 작업을 찾을 수 없습니다.")
        return dict(row)


def _parse_dictionary_payload(word: str, payload: object) -> dict:
    entries = payload if isinstance(payload, list) else []
    entry = entries[0] if entries and isinstance(entries[0], dict) else None
    if not entry:
        return _dictionary_not_found_payload(word)
    phonetic = str(entry.get("phonetic") or "").strip()
    if not phonetic:
        for item in entry.get("phonetics") or []:
            if isinstance(item, dict) and str(item.get("text") or "").strip():
                phonetic = str(item.get("text") or "").strip()
                break
    meanings = []
    for meaning in entry.get("meanings") or []:
        if not isinstance(meaning, dict):
            continue
        definitions = []
        for item in meaning.get("definitions") or []:
            if not isinstance(item, dict):
                continue
            definition = str(item.get("definition") or "").strip()
            if definition:
                definitions.append(definition)
            if len(definitions) >= 2:
                break
        if definitions:
            meanings.append({
                "part_of_speech": str(
                    meaning.get("partOfSpeech") or ""
                ).strip(),
                "definitions": definitions,
            })
        if len(meanings) >= 3:
            break
    if not meanings:
        return _dictionary_not_found_payload(word)
    return {
        "found": True,
        "status": "ok",
        "word": str(entry.get("word") or word),
        "phonetic": phonetic,
        "meanings": meanings,
    }


def _dictionary_not_found_payload(word: str) -> dict:
    return {"found": False, "status": "not_found", "word": word}


def _dictionary_lookup_failed_payload(word: str) -> dict:
    return {"found": False, "status": "lookup_failed", "word": word}


def _parse_translation_notes_list(raw: object) -> list[dict]:
    data = raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if isinstance(data, dict):
        nested = (
            data.get("notes")
            or data.get("translation_notes")
            or data.get("paraphrases")
        )
        data = nested if nested is not None else data
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _format_translation_notes_for_prompt(notes: list[dict]) -> str:
    lines = []
    for item in notes:
        translated_as = str(item.get("translated_as") or "").strip()
        source_phrase = str(item.get("source_phrase") or "").strip()
        note = str(
            item.get("note")
            or item.get("text")
            or item.get("reason")
            or ""
        ).strip()
        if not note:
            continue
        if translated_as or source_phrase:
            prefix = " → ".join(
                part for part in (source_phrase, translated_as) if part
            )
            lines.append(f"{prefix}: {note}")
        else:
            lines.append(note)
    return "\n".join(lines) if lines else "(없음)"


def _note_explanation_for_word(notes: list[dict], word: str) -> str:
    token = word.casefold()
    if not token:
        return ""
    pattern = re.compile(r"\b" + re.escape(word) + r"\b", re.IGNORECASE)
    for item in notes:
        translated_as = str(item.get("translated_as") or "")
        source_phrase = str(item.get("source_phrase") or "")
        note = str(
            item.get("note")
            or item.get("text")
            or item.get("reason")
            or ""
        ).strip()
        if not note:
            continue
        haystack = f"{translated_as} {source_phrase}"
        if pattern.search(haystack) or token == translated_as.casefold():
            return note
    return ""


def _parse_word_context_explanation(raw: str) -> str:
    try:
        parsed = _extract_json_object(raw)
    except (ValueError, json.JSONDecodeError, TypeError):
        parsed = {}
    explanation = str(parsed.get("explanation") or "").strip()
    if explanation:
        return explanation
    text = str(raw or "").strip()
    if not text:
        raise ValueError("문맥 설명을 만들지 못했어요.")
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
        try:
            loaded, _end = json.JSONDecoder().raw_decode(text, start)
        except json.JSONDecodeError as error:
            raise ValueError("JSON 응답을 읽지 못했습니다.") from error
        if isinstance(loaded, dict):
            return loaded
    raise ValueError("JSON 응답을 읽지 못했습니다.")


def _mapping(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
