"""Extract character-sheet fields from imported manuscript text (Gemini).

Empty sheet fields are filled in place. Occupied fields are never overwritten;
the new analysis is stored in character_tori_analysis for a later explicit apply.
"""

from __future__ import annotations

import json
import re
import sqlite3


SHEET_FIELDS: tuple[tuple[str, str], ...] = (
    ("short_description", "한 줄 소개"),
    ("profile_md", "인물 설정"),
    ("strengths_md", "무기 · 강점"),
    ("weaknesses_md", "약점"),
)
SHEET_FIELD_KEYS = {key for key, _label in SHEET_FIELDS}
FIELD_LABELS = {key: label for key, label in SHEET_FIELDS}

ROLE_KEYS = {"protagonist", "antagonist", "supporting", "minor"}
ROLE_ALIASES = {
    "protagonist": "protagonist",
    "주인공": "protagonist",
    "주역": "protagonist",
    "antagonist": "antagonist",
    "대립": "antagonist",
    "대립 인물": "antagonist",
    "악역": "antagonist",
    "supporting": "supporting",
    "조연": "supporting",
    "minor": "minor",
    "단역": "minor",
}

SKIP_NAMES = {
    "그", "그녀", "그이", "남자", "여자", "사람", "사람들", "모두", "누군가",
    "손님", "병사", "병사들", "군중", "아이", "아이들",
}

MAX_MANUSCRIPT_CHARS = 60_000
MAX_CHARACTERS = 24
MAX_FIELD_CHARS = 4000
TORI_TEXT_PREFIX = "〔토리〕 "

_GENERIC_NAME = re.compile(r"^(?:그(?:녀)?|남자|여자|사람|아이|손님)\d*$")


def field_label(field_name: str) -> str:
    return FIELD_LABELS.get(field_name, field_name)


def is_sheet_field(field_name: str) -> bool:
    return str(field_name or "") in SHEET_FIELD_KEYS


def is_field_empty(value: object) -> bool:
    return not str(value or "").strip()


def is_tori_text(value: object) -> bool:
    return str(value or "").lstrip().startswith("〔토리〕")


def mark_tori_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if is_tori_text(text):
        return text
    return f"{TORI_TEXT_PREFIX}{text}"


def normalise_name(name: object) -> str:
    return re.sub(r"\s+", "", str(name or "").strip())


def normalise_role(value: object) -> str | None:
    key = str(value or "").strip().lower()
    if not key:
        return None
    mapped = ROLE_ALIASES.get(key) or ROLE_ALIASES.get(str(value or "").strip())
    if mapped in ROLE_KEYS:
        return mapped
    return None


def _usable_name(name: str) -> bool:
    cleaned = normalise_name(name)
    if len(cleaned) < 1 or len(cleaned) > 80:
        return False
    if cleaned in SKIP_NAMES:
        return False
    if _GENERIC_NAME.match(cleaned):
        return False
    return True


def compose_profile_md(item: dict) -> str:
    direct = str(item.get("profile_md") or item.get("profile") or "").strip()
    if direct:
        return direct[:MAX_FIELD_CHARS]
    parts: list[str] = []
    for key, heading in (
        ("appearance", "외모"),
        ("personality", "성격"),
        ("speech", "말투"),
        ("relations", "관계"),
        ("background", "과거"),
        ("goal", "목표"),
    ):
        text = str(item.get(key) or "").strip()
        if text:
            parts.append(f"[{heading}]\n{text}")
    return "\n\n".join(parts)[:MAX_FIELD_CHARS]


def parse_analysis_json(raw: object) -> list[dict]:
    cleaned = str(raw or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.S)
    if fence:
        cleaned = fence.group(1).strip()
    data: object
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    rows: object = data
    if isinstance(data, dict):
        rows = data.get("characters") or data.get("items") or data.get("people") or []
    if not isinstance(rows, list):
        return []
    parsed: list[dict] = []
    seen: set[str] = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not _usable_name(name):
            continue
        key = normalise_name(name).casefold()
        if key in seen:
            continue
        seen.add(key)
        fields = {
            "short_description": str(item.get("short_description") or item.get("summary") or "").strip()[:400],
            "profile_md": compose_profile_md(item),
            "strengths_md": str(item.get("strengths_md") or item.get("strengths") or "").strip()[:MAX_FIELD_CHARS],
            "weaknesses_md": str(item.get("weaknesses_md") or item.get("weaknesses") or "").strip()[:MAX_FIELD_CHARS],
        }
        if not any(fields.values()):
            continue
        parsed.append({
            "name": name[:120],
            "role": normalise_role(item.get("role")),
            "fields": fields,
        })
        if len(parsed) >= MAX_CHARACTERS:
            break
    return parsed


def build_analysis_prompt(
    manuscript: str,
    existing_names: list[str],
    *,
    plot_context: str = "",
    infer: bool = False,
) -> tuple[str, str]:
    names = "、".join(existing_names[:40]) if existing_names else "(없음)"
    body = str(manuscript or "").strip()
    if len(body) > MAX_MANUSCRIPT_CHARS:
        body = body[:MAX_MANUSCRIPT_CHARS] + "\n…(이하 생략)"
    plot = str(plot_context or "").strip()
    if len(plot) > 12_000:
        plot = plot[:12_000] + "\n…(이하 생략)"
    has_manuscript = len(body) >= 40
    json_spec = (
        "[출력 JSON]\n"
        "{\n"
        '  "characters": [\n'
        "    {\n"
        '      "name": "이름",\n'
        '      "role": "protagonist | antagonist | supporting | minor",\n'
        '      "short_description": "한 줄 소개",\n'
        '      "appearance": "외모",\n'
        '      "personality": "성격",\n'
        '      "speech": "말투",\n'
        '      "relations": "관계",\n'
        '      "strengths_md": "무기·강점",\n'
        '      "weaknesses_md": "약점"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "JSON 외 텍스트는 출력하지 마세요.\n\n"
    )
    if infer:
        system = (
            "당신은 한국어 소설 설정집 도우미 토리입니다. "
            "작가가 이미 적어 둔 칸은 건드리지 않고, 비어 있는 칸만 채웁니다. "
            "출력은 JSON만 합니다."
        )
        if has_manuscript:
            task = (
                "[작업]\n"
                "아래 원고를 적극 반영해 등장인물 시트의 빈 칸을 모두 채우세요. "
                "줄거리·로그라인은 보조 자료입니다.\n"
                "이미 있는 인물 이름이 있으면 그 표기를 그대로 쓰세요.\n"
                "군중·무명 엑스트라·대명사(그/그녀)는 빼세요.\n"
                "각 인물의 short_description, appearance, personality, speech, "
                "relations, strengths_md, weaknesses_md를 가능한 한 비우지 마세요.\n\n"
            )
        else:
            task = (
                "[작업]\n"
                "원고는 없고 줄거리만 있습니다. 줄거리·로그라인을 바탕으로 "
                "등장인물 시트를 대략 작성하세요. 줄거리와 모순되지 않게 보완하세요.\n"
                "이미 있는 인물 이름이 있으면 그 표기를 그대로 쓰세요.\n"
                "각 인물의 칸을 가능한 한 모두 채우세요.\n\n"
            )
        extras = ""
        if plot:
            extras += f"[줄거리·설정]\n{plot}\n\n"
        extras += f"[이미 있는 인물 이름]\n{names}\n\n"
        source = f"[원고]\n{body}" if has_manuscript else "[원고]\n(없음)"
        return system, task + extras + json_spec + source
    system = (
        "당신은 한국어 소설 설정집 도우미 토리입니다. "
        "원고에 실제로 나온 등장인물만 적고, 없는 설정은 지어내지 마세요. "
        "출력은 JSON만 합니다."
    )
    user = (
        "[작업]\n"
        "아래 원고에서 이름이 있는 등장인물을 찾아 캐릭터 시트 칸에 맞춰 정리하세요.\n"
        "군중·무명 엑스트라·대명사(그/그녀)는 빼세요.\n"
        "이미 있는 인물 이름이 있으면 그 표기를 그대로 쓰세요.\n\n"
        "[이미 있는 인물 이름]\n"
        f"{names}\n\n"
        f"{json_spec}"
        "근거가 없는 칸은 빈 문자열로 두세요.\n\n"
        "[원고]\n"
        f"{body}"
    )
    return system, user


def list_existing_characters(connection: sqlite3.Connection, project_id: int) -> list[dict]:
    rows = connection.execute(
        "SELECT id, name, role, short_description, profile_md, strengths_md, weaknesses_md "
        "FROM character WHERE project_id = ? AND deleted_at IS NULL ORDER BY sort_order, id",
        (int(project_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def match_character_id(existing: list[dict], name: str) -> int | None:
    target = normalise_name(name).casefold()
    if not target:
        return None
    for row in existing:
        if normalise_name(row.get("name")).casefold() == target:
            try:
                return int(row["id"])
            except (TypeError, ValueError, KeyError):
                return None
    return None


def _next_sort_order(connection: sqlite3.Connection, project_id: int) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(sort_order) + 1, 0) FROM character "
        "WHERE project_id = ? AND deleted_at IS NULL",
        (int(project_id),),
    ).fetchone()
    return int(row[0] if row else 0)


def _upsert_pending(
    connection: sqlite3.Connection,
    character_id: int,
    field_name: str,
    content: str,
) -> None:
    connection.execute(
        "INSERT INTO character_tori_analysis(character_id, field_name, analyzed_content) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT(character_id, field_name) DO UPDATE SET "
        "analyzed_content = excluded.analyzed_content, "
        "created_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
        (int(character_id), field_name, content),
    )


def _clear_pending(connection: sqlite3.Connection, character_id: int, field_name: str) -> None:
    connection.execute(
        "DELETE FROM character_tori_analysis WHERE character_id = ? AND field_name = ?",
        (int(character_id), field_name),
    )


def apply_parsed_characters(
    connection: sqlite3.Connection,
    project_id: int,
    parsed: list[dict],
) -> dict[str, int]:
    """Fill empty fields; store occupied-field analysis as pending. Never overwrite."""
    stats = {"created": 0, "matched": 0, "filled": 0, "pending": 0}
    existing = list_existing_characters(connection, project_id)
    for item in parsed:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        fields: dict[str, str] = dict(item.get("fields") or {})
        character_id = match_character_id(existing, name)
        created = False
        if character_id is None:
            sort_order = _next_sort_order(connection, project_id)
            role = item.get("role") or "supporting"
            if role not in ROLE_KEYS:
                role = "supporting"
            cursor = connection.execute(
                "INSERT INTO character(project_id, name, role, sort_order) VALUES (?, ?, ?, ?)",
                (int(project_id), name, role, sort_order),
            )
            character_id = int(cursor.lastrowid)
            created = True
            stats["created"] += 1
            existing.append({
                "id": character_id,
                "name": name,
                "role": role,
                "short_description": "",
                "profile_md": "",
                "strengths_md": "",
                "weaknesses_md": "",
            })
        else:
            stats["matched"] += 1

        row = next((entry for entry in existing if int(entry["id"]) == int(character_id)), None)
        if row is None:
            continue
        updates: dict[str, str] = {}
        for field_name in SHEET_FIELD_KEYS:
            content = str(fields.get(field_name) or "").strip()
            if not content:
                continue
            current = row.get(field_name)
            marked = mark_tori_text(content)
            if created or is_field_empty(current):
                updates[field_name] = marked
                row[field_name] = marked
                _clear_pending(connection, character_id, field_name)
                stats["filled"] += 1
            else:
                _upsert_pending(connection, character_id, field_name, marked)
                stats["pending"] += 1
        if updates:
            assignments = ", ".join(f"{key} = ?" for key in updates)
            connection.execute(
                f"UPDATE character SET {assignments} WHERE id = ? AND deleted_at IS NULL",
                (*updates.values(), int(character_id)),
            )
    return stats


def list_pending_for_character(connection: sqlite3.Connection, character_id: int) -> dict[str, dict]:
    try:
        rows = connection.execute(
            "SELECT field_name, analyzed_content, created_at "
            "FROM character_tori_analysis WHERE character_id = ? ORDER BY field_name",
            (int(character_id),),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    pending: dict[str, dict] = {}
    for row in rows:
        key = str(row["field_name"] or "")
        if key not in SHEET_FIELD_KEYS:
            continue
        pending[key] = {
            "field_name": key,
            "label": field_label(key),
            "content": str(row["analyzed_content"] or ""),
            "created_at": row["created_at"],
        }
    return pending


def pending_character_ids(connection: sqlite3.Connection, project_id: int) -> set[int]:
    try:
        rows = connection.execute(
            "SELECT DISTINCT a.character_id FROM character_tori_analysis a "
            "JOIN character c ON c.id = a.character_id "
            "WHERE c.project_id = ? AND c.deleted_at IS NULL",
            (int(project_id),),
        ).fetchall()
    except sqlite3.OperationalError:
        return set()
    ids: set[int] = set()
    for row in rows:
        try:
            ids.add(int(row[0]))
        except (TypeError, ValueError):
            continue
    return ids


def apply_pending_field(
    connection: sqlite3.Connection,
    character_id: int,
    field_name: str,
) -> str:
    if not is_sheet_field(field_name):
        raise ValueError("바꿀 수 있는 인물 설정 칸이 아닙니다.")
    row = connection.execute(
        "SELECT analyzed_content FROM character_tori_analysis "
        "WHERE character_id = ? AND field_name = ?",
        (int(character_id), field_name),
    ).fetchone()
    if row is None:
        raise ValueError("적용할 토리 분석이 없습니다.")
    content = str(row["analyzed_content"] or "")
    connection.execute(
        f"UPDATE character SET {field_name} = ? WHERE id = ? AND deleted_at IS NULL",
        (content, int(character_id)),
    )
    _clear_pending(connection, character_id, field_name)
    return content


def load_manuscript_text(
    connection: sqlite3.Connection,
    project_id: int,
    scene_ids: list[int] | None = None,
) -> str:
    if scene_ids:
        placeholders = ",".join("?" for _ in scene_ids)
        rows = connection.execute(
            "SELECT r.content_md FROM scene s "
            "JOIN scene_revision r ON r.scene_id = s.id AND r.is_current = 1 "
            "WHERE s.project_id = ? AND s.deleted_at IS NULL "
            f"AND s.id IN ({placeholders}) "
            "ORDER BY s.id",
            (int(project_id), *[int(item) for item in scene_ids]),
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT r.content_md FROM scene s "
            "JOIN scene_revision r ON r.scene_id = s.id AND r.is_current = 1 "
            "WHERE s.project_id = ? AND s.deleted_at IS NULL "
            "ORDER BY s.id",
            (int(project_id),),
        ).fetchall()
    chunks: list[str] = []
    total = 0
    for row in rows:
        text = str(row["content_md"] or "").strip()
        if not text:
            continue
        chunks.append(text)
        total += len(text)
        if total >= MAX_MANUSCRIPT_CHARS:
            break
    return "\n\n".join(chunks)


def strip_html_rough(text: str) -> str:
    cleaned = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text or "")
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"&nbsp;", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def load_plot_context(connection: sqlite3.Connection, project_id: int) -> str:
    row = connection.execute(
        "SELECT title, description_md, logline_md, intro_md, intent_md, "
        "main_genre, sub_genre FROM project "
        "WHERE id = ? AND deleted_at IS NULL",
        (int(project_id),),
    ).fetchone()
    if row is None:
        return ""
    parts: list[str] = []
    title = str(row["title"] or "").strip()
    if title:
        parts.append(f"[작품 제목]\n{title}")
    genre = " / ".join(
        piece for piece in (
            str(row["main_genre"] or "").strip(),
            str(row["sub_genre"] or "").strip(),
        ) if piece
    )
    if genre:
        parts.append(f"[장르]\n{genre}")
    logline = strip_html_rough(str(row["logline_md"] or ""))
    if logline:
        parts.append(f"[로그라인]\n{logline}")
    synopsis = strip_html_rough(str(row["description_md"] or ""))
    if synopsis:
        parts.append(f"[줄거리]\n{synopsis}")
    intro = strip_html_rough(str(row["intro_md"] or ""))
    if intro:
        parts.append(f"[작품 소개]\n{intro}")
    intent = strip_html_rough(str(row["intent_md"] or ""))
    if intent:
        parts.append(f"[집필 의도]\n{intent}")
    return "\n\n".join(parts)
