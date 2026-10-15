"""번역 파이프라인 HTTP API. app.py는 라우트만 연결한다.

잡·세그먼트·고유명사·제출 패키지·사전/문맥·폴리시·채팅을 담당한다.
스키마 ensure_* 함수는 initialise_database 소속이라 app.py에 남긴다.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from http import HTTPStatus
from urllib.parse import parse_qs, unquote, urlparse

import character_import_analysis
import gemini_client
import translation_context
import translation_prompts
import world_import_analysis
from repositories.translation_extras_repository import TranslationExtrasRepository
from repositories.translation_job_repository import TranslationJobRepository
from repositories.translation_preparation_repository import (
    TranslationPreparationRepository,
)
from repositories.translation_segment_repository import TranslationSegmentRepository
from services import (
    translation_batch_service,
    translation_extras_service,
    translation_job_service,
    translation_preparation_service,
)


def _ensure_pipeline_schema(connection):
    import app
    return app.ensure_translation_pipeline_schema(connection)


def _ensure_manual_review(connection):
    import app
    return app.ensure_translation_segment_manual_review_column(connection)


def _ensure_polish_cols(connection):
    import app
    return app.ensure_translation_segment_polish_proposal_columns(connection)


def _ensure_word_cache(connection):
    import app
    return app.ensure_translation_word_context_cache(connection)


def _list_scenes_in_binder_order(connection, project_id):
    import app
    return app.list_scenes_in_binder_order(connection, project_id)


def _load_segment_scene_tags_shared(*args, **kwargs):
    import app
    return app._load_segment_scene_tags(*args, **kwargs)


def _paragraph_empty_retry_delay_shared(*args, **kwargs):
    import app
    return app._paragraph_empty_retry_delay(*args, **kwargs)


def _database():
    import app

    return app.database()


def _as_dict(row):
    import app

    return app.as_dict(row)


def _now() -> str:
    import app

    return app.utc_timestamp_now()


def _plain_text(content: object) -> str:
    import app

    return app.plain_text_from_content(content)


def _data_dir():
    import app

    return app.DATA_DIR


def _handler_cls():
    import app

    return app.SuperToryHandler


def _translation_proper_noun_columns(connection: sqlite3.Connection) -> set[str]:
    import app

    return app._translation_proper_noun_columns(connection)


TRANSLATION_JOB_STATUSES = (
    "draft",
    "awaiting_review",
    "in_progress",
    "translated",
    "completed",
)

TRANSLATION_PIPELINE_STEPS = (
    "narrative_formatting",
    "scene_split",
    "proper_nouns",
    "paragraph_translation",
    "submission_package",
)


def _translation_job_columns(connection: sqlite3.Connection) -> set[str]:
    import app

    return app._translation_job_columns(connection)


def _translation_segment_columns(connection: sqlite3.Connection) -> set[str]:
    import app

    return app._translation_segment_columns(connection)


TRANSLATION_SEPARATOR_PARAGRAPH_RE = (
    translation_batch_service.TRANSLATION_SEPARATOR_PARAGRAPH_RE
)


def is_translation_separator_paragraph(text: str) -> bool:
    """True when the paragraph is only a repeated divider like ==== or ----."""
    return translation_batch_service.is_translation_separator_paragraph(text)


def apply_translation_separator_passthrough(
    connection: sqlite3.Connection, row: sqlite3.Row | dict
) -> bool:
    """Copy source to translation without Gemini. Returns True when applied."""
    source = str(row["source_text"] or "")
    if not is_translation_separator_paragraph(source):
        return False
    repository = get_translation_segment_repository(connection)
    repository.save_separator_passthrough(int(row["id"]))
    repository.commit()
    return True


def split_source_paragraphs(text: str) -> list[str]:
    """Split manuscript HTML/markdown into paragraph-sized translation segments."""
    plain = _plain_text(text or "")
    chunks = re.split(r"\n\s*\n+", plain)
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def get_translation_job_repository(
    connection: sqlite3.Connection,
) -> TranslationJobRepository:
    return TranslationJobRepository(
        connection,
        scene_loader=_list_scenes_in_binder_order,
        paragraph_splitter=split_source_paragraphs,
        separator_checker=is_translation_separator_paragraph,
        timestamp_provider=_now,
    )


def get_translation_preparation_repository(
    connection: sqlite3.Connection,
) -> TranslationPreparationRepository:
    return TranslationPreparationRepository(
        connection,
        timestamp_provider=_now,
    )


def get_translation_segment_repository(
    connection: sqlite3.Connection,
) -> TranslationSegmentRepository:
    return TranslationSegmentRepository(connection)


def get_translation_extras_repository(
    connection: sqlite3.Connection,
) -> TranslationExtrasRepository:
    return TranslationExtrasRepository(
        connection,
        timestamp_provider=_now,
    )


def get_translation_job_service(
    connection: sqlite3.Connection,
) -> translation_job_service.TranslationJobService:
    return translation_job_service.TranslationJobService(
        get_translation_job_repository(connection),
        get_translation_preparation_repository(connection),
        get_translation_segment_repository(connection),
        get_translation_extras_repository(connection),
        pipeline_wait_provider=_translation_pipeline_wait_seconds,
    )


def get_translation_preparation_service(
    connection: sqlite3.Connection,
) -> translation_preparation_service.TranslationPreparationService:
    return translation_preparation_service.TranslationPreparationService(
        get_translation_preparation_repository(connection),
        get_translation_job_repository(connection),
        get_translation_job_service(connection),
        index_terms_provider=lambda project_id: collect_character_world_index_terms(
            connection, project_id
        ),
        gemini_generate=_translation_pipeline_gemini,
        gemini_is_configured=gemini_client.is_configured,
    )


def get_translation_batch_service(
    connection: sqlite3.Connection,
) -> translation_batch_service.TranslationBatchService:
    return translation_batch_service.TranslationBatchService(
        get_translation_segment_repository(connection),
        get_translation_job_repository(connection),
        get_translation_job_service(connection),
        get_translation_preparation_repository(connection),
        gemini_generate=_translation_pipeline_gemini,
        sleep_provider=_translation_sleep,
        empty_retry_delay_provider=_paragraph_empty_retry_delay_shared,
        debug_log_path=_data_dir() / "translation_paragraph_debug.log",
    )


def get_translation_extras_service(
    connection: sqlite3.Connection,
) -> translation_extras_service.TranslationExtrasService:
    return translation_extras_service.TranslationExtrasService(
        get_translation_extras_repository(connection),
        get_translation_preparation_repository(connection),
        get_translation_job_service(connection),
        gemini_generate=_translation_pipeline_gemini,
        dictionary_fetch=fetch_free_dictionary_payload,
    )


def serialize_translation_job(row: sqlite3.Row | dict) -> dict:
    data = dict(row)
    return translation_job_service.serialize_translation_job(
        data,
        pipeline_wait_seconds=_translation_pipeline_wait_seconds(int(data["id"])),
    )


def translation_job_progress(connection: sqlite3.Connection, job_id: int) -> dict:
    return get_translation_segment_repository(connection).translation_progress(int(job_id))


def decorate_translation_job(connection: sqlite3.Connection, job: dict) -> dict:
    job.update(translation_job_progress(connection, int(job["id"])))
    return job


def serialize_translation_scene_context(row: sqlite3.Row | dict) -> dict:
    return translation_preparation_service.serialize_scene_context(dict(row))


def serialize_translation_segment(row: sqlite3.Row | dict) -> dict:
    return translation_batch_service.serialize_translation_segment(dict(row))


def serialize_translation_proper_noun(row: sqlite3.Row | dict) -> dict:
    return translation_preparation_service.serialize_proper_noun(dict(row))


def serialize_translation_chat_message(row: sqlite3.Row | dict) -> dict:
    return translation_extras_service.serialize_qa_message(dict(row))


def load_translation_job(
    connection: sqlite3.Connection, job_id: int
) -> dict:
    row = get_translation_job_repository(connection).get_job(int(job_id))
    if row is None:
        raise LookupError("번역 작업을 찾을 수 없습니다.")
    return row


def translation_job_chapter_catalog(
    connection: sqlite3.Connection, job_id: int, project_id: int
) -> list[dict]:
    del project_id
    return get_translation_job_service(connection).get_job_summary(
        int(job_id)
    )["chapters"]

TranslationSettingsConfirmRequired = (
    translation_job_service.TranslationSettingsConfirmRequired
)


def translation_job_detail(connection: sqlite3.Connection, job_id: int) -> dict:
    return get_translation_job_service(connection).get_job(int(job_id))


def list_translation_scene_contexts(
    connection: sqlite3.Connection, job_id: int
) -> list[dict]:
    return get_translation_preparation_service(connection).list_scene_contexts(
        int(job_id)
    )


def load_translation_submission_package(
    connection: sqlite3.Connection, job_id: int
) -> dict | None:
    row = get_translation_extras_repository(connection).get_submission_package(
        int(job_id)
    )
    if row is None:
        return None
    return translation_extras_service.serialize_submission_package(row)


def list_translation_segments(
    connection: sqlite3.Connection, job_id: int, chapter: int | None
) -> dict:
    _ensure_polish_cols(connection)
    return get_translation_batch_service(connection).list_segments(
        int(job_id), chapter
    )


def list_translation_proper_nouns(connection: sqlite3.Connection, job_id: int) -> dict:
    return get_translation_preparation_service(connection).list_proper_nouns(
        int(job_id)
    )

PROPER_NOUN_TERM_TYPES = translation_preparation_service.PROPER_NOUN_TERM_TYPES

_INDEX_TERM_DELIMS = set(",，、/;·•|\n")

_INDEX_TERM_OPEN = set("(（")

_INDEX_TERM_CLOSE = set(")）")

_INDEX_ORG_HINT = re.compile(
    r"(제국|왕국|공국|공화국|기사단|왕가|길드|교단|함대|연합|부족)$"
)

_TRAILING_PAREN_LIST = re.compile(
    r"^(?P<name>.+?)\s*[\(（](?P<inner>[^()（）]+)[\)）]\s*$"
)


def _split_outside_parens(text: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for char in str(text or ""):
        if char in _INDEX_TERM_OPEN:
            depth += 1
            buf.append(char)
            continue
        if char in _INDEX_TERM_CLOSE:
            if depth:
                depth -= 1
            buf.append(char)
            continue
        if depth == 0 and char in _INDEX_TERM_DELIMS:
            part = "".join(buf).strip()
            if part:
                parts.append(part)
            buf = []
            continue
        buf.append(char)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _peel_trailing_paren_list(chunk: str) -> tuple[str, str | None]:
    text = str(chunk or "").strip()
    match = _TRAILING_PAREN_LIST.match(text)
    if not match:
        return text, None
    name = " ".join(match.group("name").split()).strip()
    inner = " ".join(match.group("inner").split()).strip()
    if not inner:
        return text, None
    return name, inner


def _clean_index_term_token(raw: object) -> str:
    token = character_import_analysis.strip_tori_text(raw)
    token = " ".join(token.split()).strip(" -–—·•")
    if not token or len(token) > 40:
        return ""
    if translation_preparation_service.is_sentence_like_proper_noun(token):
        return ""
    return token


def _split_index_term_tokens(raw: object) -> list[str]:
    text = character_import_analysis.strip_tori_text(raw)
    if not text:
        return []
    tokens: list[str] = []
    seen: set[str] = set()

    def emit(piece: object) -> None:
        token = _clean_index_term_token(piece)
        if not token:
            return
        key = token.casefold()
        if key in seen:
            return
        seen.add(key)
        tokens.append(token)

    for chunk in _split_outside_parens(text):
        outer_name, inner = _peel_trailing_paren_list(chunk)
        if inner is None:
            emit(chunk)
            continue
        if outer_name:
            emit(outer_name)
        for inner_chunk in _split_outside_parens(inner):
            emit(inner_chunk)
    return tokens


def _index_term_looks_like_organization(name: str) -> bool:
    return bool(_INDEX_ORG_HINT.search(str(name or "").strip()))


def _resolve_index_term_type(
    name: str, incoming: str, current: str | None = None
) -> str:
    incoming_kind = incoming if incoming in PROPER_NOUN_TERM_TYPES else "item"
    if _index_term_looks_like_organization(name) and incoming_kind in {
        "place",
        "organization",
        "item",
    }:
        incoming_kind = "organization"
    if current is None:
        return incoming_kind
    current_kind = current if current in PROPER_NOUN_TERM_TYPES else "item"
    if current_kind == incoming_kind:
        return current_kind
    kinds = {current_kind, incoming_kind}
    if kinds <= {"place", "organization"}:
        return (
            "organization"
            if _index_term_looks_like_organization(name)
            else current_kind
        )
    rank = translation_preparation_service.INDEX_TERM_TYPE_RANK
    fallback = rank.get("dictionary", -1)
    if rank.get(incoming_kind, fallback) > rank.get(current_kind, fallback):
        return incoming_kind
    return current_kind


def _index_term_from_item(item: object) -> str:
    if isinstance(item, dict):
        for key in ("name", "term", "title", "source_term"):
            value = str(item.get(key) or "").strip()
            if value:
                return value
        return ""
    return str(item or "").strip()


def collect_character_world_index_terms(
    connection: sqlite3.Connection, project_id: int
) -> list[dict[str, str]]:
    """Reuse character sheets + project auto-index + worldbuilding names/places/terms."""
    collected: list[dict[str, str]] = []
    seen: dict[str, dict[str, str]] = {}

    def add(term: object, term_type: str) -> None:
        text = character_import_analysis.strip_tori_text(term)
        text = " ".join(text.split()).strip()
        if not text:
            return
        key = text.casefold()
        existing = seen.get(key)
        if existing is not None:
            existing["term_type"] = _resolve_index_term_type(
                text, term_type, existing.get("term_type")
            )
            return
        kind = _resolve_index_term_type(text, term_type)
        item = {"source_term": text, "term_type": kind}
        seen[key] = item
        collected.append(item)

    try:
        existing = character_import_analysis.list_existing_characters(
            connection, int(project_id)
        )
    except sqlite3.Error:
        existing = []
    for row in existing:
        add(row.get("name"), "character")

    try:
        alias_rows = connection.execute(
            "SELECT alias FROM character_alias WHERE project_id = ? ORDER BY id",
            (int(project_id),),
        ).fetchall()
    except sqlite3.Error:
        alias_rows = []
    for row in alias_rows:
        add(row["alias"] if "alias" in row.keys() else row[0], "character")

    try:
        index_cols = {
            str(col[1])
            for col in connection.execute("PRAGMA table_info(project_index)").fetchall()
        }
    except sqlite3.Error:
        index_cols = set()
    char_col = "characters_json" if "characters_json" in index_cols else None
    world_col = "world_rules_json" if "world_rules_json" in index_cols else None
    if char_col or world_col:
        select_cols = ", ".join(part for part in (char_col, world_col) if part)
        try:
            index_row = connection.execute(
                f"SELECT {select_cols} FROM project_index WHERE project_id = ?",
                (int(project_id),),
            ).fetchone()
        except sqlite3.Error:
            index_row = None
        if index_row is not None:
            parse_list = _handler_cls()._parse_json_list
            if char_col:
                for item in parse_list(index_row[char_col]):
                    add(_index_term_from_item(item), "character")
            if world_col:
                for item in parse_list(index_row[world_col]):
                    label = _index_term_from_item(item)
                    for token in _split_index_term_tokens(label):
                        add(token, "item")

    try:
        project_cols = {
            str(col[1])
            for col in connection.execute("PRAGMA table_info(project)").fetchall()
        }
        world_field = (
            "worldbuilding_md" if "worldbuilding_md" in project_cols else None
        )
        world_md = ""
        if world_field:
            world_row = connection.execute(
                f"SELECT {world_field} FROM project WHERE id = ?",
                (int(project_id),),
            ).fetchone()
            world_md = str(world_row[0] if world_row else "") if world_row else ""
        values = world_import_analysis.parse_worldbuilding_md(world_md)
    except (sqlite3.Error, AttributeError, TypeError):
        values = {}
    field_types = {
        "locale": "place",
        "factions": "organization",
        "special": "item",
        "unique": "item",
    }
    for field_id, term_type in field_types.items():
        for token in _split_index_term_tokens(values.get(field_id)):
            add(token, term_type)

    try:
        dict_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'custom_dictionary_terms'"
        ).fetchone()
    except sqlite3.Error:
        dict_exists = None
    if dict_exists is not None:
        try:
            dict_cols = {
                str(col[1])
                for col in connection.execute(
                    "PRAGMA table_info(custom_dictionary_terms)"
                ).fetchall()
            }
        except sqlite3.Error:
            dict_cols = set()
        if "term" in dict_cols:
            select_cols = ["term"]
            if "aliases" in dict_cols:
                select_cols.append("aliases")
            try:
                dict_rows = connection.execute(
                    f"SELECT {', '.join(select_cols)} FROM custom_dictionary_terms "
                    "WHERE project_id = ?",
                    (int(project_id),),
                ).fetchall()
            except sqlite3.Error:
                dict_rows = []
            for row in dict_rows:
                data = dict(row)
                add(data.get("term"), "dictionary")
                for token in _split_index_term_tokens(data.get("term")):
                    add(token, "dictionary")
                if "aliases" in dict_cols:
                    for token in _split_index_term_tokens(data.get("aliases")):
                        add(token, "dictionary")
    return collected


def extract_translation_proper_nouns(
    connection: sqlite3.Connection, job_id: int
) -> dict:
    return get_translation_preparation_service(connection).extract_proper_nouns(
        int(job_id)
    )


def refresh_translation_proper_nouns(
    connection: sqlite3.Connection, job_id: int
) -> dict:
    return get_translation_preparation_service(connection).refresh_proper_nouns(
        int(job_id)
    )


def decide_translation_proper_noun(
    connection: sqlite3.Connection, noun_id: int, body: dict | None
) -> dict:
    return get_translation_preparation_service(connection).decide_proper_noun(
        int(noun_id), body
    )


def confirm_translation_proper_nouns(
    connection: sqlite3.Connection, job_id: int
) -> dict:
    return get_translation_preparation_service(
        connection
    ).confirm_all_proper_nouns(int(job_id))


def _translation_sleep(seconds: float) -> None:
    translation_batch_service._translation_sleep(seconds)


def _is_translation_rate_limit_error(error: BaseException) -> bool:
    return translation_batch_service.is_translation_rate_limit_error(error)


def _translation_rate_limit_wait_seconds(error: BaseException) -> float:
    return translation_batch_service.translation_rate_limit_wait_seconds(error)


def _set_translation_pipeline_wait(job_id: int | None, seconds: float) -> None:
    translation_batch_service.set_translation_pipeline_wait(job_id, seconds)


def _clear_translation_pipeline_wait(job_id: int | None) -> None:
    translation_batch_service.clear_translation_pipeline_wait(job_id)


def _translation_pipeline_wait_seconds(job_id: int | None) -> float:
    return translation_batch_service.translation_pipeline_wait_seconds(job_id)


def _translation_pipeline_gemini(
    prompt: str,
    *,
    temperature: float,
    max_output_tokens: int,
    job_id: int | None = None,
) -> str:
    return translation_batch_service.generate_translation_text(
        prompt,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        job_id=job_id,
        sleep_provider=_translation_sleep,
    )


def _record_translation_pipeline_failure(
    connection: sqlite3.Connection, job_id: int, step: str, error: BaseException
) -> None:
    message = str(error).strip() or step
    repository = get_translation_preparation_repository(connection)
    repository.record_pipeline_failure(int(job_id), step, message)
    repository.commit()


def _clear_translation_pipeline_failure(
    connection: sqlite3.Connection, job_id: int
) -> None:
    get_translation_preparation_repository(connection).clear_pipeline_failure(
        int(job_id)
    )


def _translation_chapter_numbers(
    connection: sqlite3.Connection, job_id: int
) -> list[int]:
    return get_translation_preparation_repository(connection).chapter_numbers(
        int(job_id)
    )


def _translation_chapter_source_text(
    connection: sqlite3.Connection, job_id: int, chapter_number: int
) -> str:
    return get_translation_preparation_repository(connection).source_text(
        int(job_id), int(chapter_number)
    )


def _translation_chapter_has_scenes(
    connection: sqlite3.Connection, job_id: int, chapter_number: int
) -> bool:
    return get_translation_preparation_repository(
        connection
    ).chapter_has_scene_contexts(int(job_id), int(chapter_number))


def detect_translation_narrative_formatting(
    connection: sqlite3.Connection, job_id: int
) -> tuple[dict, bool]:
    return get_translation_preparation_service(
        connection
    ).detect_formatting_rules(int(job_id))


def split_translation_scenes(
    connection: sqlite3.Connection, job_id: int
) -> tuple[list[dict], list[int]]:
    return get_translation_preparation_service(connection).split_scenes(
        int(job_id)
    )


def start_translation_pipeline(connection: sqlite3.Connection, job_id: int) -> dict:
    return get_translation_preparation_service(connection).start_preparation(
        int(job_id)
    )

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
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
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


def _parse_paragraph_translation_output(raw: str) -> tuple[str, object]:
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
        prose = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
        if prose and prose[0] not in "{[":
            translated = prose
    if not translated:
        if parsed:
            return "", notes
        raise ValueError("문단 번역 결과를 읽지 못했어요. 다시 「번역 진행」을 눌러 주세요.")
    return translated, notes


def _debug_log_paragraph_translation_raw(
    *,
    segment_id: int,
    source_text: str,
    raw: str,
    error: BaseException | None = None,
    attempt: int | None = None,
    retrying: bool = False,
    used_fallback: bool = False,
) -> None:
    """Temporary diagnostics for paragraph-translation parse failures. Remove later."""
    text = str(raw or "")
    stripped = text.strip()
    reasons: list[str] = []
    if not stripped:
        reasons.append("empty_or_whitespace")
    else:
        reasons.append(f"len={len(text)}")
        reasons.append(f"stripped_len={len(stripped)}")
        reasons.append(f"first={stripped[:1]!r}")
        if stripped[:1] in "{[":
            reasons.append("starts_with_json_bracket")
        try:
            json.loads(stripped)
            reasons.append("json_loads=ok")
        except json.JSONDecodeError as exc:
            reasons.append(
                f"json_loads={type(exc).__name__}:{exc.msg} pos={exc.pos}"
            )
        candidates = _candidate_json_objects(stripped)
        reasons.append(f"candidates={len(candidates)}")
        if candidates:
            keys = list(candidates[0].keys())
            reasons.append(f"first_keys={keys}")
            mapped = _translated_text_from_mapping(candidates[0])
            reasons.append(f"mapped_text_len={len(mapped or '')}")
    if attempt is not None:
        reasons.append(f"attempt={attempt}")
    if retrying:
        reasons.append("retrying=True")
    if used_fallback:
        reasons.append("used_fallback=True")
    header = (
        f"[translation-paragraph-debug] segment={segment_id} "
        f"source_len={len(source_text or '')} source={source_text!r} "
        f"error={type(error).__name__ if error else '-'} "
        f"error_msg={str(error) if error else '-'} "
        f"reasons={'; '.join(reasons)}"
    )
    print(header, flush=True)
    preview = stripped[:2000]
    print(f"[translation-paragraph-debug] raw_preview={preview!r}", flush=True)
    try:
        path = _data_dir() / "translation_paragraph_debug.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(header + "\n")
            handle.write("--- RAW START ---\n")
            handle.write(text)
            handle.write("\n--- RAW END ---\n\n")
    except OSError as write_error:
        print(
            f"[translation-paragraph-debug] log write failed: {write_error}",
            flush=True,
        )

PARAGRAPH_FALLBACK_NOTE = translation_batch_service.PARAGRAPH_FALLBACK_NOTE


def _translation_notes_as_list(notes: object) -> list:
    if isinstance(notes, list):
        return [item for item in notes if item not in (None, "")]
    if isinstance(notes, str) and notes.strip():
        return [{"note": notes.strip()}]
    return []


def _fallback_paragraph_translation(source_text: str, notes: object) -> tuple[str, list]:
    fallback_notes = _translation_notes_as_list(notes)
    fallback_notes.append(
        {"note": PARAGRAPH_FALLBACK_NOTE, "needs_manual_review": True}
    )
    return str(source_text or ""), fallback_notes

TRANSLATION_PARAGRAPH_BATCH_SIZE = (
    translation_batch_service.TRANSLATION_PARAGRAPH_BATCH_SIZE
)

TRANSLATION_BATCH_STRUCTURE_ATTEMPTS = (
    translation_batch_service.TRANSLATION_BATCH_STRUCTURE_ATTEMPTS
)


def _parse_paragraph_translation_batch_output(raw: str) -> list[dict]:
    try:
        parsed = _handler_cls()._extract_json_object(str(raw or "").strip())
    except (ValueError, json.JSONDecodeError, AttributeError, TypeError):
        return []
    items = parsed.get("paragraphs") if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        return []
    result: list[dict] = []
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
        result.append(
            {
                "id": segment_id,
                "translated_text": str(item.get("translated_text") or "").strip(),
                "translation_notes": notes,
            }
        )
    return result


def _paragraph_translation_batch_settings(
    connection: sqlite3.Connection,
    rows: list[sqlite3.Row],
    job: dict,
    *,
    style: dict,
    glossary: object,
    formatting: object,
) -> tuple[list[dict], dict]:
    first = rows[0]
    previous = translation_context.load_previous_translated_context(
        connection,
        int(job["id"]),
        before_chapter_number=int(first["chapter_number"]),
        before_segment_order=int(first["segment_order"]),
    )
    paragraphs = []
    for row in rows:
        relationship, mood = _load_segment_scene_tags_shared(
            connection, row["scene_context_id"]
        )
        paragraphs.append(
            {
                "id": int(row["id"]),
                "source_text": str(row["source_text"] or ""),
                "relationship_tag": relationship,
                "mood_tag": mood,
            }
        )
    settings = {
        "target_language": job.get("target_language") or "en",
        "tense": style.get("tense") or "",
        "character_voices": style.get("character_voices")
        or style.get("voices")
        or "",
        "proper_nouns_confirmed": glossary,
        "culture_localization_level": job.get("culture_localization_level")
        or "moderate",
        "narrative_formatting_rules": formatting,
        "previous_context_summary": previous,
    }
    return paragraphs, settings


def _store_paragraph_translation_batch(
    connection: sqlite3.Connection,
    rows: list[sqlite3.Row],
    translated_items: list[dict],
) -> list[dict]:
    empty_rows: list[sqlite3.Row] = []
    by_id = {int(row["id"]): row for row in rows}
    for item in translated_items:
        row = by_id[int(item["id"])]
        translated = str(item.get("translated_text") or "").strip()
        if not translated:
            empty_rows.append(row)
            continue
    repository = get_translation_segment_repository(connection)
    repository.save_translated_batch(
        int(rows[0]["translation_job_id"]) if rows else 0,
        translated_items,
    )
    repository.commit()
    return empty_rows


def proceed_translation_pipeline(connection: sqlite3.Connection, job_id: int) -> dict:
    """Translate remaining segments in order after proper nouns are confirmed."""
    _ensure_pipeline_schema(connection)
    _ensure_manual_review(connection)
    _ensure_polish_cols(connection)
    return get_translation_batch_service(connection).run_batch_translation(
        int(job_id)
    )


def generate_translation_submission_package(
    connection: sqlite3.Connection, job_id: int
) -> dict:
    """Build the target-language logline/synopsis package. Skip when already filled."""
    _ensure_pipeline_schema(connection)
    return get_translation_extras_service(
        connection
    ).generate_submission_package(int(job_id))


def get_translation_submission_result(
    connection: sqlite3.Connection, job_id: int
) -> dict:
    _ensure_pipeline_schema(connection)
    return get_translation_extras_service(connection).get_submission_result(
        int(job_id)
    )


def export_translation_submission_package(
    connection: sqlite3.Connection, job_id: int, format_key: str
):
    _ensure_pipeline_schema(connection)
    return get_translation_extras_service(connection).export_submission_package(
        int(job_id),
        format_key,
    )


def fetch_free_dictionary_payload(
    word: str, target_language: object = "en"
) -> tuple[int, object]:
    """Call Free Dictionary API once. Returns (status_code, parsed_json)."""
    return translation_extras_service.fetch_free_dictionary_payload(
        word, target_language
    )


def lookup_translation_dictionary(
    word: object,
    target_language: object = "en",
    segment_id: int | None = None,
) -> dict:
    with _database() as connection:
        return get_translation_extras_service(connection).lookup_word(
            segment_id, word, target_language
        )


def explain_translation_word_context(
    connection: sqlite3.Connection, body: dict | None
) -> dict:
    _ensure_word_cache(connection)
    return get_translation_extras_service(connection).explain_word_context(
        body
    )


def approve_translation_segment(
    connection: sqlite3.Connection, segment_id: int, body: dict | None
) -> dict:
    return get_translation_batch_service(connection).approve_segment(
        int(segment_id), body
    )


def approve_translation_chapter_segments(
    connection: sqlite3.Connection, job_id: int, chapter_number: int
) -> dict:
    return get_translation_batch_service(connection).approve_chapter_segments(
        int(job_id), int(chapter_number)
    )


def replace_translation_segment_text(
    connection: sqlite3.Connection, segment_id: int, body: dict | None
) -> dict:
    return get_translation_batch_service(connection).replace_translated_text(
        int(segment_id), body
    )


def retranslate_translation_segment(
    connection: sqlite3.Connection, segment_id: int
) -> dict:
    _ensure_pipeline_schema(connection)
    _ensure_manual_review(connection)
    return get_translation_batch_service(connection).retranslate_segment(
        int(segment_id)
    )


def polish_translation_chapter(
    connection: sqlite3.Connection, job_id: int, chapter_number: int
) -> dict:
    """Review one approved English chapter and store per-paragraph proposals."""
    _ensure_polish_cols(connection)
    return get_translation_batch_service(connection).run_chapter_polish(
        int(job_id), int(chapter_number)
    )


def choose_translation_segment_polish(
    connection: sqlite3.Connection, segment_id: int, body: dict | None
) -> dict:
    _ensure_polish_cols(connection)
    return get_translation_batch_service(connection).choose_polish(
        int(segment_id), body
    )


def apply_all_translation_chapter_polish(
    connection: sqlite3.Connection, job_id: int, chapter_number: int
) -> dict:
    _ensure_polish_cols(connection)
    return get_translation_batch_service(connection).apply_all_polish(
        int(job_id), int(chapter_number)
    )


def post_translation_chat(connection: sqlite3.Connection, body: dict | None) -> dict:
    return get_translation_extras_service(connection).answer_qa_payload(body)

TRANSLATION_GEMINI_GAP_SECONDS = (
    translation_batch_service.TRANSLATION_GEMINI_GAP_SECONDS
)

TRANSLATION_RATE_LIMIT_RETRIES = (
    translation_batch_service.TRANSLATION_RATE_LIMIT_RETRIES
)

TRANSLATION_RATE_LIMIT_DEFAULT_WAIT = (
    translation_batch_service.TRANSLATION_RATE_LIMIT_DEFAULT_WAIT
)


# dispatch filled after extract
