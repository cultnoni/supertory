"""글럼프 ER HTTP API. app.py는 라우트만 연결한다.

진단·전환 도구(와일드카드, 빈칸 채우기, 핑퐁, 행운의 문장, 캐릭터 심문,
무드 보드, 단어/문장 목록, 작명소, 타로)와 멘탈 비타민용 하이라이트 추출이
모두 여기에 있다. app.py와 공유하는 몇 안 되는 헬퍼는 아래 얇은 래퍼를 거친다
(feedback_api와 같은 지연 import 방식).
"""

from __future__ import annotations

import json
import re
import sqlite3
import urllib.error
import urllib.request
import uuid
from http import HTTPStatus
from threading import Lock, Thread
from urllib.parse import parse_qs, quote, urlparse

import env_loader
import gemini_client
import tarot_deck


def _database():
    import app

    return app.database()


def _now() -> str:
    import app

    return app.utc_timestamp_now()


def _plain_text(content: object) -> str:
    import app

    return app.plain_text_from_content(content)


def _role_labels() -> dict[str, str]:
    import app

    return app._CHARACTER_ROLE_KO


def _scene_episode_order(connection, project_id, scene_id):
    import app

    return app._scene_episode_order(connection, project_id, scene_id)


def _tory_core_identity() -> str:
    import app

    return app.SuperToryHandler._tory_core_identity_system_prompt()


def _strip_json_fence(raw: object) -> str:
    import app

    return app.SuperToryHandler._strip_json_fence(raw)


def _genre_display_label(value: object) -> str:
    import app

    return app.SuperToryHandler._genre_display_label(None, value)


GLUMP_Q1_ANSWERS = ("block", "perfectionism", "self_doubt", "burnout")
GLUMP_Q2_ANSWERS = ("event", "sentence_struggle", "start", "together")
GLUMP_Q2_TOOLS = {
    "event": "wildcard_spark",
    "sentence_struggle": "fill_blank_game",
    "start": "lucky_sentence",
    "together": "pingpong_relay",
}
GLUMP_Q2_MESSAGES = {
    "event": "다음에 무슨 일이 일어날지 막막하시죠. 예상 못한 사건을 하나 던져 드릴게요.",
    "sentence_struggle": "문장이 안 풀릴 때는 빈칸만 채워도 흐름이 다시 잡혀요. 한 줄씩 이어가 보세요.",
    "start": "시작이 제일 어렵죠. 첫 문장만 골라 주시면 그다음부터는 훨씬 수월해져요.",
    "together": "혼자 밀고 가기 버거울 때는 한 문장씩 주고받아 보는 게 도움이 돼요.",
}
GLUMP_CLIENT_TOOL_IDS = ("no_edit_timer", "blind_mode", "eraser_seal")
GLUMP_HIGHLIGHT_MIN_CHARS = 500
GLUMP_HIGHLIGHT_CHUNK_MAX = 4000
GLUMP_HIGHLIGHT_TYPES = ("dialogue", "description", "scene")
_glump_highlight_inflight: set[str] = set()
_glump_highlight_inflight_lock = Lock()


def diagnose_glump(q1_answer: object, q2_answer: object = None) -> dict:
    """Rule-based Glump ER routing. Never calls Gemini."""
    q1 = str(q1_answer or "").strip()
    q2_raw = str(q2_answer or "").strip()
    q2 = q2_raw or None
    if q1 not in GLUMP_Q1_ANSWERS:
        raise ValueError("진단 보기를 선택해 주세요.")
    if q1 == "perfectionism":
        return {
            "q1_answer": q1,
            "q2_answer": None,
            "recommended_tool": "ten_min_sprint",
            "message": "완벽하게 써야 한다는 압박이 크시군요. 10분 동안은 수정 없이 막 써 내려가 보세요.",
        }
    if q1 == "self_doubt":
        return {
            "q1_answer": q1,
            "q2_answer": None,
            "recommended_tool": "mental_vitamin",
            "message": "내 글이 별로인 것 같을 때일수록, 이미 잘 쓴 부분들을 다시 보는 게 도움이 돼요.",
        }
    if q1 == "burnout":
        return {
            "q1_answer": q1,
            "q2_answer": None,
            "recommended_tool": None,
            "message": "오늘 목표는 300자예요. 한 글자도 안 써도 괜찮아요.",
            "show_rest_choice": True,
        }
    if not q2:
        raise ValueError("다음 질문에 답해주세요")
    if q2 not in GLUMP_Q2_TOOLS:
        raise ValueError("다음 질문에 답해주세요")
    return {
        "q1_answer": q1,
        "q2_answer": q2,
        "recommended_tool": GLUMP_Q2_TOOLS[q2],
        "message": GLUMP_Q2_MESSAGES[q2],
    }


class GlumpRetryError(RuntimeError):
    """Tool response could not be parsed; front should retry."""


MOOD_BOARD_EMPTY_KEY_MSG = (
    "이미지 검색 기능을 쓰려면 관리자가 API 키를 설정해야 해요"
)
PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"
_HEX_COLOR_RE = re.compile(r"^#?([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$")

WILDCARD_SPARK_MIN_CHARS = 100
WILDCARD_SPARK_MAX_CHARS = 1500
FILL_BLANK_MIN_CHARS = WILDCARD_SPARK_MIN_CHARS
FILL_BLANK_EPISODE_CAP = 1500
FILL_BLANK_MIN_BLANKS = 3
FILL_BLANK_MAX_BLANKS = 5
FILL_BLANK_STATUSES = ("in_progress", "completed", "abandoned")
PINGPONG_MIN_CHARS = WILDCARD_SPARK_MIN_CHARS
PINGPONG_EPISODE_CAP = 1500
PINGPONG_REPLY_MAX = 200
PINGPONG_CHECKIN_CHARS = 400
LUCKY_SENTENCE_MIN_CHARS = WILDCARD_SPARK_MIN_CHARS
LUCKY_SENTENCE_EPISODE_CAP = 1000
LUCKY_SENTENCE_MAX_DRAWS = 3
INTERROGATION_MIN_CHARS = WILDCARD_SPARK_MIN_CHARS
INTERROGATION_EPISODE_CAP = 1500


def _clip_recent_episode(episode_content: object, limit: int = WILDCARD_SPARK_MAX_CHARS) -> str:
    text = str(episode_content or "").strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def _wildcard_spark_prompt(
    genre: object, episode_content: object, stage: object = None
) -> tuple[str, str]:
    """Assemble Wildcard Spark prompts. Reuses Tory Core Identity as-is."""
    excerpt = _clip_recent_episode(episode_content)
    genre_label = str(genre or "").strip() or "미정"
    core = _tory_core_identity()
    dynamic = (
        "[Dynamic Context]\n"
        f"작품 장르: {genre_label}\n\n"
        "아래는 작가가 막힌 지점의 최근 원고입니다.\n"
        f"{excerpt}"
    )
    stage_text = str(stage or "").strip()
    stage_line = ""
    if stage_text:
        stage_line = (
            f"\n현재 작품 단계는 '{stage_text}'입니다. 이 단계에 맞게 사건 강도를 조절하세요. "
            "클라이맥스에 가까울수록 더 큰 반전을, 중간 전개면 상대적으로 작은 사건을 제안하세요."
        )
    user_prompt = (
        "[Task Instruction]\n"
        "작가가 다음 전개를 못 정해서 막혀있습니다. 지금 원고 맥락에서 일어날 수 있는 "
        "가장 파격적이고 예상 못한 사건 3가지를 제안하세요. 각각 1~2문장으로 짧게. "
        "장르 톤은 유지하되 안전한 선택지 말고 정말 의외의 전개를 던지세요."
        f"{stage_line}\n\n"
        "출력은 JSON 배열만 반환하세요. 설명 문장이나 마크다운 코드펜스를 넣지 마세요.\n"
        '형식: [{"title": "짧은 제목", "description": "1~2문장 설명"}, ...]\n'
        "항목은 반드시 3개입니다."
    )
    return core + "\n" + dynamic, user_prompt


def _parse_wildcard_spark_events(raw: object) -> list[dict]:
    cleaned = _strip_json_fence(str(raw or ""))
    data: object
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", cleaned, re.S)
        if not match:
            raise ValueError("json")
        data = json.loads(match.group(0))
    if isinstance(data, dict):
        data = data.get("events") or data.get("ideas") or data.get("items")
    if not isinstance(data, list):
        raise ValueError("json")
    events: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        description = str(item.get("description") or item.get("text") or "").strip()
        if title and description:
            events.append({"title": title, "description": description})
        if len(events) == 3:
            break
    if len(events) != 3:
        raise ValueError("json")
    return events


def _glump_character_settings(work_id: object) -> str:
    """설정집 등장인물 이름·말투 요약. 목록 API와 같은 캐릭터 컬럼을 재사용한다."""
    work_key = str(work_id or "").strip()
    if not work_key:
        return "(설정집 인물 없음)"
    with _database() as connection:
        rows = connection.execute(
            "SELECT id, name, role, short_description, profile_md "
            "FROM character "
            "WHERE project_id = ? AND deleted_at IS NULL "
            "ORDER BY sort_order, id",
            (work_key,),
        ).fetchall()
        aliases = connection.execute(
            "SELECT character_id, alias FROM character_alias "
            "WHERE project_id = ? ORDER BY id",
            (work_key,),
        ).fetchall()
    alias_by_id: dict[int, list[str]] = {}
    for row in aliases:
        alias_by_id.setdefault(int(row["character_id"]), []).append(
            str(row["alias"] or "").strip()
        )
    blocks: list[str] = []
    for row in rows:
        name = str(row["name"] or "").strip() or "이름 없는 인물"
        role_ko = _role_labels().get(str(row["role"] or ""), str(row["role"] or ""))
        bits = [f"이름: {name}"]
        if role_ko:
            bits.append(f"역할: {role_ko}")
        nick = [item for item in alias_by_id.get(int(row["id"]), []) if item]
        if nick:
            bits.append("별칭: " + ", ".join(nick[:6]))
        summary = str(row["short_description"] or "").strip()
        if summary:
            bits.append(f"한줄: {summary[:400]}")
        profile = str(row["profile_md"] or "").strip()
        if profile:
            bits.append(f"말투·설정: {profile[:800]}")
        blocks.append("- " + " / ".join(bits))
        if len(blocks) >= 24:
            break
    if not blocks:
        return "(설정집 인물 없음)"
    text = "\n".join(blocks)
    if len(text) > 4000:
        return text[:4000] + "…"
    return text


def _glump_load_interrogation_character(
    work_id: object, character_name: object = None
) -> dict:
    """Pick the named character, or the 설정집 protagonist if name is empty."""
    work_key = str(work_id or "").strip()
    wanted = str(character_name or "").strip()
    if not work_key:
        raise ValueError("작품을 선택해 주세요.")
    with _database() as connection:
        try:
            rows = connection.execute(
                "SELECT id, name, role, short_description, profile_md, "
                "strengths_md, weaknesses_md "
                "FROM character "
                "WHERE project_id = ? AND deleted_at IS NULL "
                "ORDER BY sort_order, id",
                (work_key,),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = connection.execute(
                "SELECT id, name, role, short_description, profile_md "
                "FROM character "
                "WHERE project_id = ? AND deleted_at IS NULL "
                "ORDER BY sort_order, id",
                (work_key,),
            ).fetchall()
        aliases = connection.execute(
            "SELECT character_id, alias FROM character_alias "
            "WHERE project_id = ? ORDER BY id",
            (work_key,),
        ).fetchall()
    alias_by_id: dict[int, list[str]] = {}
    for row in aliases:
        alias_by_id.setdefault(int(row["character_id"]), []).append(
            str(row["alias"] or "").strip()
        )
    picked = None
    if wanted:
        wanted_fold = wanted.casefold()
        for row in rows:
            name = str(row["name"] or "").strip()
            nicks = [item for item in alias_by_id.get(int(row["id"]), []) if item]
            hay = [name] + nicks
            if any(item.casefold() == wanted_fold for item in hay):
                picked = row
                break
        if picked is None:
            raise ValueError("그 이름의 캐릭터를 설정집에서 찾을 수 없어요")
    else:
        for row in rows:
            if str(row["role"] or "") == "protagonist":
                picked = row
                break
        if picked is None:
            raise ValueError("설정집에서 주인공으로 표시된 캐릭터를 찾을 수 없어요")
    name = str(picked["name"] or "").strip() or "이름 없는 인물"
    role = str(picked["role"] or "")
    role_ko = _role_labels().get(role, role)
    summary = str(picked["short_description"] or "").strip()
    profile = str(picked["profile_md"] or "").strip()
    strengths = str(dict(picked).get("strengths_md") or "").strip()
    weaknesses = str(dict(picked).get("weaknesses_md") or "").strip()
    nicks = [item for item in alias_by_id.get(int(picked["id"]), []) if item]
    bits = [f"이름: {name}"]
    if role_ko:
        bits.append(f"역할: {role_ko}")
    if nicks:
        bits.append("별칭: " + ", ".join(nicks[:6]))
    if summary:
        bits.append(f"한줄: {summary[:400]}")
    if profile:
        bits.append(f"말투·설정: {profile[:800]}")
    if strengths:
        bits.append(f"강점: {strengths[:240]}")
    if weaknesses:
        bits.append(f"약점: {weaknesses[:240]}")
    return {
        "name": name,
        "role": role,
        "short_description": summary,
        "profile_md": profile,
        "strengths_md": strengths,
        "weaknesses_md": weaknesses,
        "settings_text": "- " + " / ".join(bits),
        "chat_partner": {
            "name": name,
            "role": role,
            "short_description": summary,
            "profile_md": profile,
            "strengths_md": strengths,
            "weaknesses_md": weaknesses,
        },
    }


def _glump_project_assist_fields(work_id: object) -> dict:
    work_key = str(work_id or "").strip()
    with _database() as connection:
        row = connection.execute(
            "SELECT title, purpose, main_genre, sub_genre FROM project "
            "WHERE id = ? AND deleted_at IS NULL",
            (work_key,),
        ).fetchone()
    if row is None:
        raise LookupError("작품을 찾을 수 없습니다.")
    return {
        "project_id": work_key,
        "project_title": str(row["title"] or "").strip(),
        "purpose": str(row["purpose"] or "general_novel").strip() or "general_novel",
        "main_genre": str(row["main_genre"] or "").strip(),
        "sub_genre": str(row["sub_genre"] or "").strip(),
    }


def _interrogation_questions_prompt(
    genre: object,
    episode_content: object,
    character_name: object,
    character_settings: object,
) -> tuple[str, str]:
    """Assemble interrogation questions. Reuses Tory Core Identity as-is."""
    excerpt = _clip_recent_episode(episode_content, INTERROGATION_EPISODE_CAP)
    genre_label = str(genre or "").strip() or "미정"
    name = str(character_name or "").strip() or "주인공"
    settings = str(character_settings or "").strip() or "(설정집 인물 없음)"
    core = _tory_core_identity()
    dynamic = (
        "[Dynamic Context]\n"
        f"작품 장르: {genre_label}\n\n"
        "[해당 캐릭터 설정]\n"
        f"{settings}\n\n"
        "아래는 작가가 막힌 지점의 최근 원고입니다.\n"
        f"{excerpt}"
    )
    user_prompt = (
        "[Task Instruction]\n"
        "작가가 다음 전개를 못 정해서 막혀있습니다. "
        f"{name}을(를) 소환해서 직접 추궁할 날카로운 질문 2~3개를 만드세요. "
        "막연한 질문 말고, 지금 이 상황에서 이 캐릭터가 반드시 답해야 할 "
        "구체적인 질문이어야 합니다 "
        "(예: '너 지금 도망칠 거야, 맞설 거야?', '왜 그 사람한테 거짓말했어?').\n\n"
        "출력은 JSON 객체만 반환하세요. 설명 문장이나 마크다운 코드펜스를 넣지 마세요.\n"
        '형식: {"questions": ["질문1", "질문2", "질문3"]}\n'
        "질문은 2개 또는 3개입니다."
    )
    return core + "\n" + dynamic, user_prompt


def _parse_interrogation_questions(raw: object) -> list[str]:
    cleaned = _strip_json_fence(str(raw or ""))
    data: object
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}|\[.*\]", cleaned, re.S)
        if not match:
            raise ValueError("json")
        data = json.loads(match.group(0))
    items = data
    if isinstance(data, dict):
        items = data.get("questions") or data.get("items") or data.get("list")
    if not isinstance(items, list):
        raise ValueError("json")
    questions: list[str] = []
    for item in items:
        if isinstance(item, dict):
            text = str(item.get("question") or item.get("text") or "").strip()
        else:
            text = str(item or "").strip()
        if text:
            questions.append(text)
        if len(questions) == 3:
            break
    if len(questions) < 2:
        raise ValueError("json")
    return questions


def _interrogation_summary_prompt(qa_json: object) -> tuple[str, str]:
    """Assemble interrogation hint cards. Same card shape as Wildcard Spark."""
    if isinstance(qa_json, str):
        qa_text = qa_json.strip() or "[]"
    else:
        qa_text = json.dumps(qa_json or [], ensure_ascii=False)
    core = _tory_core_identity()
    dynamic = (
        "[Dynamic Context]\n"
        "다음은 작가가 캐릭터를 추궁한 질문과 답변입니다.\n"
        f"{qa_text}"
    )
    user_prompt = (
        "[Task Instruction]\n"
        "다음은 작가가 캐릭터를 추궁한 질문과 답변입니다. 이 답변들을 바탕으로, "
        "실제로 다음 전개에 쓸 수 있는 방향 힌트 2~3개를 카드 형태로 정리하세요. "
        "캐릭터의 답변에서 드러난 감정과 의도를 반영해야 합니다.\n\n"
        "출력은 JSON 배열만 반환하세요. 설명 문장이나 마크다운 코드펜스를 넣지 마세요.\n"
        '형식: [{"title": "짧은 제목", "description": "1~2문장 설명"}, ...]\n'
        "항목은 2개 또는 3개입니다."
    )
    return core + "\n" + dynamic, user_prompt


def _parse_interrogation_hint_cards(raw: object) -> list[dict]:
    cleaned = _strip_json_fence(str(raw or ""))
    data: object
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", cleaned, re.S)
        if not match:
            raise ValueError("json")
        data = json.loads(match.group(0))
    if isinstance(data, dict):
        data = (
            data.get("hint_cards")
            or data.get("events")
            or data.get("ideas")
            or data.get("items")
        )
    if not isinstance(data, list):
        raise ValueError("json")
    cards: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        description = str(item.get("description") or item.get("text") or "").strip()
        if title and description:
            cards.append({"title": title, "description": description})
        if len(cards) == 3:
            break
    if len(cards) < 2:
        raise ValueError("json")
    return cards


def _parse_interrogation_qa(raw: object) -> list[dict]:
    data: object = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = []
    if not isinstance(data, list):
        return []
    items: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        items.append(
            {
                "question": question,
                "answer": str(item.get("answer") or "").strip(),
            }
        )
    return items


def _normalise_hex_color(value: object) -> str:
    text = str(value or "").strip()
    match = _HEX_COLOR_RE.fullmatch(text)
    if not match:
        return ""
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return "#" + digits.upper()


def _mood_color_prompt(
    genre: object, character_name: object, character_settings: object
) -> tuple[str, str]:
    """Assemble personal-color prompts. Reuses Tory Core Identity as-is."""
    genre_label = str(genre or "").strip() or "미정"
    name = str(character_name or "").strip() or "주인공"
    settings = str(character_settings or "").strip() or "(설정집 인물 없음)"
    core = _tory_core_identity()
    dynamic = (
        "[Dynamic Context]\n"
        f"작품 장르: {genre_label}\n\n"
        "[해당 캐릭터 설정]\n"
        f"{settings}"
    )
    user_prompt = (
        "[Task Instruction]\n"
        f"{name}의 성격과 외모 묘사를 기준으로, 이 캐릭터에게 어울리는 "
        "퍼스널컬러 팔레트를 고르세요. 작품 전체 장르나 분위기만 보고 고르지 말고, "
        "아래 캐릭터 설정에 나온 성격·외모에 맞춰 색을 고르세요. "
        "작가가 원고를 고치거나 따라 쓰게 만들지 말고, 그냥 보고 기분 전환할 "
        "색 조합만 제안하세요. 색은 3~4개입니다.\n\n"
        "출력은 JSON 객체만 반환하세요. 설명 문장이나 마크다운 코드펜스를 넣지 마세요.\n"
        '형식: {"colors": [{"name": "색 이름", "hex": "#RRGGBB", "reason": "한 줄 이유"}]}\n'
        "hex는 반드시 #으로 시작하는 6자리 헥스코드입니다. "
        "reason에는 이 캐릭터의 어떤 성격이나 외모 때문에 그 색인지 짧게 적으세요."
    )
    return core + "\n" + dynamic, user_prompt


def _parse_mood_colors(raw: object) -> list[dict]:
    cleaned = _strip_json_fence(str(raw or ""))
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise ValueError("json")
        data = json.loads(match.group(0))
    if isinstance(data, dict):
        data = data.get("colors") or data.get("palette") or data.get("items")
    if not isinstance(data, list):
        raise ValueError("json")
    colors: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        hex_code = _normalise_hex_color(item.get("hex") or item.get("color"))
        reason = str(item.get("reason") or item.get("why") or "").strip()
        if not name or not hex_code:
            continue
        colors.append({"name": name, "hex": hex_code, "reason": reason})
        if len(colors) >= 4:
            break
    if len(colors) < 3:
        raise ValueError("json")
    return colors


def _mood_playlist_prompt(
    genre: object, character_name: object, character_settings: object
) -> tuple[str, str]:
    """Assemble virtual playlist prompts. Reuses Tory Core Identity as-is."""
    genre_label = str(genre or "").strip() or "미정"
    name = str(character_name or "").strip() or "주인공"
    settings = str(character_settings or "").strip() or "(설정집 인물 없음)"
    core = _tory_core_identity()
    dynamic = (
        "[Dynamic Context]\n"
        f"작품 장르: {genre_label}\n\n"
        "[해당 캐릭터 설정]\n"
        f"{settings}"
    )
    user_prompt = (
        "[Task Instruction]\n"
        f"{name}의 오늘을 위한 가상 플레이리스트를 만드세요. "
        "실제 존재하는 노래 제목, 가수, 앨범, OST 제목을 절대 쓰지 마세요. "
        "완전히 지어낸 곡 제목과 짧은 분위기만 적으세요. 곡은 5곡입니다.\n\n"
        "출력은 JSON 객체만 반환하세요. 설명 문장이나 마크다운 코드펜스를 넣지 마세요.\n"
        '형식: {"playlist_title": "플레이리스트 제목", '
        '"tracks": [{"title": "가상 곡 제목", "mood": "분위기 한 줄"}]}\n'
        "tracks는 반드시 5개입니다."
    )
    return core + "\n" + dynamic, user_prompt


def _parse_mood_playlist(raw: object) -> dict:
    cleaned = _strip_json_fence(str(raw or ""))
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise ValueError("json")
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("json")
    title = str(data.get("playlist_title") or data.get("title") or "").strip()
    tracks_raw = data.get("tracks") or data.get("songs") or data.get("items")
    if not isinstance(tracks_raw, list):
        raise ValueError("json")
    tracks: list[dict] = []
    for item in tracks_raw:
        if not isinstance(item, dict):
            continue
        song = str(item.get("title") or item.get("name") or "").strip()
        mood = str(item.get("mood") or item.get("vibe") or "").strip()
        if not song:
            continue
        tracks.append({"title": song, "mood": mood})
        if len(tracks) >= 5:
            break
    if not title or len(tracks) < 5:
        raise ValueError("json")
    return {"playlist_title": title, "tracks": tracks[:5]}


def _mood_board_keywords_prompt(genre: object) -> tuple[str, str]:
    """Assemble English Pexels query prompts. Reuses Tory Core Identity as-is."""
    genre_label = str(genre or "").strip() or "미정"
    core = _tory_core_identity()
    dynamic = (
        "[Dynamic Context]\n"
        f"작품 장르: {genre_label}"
    )
    user_prompt = (
        "[Task Instruction]\n"
        "이 작품 분위기를 담은 사진 검색용 영어 키워드 2~3개를 만드세요. "
        "Pexels 검색에 바로 쓸 짧은 영어 구만 사용하세요. "
        "한국어, 설명 문장, 실존 작품/인물 이름은 넣지 마세요.\n\n"
        "출력은 JSON 객체만 반환하세요. 설명 문장이나 마크다운 코드펜스를 넣지 마세요.\n"
        '형식: {"keywords": ["foggy forest path", "candlelight portrait"]}'
    )
    return core + "\n" + dynamic, user_prompt


def _parse_mood_board_keywords(raw: object) -> list[str]:
    cleaned = _strip_json_fence(str(raw or ""))
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", cleaned, re.S)
        if not match:
            raise ValueError("json")
        data = json.loads(match.group(0))
    if isinstance(data, dict):
        data = data.get("keywords") or data.get("queries") or data.get("items")
    if not isinstance(data, list):
        raise ValueError("json")
    keywords: list[str] = []
    for item in data:
        text = str(item or "").strip()
        if not text:
            continue
        keywords.append(text[:80])
        if len(keywords) >= 3:
            break
    if len(keywords) < 2:
        raise ValueError("json")
    return keywords


def _normalise_mood_keywords(raw: object) -> list[str]:
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = [part.strip() for part in text.split(",") if part.strip()]
            return _normalise_mood_keywords(parsed)
        return [part.strip()[:80] for part in re.split(r"[,/\n]+", text) if part.strip()][:3]
    if not isinstance(raw, list):
        return []
    keywords: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        keywords.append(text[:80])
        if len(keywords) >= 3:
            break
    return keywords


def _pexels_api_key() -> str:
    return str(env_loader.get_env("PEXELS_API_KEY") or "").strip()


def _fetch_pexels_photos(keywords: list[str], per_page: int = 6) -> list[dict]:
    key = _pexels_api_key()
    if not key:
        raise ValueError(MOOD_BOARD_EMPTY_KEY_MSG)
    queries = [item for item in keywords if str(item).strip()][:3]
    if not queries:
        queries = ["moody atmosphere"]
    photos: list[dict] = []
    seen: set[str] = set()
    share = max(2, min(6, per_page // max(1, len(queries))))
    for query in queries:
        params = quote(query)
        url = f"{PEXELS_SEARCH_URL}?query={params}&per_page={share}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": key,
                "User-Agent": "SuperTory/1.0",
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                raise ValueError(MOOD_BOARD_EMPTY_KEY_MSG) from error
            raise ValueError("이미지 검색에 실패했어요") from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ValueError("이미지 검색에 실패했어요") from error
        rows = payload.get("photos") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, dict):
                continue
            src = item.get("src")
            src = src if isinstance(src, dict) else {}
            image_url = str(
                src.get("large") or src.get("medium") or src.get("original") or ""
            ).strip()
            if not image_url or image_url in seen:
                continue
            if not image_url.startswith(("https://", "http://")):
                continue
            seen.add(image_url)
            photos.append(
                {
                    "url": image_url,
                    "photographer": str(item.get("photographer") or "").strip(),
                    "alt": str(item.get("alt") or "").strip(),
                }
            )
            if len(photos) >= per_page:
                return photos
    if not photos:
        raise ValueError("맞는 이미지를 찾지 못했어요")
    return photos


def _word_list_prompt(genre: object) -> tuple[str, str]:
    """Assemble genre word-list prompts. Reuses Tory Core Identity as-is."""
    genre_label = str(genre or "").strip() or "미정"
    core = _tory_core_identity()
    dynamic = (
        "[Dynamic Context]\n"
        f"작품 장르: {genre_label}"
    )
    user_prompt = (
        "[Task Instruction]\n"
        "이 장르를 쓸 때 입안에서 굴려보면 기분 좋아지는 한국어 단어 10개를 고르세요. "
        "작가가 지금 원고에 넣으라고 재촉하지 말고, 단어와 짧은 뉘앙스만 보여 주세요.\n\n"
        "출력은 JSON 객체만 반환하세요. 설명 문장이나 마크다운 코드펜스를 넣지 마세요.\n"
        '형식: {"words": [{"word": "단어", "nuance": "짧은 뉘앙스"}]}\n'
        "words는 반드시 10개입니다."
    )
    return core + "\n" + dynamic, user_prompt


def _parse_word_list(raw: object) -> list[dict]:
    cleaned = _strip_json_fence(str(raw or ""))
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", cleaned, re.S)
        if not match:
            raise ValueError("json")
        data = json.loads(match.group(0))
    if isinstance(data, dict):
        data = data.get("words") or data.get("items") or data.get("list")
    if not isinstance(data, list):
        raise ValueError("json")
    words: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        word = str(item.get("word") or item.get("text") or "").strip()
        nuance = str(item.get("nuance") or item.get("note") or "").strip()
        if not word:
            continue
        words.append({"word": word, "nuance": nuance})
        if len(words) >= 10:
            break
    if len(words) < 8:
        raise ValueError("json")
    return words[:10]

SENTENCE_LIST_KINDS = ("novel", "quote", "wit", "fact")


def _sentence_kind_key(label: object) -> str:
    text = str(label or "").strip()
    folded = text.lower()
    aliases = {
        "novel": "novel",
        "quote": "quote",
        "wit": "wit",
        "witty": "wit",
        "fact": "fact",
        "trivia": "fact",
        "소설": "novel",
        "소설 문장": "novel",
        "소설문장": "novel",
        "명언": "quote",
        "격언": "quote",
        "위트": "wit",
        "재치": "wit",
        "한 줄": "wit",
        "놀라운 사실": "fact",
        "사실": "fact",
    }
    if folded in aliases:
        return aliases[folded]
    if text in aliases:
        return aliases[text]
    if "소설" in text:
        return "novel"
    if "명언" in text or "격언" in text:
        return "quote"
    if "위트" in text or "재치" in text:
        return "wit"
    if "사실" in text or "trivia" in folded:
        return "fact"
    return ""


def _sentence_list_prompt(genre: object) -> tuple[str, str]:
    """Assemble genre sentence-list prompts. Reuses Tory Core Identity as-is."""
    genre_label = str(genre or "").strip() or "미정"
    core = _tory_core_identity()
    dynamic = (
        "[Dynamic Context]\n"
        f"작품 장르: {genre_label}"
    )
    user_prompt = (
        "[Task Instruction]\n"
        "이 장르를 쓸 때 입안에서 굴려보면 기분 좋아지는 한국어 문장집 10줄을 고르세요. "
        "작가가 지금 원고에 넣으라고 재촉하지 말고, 문장과 짧은 여운만 보여 주세요.\n"
        "종류를 섞으세요. 한 종류만 몰아치지 말고, 아래 네 종류가 모두 들어가게 하세요.\n"
        "1. novel: 소설에 나올 법한 한 문장. 대사나 묘사 모두 가능.\n"
        "2. quote: 명언처럼 남는 짧은 한 줄. 실제 유명인의 말을 베끼지 말고 새로 창작하세요.\n"
        "3. wit: 위트 있는 한 줄. 살짝 비꼬거나 웃긴 관찰.\n"
        "4. fact: '세상에 이런 일이'처럼 놀랍고 그럴듯한 사실 한 줄. "
        "교과서 나열이 아니라, 이야기에 쓸 수 있을 법한 여운으로.\n"
        "저작권이 있는 소설·명언·대사를 그대로 옮기지 마세요.\n\n"
        "출력은 JSON 객체만 반환하세요. 설명 문장이나 마크다운 코드펜스를 넣지 마세요.\n"
        '형식: {"sentences": [{"kind": "novel|quote|wit|fact", '
        '"text": "문장", "note": "짧은 여운"}]}\n'
        "sentences는 반드시 10개입니다. kind는 영어 소문자 키만 쓰세요."
    )
    return core + "\n" + dynamic, user_prompt


def _parse_sentence_list(raw: object) -> list[dict]:
    cleaned = _strip_json_fence(str(raw or ""))
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", cleaned, re.S)
        if not match:
            raise ValueError("json")
        data = json.loads(match.group(0))
    if isinstance(data, dict):
        data = data.get("sentences") or data.get("items") or data.get("list")
    if not isinstance(data, list):
        raise ValueError("json")
    sentences: list[dict] = []
    kinds: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("sentence") or item.get("line") or "").strip()
        if not text:
            continue
        kind = _sentence_kind_key(item.get("kind") or item.get("type") or item.get("label"))
        note = str(item.get("note") or item.get("nuance") or "").strip()
        if kind in SENTENCE_LIST_KINDS:
            kinds.add(kind)
        sentences.append({
            "kind": kind if kind in SENTENCE_LIST_KINDS else "novel",
            "text": text,
            "note": note,
        })
        if len(sentences) >= 10:
            break
    if len(sentences) < 8:
        raise ValueError("json")
    if len(kinds) < 3:
        raise ValueError("json")
    return sentences[:10]

NAMING_SHOP_CATEGORIES = (
    "동양풍 인명",
    "서양풍 인명",
    "현대 인명",
    "고대 무기·아이템",
    "동서양 요괴·괴물",
    "지명·장소",
    "무협 용어",
)


def _naming_shop_prompt(genre: object) -> tuple[str, str]:
    """Assemble naming-shop prompts. Reuses Tory Core Identity as-is."""
    genre_label = str(genre or "").strip() or "미정"
    core = _tory_core_identity()
    dynamic = (
        "[Dynamic Context]\n"
        f"작품 장르: {genre_label}"
    )
    user_prompt = (
        "[Task Instruction]\n"
        "다음 7개 그룹의 이름/명칭을 각각 4~5개씩 만드세요. "
        "하나의 스타일에 국한하지 말고 다양하게:\n"
        "1. 동양풍 인명 (한자 느낌의 이름)\n"
        "2. 서양풍 인명 (판타지 서양 느낌의 이름)\n"
        "3. 현대 인명 (실제 한국 이름 같은 자연스러운 이름)\n"
        "4. 고대 무기·아이템 이름 (전설의 검, 유물 등)\n"
        "5. 동서양 요괴·괴물 이름 (한국·동아시아 전통 설화 느낌과 서양 판타지 "
        "괴물 느낌을 섞어서, 창작 요괴 포함해 4~5개)\n"
        "6. 지명·장소 이름 (마을, 성, 숲 등)\n"
        "7. 무협 용어 (문파 이름, 무공/초식 이름, 내공심법 이름, 진법 이름을 "
        "섞어서 4~5개, 한자어 느낌으로 그럴듯하게)\n"
        "각 이름에 짧은 어감/뉘앙스 설명을 한 줄씩 붙이세요.\n"
        "저작권이 있는 기존 소설·만화·게임의 유명 캐릭터명, 지명, 무기명, "
        "요괴명과 겹치지 않게, 새로 창작한 이름만 쓰세요.\n\n"
        "출력은 JSON 객체만 반환하세요. 설명 문장이나 마크다운 코드펜스를 넣지 마세요.\n"
        '형식: {"groups": [{"category": "동양풍 인명", '
        '"items": [{"name": "이름", "nuance": "짧은 뉘앙스"}]}, '
        '{"category": "무협 용어", "items": [{"name": "이름", "nuance": "짧은 뉘앙스"}]}]}\n'
        "groups는 위 7개 카테고리를 모두 포함해야 하고, 각 items는 4개 또는 5개입니다."
    )
    return core + "\n" + dynamic, user_prompt


def _naming_shop_category_key(label: object) -> str:
    text = str(label or "").strip()
    if text in NAMING_SHOP_CATEGORIES:
        return text
    if (
        "무협" in text
        or "문파" in text
        or "무공" in text
        or "초식" in text
        or "내공" in text
        or "진법" in text
    ):
        return "무협 용어"
    if "무기" in text or "아이템" in text or "유물" in text:
        return "고대 무기·아이템"
    if "요괴" in text or "괴물" in text:
        return "동서양 요괴·괴물"
    if "지명" in text or "장소" in text or "마을" in text:
        return "지명·장소"
    if "현대" in text:
        return "현대 인명"
    if "서양" in text:
        return "서양풍 인명"
    if "동양" in text or "한자" in text:
        return "동양풍 인명"
    return ""


def _parse_naming_shop(raw: object) -> list[dict]:
    cleaned = _strip_json_fence(str(raw or ""))
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise ValueError("json")
        data = json.loads(match.group(0))
    groups_raw = None
    if isinstance(data, dict):
        groups_raw = data.get("groups") or data.get("categories") or data.get("items")
        if groups_raw is None:
            groups_raw = [
                {"category": key, "items": value}
                for key, value in data.items()
                if key in NAMING_SHOP_CATEGORIES
            ]
    elif isinstance(data, list):
        groups_raw = data
    if not isinstance(groups_raw, list):
        raise ValueError("json")
    by_category: dict[str, list[dict]] = {}
    for group in groups_raw:
        if not isinstance(group, dict):
            continue
        category = _naming_shop_category_key(
            group.get("category") or group.get("title") or group.get("name")
        )
        items_raw = group.get("items") or group.get("names") or group.get("list")
        if not category or not isinstance(items_raw, list):
            continue
        items: list[dict] = []
        for item in items_raw:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("title") or item.get("word") or "").strip()
                nuance = str(item.get("nuance") or item.get("note") or item.get("meaning") or "").strip()
            else:
                name = str(item or "").strip()
                nuance = ""
            if not name:
                continue
            items.append({"name": name, "nuance": nuance})
            if len(items) >= 5:
                break
        if len(items) >= 4:
            by_category[category] = items[:5]
    groups = [
        {"category": category, "items": by_category[category]}
        for category in NAMING_SHOP_CATEGORIES
        if category in by_category
    ]
    if len(groups) < len(NAMING_SHOP_CATEGORIES):
        raise ValueError("json")
    return groups


def _character_tarot_prompt(
    genre: object,
    character_name: object,
    character_settings: object,
    picked_cards: list[dict],
    style: object,
) -> tuple[str, str]:
    """Assemble a selected-card character-tarot prompt with a distinct Tory voice."""
    genre_label = str(genre or "").strip() or "미정"
    name = str(character_name or "").strip() or "주인공"
    settings = str(character_settings or "").strip() or "(설정집 인물 없음)"
    style_key = tarot_deck.normalize_tarot_style(style)
    reader_name = "몽상가 토리" if style_key == "dreamer" else "모험가 토리"
    voice = (
        "엉뚱하고 몽환적인 비유를 쓰되 문장은 또렷하게 끝내세요. "
        "꿈, 별빛, 구름, 엉뚱한 사물의 연결로 웃음을 만들고 마지막에 현실적인 한마디로 착지하세요."
        if style_key == "dreamer"
        else
        "빠르고 씩씩한 탐험 비유를 쓰세요. 지도, 배낭, 함정, 퀘스트 같은 표현으로 "
        "경쾌하게 놀리되 마지막에는 당장 써먹을 수 있는 서사 힌트로 착지하세요."
    )
    positions = [
        f"{tarot_deck.TAROT_POSITION_LABELS[position]}: {card['id']}번 {card['ko']} ({card['en']})"
        for position, card in zip(tarot_deck.TAROT_POSITIONS, picked_cards)
    ]
    core = _tory_core_identity()
    dynamic = (
        "[Dynamic Context]\n"
        f"작품 장르: {genre_label}\n\n"
        "[해당 캐릭터 설정]\n"
        f"{settings}\n\n"
        f"[타로 리더]\n{reader_name}\n\n"
        "[사용자가 직접 고른 카드와 위치]\n"
        + "\n".join(positions)
    )
    user_prompt = (
        "[Task Instruction]\n"
        f"사용자가 고른 타로 카드 세 장만 사용해 {name}의 서사를 풀이하세요. 카드나 순서를 바꾸지 마세요.\n"
        f"{reader_name}의 말투 지침: {voice}\n\n"
        "반드시 캐릭터 설정에 나온 구체적인 성격, 버릇, 관계, 갈등 중 하나 이상을 각 풀이에 연결하세요. "
        "누구에게나 통하는 위로, 엄숙한 예언, '새로운 시작', '내면의 힘' 같은 상투어만으로 채우지 마세요. "
        "각 항목은 2~3문장으로 쓰고, 적어도 한 문장은 짧은 비유·말장난·반전으로 웃기세요. "
        "비웃거나 모욕하지 말고 캐릭터를 다정하게 놀리세요. "
        "generalTip은 작가가 다음 장면에 바로 써먹을 수 있는 한 문장과 재치 있는 마무리 한 문장으로 쓰세요. "
        "실제 점술이 아니라 캐릭터 서사를 위한 창작 놀이임이 자연스럽게 느껴져야 합니다.\n\n"
        "출력은 JSON 객체만 반환하세요. 설명 문장이나 마크다운 코드펜스를 넣지 마세요.\n"
        '형식: {"positions":{"cause":"원인 카드 풀이","incident":"돌발사건 카드 풀이",'
        '"ending":"결말 카드 풀이"},"generalTip":"종합 한마디"}'
    )
    return core + "\n" + dynamic, user_prompt


def _parse_character_tarot(raw: object) -> list[dict]:
    cleaned = _strip_json_fence(str(raw or ""))
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}|\[.*\]", cleaned, re.S)
        if not match:
            raise ValueError("json")
        data = json.loads(match.group(0))
    if isinstance(data, dict):
        data = data.get("cards") or data.get("items") or data.get("list")
    if not isinstance(data, list):
        raise ValueError("json")
    cards: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("title") or "").strip()
        meaning = str(item.get("meaning") or item.get("reading") or item.get("text") or "").strip()
        if not name or not meaning:
            continue
        cards.append({"name": name, "meaning": meaning})
        if len(cards) >= 3:
            break
    if len(cards) < 2:
        raise ValueError("json")
    return cards


def _fill_blank_skeleton_prompt(
    genre: object, episode_content: object, character_settings: object
) -> tuple[str, str]:
    """Assemble Fill-blank Game prompts. Reuses Tory Core Identity as-is."""
    excerpt = _clip_recent_episode(episode_content, FILL_BLANK_EPISODE_CAP)
    genre_label = str(genre or "").strip() or "미정"
    settings = str(character_settings or "").strip() or "(설정집 인물 없음)"
    core = _tory_core_identity()
    dynamic = (
        "[Dynamic Context]\n"
        f"작품 장르: {genre_label}\n\n"
        "[설정집 · 등장인물 이름/말투]\n"
        f"{settings}\n\n"
        "아래는 작가가 막힌 지점의 최근 원고입니다.\n"
        f"{excerpt}"
    )
    user_prompt = (
        "[Task Instruction]\n"
        "다음 원고에 이어질 장면의 뼈대를 만드세요. 이미 정해진 지문·대사는 "
        "그대로 쓰고, 작가가 직접 채워야 할 부분만 빈칸으로 남기세요. 빈칸은 3~5개 사이로, "
        "각 빈칸에는 무엇을 채워야 하는지 짧은 힌트를 붙이세요. 등장인물 이름과 말투는 "
        "설정집 내용을 정확히 따르세요.\n\n"
        "출력은 JSON 객체만 반환하세요. 설명 문장이나 마크다운 코드펜스를 넣지 마세요.\n"
        "형식:\n"
        "{\n"
        '  "segments": [\n'
        '    {"type": "fixed", "text": "이미 정해진 문장"},\n'
        '    {"type": "blank", "id": "b1", "hint": "인물 A가 방에 들어오는 동작 묘사"},\n'
        '    {"type": "fixed", "text": "\\"..."},\n'
        '    {"type": "blank", "id": "b2", "hint": "B의 감정이 드러나는 대사"}\n'
        "  ]\n"
        "}\n"
        "fixed와 blank가 순서대로 섞여서 이어붙이면 하나의 완성된 문단이 되어야 합니다. "
        "blank id는 b1, b2, … 처럼 서로 달라야 합니다."
    )
    return core + "\n" + dynamic, user_prompt


def _parse_fill_blank_skeleton(raw: object) -> list[dict]:
    cleaned = _strip_json_fence(str(raw or "")).strip()
    data: object
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise ValueError("json")
        data = json.loads(match.group(0))
    if isinstance(data, dict):
        segments_raw = data.get("segments")
        if segments_raw is None:
            segments_raw = data.get("items") or data.get("skeleton")
    else:
        segments_raw = data
    if not isinstance(segments_raw, list) or not segments_raw:
        raise ValueError("json")
    segments: list[dict] = []
    used_ids: set[str] = set()
    blank_n = 0
    for item in segments_raw:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").strip().lower()
        if kind in {"text", "given", "fixed_text"}:
            kind = "fixed"
        if kind == "fixed":
            text = str(item.get("text") or item.get("content") or "")
            segments.append({"type": "fixed", "text": text})
            continue
        if kind not in {"blank", "fill", "slot"}:
            continue
        blank_n += 1
        blank_id = str(item.get("id") or "").strip() or f"b{blank_n}"
        if blank_id in used_ids:
            suffix = 2
            while f"{blank_id}_{suffix}" in used_ids:
                suffix += 1
            blank_id = f"{blank_id}_{suffix}"
        used_ids.add(blank_id)
        hint = str(item.get("hint") or item.get("prompt") or item.get("label") or "").strip()
        if not hint:
            hint = "이 칸을 채워 주세요"
        segments.append({"type": "blank", "id": blank_id, "hint": hint})
    blank_count = sum(1 for item in segments if item["type"] == "blank")
    if blank_count < FILL_BLANK_MIN_BLANKS or blank_count > FILL_BLANK_MAX_BLANKS:
        raise ValueError("json")
    if not any(item["type"] == "fixed" for item in segments):
        raise ValueError("json")
    return segments


def _assemble_fill_blank_text(segments: list[dict], answers: object) -> str:
    mapping: dict[str, str] = {}
    if isinstance(answers, dict):
        mapping = {str(key): str(value or "") for key, value in answers.items()}
    blank_ids = [item["id"] for item in segments if item.get("type") == "blank"]
    if not blank_ids:
        raise ValueError("빈칸 뼈대가 손상되었습니다.")
    for blank_id in blank_ids:
        if not str(mapping.get(blank_id) or "").strip():
            raise ValueError("모든 빈칸을 채워주세요")
    parts: list[str] = []
    for item in segments:
        if item.get("type") == "fixed":
            parts.append(str(item.get("text") or ""))
            continue
        parts.append(str(mapping.get(item.get("id")) or ""))
    return "".join(parts)


def _parse_pingpong_turns(raw: object) -> list[dict]:
    if isinstance(raw, list):
        data = raw
    else:
        text = str(raw or "").strip() or "[]"
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
    if not isinstance(data, list):
        return []
    turns: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        speaker = str(item.get("speaker") or "").strip().lower()
        if speaker in {"author", "writer", "human"}:
            speaker = "user"
        if speaker in {"tory", "ai", "assistant"}:
            speaker = "tori"
        if speaker not in {"user", "tori"}:
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        turns.append({"speaker": speaker, "text": text})
    return turns


def _format_pingpong_turns(turns: list[dict]) -> str:
    if not turns:
        return "(아직 주고받은 문장이 없습니다. 작가가 첫 문장을 건넵니다.)"
    lines: list[str] = []
    for item in turns:
        who = "작가" if item.get("speaker") == "user" else "토리"
        lines.append(f"{who}: {item.get('text') or ''}")
    text = "\n".join(lines)
    if len(text) > 4000:
        return "…\n" + text[-4000:]
    return text


def _clip_pingpong_reply(raw: object, limit: int = PINGPONG_REPLY_MAX) -> str:
    text = _strip_json_fence(str(raw or "")).strip()
    text = re.sub(r"^(토리|Tory)\s*[:：]\s*", "", text).strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    truncated = text[:limit]
    for sep in ("다. ", "요. ", "죠. ", "다.", "요.", "죠.", ". ", ".\n"):
        pos = truncated.rfind(sep)
        if pos >= 24:
            end = pos + len(sep.rstrip())
            clipped = truncated[:end].strip()
            if clipped:
                return clipped
    return truncated.rstrip()


def _assemble_pingpong_text(turns: list[dict]) -> str:
    parts = [
        str(item.get("text") or "").strip()
        for item in turns
        if str(item.get("text") or "").strip()
    ]
    return " ".join(parts)


def _pingpong_turn_prompt(
    genre: object,
    episode_content: object,
    character_settings: object,
    turns: object,
) -> tuple[str, str]:
    """Assemble ping-pong relay prompts. Reuses Tory Core Identity as-is."""
    excerpt = _clip_recent_episode(episode_content, PINGPONG_EPISODE_CAP)
    genre_label = str(genre or "").strip() or "미정"
    settings = str(character_settings or "").strip() or "(설정집 인물 없음)"
    parsed = _parse_pingpong_turns(turns)
    core = _tory_core_identity()
    dynamic = (
        "[Dynamic Context]\n"
        f"작품 장르: {genre_label}\n\n"
        "[설정집 · 등장인물 이름/말투]\n"
        f"{settings}\n\n"
        "아래는 작가가 막힌 지점의 최근 원고입니다.\n"
        f"{excerpt}\n\n"
        "[이 릴레이에서 지금까지 주고받은 문장]\n"
        f"{_format_pingpong_turns(parsed)}"
    )
    user_prompt = (
        "[Task Instruction]\n"
        "지금 작가와 번갈아가며 소설을 이어 쓰고 있습니다. 작가가 방금 쓴 부분 "
        "다음에 이어질 한두 문장만 쓰세요. 절대 장면을 혼자 마무리 짓거나 여러 문단을 쓰지 마세요 "
        "— 짧게 쓰고 다시 작가에게 넘겨야 합니다. 이야기에 자연스럽게 다음 전개의 여지를 남기되, "
        "지나치게 사건을 크게 벌이지는 마세요. 설정집의 인물 말투와 세계관을 정확히 따르세요.\n\n"
        "출력은 순수 텍스트 한두 문장만 반환하세요. JSON, 제목, 화자 이름, 따옴표 설명은 넣지 마세요."
    )
    return core + "\n" + dynamic, user_prompt


def _lucky_sentence_prompt(genre: object, episode_content: object) -> tuple[str, str]:
    """Assemble Lucky Sentence prompts. Reuses Tory Core Identity as-is."""
    excerpt = _clip_recent_episode(episode_content, LUCKY_SENTENCE_EPISODE_CAP)
    genre_label = str(genre or "").strip() or "미정"
    core = _tory_core_identity()
    dynamic = (
        "[Dynamic Context]\n"
        f"작품 장르: {genre_label}\n\n"
        "아래는 작가가 막힌 지점의 최근 원고입니다.\n"
        f"{excerpt}"
    )
    user_prompt = (
        "[Task Instruction]\n"
        "지금 원고 맥락과 어울리면서도, 다음 문단에 자연스럽게 녹여 쓸 수 있는 "
        "인상적인 문장 하나를 만드세요. 이 문장 자체가 다음 내용을 미리 정해버리면 안 되고, "
        "다양한 방향으로 이어질 수 있도록 열려있어야 합니다. 한 문장으로, 20~40자 사이로 "
        "짧고 강렬하게 쓰세요.\n\n"
        "출력은 순수 텍스트 한 문장만 반환하세요. JSON, 따옴표, 설명 문장은 넣지 마세요."
    )
    return core + "\n" + dynamic, user_prompt


def _clip_lucky_sentence(raw: object) -> str:
    text = _strip_json_fence(str(raw or "")).strip()
    text = text.strip("「」\"'`").strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return ""
    match = re.search(r".+?[.!?。]|[^.!?。]+", text)
    sentence = (match.group(0) if match else text).strip()
    if len(sentence) <= 60:
        return sentence
    return sentence[:40].rstrip()


def _highlight_extraction_prompt(episode_content: object) -> tuple[str, str]:
    """Assemble Mental Vitamin highlight prompts. Reuses Tory Core Identity as-is."""
    excerpt = str(episode_content or "").strip()
    core = _tory_core_identity()
    user_prompt = (
        "[Task Instruction]\n"
        "다음 원고에서 아래 3가지를 찾을 수 있는 만큼 뽑으세요 (없으면 생략 가능):\n"
        "1. 대사: 캐릭터의 개성이나 감정이 가장 잘 드러난 대사 한 줄\n"
        "2. 묘사: 문장 리듬이나 표현이 인상적인 묘사 한 부분\n"
        "3. 장면: 감정선이나 전개가 특히 잘 짜인 장면 한 부분\n"
        "각각 원문 그대로 발췌하고, 왜 뽑았는지 한 줄 이유를 덧붙이세요. "
        "특별히 눈에 띄는 게 없으면 억지로 뽑지 말고 빈 배열을 반환하세요.\n\n"
        "출력은 JSON 배열만 반환하세요. 설명 문장이나 마크다운 코드펜스를 넣지 마세요.\n"
        '형식: [{"type": "dialogue", "excerpt": "...", "reason": "..."}, ...]\n'
        "type은 dialogue / description / scene 중 하나입니다. 항목은 0~3개입니다.\n\n"
        "[원고]\n"
        f"{excerpt}"
    )
    return core, user_prompt


def _parse_highlight_moments(raw: object) -> list[dict]:
    cleaned = _strip_json_fence(str(raw or "")).strip()
    if not cleaned or cleaned in {"[]", "null", "None"}:
        return []
    data: object
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", cleaned, re.S)
        if not match:
            raise ValueError("json")
        data = json.loads(match.group(0))
    if isinstance(data, dict):
        data = data.get("moments") or data.get("items") or data.get("highlights") or []
    if not isinstance(data, list):
        raise ValueError("json")
    seen_types: set[str] = set()
    moments: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or item.get("moment_type") or "").strip().lower()
        excerpt = str(item.get("excerpt") or item.get("text") or "").strip()
        reason = str(item.get("reason") or item.get("why") or "").strip()
        if kind not in GLUMP_HIGHLIGHT_TYPES:
            continue
        if kind in seen_types:
            continue
        if not excerpt or not reason:
            continue
        seen_types.add(kind)
        moments.append({"type": kind, "excerpt": excerpt, "reason": reason})
        if len(moments) == 3:
            break
    return moments


def _highlight_char_count(content: object) -> tuple[str, int]:
    plain = _plain_text(str(content or ""))
    return plain, len(plain)


def _upsert_highlight_progress(
    connection: sqlite3.Connection, episode_id: str, length: int
) -> None:
    stamp = _now()
    connection.execute(
        """
        INSERT INTO glump_highlight_progress (episode_id, last_analyzed_length, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(episode_id) DO UPDATE SET
            last_analyzed_length = excluded.last_analyzed_length,
            updated_at = excluded.updated_at
        """,
        (episode_id, int(length), stamp),
    )


def schedule_glump_highlight_analysis(scene_id: int, content: str) -> None:
    """Fire-and-forget highlight scan. Must never block the save path."""
    try:
        worker = Thread(
            target=_glump_highlight_worker,
            args=(int(scene_id), str(content or "")),
            daemon=True,
            name=f"glump-highlight-{scene_id}",
        )
        worker.start()
    except Exception:
        pass


def _glump_highlight_worker(scene_id: int, content: str) -> None:
    try:
        maybe_extract_glump_highlights(scene_id, content)
    except Exception:
        pass


def maybe_extract_glump_highlights(scene_id: int, content: str) -> None:
    """Analyze newly added manuscript text for Mental Vitamin, if +500 chars."""
    episode_key = str(int(scene_id))
    with _glump_highlight_inflight_lock:
        if episode_key in _glump_highlight_inflight:
            return
        _glump_highlight_inflight.add(episode_key)
    try:
        _extract_glump_highlights_unlocked(scene_id, content)
    finally:
        with _glump_highlight_inflight_lock:
            _glump_highlight_inflight.discard(episode_key)


def _extract_glump_highlights_unlocked(scene_id: int, content: str) -> None:
    if not gemini_client.is_configured():
        return
    plain, current_len = _highlight_char_count(content)
    with _database() as connection:
        scene = connection.execute(
            "SELECT project_id FROM scene WHERE id = ? AND deleted_at IS NULL",
            (scene_id,),
        ).fetchone()
        if scene is None:
            return
        work_id = str(scene["project_id"])
        progress = connection.execute(
            "SELECT last_analyzed_length FROM glump_highlight_progress "
            "WHERE episode_id = ?",
            (str(scene_id),),
        ).fetchone()
        last_len = int(progress["last_analyzed_length"] or 0) if progress else 0
        if current_len < last_len:
            _upsert_highlight_progress(connection, str(scene_id), current_len)
            return
        if current_len - last_len < GLUMP_HIGHLIGHT_MIN_CHARS:
            return
        episode_order = _scene_episode_order(
            connection, int(scene["project_id"]), int(scene_id)
        )
    chunk = plain[last_len:]
    if len(chunk) > GLUMP_HIGHLIGHT_CHUNK_MAX:
        chunk = chunk[-GLUMP_HIGHLIGHT_CHUNK_MAX:]
    if len(chunk.strip()) < GLUMP_HIGHLIGHT_MIN_CHARS:
        with _database() as connection:
            _upsert_highlight_progress(connection, str(scene_id), current_len)
        return
    system, user_prompt = _highlight_extraction_prompt(chunk)
    try:
        raw = gemini_client.generate_text(
            user_prompt, system=system, temperature=0.4
        )
        moments = _parse_highlight_moments(raw)
    except Exception:
        return
    stamp = _now()
    with _database() as connection:
        for item in moments:
            connection.execute(
                """
                INSERT INTO glump_highlight_moments
                    (id, work_id, episode_id, episode_order, moment_type,
                     excerpt, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    work_id,
                    str(scene_id),
                    int(episode_order or 0),
                    item["type"],
                    item["excerpt"],
                    item["reason"],
                    stamp,
                ),
            )
        live = connection.execute(
            "SELECT content_md FROM scene_revision "
            "WHERE scene_id = ? AND is_current = 1",
            (scene_id,),
        ).fetchone()
        live_len = current_len
        if live is not None:
            live_len = _highlight_char_count(live["content_md"])[1]
        _upsert_highlight_progress(
            connection, str(scene_id), min(current_len, live_len)
        )


def pick_mental_vitamin_moments(rows: list) -> list[dict]:
    """Newest-first: fill dialogue/description/scene, skip same type in the same episode."""
    picked: list[dict] = []
    have_types: set[str] = set()
    seen_episode_type: set[tuple[str, str]] = set()
    for row in rows:
        kind = str(row["moment_type"] or "").strip()
        episode_id = str(row["episode_id"] or "")
        if kind not in GLUMP_HIGHLIGHT_TYPES or kind in have_types:
            continue
        marker = (episode_id, kind)
        if marker in seen_episode_type:
            continue
        seen_episode_type.add(marker)
        have_types.add(kind)
        picked.append(
            {
                "type": kind,
                "excerpt": str(row["excerpt"] or ""),
                "reason": str(row["reason"] or ""),
                "episode_id": episode_id,
                "episode_order": int(row["episode_order"] or 0),
            }
        )
        if len(have_types) == 3:
            break
    return picked


def _glump_work_genre_label(work_id: object) -> str:
    work_key = str(work_id or "").strip()
    if not work_key:
        raise ValueError("작품을 선택해 주세요.")
    with _database() as connection:
        row = connection.execute(
            "SELECT main_genre, sub_genre FROM project "
            "WHERE id = ? AND deleted_at IS NULL",
            (work_key,),
        ).fetchone()
    if row is None:
        raise LookupError("작품을 찾을 수 없습니다.")
    main = _genre_display_label(str(row["main_genre"] or ""))
    sub = _genre_display_label(str(row["sub_genre"] or ""))
    if sub and sub != "미정":
        return f"{main} / {sub}"
    return main


def glump_diagnose(handler, body: dict) -> dict:
    """Rule-based Glump ER diagnosis. Does not call Gemini."""
    work_id = str(body.get("work_id") or body.get("project_id") or "").strip()
    if not work_id:
        raise ValueError("작품을 선택해 주세요.")
    diagnosis = diagnose_glump(body.get("q1_answer"), body.get("q2_answer"))
    payload = {
        "recommended_tool": diagnosis["recommended_tool"],
        "message": diagnosis["message"],
    }
    if diagnosis.get("show_rest_choice"):
        payload["show_rest_choice"] = True
    with _database() as connection:
        connection.execute(
            """
            INSERT INTO glump_diagnosis_logs
                (id, work_id, q1_answer, q2_answer, recommended_tool, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                work_id,
                diagnosis["q1_answer"],
                diagnosis["q2_answer"],
                diagnosis["recommended_tool"],
                _now(),
            ),
        )
    return payload


def glump_diagnosis_stats(handler) -> dict:
    q1_counts = {key: 0 for key in GLUMP_Q1_ANSWERS}
    q2_counts = {key: 0 for key in GLUMP_Q2_ANSWERS}
    with _database() as connection:
        for row in connection.execute(
            "SELECT q1_answer, COUNT(*) AS n FROM glump_diagnosis_logs "
            "GROUP BY q1_answer"
        ):
            key = str(row["q1_answer"] or "")
            if key in q1_counts:
                q1_counts[key] = int(row["n"])
        for row in connection.execute(
            "SELECT q2_answer, COUNT(*) AS n FROM glump_diagnosis_logs "
            "WHERE q2_answer IS NOT NULL GROUP BY q2_answer"
        ):
            key = str(row["q2_answer"] or "")
            if key in q2_counts:
                q2_counts[key] = int(row["n"])
    return {"q1_answer": q1_counts, "q2_answer": q2_counts}


def glump_wildcard_spark(handler, body: dict) -> dict:
    """Glump ER wildcard spark. Does not touch Tory/reader chat handlers."""
    work_id = str(body.get("work_id") or body.get("project_id") or "").strip()
    episode_content = str(body.get("episode_content") or "").strip()
    stage = str(body.get("stage") or "").strip() or None
    if not work_id:
        raise ValueError("작품을 선택해 주세요.")
    if len(episode_content) < WILDCARD_SPARK_MIN_CHARS:
        raise ValueError(
            "원고 내용이 너무 짧아요, 조금 더 써주시면 맥락을 파악할 수 있어요"
        )
    genre = _glump_work_genre_label(work_id)
    system, user_prompt = _wildcard_spark_prompt(genre, episode_content, stage)
    last_error: Exception | None = None
    events: list[dict] | None = None
    for _attempt in range(2):
        try:
            raw = gemini_client.generate_text(
                user_prompt, system=system, temperature=0.95
            )
            events = _parse_wildcard_spark_events(raw)
            break
        except gemini_client.GeminiError as error:
            raise ValueError(str(error)) from error
        except (ValueError, json.JSONDecodeError) as error:
            last_error = error
    if events is None:
        raise GlumpRetryError("다시 시도해주세요") from last_error
    _insert_glump_tool_log(work_id, "wildcard_spark")
    return {"events": events}


def glump_fill_blank_start(handler, body: dict) -> dict:
    """Create a fill-in-the-blank skeleton. Does not touch Tory/reader chat."""
    work_id = str(body.get("work_id") or body.get("project_id") or "").strip()
    episode_id = str(body.get("episode_id") or body.get("scene_id") or "").strip()
    episode_content = str(body.get("episode_content") or "").strip()
    if not work_id:
        raise ValueError("작품을 선택해 주세요.")
    if len(episode_content) < FILL_BLANK_MIN_CHARS:
        raise ValueError(
            "원고 내용이 너무 짧아요, 조금 더 써주시면 맥락을 파악할 수 있어요"
        )
    genre = _glump_work_genre_label(work_id)
    settings = _glump_character_settings(work_id)
    system, user_prompt = _fill_blank_skeleton_prompt(
        genre, episode_content, settings
    )
    last_error: Exception | None = None
    segments: list[dict] | None = None
    for _attempt in range(2):
        try:
            raw = gemini_client.generate_text(
                user_prompt, system=system, temperature=0.7
            )
            segments = _parse_fill_blank_skeleton(raw)
            break
        except gemini_client.GeminiError as error:
            raise ValueError(str(error)) from error
        except (ValueError, json.JSONDecodeError) as error:
            last_error = error
    if segments is None:
        raise GlumpRetryError("다시 시도해주세요") from last_error
    session_id = uuid.uuid4().hex
    stamp = _now()
    payload = {"segments": segments}
    with _database() as connection:
        connection.execute(
            """
            UPDATE glump_fill_blank_sessions
            SET status = 'abandoned'
            WHERE work_id = ? AND episode_id = ? AND status = 'in_progress'
            """,
            (work_id, episode_id),
        )
        connection.execute(
            """
            INSERT INTO glump_fill_blank_sessions
                (id, work_id, episode_id, skeleton_json, status, created_at)
            VALUES (?, ?, ?, ?, 'in_progress', ?)
            """,
            (
                session_id,
                work_id,
                episode_id,
                json.dumps(payload, ensure_ascii=False),
                stamp,
            ),
        )
    return {"session_id": session_id, "segments": segments}


def glump_fill_blank_submit(handler, body: dict) -> dict:
    """Stitch filled blanks into one paragraph. Does not call Gemini."""
    session_id = str(body.get("session_id") or body.get("id") or "").strip()
    if not session_id:
        raise ValueError("세션을 찾을 수 없습니다.")
    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("모든 빈칸을 채워주세요")
    with _database() as connection:
        row = connection.execute(
            """
            SELECT id, work_id, skeleton_json, status
            FROM glump_fill_blank_sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            raise ValueError("세션을 찾을 수 없습니다.")
        if str(row["status"] or "") != "in_progress":
            raise ValueError("이미 끝난 빈칸 세션입니다.")
        try:
            skeleton = json.loads(str(row["skeleton_json"] or "{}"))
        except json.JSONDecodeError as error:
            raise ValueError("빈칸 뼈대가 손상되었습니다.") from error
        segments = skeleton.get("segments") if isinstance(skeleton, dict) else None
        if not isinstance(segments, list):
            raise ValueError("빈칸 뼈대가 손상되었습니다.")
        final_text = _assemble_fill_blank_text(segments, answers)
        connection.execute(
            "UPDATE glump_fill_blank_sessions SET status = 'completed' WHERE id = ?",
            (session_id,),
        )
        work_id = str(row["work_id"] or "")
    if work_id:
        _insert_glump_tool_log(work_id, "fill_blank_game")
    return {"final_text": final_text}


def glump_pingpong_start(handler, body: dict) -> dict:
    """Open a ping-pong relay. Does not call Gemini or touch Tory/reader handlers."""
    work_id = str(body.get("work_id") or body.get("project_id") or "").strip()
    episode_id = str(body.get("episode_id") or body.get("scene_id") or "").strip()
    episode_content = str(body.get("episode_content") or "").strip()
    if not work_id:
        raise ValueError("작품을 선택해 주세요.")
    if len(episode_content) < PINGPONG_MIN_CHARS:
        raise ValueError(
            "원고 내용이 너무 짧아요, 조금 더 써주시면 맥락을 파악할 수 있어요"
        )
    _glump_work_genre_label(work_id)
    excerpt = _clip_recent_episode(episode_content, PINGPONG_EPISODE_CAP)
    session_id = uuid.uuid4().hex
    stamp = _now()
    with _database() as connection:
        connection.execute(
            """
            UPDATE glump_pingpong_sessions
            SET status = 'ended', updated_at = ?
            WHERE work_id = ? AND episode_id = ? AND status = 'active'
            """,
            (stamp, work_id, episode_id),
        )
        connection.execute(
            """
            INSERT INTO glump_pingpong_sessions
                (id, work_id, episode_id, episode_excerpt, turns_json,
                 chars_since_checkin, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, '[]', 0, 'active', ?, ?)
            """,
            (session_id, work_id, episode_id, excerpt, stamp, stamp),
        )
    return {"session_id": session_id}


def glump_pingpong_turn(handler, body: dict) -> dict:
    """Author turn + Tory one-or-two sentence continuation."""
    session_id = str(body.get("session_id") or body.get("id") or "").strip()
    user_text = str(body.get("user_text") or body.get("text") or "").strip()
    if not session_id:
        raise LookupError("세션을 찾을 수 없습니다.")
    if not user_text:
        raise ValueError("한 문장을 먼저 써 주세요.")
    with _database() as connection:
        row = connection.execute(
            """
            SELECT work_id, episode_excerpt, turns_json, chars_since_checkin, status
            FROM glump_pingpong_sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
    if row is None:
        raise LookupError("세션을 찾을 수 없습니다.")
    if str(row["status"] or "") != "active":
        raise ValueError("이미 끝난 세션이에요")
    work_id = str(row["work_id"] or "")
    turns = _parse_pingpong_turns(row["turns_json"])
    pending = list(turns)
    pending.append({"speaker": "user", "text": user_text})
    genre = _glump_work_genre_label(work_id)
    settings = _glump_character_settings(work_id)
    system, user_prompt = _pingpong_turn_prompt(
        genre, row["episode_excerpt"] or "", settings, pending
    )
    last_error: Exception | None = None
    tori_text = ""
    for _attempt in range(2):
        try:
            raw = gemini_client.generate_text(
                user_prompt, system=system, temperature=0.8
            )
            tori_text = _clip_pingpong_reply(raw)
            if tori_text:
                break
            last_error = ValueError("empty")
        except gemini_client.GeminiError as error:
            raise ValueError(str(error)) from error
    if not tori_text:
        raise GlumpRetryError("다시 시도해주세요") from last_error
    pending.append({"speaker": "tori", "text": tori_text})
    chars = int(row["chars_since_checkin"] or 0) + len(user_text) + len(tori_text)
    checkin = chars >= PINGPONG_CHECKIN_CHARS
    written_chars = sum(len(str(item.get("text") or "")) for item in pending)
    persist_chars = 0 if checkin else chars
    stamp = _now()
    with _database() as connection:
        connection.execute(
            """
            UPDATE glump_pingpong_sessions
            SET turns_json = ?, chars_since_checkin = ?, updated_at = ?
            WHERE id = ? AND status = 'active'
            """,
            (
                json.dumps(pending, ensure_ascii=False),
                persist_chars,
                stamp,
                session_id,
            ),
        )
    payload = {
        "tori_text": tori_text,
        "total_turns": len(pending),
        "checkin": checkin,
        "written_chars": written_chars,
    }
    return payload


def glump_pingpong_end(handler, body: dict) -> dict:
    """Join relay turns and insert-ready final text. Does not call Gemini."""
    session_id = str(body.get("session_id") or body.get("id") or "").strip()
    if not session_id:
        raise LookupError("세션을 찾을 수 없습니다.")
    with _database() as connection:
        row = connection.execute(
            """
            SELECT work_id, turns_json, status
            FROM glump_pingpong_sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            raise LookupError("세션을 찾을 수 없습니다.")
        if str(row["status"] or "") != "active":
            raise ValueError("이미 끝난 세션이에요")
        turns = _parse_pingpong_turns(row["turns_json"])
        final_text = _assemble_pingpong_text(turns)
        connection.execute(
            """
            UPDATE glump_pingpong_sessions
            SET status = 'ended', updated_at = ?
            WHERE id = ?
            """,
            (_now(), session_id),
        )
        work_id = str(row["work_id"] or "")
    if work_id:
        _insert_glump_tool_log(work_id, "pingpong_relay")
    return {"final_text": final_text}


def glump_lucky_sentence(handler, body: dict) -> dict:
    """Draw one open-ended starter sentence. Does not touch Tory/reader handlers."""
    work_id = str(body.get("work_id") or body.get("project_id") or "").strip()
    session_id = str(body.get("session_id") or "").strip()
    episode_content = str(body.get("episode_content") or "").strip()
    if not work_id:
        raise ValueError("작품을 선택해 주세요.")
    if not session_id:
        raise ValueError("세션을 찾을 수 없습니다.")
    if len(episode_content) < LUCKY_SENTENCE_MIN_CHARS:
        raise ValueError(
            "원고 내용이 너무 짧아요, 조금 더 써주시면 맥락을 파악할 수 있어요"
        )
    with _database() as connection:
        previous = connection.execute(
            """
            SELECT sentence FROM glump_lucky_draws
            WHERE session_id = ?
            ORDER BY created_at, id
            """,
            (session_id,),
        ).fetchall()
    prior = [str(row["sentence"] or "").strip() for row in previous if str(row["sentence"] or "").strip()]
    if len(prior) >= LUCKY_SENTENCE_MAX_DRAWS:
        return {
            "force_choice": True,
            "previous_options": prior[:LUCKY_SENTENCE_MAX_DRAWS],
            "draw_count": len(prior),
        }
    genre = _glump_work_genre_label(work_id)
    system, user_prompt = _lucky_sentence_prompt(genre, episode_content)
    last_error: Exception | None = None
    sentence = ""
    for _attempt in range(2):
        try:
            raw = gemini_client.generate_text(
                user_prompt, system=system, temperature=0.9
            )
            sentence = _clip_lucky_sentence(raw)
            if sentence:
                break
            last_error = ValueError("empty")
        except gemini_client.GeminiError as error:
            raise ValueError(str(error)) from error
    if not sentence:
        raise GlumpRetryError("다시 시도해주세요") from last_error
    first_open = len(prior) == 0
    stamp = _now()
    with _database() as connection:
        connection.execute(
            """
            INSERT INTO glump_lucky_draws (id, work_id, session_id, sentence, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (uuid.uuid4().hex, work_id, session_id, sentence, stamp),
        )
        count = connection.execute(
            "SELECT COUNT(*) FROM glump_lucky_draws WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
    if first_open:
        _insert_glump_tool_log(work_id, "lucky_sentence")
    return {"sentence": sentence, "draw_count": int(count), "force_choice": False}


def _interrogation_character_reply(
    handler,
    *,
    work_id: str,
    character: dict,
    question: str,
    episode_content: str,
    history: list[dict],
) -> str:
    """Reuse ai_assist character chat. Does not change that handler."""
    meta = _glump_project_assist_fields(work_id)
    result = handler.ai_assist(
        {
            "mode": "chat",
            "chat_mode": "character_chat",
            "prompt": question,
            "user_prompt": question,
            "chat_partners": [character["chat_partner"]],
            "history": history,
            "scene_content": episode_content,
            **meta,
        }
    )
    return str((result or {}).get("text") or "").strip()


def glump_interrogation_start(handler, body: dict) -> dict:
    """Create interrogation questions. Does not rewrite Tory/reader handlers."""
    work_id = str(body.get("work_id") or body.get("project_id") or "").strip()
    episode_content = str(body.get("episode_content") or "").strip()
    character_name = str(body.get("character_name") or body.get("name") or "").strip()
    if not work_id:
        raise ValueError("작품을 선택해 주세요.")
    if len(episode_content) < INTERROGATION_MIN_CHARS:
        raise ValueError(
            "원고 내용이 너무 짧아요, 조금 더 써주시면 맥락을 파악할 수 있어요"
        )
    character = _glump_load_interrogation_character(work_id, character_name or None)
    genre = _glump_work_genre_label(work_id)
    system, user_prompt = _interrogation_questions_prompt(
        genre,
        episode_content,
        character["name"],
        character["settings_text"],
    )
    last_error: Exception | None = None
    questions: list[str] | None = None
    for _attempt in range(2):
        try:
            raw = gemini_client.generate_text(
                user_prompt, system=system, temperature=0.85
            )
            questions = _parse_interrogation_questions(raw)
            break
        except gemini_client.GeminiError as error:
            raise ValueError(str(error)) from error
        except (ValueError, json.JSONDecodeError) as error:
            last_error = error
    if questions is None:
        raise GlumpRetryError("다시 시도해주세요") from last_error
    qa = [{"question": item, "answer": ""} for item in questions]
    session_id = uuid.uuid4().hex
    stamp = _now()
    with _database() as connection:
        connection.execute(
            """
            INSERT INTO glump_interrogation_sessions
                (id, work_id, character_name, qa_json, status, created_at)
            VALUES (?, ?, ?, ?, 'in_progress', ?)
            """,
            (
                session_id,
                work_id,
                character["name"],
                json.dumps(qa, ensure_ascii=False),
                stamp,
            ),
        )
    return {
        "session_id": session_id,
        "character_name": character["name"],
        "questions": questions,
    }


def glump_interrogation_answer(handler, body: dict) -> dict:
    """Fill one interrogation answer via existing character chat."""
    session_id = str(body.get("session_id") or body.get("id") or "").strip()
    episode_content = str(body.get("episode_content") or "").strip()
    if not session_id:
        raise LookupError("세션을 찾을 수 없습니다.")
    try:
        question_index = int(body.get("question_index"))
    except (TypeError, ValueError):
        raise ValueError("질문 번호가 올바르지 않아요") from None
    with _database() as connection:
        row = connection.execute(
            """
            SELECT work_id, character_name, qa_json, status
            FROM glump_interrogation_sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            raise LookupError("세션을 찾을 수 없습니다.")
        if str(row["status"] or "") != "in_progress":
            raise ValueError("이미 끝난 청문회예요")
        qa = _parse_interrogation_qa(row["qa_json"])
        work_id = str(row["work_id"] or "")
        character_name = str(row["character_name"] or "")
    if question_index < 0 or question_index >= len(qa):
        raise ValueError("질문 번호가 올바르지 않아요")
    character = _glump_load_interrogation_character(work_id, character_name)
    history: list[dict] = []
    for item in qa[:question_index]:
        if item.get("question"):
            history.append({"role": "user", "content": item["question"]})
        if item.get("answer"):
            history.append({"role": "character", "content": item["answer"]})
    excerpt = _clip_recent_episode(episode_content, INTERROGATION_EPISODE_CAP)
    answer = _interrogation_character_reply(
        handler,
        work_id=work_id,
        character=character,
        question=qa[question_index]["question"],
        episode_content=excerpt,
        history=history,
    )
    if not answer:
        raise GlumpRetryError("다시 시도해주세요")
    qa[question_index]["answer"] = answer
    with _database() as connection:
        connection.execute(
            """
            UPDATE glump_interrogation_sessions
            SET qa_json = ?
            WHERE id = ? AND status = 'in_progress'
            """,
            (json.dumps(qa, ensure_ascii=False), session_id),
        )
    return {"answer": answer, "question_index": question_index}


def glump_interrogation_summarize(handler, body: dict) -> dict:
    """Turn finished Q&A into Wildcard-style hint cards. Reference only."""
    session_id = str(body.get("session_id") or body.get("id") or "").strip()
    if not session_id:
        raise LookupError("세션을 찾을 수 없습니다.")
    with _database() as connection:
        row = connection.execute(
            """
            SELECT work_id, qa_json, status
            FROM glump_interrogation_sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            raise LookupError("세션을 찾을 수 없습니다.")
        qa = _parse_interrogation_qa(row["qa_json"])
        work_id = str(row["work_id"] or "")
        already = str(row["status"] or "") == "summarized"
    if not qa:
        raise ValueError("아직 답하지 않은 질문이 있어요")
    if any(not item.get("answer") for item in qa):
        raise ValueError("아직 답하지 않은 질문이 있어요")
    if already:
        raise ValueError("이미 끝난 청문회예요")
    system, user_prompt = _interrogation_summary_prompt(qa)
    last_error: Exception | None = None
    cards: list[dict] | None = None
    for _attempt in range(2):
        try:
            raw = gemini_client.generate_text(
                user_prompt, system=system, temperature=0.8
            )
            cards = _parse_interrogation_hint_cards(raw)
            break
        except gemini_client.GeminiError as error:
            raise ValueError(str(error)) from error
        except (ValueError, json.JSONDecodeError) as error:
            last_error = error
    if cards is None:
        raise GlumpRetryError("다시 시도해주세요") from last_error
    with _database() as connection:
        connection.execute(
            """
            UPDATE glump_interrogation_sessions
            SET status = 'summarized'
            WHERE id = ?
            """,
            (session_id,),
        )
    if work_id:
        _insert_glump_tool_log(work_id, "character_interrogation")
    return {"hint_cards": cards}


def glump_mood_color(handler, body: dict) -> dict:
    """Show a view-only personal-color palette. Does not edit the manuscript."""
    work_id = str(body.get("work_id") or body.get("project_id") or "").strip()
    character_name = str(body.get("character_name") or "").strip()
    if not work_id:
        raise ValueError("작품을 선택해 주세요.")
    character = _glump_load_interrogation_character(work_id, character_name or None)
    genre = _glump_work_genre_label(work_id)
    system, user_prompt = _mood_color_prompt(
        genre, character["name"], character["settings_text"]
    )
    last_error: Exception | None = None
    colors: list[dict] | None = None
    for _attempt in range(2):
        try:
            raw = gemini_client.generate_text(
                user_prompt, system=system, temperature=0.8
            )
            colors = _parse_mood_colors(raw)
            break
        except gemini_client.GeminiError as error:
            raise ValueError(str(error)) from error
        except (ValueError, json.JSONDecodeError) as error:
            last_error = error
    if colors is None:
        raise GlumpRetryError("다시 시도해주세요") from last_error
    _insert_glump_tool_log(work_id, "mood_color")
    return {
        "colors": colors,
        "character_name": character["name"],
        "character_role": character.get("role") or "",
    }


def glump_mood_playlist(handler, body: dict) -> dict:
    """Show a view-only fictional playlist. Does not edit the manuscript."""
    work_id = str(body.get("work_id") or body.get("project_id") or "").strip()
    character_name = str(body.get("character_name") or "").strip()
    if not work_id:
        raise ValueError("작품을 선택해 주세요.")
    character = _glump_load_interrogation_character(work_id, character_name or None)
    genre = _glump_work_genre_label(work_id)
    system, user_prompt = _mood_playlist_prompt(
        genre, character["name"], character["settings_text"]
    )
    last_error: Exception | None = None
    playlist: dict | None = None
    for _attempt in range(2):
        try:
            raw = gemini_client.generate_text(
                user_prompt, system=system, temperature=0.9
            )
            playlist = _parse_mood_playlist(raw)
            break
        except gemini_client.GeminiError as error:
            raise ValueError(str(error)) from error
        except (ValueError, json.JSONDecodeError) as error:
            last_error = error
    if playlist is None:
        raise GlumpRetryError("다시 시도해주세요") from last_error
    _insert_glump_tool_log(work_id, "mood_playlist")
    return {**playlist, "character_name": character["name"]}


def glump_mood_board(handler, body: dict) -> dict:
    """Show a view-only Pexels mood board. Does not edit the manuscript."""
    work_id = str(body.get("work_id") or body.get("project_id") or "").strip()
    if not work_id:
        raise ValueError("작품을 선택해 주세요.")
    _glump_work_genre_label(work_id)
    if not _pexels_api_key():
        raise ValueError(MOOD_BOARD_EMPTY_KEY_MSG)
    keywords = _normalise_mood_keywords(body.get("keywords"))
    if not keywords:
        genre = _glump_work_genre_label(work_id)
        system, user_prompt = _mood_board_keywords_prompt(genre)
        last_error: Exception | None = None
        generated: list[str] | None = None
        for _attempt in range(2):
            try:
                raw = gemini_client.generate_text(
                    user_prompt, system=system, temperature=0.8
                )
                generated = _parse_mood_board_keywords(raw)
                break
            except gemini_client.GeminiError as error:
                raise ValueError(str(error)) from error
            except (ValueError, json.JSONDecodeError) as error:
                last_error = error
        if generated is None:
            raise GlumpRetryError("다시 시도해주세요") from last_error
        keywords = generated
    photos = _fetch_pexels_photos(keywords)
    _insert_glump_tool_log(work_id, "mood_board")
    return {"keywords": keywords, "photos": photos}


def glump_word_list(handler, work_id: object) -> dict:
    """Show a view-only genre word list. Does not edit the manuscript."""
    work_key = str(work_id or "").strip()
    if not work_key:
        raise ValueError("작품을 선택해 주세요.")
    genre = _glump_work_genre_label(work_key)
    system, user_prompt = _word_list_prompt(genre)
    last_error: Exception | None = None
    words: list[dict] | None = None
    for _attempt in range(2):
        try:
            raw = gemini_client.generate_text(
                user_prompt, system=system, temperature=0.8
            )
            words = _parse_word_list(raw)
            break
        except gemini_client.GeminiError as error:
            raise ValueError(str(error)) from error
        except (ValueError, json.JSONDecodeError) as error:
            last_error = error
    if words is None:
        raise GlumpRetryError("다시 시도해주세요") from last_error
    _insert_glump_tool_log(work_key, "word_list")
    return {"words": words}


def glump_sentence_list(handler, work_id: object) -> dict:
    """Show a view-only mixed sentence list. Does not edit the manuscript."""
    work_key = str(work_id or "").strip()
    if not work_key:
        raise ValueError("작품을 선택해 주세요.")
    genre = _glump_work_genre_label(work_key)
    system, user_prompt = _sentence_list_prompt(genre)
    last_error: Exception | None = None
    sentences: list[dict] | None = None
    for _attempt in range(2):
        try:
            raw = gemini_client.generate_text(
                user_prompt, system=system, temperature=0.9
            )
            sentences = _parse_sentence_list(raw)
            break
        except gemini_client.GeminiError as error:
            raise ValueError(str(error)) from error
        except (ValueError, json.JSONDecodeError) as error:
            last_error = error
    if sentences is None:
        raise GlumpRetryError("다시 시도해주세요") from last_error
    _insert_glump_tool_log(work_key, "sentence_list")
    return {"sentences": sentences}


def glump_character_tarot(handler, body: dict) -> dict:
    """Show a playful character tarot reading. Does not edit the manuscript."""
    work_id = str(body.get("work_id") or body.get("project_id") or "").strip()
    character_name = str(body.get("character_name") or "").strip()
    style = str(body.get("style") or "").strip().lower()
    if not work_id:
        raise ValueError("작품을 선택해 주세요.")
    if style not in tarot_deck.TAROT_STYLES:
        raise ValueError("타로를 읽어 줄 토리를 골라 주세요.")
    picked_cards = tarot_deck.selected_tarot_cards(body.get("card_ids"))
    character = _glump_load_interrogation_character(work_id, character_name or None)
    genre = _glump_work_genre_label(work_id)
    system, user_prompt = _character_tarot_prompt(
        genre,
        character["name"],
        character["settings_text"],
        picked_cards,
        style,
    )
    last_error: Exception | None = None
    reading: dict | None = None
    for _attempt in range(2):
        try:
            raw = gemini_client.generate_text(
                user_prompt, system=system, temperature=0.9, max_output_tokens=4096
            )
            reading = tarot_deck.parse_character_tarot_v2(raw, picked_cards, style)
            break
        except gemini_client.GeminiError as error:
            raise ValueError(str(error)) from error
        except (ValueError, json.JSONDecodeError) as error:
            last_error = error
    if reading is None:
        raise GlumpRetryError("다시 시도해주세요") from last_error
    _insert_glump_tool_log(work_id, "character_tarot")
    return {
        **reading,
        "style": style,
        "character_name": character["name"],
    }


def glump_naming_shop(handler, body: dict) -> dict:
    """Show a view-only naming shop. Does not edit the manuscript."""
    work_id = str(body.get("work_id") or body.get("project_id") or "").strip()
    if not work_id:
        raise ValueError("작품을 선택해 주세요.")
    genre = _glump_work_genre_label(work_id)
    system, user_prompt = _naming_shop_prompt(genre)
    last_error: Exception | None = None
    groups: list[dict] | None = None
    for _attempt in range(2):
        try:
            raw = gemini_client.generate_text(
                user_prompt, system=system, temperature=0.9, max_output_tokens=4096
            )
            groups = _parse_naming_shop(raw)
            break
        except gemini_client.GeminiError as error:
            raise ValueError(str(error)) from error
        except (ValueError, json.JSONDecodeError) as error:
            last_error = error
    if groups is None:
        raise GlumpRetryError("다시 시도해주세요") from last_error
    _insert_glump_tool_log(work_id, "naming_shop")
    return {"groups": groups}


def glump_log_tool(handler, body: dict) -> dict:
    """Record a client-only Glump ER tool start. Does not call Gemini."""
    work_id = str(body.get("work_id") or body.get("project_id") or "").strip()
    tool_id = str(body.get("tool_id") or "").strip()
    if not work_id:
        raise ValueError("작품을 선택해 주세요.")
    if tool_id not in GLUMP_CLIENT_TOOL_IDS:
        raise ValueError("알 수 없는 도구입니다.")
    _insert_glump_tool_log(work_id, tool_id)
    return {"ok": True, "tool_id": tool_id}


def _insert_glump_tool_log(work_id: str, tool_id: str) -> None:
    with _database() as connection:
        connection.execute(
            """
            INSERT INTO glump_tool_logs (id, work_id, tool_id, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                work_id,
                tool_id,
                _now(),
            ),
        )


def glump_mental_vitamin(handler, work_id: object) -> dict:
    """Serve saved highlight moments. Does not call Gemini."""
    work_key = str(work_id or "").strip()
    if not work_key:
        raise ValueError("작품을 선택해 주세요.")
    with _database() as connection:
        rows = connection.execute(
            """
            SELECT episode_id, episode_order, moment_type, excerpt, reason, created_at
            FROM glump_highlight_moments
            WHERE work_id = ?
            ORDER BY episode_order DESC, created_at DESC, rowid DESC
            """,
            (work_key,),
        ).fetchall()
    _insert_glump_tool_log(work_key, "mental_vitamin")
    moments = pick_mental_vitamin_moments(rows)
    if not moments:
        return {
            "empty": True,
            "message": "아직 모아둔 명장면이 없어요, 조금 더 쓰시면 좋은 부분을 찾아드릴게요",
            "moments": [],
        }
    return {"empty": False, "moments": moments}


def _work_id_param(handler) -> str:
    query = parse_qs(urlparse(handler.path).query)
    return (query.get("work_id") or [""])[0]


def try_handle_get(handler, path: str) -> bool:
    if path == "/api/glump/diagnosis-stats":
        handler.send_json(glump_diagnosis_stats(handler))
        return True
    if path == "/api/glump/mental-vitamin":
        handler.send_json(glump_mental_vitamin(handler, _work_id_param(handler)))
        return True
    # The two list tools answer their own retry/validation errors so a failed
    # Gemini parse does not fall through to do_GET's generic 500.
    if path in {"/api/glump/word-list", "/api/glump/sentence-list"}:
        build = glump_word_list if path.endswith("word-list") else glump_sentence_list
        try:
            handler.send_json(build(handler, _work_id_param(handler)))
        except GlumpRetryError as error:
            handler.api_error(str(error), HTTPStatus.INTERNAL_SERVER_ERROR)
        except ValueError as error:
            handler.api_error(str(error), HTTPStatus.BAD_REQUEST)
        except LookupError as error:
            handler.api_error(str(error), HTTPStatus.NOT_FOUND)
        return True
    return False


_POST_ROUTES = {
    "/api/glump/diagnose": glump_diagnose,
    "/api/glump/wildcard-spark": glump_wildcard_spark,
    "/api/glump/fill-blank/start": glump_fill_blank_start,
    "/api/glump/fill-blank/submit": glump_fill_blank_submit,
    "/api/glump/pingpong/start": glump_pingpong_start,
    "/api/glump/pingpong/turn": glump_pingpong_turn,
    "/api/glump/pingpong/end": glump_pingpong_end,
    "/api/glump/lucky-sentence": glump_lucky_sentence,
    "/api/glump/interrogation/start": glump_interrogation_start,
    "/api/glump/interrogation/answer": glump_interrogation_answer,
    "/api/glump/interrogation/summarize": glump_interrogation_summarize,
    "/api/glump/mood-color": glump_mood_color,
    "/api/glump/mood-playlist": glump_mood_playlist,
    "/api/glump/mood-board": glump_mood_board,
    "/api/glump/character-tarot": glump_character_tarot,
    "/api/glump/naming-shop": glump_naming_shop,
    "/api/glump/tool-log": glump_log_tool,
}


def try_handle_post(handler, path: str, body: dict | None) -> bool:
    route = _POST_ROUTES.get(path)
    if route is None:
        return False
    handler.send_json(route(handler, body or {}))
    return True
