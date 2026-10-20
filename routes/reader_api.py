"""가상 독자 HTTP API. app.py는 라우트만 연결한다.

페르소나·즐겨찾기·독자 채팅/토론·장면 독자 댓글 생성까지 포함한다.
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
import time
import uuid
from http import HTTPStatus
from threading import Lock, Thread
from urllib.parse import parse_qs, unquote, urlparse

import gemini_client
import genre_clusters
import world_import_analysis


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


def _is_quota_error(error: object) -> bool:
    import app

    return app.is_gemini_quota_error(error)


def _handler_cls():
    import app

    return app.SuperToryHandler


READER_PERSONA_CATEGORIES = (
    "genre_specialist",
    "sub_genre_specialist",
    "taste_preference",
    "narrative_critic",
    "structure_wildcard",
)

READER_FAVORITE_MAX = 6

READER_CHAT_HISTORY_LIMIT = 20

READER_CHAT_EPISODE_CAP = 12000

READER_CHAT_SYNOPSIS_CAP = 1500

READER_CHAT_WORLD_CAP = 2000

READER_DEBATE_MIN_PERSONAS = 3

READER_DEBATE_MAX_PERSONAS = 5

READER_DEBATE_TASK_ADDON = (
    "다른 독자들의 이전 발언을 참고해서, 동의하거나 다른 관점을 제시하며 "
    "자연스럽게 토론에 참여해라. 이미 나온 얘기를 그대로 반복하지 마라."
)

READER_COMMENTS_EXPECTED = 3

READER_COMMENTS_MAX_REFRESHES = 3

READER_COMMENT_JOKER_CHANCE = 0.25

_GENRE_NEEDLE_ALIASES: dict[str, tuple[str, ...]] = {
    "romfant": ("로판", "로맨스·로판"),
    "high": ("하이루판",),
}

READER_COMMENT_WILDCARD_CATEGORIES = (
    "narrative_critic",
    "taste_preference",
    "structure_wildcard",
)

READER_COMMENT_TASK_ADDON = """지금은 작가와 1:1로 대화하는 상황이 아닙니다.
작가가 이 회차를 완성으로 표시했습니다.
위의 1:1 대화 지시(이유를 설명하라, 정중하게 반응하라 등)는 이번 작업에 적용하지 마세요.
형식만 짧고 즉흥적인 댓글창 한 줄로 맞추고, 말투·어휘·감정 온도는 위에서 정의된 이 독자의 [말투]·[정체성]·[말투 예시]를 우선하세요.
ㅋㅋ/ㄹㅇ/이모지를 모든 댓글에 넣지 마세요. 이 독자의 말투에 맞을 때만 쓰세요.

[Task Instruction — 이번 작업이 우선]
이 회차를 평가하지 마세요. 웹소설 플랫폼 댓글창에 이 독자가 남길 법한 댓글 하나를 쓰세요.
다른 독자와 비슷한 유행어·말투로 평평하게 만들지 마세요.

[댓글 작성 규칙 — 형식은 공통, 말투는 페르소나마다 다름]
- 1~2문장. 짧고 즉흥적으로. 제목·이름 접두어·따옴표·머리말은 붙이지 마세요.
- 존댓말/정중한 피드백 톤 금지. 리뷰어처럼 정리하지 마세요.
- '좋았어요', '기대됩니다', '잘 읽었습니다', '다음 전개가 기대돼요', '이 부분이 좋았어요' 같은 정중한 감상평/피드백 문구는 쓰지 마세요.
- 특정 장면·대사·행동을 구체적으로 집어서 반응하세요. 막연한 감상은 금지입니다.
- [말투]와 [말투 예시]의 결을 댓글에도 적극적으로 살리세요. 문장을 그대로 베끼지는 마세요.
  · 날카롭고 분석적인 독자: ㅋㅋ/이모지 거의 없이, 팩폭하듯 단정적으로.
  · 감정 이입·취향형 독자: 감탄사, ㅠㅠ/ㅋㅋㅋ를 쓰고 흥분한 톤.
  · 구조·전개를 파고드는 독자: 복선/떡밥 회수 여부를 구체적으로 짚되, 분석하듯 살짝 냉정하게.
- 웹소설 독자 커뮤니티 표현(떡밥, 복선, 회수, 이탈, 흑화, 개연성 터짐, 실화냐, 인성 등)은 이 독자의 관심사·말투와 맞을 때만 쓰세요.
- [프로젝트 누적 정보]에 이전 화 떡밥·복선·타임라인·추적 사실이 있으면, 이번 화에서 회수되거나 어긋난 부분을 이 독자의 관심사에 맞게 녹여도 됩니다. 목록에 없는 설정은 지어내지 마세요.
- 다른 가상 독자나 AI라는 사실을 언급하지 마세요.

[형식 참고 예시 — 페르소나마다 결이 다릅니다. 이 독자의 [말투]에 가까운 결만 참고하고, 문장을 그대로 베끼지 마세요]
- (분석적) 이 전개 그냥 개연성 터진 거 아님?
- (취향·감정) 남주 인성 실화냐? 여기서 여주 버리면 진짜 이탈함
- (구조·복선) 다음 화에 주인공 흑화할 듯? 떡밥 정리해 둠
- (흥분한 회수) ㅋㅋ 3화에 나왔던 열쇠 복선 여기서 회수하네 미쳤다"""

READER_GENRE_SPECIALIST_CATEGORIES = frozenset({
    "genre_specialist",
    "sub_genre_specialist",
})

READER_EXPLICIT_GENRE_PERSONA_IDS = frozenset({"academy_immersive"})

READER_PERSONA_SPECIALTY: dict[str, tuple[str, frozenset[str]]] = {
    "roppan_cider": (
        "로맨스·로판",
        frozenset({
            "romance", "romfant", "modern", "period", "romcom", "high", "low",
            "office", "contract", "chaebol", "bl", "gl", "omega",
            "로맨스", "로판", "현대로맨스", "시대로맨스", "로코", "하이루판",
            "저루판", "오피스", "재벌", "BL", "GL",
        }),
    ),
    "roppan_narrative": (
        "로맨스·로판",
        frozenset({
            "romance", "romfant", "modern", "period", "romcom", "high", "low",
            "office", "contract", "chaebol", "bl", "gl", "omega",
            "로맨스", "로판", "현대로맨스", "시대로맨스", "로코", "하이루판",
            "저루판", "재벌", "BL", "GL",
        }),
    ),
    "modern_romance_flutter": (
        "로맨스·로판",
        frozenset({
            "romance", "romfant", "modern", "period", "romcom", "high", "low",
            "office", "contract", "chaebol",
            "로맨스", "로판", "현대로맨스", "시대로맨스", "로코", "하이루판",
            "저루판",
        }),
    ),
    "modern_romance_tension": (
        "로맨스·로판",
        frozenset({
            "romance", "romfant", "modern", "period", "romcom", "high", "low",
            "office", "contract", "chaebol",
            "로맨스", "로판", "현대로맨스", "시대로맨스", "로코", "하이루판",
            "저루판",
        }),
    ),
    "modern_fantasy_pro": (
        "현판·전문직·재벌물",
        frozenset({
            "urban", "chaebol", "office", "contemporary",
            "현판", "현대판타지", "어반판타지", "재벌", "오피스", "전문직",
        }),
    ),
    "hunter_speedrunner": (
        "헌터물",
        frozenset({
            "urban", "hidden_world",
            "헌터", "헌터물", "현대판타지", "어반판타지", "레이드", "던전", "각성",
        }),
    ),
    "game_system_maniac": (
        "게임물",
        frozenset({"game", "게임", "게임판타지", "게임물", "시스템"}),
    ),
    "high_fantasy_adventurer": (
        "정통 판타지",
        frozenset({
            "traditional", "isekai", "dark", "female",
            "정통판타지", "이세계", "다크판타지", "여성향 판타지",
        }),
    ),
    "wuxia_romantic": (
        "무협",
        frozenset({
            "martial", "classic", "new", "modern_wu", "murim",
            "무협", "정통무협", "신무협", "강호",
        }),
    ),
    "sf_hardcore_critic": (
        "SF",
        frozenset({
            "sf", "SF", "space", "dystopia", "cyberpunk", "timeslip", "postapo",
            "스페이스", "디스토피아", "사이버펑크", "타임슬립", "아포칼립스",
        }),
    ),
    "mystery_trick_analyst": (
        "추리·스릴러",
        frozenset({
            "mystery", "thriller", "honkaku", "social", "cozy", "legal", "crime",
            "psycho", "horror", "suspense",
            "추리", "스릴러", "미스터리", "호러", "본격추리", "서스펜스",
        }),
    ),
    "sports_sim_fan": (
        "스포츠·영지경영",
        frozenset({
            "sports", "스포츠", "스포츠물", "영지", "시뮬레이션", "경영", "리그",
        }),
    ),
    "alt_history_analyst": (
        "대체역사",
        frozenset({
            "alt_history", "alt", "대체역사",
        }),
    ),
    "academy_immersive": (
        "아카데미물",
        frozenset({
            "school", "youth", "academy",
            "아카데미", "아카데미물", "학원", "학원물",
        }),
    ),
}

READER_AVATAR_URL_PREFIX = "/assets/reader_avatars"

_reader_comments_inflight: set[int] = set()

_reader_comments_inflight_batch: dict[int, str] = {}

_reader_comments_last_error: dict[int, str] = {}

_reader_comments_inflight_lock = Lock()


def _as_string_list(value: object) -> list[str]:
    """Parse a JSON array (or already-decoded list) into non-empty strings."""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return [text]
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [text]


def serialize_reader_persona(row: sqlite3.Row | dict) -> dict:
    item = dict(row)
    item["criteria"] = _as_string_list(item.get("criteria"))
    item["sample_responses"] = _as_string_list(item.get("sample_responses"))
    item["display_order"] = int(item.get("display_order") or 0)
    return item


def reader_chat_session_key(work_id: object, persona_id: object) -> str:
    return f"reader_chat_{work_id}_{persona_id}"


def parse_reader_debate_persona_ids(raw: object) -> list[str]:
    """Normalize persona id list (array or comma-separated), preserving order."""
    if isinstance(raw, str):
        parts = [part.strip() for part in raw.split(",") if part.strip()]
    elif isinstance(raw, (list, tuple)):
        parts = [str(part).strip() for part in raw if str(part).strip()]
    else:
        parts = []
    return parts


def reader_debate_persona_ids_key(persona_ids: list[str]) -> str:
    """Sorted comma key for session reuse (order-independent lookup)."""
    return ",".join(sorted({str(pid).strip() for pid in persona_ids if str(pid).strip()}))


def _reader_debate_system_prompt(
    persona: dict,
    shared_context: str,
    *,
    main_genre: object = "",
    sub_genre: object = "",
    genre_detail: object = "",
) -> str:
    """Reuse 1:1 persona prompt + debate task line + shared work context."""
    return (
        _reader_persona_system_prompt(persona)
        + "\n"
        + READER_DEBATE_TASK_ADDON
        + _reader_genre_mismatch_block(
            persona,
            main_genre=main_genre,
            sub_genre=sub_genre,
            genre_detail=genre_detail,
            shared_context=shared_context,
        )
        + "\n\n"
        + shared_context
    )


def _reader_debate_user_prompt(
    reader_name: str, transcript: str, user_message: str
) -> str:
    return (
        f"아래는 작가와 여러 가상 독자의 토론, 그리고 작가의 새 메시지입니다. "
        f"당신은 '{reader_name}'로서 새 메시지와 다른 독자들의 발언에 이어서 답하세요. "
        f"이름 접두어는 붙이지 마세요.\n\n"
        f"[지금까지의 토론]\n{transcript}\n\n"
        f"[작가의 새 메시지]\n{user_message}"
    )


def _reader_debate_transcript_lines(
    rows: list[dict], persona_names: dict[str, str]
) -> list[str]:
    lines: list[str] = []
    for item in rows:
        content = str(item.get("message") or "").strip()
        if not content:
            continue
        if len(content) > 4000:
            content = content[:4000] + "…"
        speaker = str(item.get("speaker_type") or "").strip().lower()
        if speaker == "user":
            who = "작가"
        else:
            pid = str(item.get("persona_id") or "").strip()
            who = persona_names.get(pid) or "가상 독자"
        lines.append(f"{who}: {content}")
    return lines


def _reader_persona_system_prompt(persona_row: dict) -> str:
    """Build a 1:1 virtual-reader system prompt from a single persona row.

    Only this reader's fields are injected. Other personas and Tory's
    Core Identity must never appear here.
    """
    persona = serialize_reader_persona(persona_row)
    name = str(persona.get("name") or "").strip() or "가상 독자"
    identity = str(persona.get("identity") or "").strip() or "(없음)"
    tone = str(persona.get("tone") or "").strip() or "(없음)"
    forbidden = str(persona.get("forbidden") or "").strip() or "(없음)"
    criteria = persona.get("criteria") or []
    if criteria:
        criteria_block = "\n".join(
            f"{index}. {line}" for index, line in enumerate(criteria, start=1)
        )
    else:
        criteria_block = "(없음)"
    samples = (persona.get("sample_responses") or [])[:3]
    if samples:
        sample_block = "\n".join(f"- {line}" for line in samples)
    else:
        sample_block = "(없음)"
    prompt = (
        "[Core Identity]\n"
        f"당신은 '{name}'이라는 이름의 가상 독자입니다.\n\n"
        "[정체성]\n"
        f"{identity}\n\n"
        "[말투]\n"
        f"{tone}\n\n"
        "[평가 우선순위]\n"
        "아래 순서는 이 독자가 작품을 볼 때 더 중요하게 여기는 기준입니다. "
        "앞번호일수록 판단에 더 큰 비중을 두세요.\n"
        f"{criteria_block}\n\n"
        "[금지사항]\n"
        f"{forbidden}\n\n"
        "[말투 예시]\n"
        "아래는 이 독자의 말투·반응 방식을 참고하기 위한 예시입니다. "
        "문장을 그대로 따라 말하지 말고, 톤과 반응의 결만 참고하세요.\n"
        f"{sample_block}\n\n"
        "중요: 당신은 이 독자 한 명뿐입니다. 다른 가상 독자의 취향·말투·평가 기준을 "
        "끌어오거나 언급하지 마세요. 토리(SuperTORY 조력자)가 아니며, "
        "편집자·비평가 페르소나를 섞지 마세요.\n\n"
        "[Task Instruction]\n"
        "당신은 지금 작가와 1:1로 대화 중입니다. 작가가 보여주는 원고나 질문에 대해 "
        "위에서 정의된 당신의 캐릭터를 유지하며 반응하세요.\n"
        "당신은 AI라는 사실을 언급하지 말고, 실제 독자처럼 자연스럽게 반응하세요.\n"
        "평가 우선순위에 따라 반응하되, 항상 그 이유를 함께 설명하세요.\n"
        "제공된 시놉시스·세계관·원고에 명시되지 않은 플롯 장치나 설정(예: 회빙환, 빙의, 환생, "
        "숨겨진 정체 등)을 임의로 가정해서 언급하지 마라. 확인되지 않은 부분에 대해서는 "
        "'본문/설정에 안 나와서 모르겠다' 또는 '~인가요?'처럼 되묻는 방식으로 반응해라."
    )
    persona_id = str(persona.get("id") or "").strip()
    # 개연성·복선 설계 분석가만 누적 인덱스 떡밥/추적사실을 우선 점검한다.
    if persona_id == "plausibility_absolutist":
        prompt += (
            "\n"
            "[아직 안 풀린 떡밥/복선 목록]과 [추적 중인 설정/인물 사실]을 우선적으로 참고해서, "
            "지금 첨부된 장면이나 시놉시스가 이 목록의 항목들과 모순되지 않는지, "
            "너무 오래 방치된 떡밥은 없는지 짚어라. 목록에 없는 새로운 설정이 "
            "갑자기 나왔다면 그것도 짚어라."
        )
    return prompt


def _reader_filled_worldbuilding_summary(worldbuilding_md: object) -> str:
    """Reuse world_import_analysis sheet schema: only non-empty sheet fields."""
    try:
        values = world_import_analysis.parse_worldbuilding_md(worldbuilding_md)
    except Exception:
        return ""
    lines: list[str] = []
    for section in world_import_analysis.WORLD_BUILDING_SCHEMA:
        filled: list[str] = []
        for field_id, label in section["fields"]:
            raw = values.get(field_id)
            if world_import_analysis.is_field_empty(raw):
                continue
            text = _plain_text(str(raw or "")).strip()
            if not text:
                continue
            if len(text) > 220:
                text = text[:220] + "…"
            filled.append(f"- {label}: {text}")
        if filled:
            lines.append(str(section["title"]))
            lines.extend(filled)
    legacy = values.get("legacy") if isinstance(values, dict) else ""
    if not world_import_analysis.is_field_empty(legacy):
        text = _plain_text(str(legacy or "")).strip()
        if text:
            if len(text) > 400:
                text = text[:400] + "…"
            lines.append("기타 · 기존 메모")
            lines.append(f"- {text}")
    summary = "\n".join(lines).strip()
    if len(summary) > READER_CHAT_WORLD_CAP:
        summary = summary[:READER_CHAT_WORLD_CAP] + "…"
    return summary


def _reader_project_index_block(work_id: object) -> str:
    """Format existing project_index row for virtual-reader context (read-only)."""
    try:
        project_id = int(str(work_id or "").strip())
    except (TypeError, ValueError):
        return ""
    if project_id <= 0:
        return ""
    try:
        with _database() as connection:
            row = connection.execute(
                "SELECT characters_json, world_rules_json, timeline_json, "
                "open_threads_json, tracked_facts_json "
                "FROM project_index WHERE project_id = ?",
                (project_id,),
            ).fetchone()
    except sqlite3.OperationalError:
        return ""
    if row is None:
        return ""

    parse_list = _handler_cls()._parse_json_list
    normalize_facts = _handler_cls()._normalize_tracked_facts
    characters = [
        str(item).strip()
        for item in parse_list(row["characters_json"])
        if str(item).strip()
    ]
    world_rules = [
        str(item).strip()
        for item in parse_list(row["world_rules_json"])
        if str(item).strip()
    ]
    timeline = [
        str(item).strip()
        for item in parse_list(row["timeline_json"])
        if str(item).strip()
    ]
    open_threads = _handler_cls()._open_thread_unresolved_texts(row["open_threads_json"])
    # tracked_facts_json NULL (pre-048) → normalize returns []; omit quietly
    tracked_raw = None
    try:
        tracked_raw = row["tracked_facts_json"]
    except (KeyError, IndexError, TypeError):
        tracked_raw = None
    if tracked_raw is None:
        tracked_facts: list = []
    else:
        tracked_facts = normalize_facts(tracked_raw)

    sections: list[str] = []
    if timeline:
        sections.append(
            "[지금까지 전개 - 실제 원고 문장 아님, 요약임]\n"
            + "\n".join(f"- {line}" for line in timeline)
        )
    if open_threads:
        sections.append(
            "[아직 안 풀린 떡밥/복선 목록]\n"
            + "\n".join(f"- {line}" for line in open_threads)
        )
    if tracked_facts:
        fact_lines: list[str] = []
        for fact in tracked_facts:
            if not isinstance(fact, dict):
                continue
            category = str(fact.get("category") or "").strip()
            subject = str(fact.get("subject") or "").strip()
            attribute = str(fact.get("attribute") or "").strip()
            value = str(fact.get("value") or "").strip()
            since = str(fact.get("since_scene") or "").strip()
            head = " / ".join(part for part in (subject, attribute) if part)
            bits = []
            if category:
                bits.append(f"[{category}]")
            if head:
                bits.append(head)
            if value:
                bits.append(f"= {value}")
            line = " ".join(bits).strip()
            if since:
                line = f"{line} (since: {since})" if line else f"(since: {since})"
            if line:
                fact_lines.append(f"- {line}")
        if fact_lines:
            sections.append(
                "[추적 중인 설정/인물 사실]\n" + "\n".join(fact_lines)
            )
    meta_bits: list[str] = []
    if characters:
        meta_bits.append("등장 인물: " + ", ".join(characters))
    if world_rules:
        meta_bits.append(
            "확립된 세계관·설정:\n" + "\n".join(f"- {rule}" for rule in world_rules)
        )
    if meta_bits:
        sections.append("\n".join(meta_bits))
    if not sections:
        return ""
    return (
        "[프로젝트 누적 정보]\n"
        "아래는 회차 인덱스를 모아 둔 내부 요약입니다. 실제 원고 문장이 아니며, "
        "설정집 시놉시스와도 별개입니다.\n\n"
        + "\n\n".join(sections)
    )


def _reader_dynamic_context(
    work_id: object, episode_content: str | None = None
) -> str:
    """Live work genre + settings + project_index (+ optional shared manuscript)."""
    work_key = str(work_id or "").strip()
    title = ""
    main_genre = ""
    sub_genre = ""
    genre_detail = ""
    synopsis_plain = ""
    world_summary = ""
    if work_key:
        with _database() as connection:
            try:
                row = connection.execute(
                    "SELECT title, main_genre, sub_genre, genre_detail, "
                    "description_md, worldbuilding_md "
                    "FROM project WHERE id = ? AND deleted_at IS NULL",
                    (work_key,),
                ).fetchone()
            except sqlite3.OperationalError:
                row = connection.execute(
                    "SELECT title, main_genre, sub_genre FROM project "
                    "WHERE id = ? AND deleted_at IS NULL",
                    (work_key,),
                ).fetchone()
        if row is not None:
            title = str(row["title"] or "").strip()
            main_genre = str(row["main_genre"] or "").strip()
            sub_genre = str(row["sub_genre"] or "").strip()
            try:
                genre_detail = str(row["genre_detail"] or "").strip()
            except (KeyError, IndexError, TypeError):
                genre_detail = ""
            try:
                synopsis_plain = _plain_text(
                    str(row["description_md"] or "")
                ).strip()
            except (KeyError, IndexError, TypeError):
                synopsis_plain = ""
            try:
                world_summary = _reader_filled_worldbuilding_summary(
                    row["worldbuilding_md"]
                )
            except (KeyError, IndexError, TypeError):
                world_summary = ""
    if synopsis_plain and len(synopsis_plain) > READER_CHAT_SYNOPSIS_CAP:
        synopsis_plain = synopsis_plain[:READER_CHAT_SYNOPSIS_CAP] + "…"
    main_label = _handler_cls()._genre_display_label(None, main_genre)
    sub_label = _handler_cls()._genre_display_label(None, sub_genre)
    detail_label = ""
    if genre_detail:
        try:
            detail_label = str(genre_clusters.genre_detail_label(genre_detail) or "").strip()
        except Exception:
            detail_label = ""
        if not detail_label:
            detail_label = _handler_cls()._genre_display_label(None, genre_detail)
    parts = [
        "[작품 정보]",
        f"작품 제목: {title or '(없음)'}",
        f"메인 장르: {main_label}",
        f"서브 장르: {sub_label}",
    ]
    if detail_label and detail_label not in {"미정", "기타"}:
        parts.append(f"세부장르: {detail_label}")
    # 장르 → 시놉시스 → 세계관 → 누적 인덱스 → 원고
    settings_bits: list[str] = []
    if synopsis_plain:
        settings_bits.append(f"시놉시스 요약:\n{synopsis_plain}")
    if world_summary:
        settings_bits.append(f"세계관 설정 요약(채워진 항목만):\n{world_summary}")
    if settings_bits:
        parts.append("")
        parts.append(
            "[작품 설정 요약 — 설정집 정보이며 실제 원고 문장이 아님]\n"
            "아래는 작가가 설정집에 적어 둔 요약입니다. 본문 장면처럼 인용하거나 "
            "이 내용만으로 장면이 이미 쓰인 것처럼 단정하지 마세요.\n"
            + "\n\n".join(settings_bits)
        )
    index_block = _reader_project_index_block(work_key)
    if index_block:
        parts.append("")
        parts.append(index_block)
    manuscript = str(episode_content or "").strip()
    if manuscript:
        if len(manuscript) > READER_CHAT_EPISODE_CAP:
            manuscript = manuscript[:READER_CHAT_EPISODE_CAP] + "…"
        parts.append("")
        parts.append(f"다음은 작가가 공유한 원고 내용입니다:\n{manuscript}")
    else:
        parts.append("")
        parts.append(
            "[원고 미첨부]\n"
            "지금은 특정 장면 원고 없이, 시놉시스/세계관·누적 인덱스 정보만 가지고 "
            "대화하는 상황이다. "
            "구체적인 장면 평가를 요구받으면 '원고를 보여주시면 더 정확하게 봐드릴 수 있어요' "
            "같은 식으로 자연스럽게 유도해라."
        )
    return "\n".join(parts)

# Keep in sync with app.IMPORT_ANALYSIS_GEMINI_GAP_SECONDS (routes load before that constant).
READER_DEBATE_GEMINI_GAP_SECONDS = 1.8


def reader_persona_avatar_url(persona_id: object) -> str:
    token = str(persona_id or "").strip()
    if not token:
        return f"{READER_AVATAR_URL_PREFIX}/.png"
    return f"{READER_AVATAR_URL_PREFIX}/{token}.png"


def _genre_search_needles(raw_key: object) -> list[str]:
    """Keys/labels to look for inside persona name + identity."""
    key = str(raw_key or "").strip()
    needles: list[str] = []
    if not key:
        return needles
    if key.startswith("custom:"):
        custom = key[len("custom:") :].strip()
        if custom:
            needles.append(custom)
    else:
        needles.append(key)
        label = _handler_cls()._genre_display_label(None, key)
        if label and label not in {"미정", "기타"} and label not in needles:
            needles.append(label)
        # Persona names use short forms (로판, 하이루판) that the full label misses.
        for alias in _GENRE_NEEDLE_ALIASES.get(key, ()):
            if alias not in needles:
                needles.append(alias)
    return [item for item in needles if item]


def _persona_matches_needles(persona: dict, needles: list[str]) -> bool:
    if not needles:
        return False
    haystack = f"{persona.get('name') or ''}\n{persona.get('identity') or ''}"
    lowered = haystack.lower()
    for needle in needles:
        text = str(needle or "").strip()
        if not text:
            continue
        if text in haystack or text.lower() in lowered:
            return True
    return False


def _reader_persona_has_explicit_genre(persona: dict) -> bool:
    category = str(persona.get("category") or "").strip()
    persona_id = str(persona.get("id") or "").strip()
    return (
        category in READER_GENRE_SPECIALIST_CATEGORIES
        or persona_id in READER_EXPLICIT_GENRE_PERSONA_IDS
    )


def _reader_persona_specialty(persona: dict) -> tuple[str, frozenset[str]] | None:
    persona_id = str(persona.get("id") or "").strip()
    mapped = READER_PERSONA_SPECIALTY.get(persona_id)
    if mapped:
        return mapped
    if not _reader_persona_has_explicit_genre(persona):
        return None
    name = str(persona.get("name") or "").strip()
    return (name or "자기 전문 장르", frozenset())


def _reader_genre_token_hits(token: str, work_tokens: set[str]) -> bool:
    raw = str(token or "").strip()
    if not raw:
        return False
    lowered = raw.lower()
    work_lower = {item.lower() for item in work_tokens if item}
    if lowered in work_lower:
        return True
    # ASCII keys must match exactly so "modern" does not hit "modern_wu".
    if raw.isascii():
        return False
    for item in work_tokens:
        other = str(item or "").strip()
        if len(raw) < 2 or len(other) < 2:
            continue
        if lowered in other.lower() or other.lower() in lowered:
            return True
    return False


def _reader_work_genre_tokens(
    main_genre: object = "",
    sub_genre: object = "",
    genre_detail: object = "",
    extra_labels: object = None,
) -> list[str]:
    skip = {"", "미정", "기타", "(없음)", "tbd", "other"}
    tokens: list[str] = []

    def _add(raw: object) -> None:
        text = str(raw or "").strip()
        if not text or text in skip:
            return
        if text not in tokens:
            tokens.append(text)

    for key in (main_genre, sub_genre, genre_detail):
        _add(key)
        _add(_handler_cls()._genre_display_label(None, key))
    detail_key = str(genre_detail or "").strip()
    if detail_key:
        try:
            _add(genre_clusters.genre_detail_label(detail_key))
        except Exception:
            pass
    if extra_labels:
        for label in extra_labels:
            _add(label)
    return tokens


def _reader_context_genre_labels(shared_context: str) -> list[str]:
    labels: list[str] = []
    for line in str(shared_context or "").splitlines():
        stripped = line.strip()
        for prefix in ("메인 장르:", "서브 장르:", "세부장르:", "세부 장르:"):
            if stripped.startswith(prefix):
                labels.append(stripped[len(prefix) :].strip())
                break
    return labels


def _reader_work_genre_label(tokens: list[str]) -> str:
    skip_keys = {
        "romance", "fantasy", "sf", "mystery", "thriller", "historical", "martial",
        "contemporary", "youth", "literary", "romfant", "modern", "high", "low",
        "urban", "hidden_world", "traditional", "isekai", "game", "dark", "school",
        "classic", "new", "murim", "sports", "chaebol", "office", "blgl", "period",
    }
    labels = [
        item for item in tokens
        if item and item not in skip_keys and not item.isascii()
    ]
    if not labels:
        labels = [item for item in tokens if item]
    unique: list[str] = []
    for item in labels:
        if item not in unique:
            unique.append(item)
    return " · ".join(unique[:3]) if unique else "미정"


def reader_persona_genre_matches(
    persona: dict,
    *,
    main_genre: object = "",
    sub_genre: object = "",
    genre_detail: object = "",
    shared_context: str = "",
) -> bool | None:
    """True=match, False=mismatch, None=not a genre-explicit persona or no work genre."""
    if not _reader_persona_has_explicit_genre(persona):
        return None
    specialty = _reader_persona_specialty(persona)
    if specialty is None:
        return None
    work_tokens = _reader_work_genre_tokens(
        main_genre,
        sub_genre,
        genre_detail,
        extra_labels=_reader_context_genre_labels(shared_context),
    )
    if not work_tokens:
        return None
    _expected, specialty_tokens = specialty
    if not specialty_tokens:
        needles = _genre_search_needles(main_genre) + _genre_search_needles(sub_genre)
        return _persona_matches_needles(persona, needles)
    work_set = set(work_tokens)
    return any(_reader_genre_token_hits(token, work_set) for token in specialty_tokens)


def _reader_genre_mismatch_block(
    persona: dict,
    *,
    main_genre: object = "",
    sub_genre: object = "",
    genre_detail: object = "",
    shared_context: str = "",
) -> str:
    matched = reader_persona_genre_matches(
        persona,
        main_genre=main_genre,
        sub_genre=sub_genre,
        genre_detail=genre_detail,
        shared_context=shared_context,
    )
    if matched is not False:
        return ""
    specialty = _reader_persona_specialty(persona) or ("자기 전문 장르", frozenset())
    expected, _tokens = specialty
    actual = _reader_work_genre_label(
        _reader_work_genre_tokens(
            main_genre,
            sub_genre,
            genre_detail,
            extra_labels=_reader_context_genre_labels(shared_context),
        )
    )
    return (
        "\n\n[장르 불일치 인지 — 이번 작품에만 적용]\n"
        f"이 작품의 실제 장르는 '{actual}'이다. 당신은 평소 '{expected}'를 주로 읽는 독자다.\n"
        f"이 작품을 '{expected}'라고 단정하지 마라. '{expected}' 클리셰·문법이 안 나온다고 "
        "작품 오류처럼 말하지 마라.\n"
        "당신이 기대와 다르다는 걸 스스로 아는 기색이 한 번 묻어나면 된다. "
        "문장을 고정하지 말고, 매번 다른 표현을 쓰며, 이전에 썼던 대사를 반복하지 마라. "
        "아래는 톤의 결만 참고하는 방향이다. 예시 문장을 그대로 베끼지 마라.\n"
        f"- 제목이나 초반 설정만 보고 '{expected}'인 줄 알았다가, 읽고 나서 아니라는 걸 깨닫는 쪽\n"
        "- '어라, 내가 생각하던 분위기랑은 다르네' 정도의 가벼운 당황\n"
        f"- '{expected}' 쪽 용어를 꺼냈다가 스스로 정정하는 쪽\n"
        "- 대놓고 착각을 시인하지 않고 '기대한 맛은 아닌데'로 돌려 말하는 쪽\n"
        "그 다음엔 헛소리를 하지 말고, 원고에서 눈에 띄는 한 장면·대사·행동만 소박하게 반응해라. "
        f"'{expected}' 전문가인 척 장르 문법을 들이밀며 비평하지 마라. "
        f"인지한 뒤에는 '{expected}' 클리셰가 안 나온다고 아쉬워하거나 그걸 요구하지 마라. "
        "댓글이면 1~2문장 안에 이 기색을 녹이고, 장르 불일치 설명만으로 채우지 마라.\n"
    )


def _normalize_exclude_persona_ids(raw: object) -> set[str]:
    if raw is None:
        return set()
    if isinstance(raw, (str, bytes)):
        token = str(raw).strip()
        return {token} if token else set()
    try:
        items = list(raw)
    except TypeError:
        token = str(raw).strip()
        return {token} if token else set()
    return {str(item or "").strip() for item in items if str(item or "").strip()}


def _load_virtual_reader_personas(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        "SELECT id, category, name, identity, tone, criteria, forbidden, "
        "sample_responses, discussion_attitude, display_order "
        "FROM virtual_reader_personas "
        "ORDER BY display_order, id"
    ).fetchall()
    return [serialize_reader_persona(row) for row in rows]


def reader_comment_refresh_count(comment_count: object) -> int:
    n = max(0, int(comment_count or 0))
    if n <= READER_COMMENTS_EXPECTED:
        return 0
    return (n - READER_COMMENTS_EXPECTED) // READER_COMMENTS_EXPECTED


def _reader_comment_refresh_meta(
    comment_count: object,
    *,
    generating: bool,
    remaining: object,
    last_error_code: object = None,
    batch_count: object = 0,
) -> dict:
    n = max(0, int(comment_count or 0))
    batches = max(0, int(batch_count or 0))
    refresh_count = max(0, batches - 1) if batches else reader_comment_refresh_count(n)
    can_refresh = (
        n >= READER_COMMENTS_EXPECTED
        and not generating
        and str(last_error_code or "") != "quota"
    )
    return {
        "refresh_count": refresh_count,
        "max_refreshes": READER_COMMENTS_MAX_REFRESHES,
        "can_refresh": can_refresh,
    }


def _pick_reader_comment_persona(
    pool: list[dict],
    used_ids: set[str],
    rng: random.Random,
    needles: list[str] | None = None,
) -> dict | None:
    available = [
        item
        for item in pool
        if str(item.get("id") or "").strip() not in used_ids
    ]
    if not available:
        return None
    if needles:
        matched = [item for item in available if _persona_matches_needles(item, needles)]
        if matched:
            return rng.choice(matched)
    return rng.choice(available)


def select_reader_comment_personas(
    main_genre: object,
    sub_genre: object,
    *,
    connection: sqlite3.Connection | None = None,
    rng: random.Random | None = None,
    exclude_persona_ids: object = None,
) -> list[dict]:
    """Pick 3 distinct virtual readers: genre, sub-genre, then a wildcard."""
    picker = rng if rng is not None else random.Random()

    if connection is None:
        with _database() as conn:
            personas = _load_virtual_reader_personas(conn)
    else:
        personas = _load_virtual_reader_personas(connection)

    by_category: dict[str, list[dict]] = {key: [] for key in READER_PERSONA_CATEGORIES}
    for item in personas:
        category = str(item.get("category") or "")
        by_category.setdefault(category, []).append(item)

    picked: list[dict] = []
    used_ids: set[str] = _normalize_exclude_persona_ids(exclude_persona_ids)
    genre_pick = _pick_reader_comment_persona(
        by_category.get("genre_specialist") or [],
        used_ids,
        picker,
        _genre_search_needles(main_genre),
    )
    if genre_pick is not None:
        picked.append(genre_pick)
        used_ids.add(str(genre_pick.get("id") or ""))
    sub_pick = _pick_reader_comment_persona(
        by_category.get("sub_genre_specialist") or [],
        used_ids,
        picker,
        _genre_search_needles(sub_genre),
    )
    if sub_pick is not None:
        picked.append(sub_pick)
        used_ids.add(str(sub_pick.get("id") or ""))
    wildcard_pool: list[dict] = []
    for category in READER_COMMENT_WILDCARD_CATEGORIES:
        wildcard_pool.extend(by_category.get(category) or [])
    wild_pick = _pick_reader_comment_persona(wildcard_pool, used_ids, picker)
    if wild_pick is not None:
        picked.append(wild_pick)
        used_ids.add(str(wild_pick.get("id") or ""))
    if len(picked) < READER_COMMENTS_EXPECTED:
        remainder = [
            item
            for item in personas
            if str(item.get("id") or "").strip() not in used_ids
        ]
        picker.shuffle(remainder)
        for item in remainder:
            picked.append(item)
            used_ids.add(str(item.get("id") or ""))
            if len(picked) >= READER_COMMENTS_EXPECTED:
                break
    return picked[:READER_COMMENTS_EXPECTED]


def select_additional_reader_comment_personas(
    main_genre: object,
    sub_genre: object,
    *,
    exclude_persona_ids: object = None,
    connection: sqlite3.Connection | None = None,
    rng: random.Random | None = None,
) -> list[dict]:
    """Pick up to 3 unused readers; ~25% of the time inject one off-genre joker."""
    picker = rng if rng is not None else random.Random()
    exclude = _normalize_exclude_persona_ids(exclude_persona_ids)

    def _pick(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
        all_personas = _load_virtual_reader_personas(conn)
        chosen = select_reader_comment_personas(
            main_genre,
            sub_genre,
            connection=conn,
            rng=picker,
            exclude_persona_ids=exclude,
        )
        return chosen, all_personas

    if connection is None:
        with _database() as conn:
            picked, all_personas = _pick(conn)
    else:
        picked, all_personas = _pick(connection)

    if len(picked) < READER_COMMENTS_EXPECTED:
        return picked
    if picker.random() >= READER_COMMENT_JOKER_CHANCE:
        return picked
    picked_ids = {
        str(item.get("id") or "").strip()
        for item in picked
        if str(item.get("id") or "").strip()
    }
    blocked = exclude | picked_ids
    joker_pool = [
        item
        for item in all_personas
        if str(item.get("id") or "").strip() not in blocked
        and str(item.get("category") or "") in READER_COMMENT_WILDCARD_CATEGORIES
    ]
    if not joker_pool:
        return picked
    joker = picker.choice(joker_pool)
    genre_slots = [
        index
        for index, item in enumerate(picked)
        if str(item.get("category") or "")
        in {"genre_specialist", "sub_genre_specialist"}
    ]
    slot = picker.choice(genre_slots) if genre_slots else picker.randrange(len(picked))
    picked[slot] = joker
    return picked


def _reader_comment_system_prompt(
    persona: dict,
    shared_context: str,
    *,
    main_genre: object = "",
    sub_genre: object = "",
    genre_detail: object = "",
) -> str:
    return (
        _reader_persona_system_prompt(persona)
        + "\n"
        + READER_COMMENT_TASK_ADDON
        + _reader_genre_mismatch_block(
            persona,
            main_genre=main_genre,
            sub_genre=sub_genre,
            genre_detail=genre_detail,
            shared_context=shared_context,
        )
        + "\n\n"
        + shared_context
    )


def _reader_comment_user_prompt(reader_name: str, scene_title: str) -> str:
    return (
        f"웹소설 플랫폼 댓글창에 실제 독자가 남길 법한 댓글 하나를 "
        f"'{reader_name}'로서 써 주세요.\n"
        f"이 회차를 평가하지 말고, 댓글창에 바로 달 한 줄을 쓰세요.\n"
        f"회차 제목: {scene_title or '(제목 없음)'}"
    )


def _log_reader_comment_error(scene_id: int, persona_id: object, error: object) -> None:
    if getattr(error, "code", None) == "quota":
        detail = f"일일 할당량 소진 ({error})"
    else:
        detail = str(error)
    print(
        "가상독자 댓글 생성 실패: "
        f"persona={persona_id or '-'}, scene={scene_id}, error={detail}"
    )


def _reader_comments_generating(scene_id: int) -> bool:
    with _reader_comments_inflight_lock:
        return int(scene_id) in _reader_comments_inflight


def _reader_comments_last_error_code(scene_id: int) -> str | None:
    with _reader_comments_inflight_lock:
        return _reader_comments_last_error.get(int(scene_id))


def _set_reader_comments_last_error(scene_id: int, code: str | None) -> None:
    scene_id = int(scene_id)
    with _reader_comments_inflight_lock:
        token = str(code or "").strip()
        if token:
            _reader_comments_last_error[scene_id] = token
        else:
            _reader_comments_last_error.pop(scene_id, None)


def group_reader_comment_batches(comments: list[dict]) -> list[dict]:
    """Group comments by batch_id. Newest batch first.

    Rows without batch_id (pre-migration inserts) share one legacy bucket,
    stamped with the earliest created_at in that group.
    """
    groups: dict[str, dict] = {}
    order: list[str] = []
    for item in comments:
        raw_id = str(item.get("batch_id") or "").strip()
        created = str(item.get("created_at") or "").strip()
        key = raw_id or "_legacy_"
        if key not in groups:
            groups[key] = {
                "batch_id": raw_id or created or key,
                "created_at": created,
                "comments": [],
            }
            order.append(key)
        bucket = groups[key]
        bucket["comments"].append(item)
        if created and (not bucket["created_at"] or created < bucket["created_at"]):
            bucket["created_at"] = created
            if not raw_id:
                bucket["batch_id"] = created
    batches = [groups[key] for key in order]
    batches.sort(
        key=lambda batch: (
            str(batch.get("created_at") or ""),
            str(batch.get("batch_id") or ""),
        ),
        reverse=True,
    )
    return batches


def _latest_batch_persona_ids(connection: sqlite3.Connection, scene_id: int) -> list[str]:
    row = connection.execute(
        """
        SELECT batch_id FROM scene_reader_comments
        WHERE scene_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (int(scene_id),),
    ).fetchone()
    if row is None:
        return []
    batch_id = str(row[0] or "")
    rows = connection.execute(
        """
        SELECT persona_id FROM scene_reader_comments
        WHERE scene_id = ? AND batch_id = ?
        """,
        (int(scene_id), batch_id),
    ).fetchall()
    return [str(item[0] or "").strip() for item in rows if str(item[0] or "").strip()]


def list_scene_reader_comments(scene_id: int) -> dict:
    scene_id = int(scene_id)
    with _database() as connection:
        scene = connection.execute(
            "SELECT id FROM scene WHERE id = ? AND deleted_at IS NULL",
            (scene_id,),
        ).fetchone()
        if scene is None:
            raise LookupError("씬을 찾을 수 없습니다.")
        rows = connection.execute(
            """
            SELECT c.id, c.scene_id, c.persona_id, c.comment_text, c.created_at,
                   c.batch_id, p.name, p.category
            FROM scene_reader_comments c
            JOIN virtual_reader_personas p ON p.id = c.persona_id
            WHERE c.scene_id = ?
            ORDER BY c.created_at ASC, c.id ASC
            """,
            (scene_id,),
        ).fetchall()
        total = connection.execute(
            "SELECT COUNT(*) FROM virtual_reader_personas"
        ).fetchone()[0]
    comments = []
    for row in rows:
        item = _as_dict(row) or {}
        persona_id = str(item.get("persona_id") or "").strip()
        comments.append(
            {
                "id": item.get("id"),
                "scene_id": int(item.get("scene_id") or scene_id),
                "batch_id": str(item.get("batch_id") or "").strip(),
                "persona_id": persona_id,
                "persona_name": str(item.get("name") or "").strip() or "가상 독자",
                "category": str(item.get("category") or "").strip(),
                "avatar_url": reader_persona_avatar_url(persona_id),
                "comment_text": str(item.get("comment_text") or ""),
                "created_at": str(item.get("created_at") or ""),
            }
        )
    batches = group_reader_comment_batches(comments)
    generating = _reader_comments_generating(scene_id)
    with _reader_comments_inflight_lock:
        inflight_batch_id = str(_reader_comments_inflight_batch.get(scene_id) or "").strip()
    if generating and inflight_batch_id:
        known = {str(item.get("batch_id") or "") for item in batches}
        if inflight_batch_id not in known:
            batches.insert(
                0,
                {
                    "batch_id": inflight_batch_id,
                    "created_at": inflight_batch_id,
                    "comments": [],
                },
            )
    remaining = max(0, int(total or 0) - len(comments))
    last_error_code = _reader_comments_last_error_code(scene_id)
    persisted_batches = [item for item in batches if item.get("comments")]
    meta = _reader_comment_refresh_meta(
        len(comments),
        generating=generating,
        remaining=remaining,
        last_error_code=last_error_code,
        batch_count=len(persisted_batches),
    )
    latest = batches[0] if batches else None
    return {
        "ok": True,
        "scene_id": scene_id,
        "comments": comments,
        "batches": batches,
        "latest_batch_id": str((latest or {}).get("batch_id") or ""),
        "has_history": bool(comments),
        "expected": READER_COMMENTS_EXPECTED,
        "generating": generating,
        "last_error_code": last_error_code,
        **meta,
    }


def _start_reader_comments_worker(
    scene_id: int,
    personas: list[dict] | None = None,
    batch_id: str | None = None,
) -> dict:
    scene_id = int(scene_id)
    batch_id = str(batch_id or "").strip() or _now()
    with _reader_comments_inflight_lock:
        already = scene_id in _reader_comments_inflight
        if not already:
            _reader_comments_inflight.add(scene_id)
            _reader_comments_inflight_batch[scene_id] = batch_id
    if already:
        payload = list_scene_reader_comments(scene_id)
        payload["generating"] = True
        payload["started"] = False
        return payload
    try:
        worker = Thread(
            target=_scene_reader_comments_worker,
            args=(scene_id, personas, batch_id),
            daemon=True,
            name=f"scene-reader-comments-{scene_id}",
        )
        worker.start()
    except Exception:
        with _reader_comments_inflight_lock:
            _reader_comments_inflight.discard(scene_id)
            _reader_comments_inflight_batch.pop(scene_id, None)
        raise
    payload = list_scene_reader_comments(scene_id)
    payload["generating"] = True
    payload["started"] = True
    payload["can_refresh"] = False
    return payload


def schedule_new_scene_reader_comment_batch(scene_id: int) -> dict:
    """Always start a fresh batch of three comments for the scene."""
    scene_id = int(scene_id)
    payload = list_scene_reader_comments(scene_id)
    if payload["generating"]:
        payload["started"] = False
        return payload
    with _database() as connection:
        row = connection.execute(
            """
            SELECT p.main_genre, p.sub_genre
            FROM scene s
            JOIN project p ON p.id = s.project_id
            WHERE s.id = ? AND s.deleted_at IS NULL AND p.deleted_at IS NULL
            """,
            (scene_id,),
        ).fetchone()
        if row is None:
            raise LookupError("씬을 찾을 수 없습니다.")
        exclude_ids = _latest_batch_persona_ids(connection, scene_id)
        personas = select_reader_comment_personas(
            row["main_genre"],
            row["sub_genre"],
            connection=connection,
            exclude_persona_ids=exclude_ids,
        )
        if len(personas) < READER_COMMENTS_EXPECTED:
            personas = select_reader_comment_personas(
                row["main_genre"],
                row["sub_genre"],
                connection=connection,
            )
    if not personas:
        payload["started"] = False
        return payload
    return _start_reader_comments_worker(
        scene_id, personas, batch_id=_now()
    )


def schedule_scene_reader_comments(scene_id: int) -> dict:
    """Start the first batch in the background when a scene is marked complete."""
    scene_id = int(scene_id)
    payload = list_scene_reader_comments(scene_id)
    if payload["comments"]:
        payload["started"] = False
        return payload
    return schedule_new_scene_reader_comment_batch(scene_id)


def schedule_additional_scene_reader_comments(scene_id: int) -> dict:
    """Start a refresh batch of unused personas (up to 3)."""
    scene_id = int(scene_id)
    payload = list_scene_reader_comments(scene_id)
    n = len(payload["comments"])
    if reader_comment_refresh_count(n) >= READER_COMMENTS_MAX_REFRESHES:
        raise ValueError("max_refreshes_reached")
    if payload["generating"]:
        payload["started"] = False
        return payload
    used_ids = [
        str(item.get("persona_id") or "").strip()
        for item in payload["comments"]
        if str(item.get("persona_id") or "").strip()
    ]
    with _database() as connection:
        row = connection.execute(
            """
            SELECT p.main_genre, p.sub_genre
            FROM scene s
            JOIN project p ON p.id = s.project_id
            WHERE s.id = ? AND s.deleted_at IS NULL AND p.deleted_at IS NULL
            """,
            (scene_id,),
        ).fetchone()
        if row is None:
            raise LookupError("씬을 찾을 수 없습니다.")
        extra = select_additional_reader_comment_personas(
            row["main_genre"],
            row["sub_genre"],
            exclude_persona_ids=used_ids,
            connection=connection,
        )
    if not extra:
        raise ValueError("no_more_personas")
    return _start_reader_comments_worker(scene_id, extra)


def request_scene_reader_comments(scene_id: int) -> dict:
    """Always create a new comment batch; previous batches are kept."""
    return schedule_new_scene_reader_comment_batch(scene_id)


def _scene_reader_comments_worker(
    scene_id: int,
    personas: list[dict] | None = None,
    batch_id: str | None = None,
) -> None:
    try:
        generate_scene_reader_comments(scene_id, personas, batch_id=batch_id)
    except Exception as error:
        _log_reader_comment_error(scene_id, "-", error)
    finally:
        with _reader_comments_inflight_lock:
            _reader_comments_inflight.discard(int(scene_id))
            _reader_comments_inflight_batch.pop(int(scene_id), None)


def generate_scene_reader_comments(
    scene_id: int,
    personas: list[dict] | None = None,
    batch_id: str | None = None,
) -> None:
    """Generate virtual-reader comments; persist each as soon as it arrives."""
    scene_id = int(scene_id)
    batch_id = str(batch_id or "").strip() or _now()
    with _database() as connection:
        existing_ids = {
            str(row[0] or "").strip()
            for row in connection.execute(
                """
                SELECT persona_id FROM scene_reader_comments
                WHERE scene_id = ? AND batch_id = ?
                """,
                (scene_id, batch_id),
            ).fetchall()
            if str(row[0] or "").strip()
        }
        row = connection.execute(
            """
            SELECT s.id, s.project_id, s.title, p.main_genre, p.sub_genre,
                   p.genre_detail, r.content_md
            FROM scene s
            JOIN project p ON p.id = s.project_id
            JOIN scene_revision r ON r.scene_id = s.id AND r.is_current = 1
            WHERE s.id = ? AND s.deleted_at IS NULL AND p.deleted_at IS NULL
            """,
            (scene_id,),
        ).fetchone()
        if row is None:
            return
        work_id = str(row["project_id"])
        episode_plain = _plain_text(str(row["content_md"] or "")).strip()
        scene_title = str(row["title"] or "").strip()
        main_genre = str(row["main_genre"] or "").strip()
        sub_genre = str(row["sub_genre"] or "").strip()
        try:
            genre_detail = str(row["genre_detail"] or "").strip()
        except (KeyError, IndexError, TypeError):
            genre_detail = ""
        if personas is None:
            needed = READER_COMMENTS_EXPECTED - len(existing_ids)
            if needed <= 0:
                return
            exclude_ids = set(existing_ids)
            exclude_ids.update(_latest_batch_persona_ids(connection, scene_id))
            personas = select_reader_comment_personas(
                main_genre,
                sub_genre,
                connection=connection,
                exclude_persona_ids=exclude_ids,
            )[:needed]
            if len(personas) < needed:
                personas = select_reader_comment_personas(
                    main_genre,
                    sub_genre,
                    connection=connection,
                    exclude_persona_ids=existing_ids,
                )[:needed]
        else:
            personas = [
                item
                for item in personas
                if str(item.get("id") or "").strip()
                and str(item.get("id") or "").strip() not in existing_ids
            ]
    if not personas:
        return
    shared_context = _reader_dynamic_context(work_id, episode_plain)
    run_error_code: str | None = None
    saved_any = False
    for index, persona in enumerate(personas):
        persona_id = str(persona.get("id") or "").strip()
        if not persona_id:
            continue
        reader_name = str(persona.get("name") or "").strip() or "가상 독자"
        system = _reader_comment_system_prompt(
            persona,
            shared_context,
            main_genre=main_genre,
            sub_genre=sub_genre,
            genre_detail=genre_detail,
        )
        prompt = _reader_comment_user_prompt(reader_name, scene_title)
        if index > 0 and READER_DEBATE_GEMINI_GAP_SECONDS > 0:
            time.sleep(READER_DEBATE_GEMINI_GAP_SECONDS)
        try:
            reply = gemini_client.generate_text(
                prompt,
                system=system,
                temperature=0.9,
                max_output_tokens=512,
            )
        except gemini_client.GeminiError as error:
            _log_reader_comment_error(scene_id, persona_id, error)
            if error.code == "quota":
                run_error_code = "quota"
                break
            if run_error_code != "quota":
                run_error_code = error.code or "unknown"
            continue
        except Exception as error:
            _log_reader_comment_error(scene_id, persona_id, error)
            if run_error_code != "quota":
                run_error_code = "unknown"
            continue
        reply_text = str(reply or "").strip()
        if not reply_text:
            _log_reader_comment_error(scene_id, persona_id, "empty_response")
            if run_error_code != "quota":
                run_error_code = "empty"
            continue
        try:
            with _database() as connection:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO scene_reader_comments
                        (scene_id, batch_id, persona_id, comment_text, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (scene_id, batch_id, persona_id, reply_text, _now()),
                )
            saved_any = True
        except sqlite3.Error as error:
            _log_reader_comment_error(scene_id, persona_id, error)
            if run_error_code != "quota":
                run_error_code = "unknown"
    if run_error_code:
        _set_reader_comments_last_error(scene_id, run_error_code)
    elif saved_any:
        _set_reader_comments_last_error(scene_id, None)


def list_reader_personas(handler) -> dict:
    grouped = {key: [] for key in READER_PERSONA_CATEGORIES}
    with _database() as connection:
        rows = connection.execute(
            "SELECT id, category, name, identity, tone, criteria, forbidden, "
            "sample_responses, discussion_attitude, display_order "
            "FROM virtual_reader_personas "
            "ORDER BY display_order, id"
        ).fetchall()
    for row in rows:
        item = serialize_reader_persona(row)
        category = str(item.get("category") or "")
        if category not in grouped:
            grouped[category] = []
        grouped[category].append(item)
    return grouped


def _load_reader_favorite_ids(
    connection: sqlite3.Connection, project_id: int
) -> list[str]:
    rows = connection.execute(
        "SELECT persona_id FROM project_reader_favorites "
        "WHERE project_id = ? ORDER BY sort_order ASC, persona_id ASC",
        (int(project_id),),
    ).fetchall()
    return [str(row["persona_id"]) for row in rows]


def _assert_reader_persona(
    connection: sqlite3.Connection, persona_id: object
) -> str:
    key = str(persona_id or "").strip()
    if not key:
        raise ValueError("가상 독자를 선택해 주세요.")
    row = connection.execute(
        "SELECT id FROM virtual_reader_personas WHERE id = ?",
        (key,),
    ).fetchone()
    if row is None:
        raise LookupError("가상 독자를 찾을 수 없습니다.")
    return key


def list_project_reader_favorites(handler, project_id: int) -> dict:
    with _database() as connection:
        handler.require_project(connection, project_id)
        return {"persona_ids": _load_reader_favorite_ids(connection, project_id)}


def replace_project_reader_favorites(handler, project_id: int, body: dict) -> dict:
    raw = []
    if isinstance(body, dict):
        raw = body.get("persona_ids") or body.get("ids") or []
    if not isinstance(raw, list):
        raise ValueError("즐겨찾기 목록이 올바르지 않습니다.")
    ids: list[str] = []
    seen: set[str] = set()
    for item in raw:
        persona_id = str(item or "").strip()
        if not persona_id or persona_id in seen:
            continue
        seen.add(persona_id)
        ids.append(persona_id)
    if len(ids) > READER_FAVORITE_MAX:
        raise ValueError("즐겨찾기는 최대 6명까지 등록할 수 있어요")
    with _database() as connection:
        handler.require_project(connection, project_id)
        for persona_id in ids:
            _assert_reader_persona(connection, persona_id)
        connection.execute(
            "DELETE FROM project_reader_favorites WHERE project_id = ?",
            (int(project_id),),
        )
        for order, persona_id in enumerate(ids):
            connection.execute(
                "INSERT INTO project_reader_favorites"
                "(project_id, persona_id, sort_order) VALUES (?, ?, ?)",
                (int(project_id), persona_id, order),
            )
        return {"persona_ids": _load_reader_favorite_ids(connection, project_id)}


def add_project_reader_favorite(handler, project_id: int, persona_id: str) -> dict:
    with _database() as connection:
        handler.require_project(connection, project_id)
        key = _assert_reader_persona(connection, persona_id)
        current = _load_reader_favorite_ids(connection, project_id)
        if key in current:
            return {"persona_ids": current}
        if len(current) >= READER_FAVORITE_MAX:
            raise ValueError("즐겨찾기는 최대 6명까지 등록할 수 있어요")
        row = connection.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS max_order "
            "FROM project_reader_favorites WHERE project_id = ?",
            (int(project_id),),
        ).fetchone()
        next_order = int(row["max_order"] if row is not None else -1) + 1
        connection.execute(
            "INSERT INTO project_reader_favorites"
            "(project_id, persona_id, sort_order) VALUES (?, ?, ?)",
            (int(project_id), key, next_order),
        )
        return {"persona_ids": _load_reader_favorite_ids(connection, project_id)}


def delete_project_reader_favorite(handler, project_id: int, persona_id: str) -> dict:
    with _database() as connection:
        handler.require_project(connection, project_id)
        key = str(persona_id or "").strip()
        if key:
            connection.execute(
                "DELETE FROM project_reader_favorites "
                "WHERE project_id = ? AND persona_id = ?",
                (int(project_id), key),
            )
        return {"persona_ids": _load_reader_favorite_ids(connection, project_id)}


def reader_chat_history(handler, work_id: object, persona_id: object) -> dict:
    work_key = str(work_id or "").strip()
    persona_key = str(persona_id or "").strip()
    if not work_key:
        raise ValueError("작품을 선택해 주세요.")
    if not persona_key:
        raise ValueError("가상 독자를 선택해 주세요.")
    session_key = reader_chat_session_key(work_key, persona_key)
    with _database() as connection:
        persona = connection.execute(
            "SELECT id, name FROM virtual_reader_personas WHERE id = ?",
            (persona_key,),
        ).fetchone()
        if persona is None:
            raise LookupError("가상 독자를 찾을 수 없습니다.")
        session = connection.execute(
            "SELECT id, work_id, persona_id, session_key, created_at, updated_at "
            "FROM reader_chat_sessions WHERE session_key = ?",
            (session_key,),
        ).fetchone()
        messages = []
        session_id = None
        if session is not None:
            session_id = session["id"]
            message_rows = connection.execute(
                "SELECT id, role, content, created_at FROM reader_chat_messages "
                "WHERE session_id = ? ORDER BY created_at ASC, rowid ASC",
                (session_id,),
            ).fetchall()
            messages = [_as_dict(row) for row in message_rows]
    return {
        "session_id": session_id,
        "work_id": work_key,
        "persona_id": persona_key,
        "persona_name": persona["name"],
        "messages": messages,
    }


def reader_chat(handler, body: dict) -> dict:
    work_id = str(body.get("work_id") or body.get("project_id") or "").strip()
    persona_id = str(body.get("persona_id") or "").strip()
    user_message = str(
        body.get("user_message") or body.get("message") or ""
    ).strip()
    session_id = str(body.get("session_id") or "").strip()
    episode_raw = body.get("episode_content")
    episode_content = None
    if episode_raw is not None and str(episode_raw).strip():
        episode_content = str(episode_raw).strip()
    if not work_id:
        raise ValueError("작품을 선택해 주세요.")
    if not persona_id:
        raise ValueError("가상 독자를 선택해 주세요.")
    if not user_message:
        raise ValueError("메시지를 입력해 주세요.")

    with _database() as connection:
        persona_row = connection.execute(
            "SELECT * FROM virtual_reader_personas WHERE id = ?",
            (persona_id,),
        ).fetchone()
        if persona_row is None:
            raise LookupError("가상 독자를 찾을 수 없습니다.")
        persona = serialize_reader_persona(persona_row)
        project = connection.execute(
            "SELECT id FROM project WHERE id = ? AND deleted_at IS NULL",
            (work_id,),
        ).fetchone()
        if project is None:
            raise LookupError("소설을 찾을 수 없습니다.")
        if session_id:
            session = connection.execute(
                "SELECT id, work_id, persona_id FROM reader_chat_sessions "
                "WHERE id = ?",
                (session_id,),
            ).fetchone()
            if session is None:
                raise LookupError("대화 세션을 찾을 수 없습니다.")
        else:
            session_key = reader_chat_session_key(work_id, persona_id)
            session = connection.execute(
                "SELECT id, work_id, persona_id FROM reader_chat_sessions "
                "WHERE session_key = ?",
                (session_key,),
            ).fetchone()
            if session is None:
                stamp = _now()
                session_id = uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO reader_chat_sessions
                        (id, work_id, persona_id, session_key, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        str(work_id),
                        persona_id,
                        session_key,
                        stamp,
                        stamp,
                    ),
                )
                session = connection.execute(
                    "SELECT id, work_id, persona_id FROM reader_chat_sessions "
                    "WHERE id = ?",
                    (session_id,),
                ).fetchone()
        session_id = str(session["id"])
        history_rows = connection.execute(
            """
            SELECT role, content FROM reader_chat_messages
            WHERE session_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (session_id, READER_CHAT_HISTORY_LIMIT),
        ).fetchall()

    history_rows = list(reversed(history_rows))
    history_lines: list[str] = []
    reader_name = str(persona.get("name") or "가상 독자")
    for item in history_rows:
        role = str(item["role"] or "").strip().lower()
        content = str(item["content"] or "").strip()
        if not content:
            continue
        if len(content) > 4000:
            content = content[:4000] + "…"
        who = "작가" if role == "user" else reader_name
        history_lines.append(f"{who}: {content}")
    transcript = "\n".join(history_lines) if history_lines else "(이전 대화 없음)"
    shared_context = _reader_dynamic_context(work_id, episode_content)
    system = (
        _reader_persona_system_prompt(persona)
        + _reader_genre_mismatch_block(
            persona,
            shared_context=shared_context,
        )
        + "\n\n"
        + shared_context
    )
    full_prompt = (
        f"아래는 작가와 '{reader_name}'의 이전 대화, 그리고 작가의 새 메시지입니다. "
        f"{reader_name}로서 새 메시지에만 이어서 답하세요. 이름 접두어는 붙이지 마세요.\n\n"
        f"[이전 대화]\n{transcript}\n\n"
        f"[작가의 새 메시지]\n{user_message}"
    )
    try:
        reply = gemini_client.generate_text(
            full_prompt, system=system, temperature=0.9
        )
    except gemini_client.GeminiError as error:
        raise ValueError(str(error)) from error

    user_stamp = _now()
    assistant_stamp = _now()
    with _database() as connection:
        connection.execute(
            """
            INSERT INTO reader_chat_messages
                (id, session_id, role, content, created_at)
            VALUES (?, ?, 'user', ?, ?)
            """,
            (uuid.uuid4().hex, session_id, user_message, user_stamp),
        )
        connection.execute(
            """
            INSERT INTO reader_chat_messages
                (id, session_id, role, content, created_at)
            VALUES (?, ?, 'assistant', ?, ?)
            """,
            (uuid.uuid4().hex, session_id, reply, assistant_stamp),
        )
        connection.execute(
            "UPDATE reader_chat_sessions SET updated_at = ? WHERE id = ?",
            (assistant_stamp, session_id),
        )
    return {
        "session_id": session_id,
        "persona_name": reader_name,
        "reply": reply,
    }


def reader_debate_history(handler, work_id: object, persona_ids: object) -> dict:
    work_key = str(work_id or "").strip()
    order = parse_reader_debate_persona_ids(persona_ids)
    if not work_key:
        raise ValueError("작품을 선택해 주세요.")
    if not order:
        raise ValueError("토론에 참여할 가상 독자를 선택해 주세요.")
    ids_key = reader_debate_persona_ids_key(order)
    with _database() as connection:
        session = connection.execute(
            """
            SELECT id, work_id, persona_ids_key, persona_order_json,
                   created_at, updated_at
            FROM reader_debate_sessions
            WHERE work_id = ? AND persona_ids_key = ?
            """,
            (work_key, ids_key),
        ).fetchone()
        if session is None:
            return {
                "session_id": None,
                "work_id": work_key,
                "persona_ids_key": ids_key,
                "persona_order": order,
                "rounds": [],
            }
        session_id = str(session["id"])
        try:
            stored_order = json.loads(str(session["persona_order_json"] or "[]"))
        except json.JSONDecodeError:
            stored_order = []
        if not isinstance(stored_order, list):
            stored_order = []
        stored_order = [str(pid).strip() for pid in stored_order if str(pid).strip()]
        message_rows = connection.execute(
            """
            SELECT id, round_number, speaker_type, persona_id, message,
                   turn_order, created_at
            FROM reader_debate_messages
            WHERE session_id = ?
            ORDER BY round_number ASC, turn_order ASC, created_at ASC, id ASC
            """,
            (session_id,),
        ).fetchall()
        persona_name_map: dict[str, str] = {}
        persona_ids_needed = {
            str(row["persona_id"] or "").strip()
            for row in message_rows
            if str(row["speaker_type"] or "").strip().lower() == "persona"
            and str(row["persona_id"] or "").strip()
        }
        for pid in persona_ids_needed | set(stored_order):
            name_row = connection.execute(
                "SELECT name FROM virtual_reader_personas WHERE id = ?",
                (pid,),
            ).fetchone()
            if name_row is not None:
                persona_name_map[pid] = str(name_row["name"] or "").strip() or pid
        rounds_map: dict[int, list[dict]] = {}
        for row in message_rows:
            item = _as_dict(row) or {}
            rn = int(item.get("round_number") or 0)
            pid = str(item.get("persona_id") or "").strip() or None
            item["persona_id"] = pid
            item["persona_name"] = (
                persona_name_map.get(pid) if pid else None
            )
            rounds_map.setdefault(rn, []).append(item)
    rounds = [
        {"round_number": rn, "messages": rounds_map[rn]}
        for rn in sorted(rounds_map)
    ]
    return {
        "session_id": session_id,
        "work_id": work_key,
        "persona_ids_key": str(session["persona_ids_key"]),
        "persona_order": stored_order or order,
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "rounds": rounds,
    }


def reader_debate(handler, body: dict) -> dict:
    work_id = str(body.get("work_id") or body.get("project_id") or "").strip()
    persona_ids = parse_reader_debate_persona_ids(body.get("persona_ids"))
    user_message = str(
        body.get("user_message") or body.get("message") or ""
    ).strip()
    episode_raw = body.get("episode_content")
    episode_content = None
    if episode_raw is not None and str(episode_raw).strip():
        episode_content = str(episode_raw).strip()
    if not work_id:
        raise ValueError("작품을 선택해 주세요.")
    if not user_message:
        raise ValueError("메시지를 입력해 주세요.")
    if len(persona_ids) < READER_DEBATE_MIN_PERSONAS:
        raise ValueError(
            f"토론에는 가상 독자를 최소 {READER_DEBATE_MIN_PERSONAS}명 골라 주세요."
        )
    if len(persona_ids) > READER_DEBATE_MAX_PERSONAS:
        raise ValueError(
            f"토론에는 가상 독자를 최대 {READER_DEBATE_MAX_PERSONAS}명까지 고를 수 있어요."
        )
    if len(persona_ids) != len(set(persona_ids)):
        raise ValueError("같은 가상 독자를 중복으로 고를 수 없습니다.")
    ids_key = reader_debate_persona_ids_key(persona_ids)

    with _database() as connection:
        project = connection.execute(
            "SELECT id FROM project WHERE id = ? AND deleted_at IS NULL",
            (work_id,),
        ).fetchone()
        if project is None:
            raise LookupError("소설을 찾을 수 없습니다.")
        personas_by_id: dict[str, dict] = {}
        for pid in persona_ids:
            persona_row = connection.execute(
                "SELECT * FROM virtual_reader_personas WHERE id = ?",
                (pid,),
            ).fetchone()
            if persona_row is None:
                raise LookupError(f"가상 독자를 찾을 수 없습니다: {pid}")
            personas_by_id[pid] = serialize_reader_persona(persona_row)
        session = connection.execute(
            """
            SELECT id, work_id, persona_ids_key, persona_order_json
            FROM reader_debate_sessions
            WHERE work_id = ? AND persona_ids_key = ?
            """,
            (work_id, ids_key),
        ).fetchone()
        if session is None:
            stamp = _now()
            session_id = uuid.uuid4().hex
            connection.execute(
                """
                INSERT INTO reader_debate_sessions
                    (id, work_id, persona_ids_key, persona_order_json,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    work_id,
                    ids_key,
                    json.dumps(persona_ids, ensure_ascii=False),
                    stamp,
                    stamp,
                ),
            )
            speaking_order = list(persona_ids)
        else:
            session_id = str(session["id"])
            try:
                stored = json.loads(str(session["persona_order_json"] or "[]"))
            except json.JSONDecodeError:
                stored = []
            if isinstance(stored, list) and stored:
                speaking_order = [
                    str(pid).strip() for pid in stored if str(pid).strip()
                ]
            else:
                speaking_order = list(persona_ids)
            # Fill any missing ids from the request (should not happen for same key).
            for pid in persona_ids:
                if pid not in speaking_order:
                    speaking_order.append(pid)
        max_round = connection.execute(
            """
            SELECT COALESCE(MAX(round_number), 0)
            FROM reader_debate_messages
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()[0]
        round_number = int(max_round or 0) + 1
        user_stamp = _now()
        connection.execute(
            """
            INSERT INTO reader_debate_messages
                (id, session_id, round_number, speaker_type, persona_id,
                 message, turn_order, created_at)
            VALUES (?, ?, ?, 'user', NULL, ?, 0, ?)
            """,
            (
                uuid.uuid4().hex,
                session_id,
                round_number,
                user_message,
                user_stamp,
            ),
        )
        history_rows = connection.execute(
            """
            SELECT speaker_type, persona_id, message
            FROM reader_debate_messages
            WHERE session_id = ?
            ORDER BY round_number ASC, turn_order ASC, created_at ASC, id ASC
            """,
            (session_id,),
        ).fetchall()
        connection.execute(
            "UPDATE reader_debate_sessions SET updated_at = ? WHERE id = ?",
            (user_stamp, session_id),
        )

    persona_names = {
        pid: str(personas_by_id[pid].get("name") or "").strip() or "가상 독자"
        for pid in speaking_order
        if pid in personas_by_id
    }
    # Personas from stored order may include ids not in this request — load names.
    for pid in speaking_order:
        if pid in persona_names:
            continue
        with _database() as connection:
            row = connection.execute(
                "SELECT * FROM virtual_reader_personas WHERE id = ?",
                (pid,),
            ).fetchone()
        if row is None:
            continue
        personas_by_id[pid] = serialize_reader_persona(row)
        persona_names[pid] = (
            str(personas_by_id[pid].get("name") or "").strip() or "가상 독자"
        )

    transcript_rows = [_as_dict(row) or {} for row in history_rows]
    transcript_lines = _reader_debate_transcript_lines(
        transcript_rows, persona_names
    )
    shared_context = _reader_dynamic_context(work_id, episode_content)
    replies: list[dict] = []
    gemini_calls = 0

    for turn_index, persona_id in enumerate(speaking_order, start=1):
        persona = personas_by_id.get(persona_id)
        if persona is None:
            replies.append(
                {
                    "persona_id": persona_id,
                    "persona_name": persona_id,
                    "message": "",
                    "turn_order": turn_index,
                    "ok": False,
                    "skipped": True,
                    "error": "가상 독자를 찾을 수 없습니다.",
                }
            )
            continue
        reader_name = persona_names.get(persona_id) or "가상 독자"
        transcript = (
            "\n".join(transcript_lines) if transcript_lines else "(이전 발언 없음)"
        )
        system = _reader_debate_system_prompt(
            persona,
            shared_context,
        )
        full_prompt = _reader_debate_user_prompt(
            reader_name, transcript, user_message
        )
        if gemini_calls > 0 and READER_DEBATE_GEMINI_GAP_SECONDS > 0:
            time.sleep(READER_DEBATE_GEMINI_GAP_SECONDS)
        try:
            gemini_calls += 1
            reply = gemini_client.generate_text(
                full_prompt, system=system, temperature=0.9
            )
        except gemini_client.GeminiError as error:
            if not _is_quota_error(error):
                print(f"가상독자 토론 건너뜀 ({reader_name}): {error}")
            replies.append(
                {
                    "persona_id": persona_id,
                    "persona_name": reader_name,
                    "message": "",
                    "turn_order": turn_index,
                    "ok": False,
                    "skipped": True,
                    "error": str(error),
                }
            )
            continue
        reply_text = str(reply or "").strip()
        if not reply_text:
            replies.append(
                {
                    "persona_id": persona_id,
                    "persona_name": reader_name,
                    "message": "",
                    "turn_order": turn_index,
                    "ok": False,
                    "skipped": True,
                    "error": "빈 응답",
                }
            )
            continue
        reply_stamp = _now()
        with _database() as connection:
            connection.execute(
                """
                INSERT INTO reader_debate_messages
                    (id, session_id, round_number, speaker_type, persona_id,
                     message, turn_order, created_at)
                VALUES (?, ?, ?, 'persona', ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    session_id,
                    round_number,
                    persona_id,
                    reply_text,
                    turn_index,
                    reply_stamp,
                ),
            )
            connection.execute(
                "UPDATE reader_debate_sessions SET updated_at = ? WHERE id = ?",
                (reply_stamp, session_id),
            )
        transcript_lines.append(f"{reader_name}: {reply_text}")
        replies.append(
            {
                "persona_id": persona_id,
                "persona_name": reader_name,
                "message": reply_text,
                "turn_order": turn_index,
                "ok": True,
                "skipped": False,
            }
        )

    return {
        "session_id": session_id,
        "work_id": work_id,
        "persona_ids_key": ids_key,
        "persona_order": speaking_order,
        "round_number": round_number,
        "replies": replies,
    }


def mark_reader_comments_started(handler, scene_id: int) -> dict:
    """Record that the virtual-reader comments flow was opened for this scene.

    Sets ``updated_at`` so ``scene_touch`` does not bump ``row_version``
    (same pattern as ``save_scene_goal`` — avoids rejecting an in-flight manuscript save).
    """
    with _database() as connection:
        scene = connection.execute(
            "SELECT id FROM scene WHERE id = ? AND deleted_at IS NULL",
            (scene_id,),
        ).fetchone()
        if scene is None:
            raise ValueError("씬을 찾을 수 없습니다.")
        connection.execute(
            "UPDATE scene SET reader_comments_started = 1, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE id = ?",
            (scene_id,),
        )
    return {"ok": True, "reader_comments_started": 1}


def _query_param(handler, name: str) -> str:
    query = parse_qs(urlparse(handler.path).query)
    return (query.get(name) or [""])[0]


def try_handle_get(handler, path: str) -> bool:
    if path == "/api/reader-personas":
        handler.send_json(list_reader_personas(handler))
        return True
    if path == "/api/reader-chat/history":
        handler.send_json(
            reader_chat_history(
                handler,
                _query_param(handler, "work_id"),
                _query_param(handler, "persona_id"),
            )
        )
        return True
    if path == "/api/reader-debate/history":
        handler.send_json(
            reader_debate_history(
                handler,
                _query_param(handler, "work_id"),
                _query_param(handler, "persona_ids"),
            )
        )
        return True
    match = re.fullmatch(r"/api/projects/(\d+)/reader-favorites", path)
    if match:
        handler.send_json(list_project_reader_favorites(handler, int(match.group(1))))
        return True
    match = re.fullmatch(r"/api/scenes/(\d+)/reader-comments", path)
    if match:
        handler.send_json(list_scene_reader_comments(int(match.group(1))))
        return True
    return False


def try_handle_post(handler, path: str, body: dict | None) -> bool:
    body = body or {}
    match = re.fullmatch(r"/api/projects/(\d+)/reader-favorites/([^/]+)", path)
    if match:
        handler.send_json(
            add_project_reader_favorite(
                handler, int(match.group(1)), unquote(match.group(2))
            )
        )
        return True
    if path == "/api/reader-chat":
        handler.send_json(reader_chat(handler, body))
        return True
    if path == "/api/reader-debate":
        handler.send_json(reader_debate(handler, body))
        return True
    match = re.fullmatch(r"/api/scenes/(\d+)/reader-comments/generate", path)
    if match:
        result = request_scene_reader_comments(int(match.group(1)))
        status = (
            HTTPStatus.ACCEPTED
            if result.get("started") or result.get("generating")
            else HTTPStatus.OK
        )
        handler.send_json(result, status)
        return True
    match = re.fullmatch(r"/api/scenes/(\d+)/reader-comments-started", path)
    if match:
        handler.send_json(mark_reader_comments_started(handler, int(match.group(1))))
        return True
    return False


def try_handle_put(handler, path: str, body: dict | None) -> bool:
    match = re.fullmatch(r"/api/projects/(\d+)/reader-favorites", path)
    if match is None:
        return False
    handler.send_json(
        replace_project_reader_favorites(handler, int(match.group(1)), body or {})
    )
    return True


def try_handle_delete(handler, path: str) -> bool:
    match = re.fullmatch(r"/api/projects/(\d+)/reader-favorites/([^/]+)", path)
    if match is None:
        return False
    handler.send_json(
        delete_project_reader_favorite(
            handler, int(match.group(1)), unquote(match.group(2))
        )
    )
    return True
