"""Extract worldbuilding-sheet fields from imported manuscript text (Gemini).

Empty sheet fields are filled in place. Occupied fields are never overwritten;
the new analysis is stored in world_tori_analysis for a later explicit apply.
"""

from __future__ import annotations

import json
import re
import sqlite3

from character_import_analysis import mark_tori_text


WORLD_BUILDING_SCHEMA: tuple[dict, ...] = (
    {
        "id": "where_when",
        "title": "1. 무대 및 시대 (Where & When)",
        "blurb": "작품의 기본 바탕이 되는 공간과 시간선입니다.",
        "fields": (
            ("reality", "현실 / 가상 구분"),
            ("era", "시대 배경"),
            ("locale", "주요 배경"),
        ),
    },
    {
        "id": "unique_concept",
        "title": "2. 세계의 특이점 (Unique Concept)",
        "blurb": "이 세계를 다른 세계관과 다르게 만드는 단 하나의 핵심 규칙입니다.",
        "fields": (
            ("special", "특수 요소"),
            ("rules", "작동 규칙"),
            ("limits", "한계와 대가"),
        ),
    },
    {
        "id": "extreme_factor",
        "title": "3. 영향을 주는 극단적 요소 (Extreme Factor)",
        "blurb": "이 세계의 평범한 일상을 위협하거나 전체 시스템을 흔드는 가장 거대한 변수입니다.",
        "fields": (
            ("extreme_event", "극단적 사건 / 환경"),
            ("extreme_impact", "세상에 미친 영향"),
        ),
    },
    {
        "id": "system_life",
        "title": "4. 기본 체계 및 사회 (System & Life)",
        "blurb": "사람들이 살아가는 데 필요한 최소한의 사회적 약속입니다.",
        "fields": (
            ("power", "지배 구조"),
            ("daily", "기본 생활"),
            ("class", "계급 / 신분"),
        ),
    },
    {
        "id": "factions",
        "title": "5. 세력 및 갈등 (Factions)",
        "blurb": "주인공과 인물들이 부딪히게 될 주체들입니다.",
        "fields": (
            ("factions", "주요 세력"),
            ("conflict", "갈등의 원인"),
        ),
    },
)

SHEET_FIELDS: tuple[tuple[str, str, str], ...] = tuple(
    (section["id"], field_id, label)
    for section in WORLD_BUILDING_SCHEMA
    for field_id, label in section["fields"]
)
SHEET_FIELD_KEYS = {field_id for _section, field_id, _label in SHEET_FIELDS}
FIELD_LABELS = {field_id: label for _section, field_id, label in SHEET_FIELDS}
FIELD_SECTIONS = {field_id: section_id for section_id, field_id, _label in SHEET_FIELDS}
LABEL_TO_FIELD = {label: field_id for _section, field_id, label in SHEET_FIELDS}
LABEL_TO_FIELD["기타 · 기존 메모"] = "legacy"
LABEL_TO_FIELD["기타"] = "legacy"

MAX_MANUSCRIPT_CHARS = 60_000
MAX_FIELD_CHARS = 4000
MAX_WORLD_MD_CHARS = 50_000


def field_label(field_name: str) -> str:
    return FIELD_LABELS.get(field_name, field_name)


def field_section(field_name: str) -> str:
    return FIELD_SECTIONS.get(field_name, "")


def is_sheet_field(field_name: str) -> bool:
    return str(field_name or "") in SHEET_FIELD_KEYS


def is_field_empty(value: object) -> bool:
    return not str(value or "").strip()


def empty_world_values() -> dict[str, str]:
    values = {field_id: "" for field_id in SHEET_FIELD_KEYS}
    values["legacy"] = ""
    return values


def compose_worldbuilding_md(values: dict | None) -> str:
    data = values or empty_world_values()
    parts: list[str] = []
    for section in WORLD_BUILDING_SCHEMA:
        parts.append(f"## {section['title']}")
        parts.append(str(section["blurb"]))
        parts.append("")
        for field_id, label in section["fields"]:
            text = str(data.get(field_id) or "").strip()
            parts.append(f"### {label}")
            parts.append(text)
            parts.append("")
    legacy = str(data.get("legacy") or "").strip()
    if legacy:
        parts.append("## 기타 · 기존 메모")
        parts.append(legacy)
        parts.append("")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(parts)).strip()


def _plain_title(title: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", str(title or "")).strip()


def _is_section_heading(heading: str) -> bool:
    text = str(heading or "").strip()
    plain = _plain_title(text)
    for section in WORLD_BUILDING_SCHEMA:
        title = str(section["title"])
        if text == title or plain == _plain_title(title) or text.startswith(title[:8]):
            return True
    return False


def parse_worldbuilding_md(raw: object) -> dict[str, str]:
    values = empty_world_values()
    text = str(raw or "").strip()
    if not text:
        return values
    has_structured = any(
        section["title"] in text or _plain_title(section["title"]) in text
        for section in WORLD_BUILDING_SCHEMA
    )
    if not has_structured:
        values["legacy"] = text
        return values

    chunks = re.split(r"\n(?=#{2,3}\s+)", text)
    current_field: str | None = None
    buffers: dict[str, str] = {}
    for chunk in chunks:
        heading_match = re.match(r"^#{2,3}\s+(.+?)\s*(?:\n|$)", chunk)
        if not heading_match:
            if current_field:
                buffers[current_field] = f"{buffers.get(current_field) or ''}\n{chunk}".strip()
            else:
                values["legacy"] = f"{values['legacy']}\n{chunk}".strip()
            continue
        heading = re.sub(r"\s+", " ", heading_match.group(1)).strip()
        body = chunk[heading_match.end() :].strip()
        if _is_section_heading(heading) and heading not in LABEL_TO_FIELD:
            current_field = None
            cleaned_lines = []
            for line in body.split("\n"):
                stripped = line.strip()
                if not stripped:
                    continue
                if any(section["blurb"] == stripped for section in WORLD_BUILDING_SCHEMA):
                    continue
                cleaned_lines.append(stripped)
            cleaned = "\n".join(cleaned_lines).strip()
            if cleaned:
                values["legacy"] = f"{values['legacy']}\n{cleaned}".strip()
            continue
        field_id = LABEL_TO_FIELD.get(heading) or LABEL_TO_FIELD.get(_plain_title(heading))
        if field_id:
            current_field = field_id
            buffers[field_id] = body
        elif "기타" in heading:
            current_field = "legacy"
            buffers["legacy"] = body
        else:
            current_field = None
            if body:
                values["legacy"] = f"{values['legacy']}\n{body}".strip()
    for key, val in buffers.items():
        values[key] = str(val or "").strip()
    return values


def _flatten_analysis_dict(data: dict) -> dict[str, str]:
    found: dict[str, str] = {}

    def take(key: object, value: object) -> None:
        field_id = str(key or "").strip()
        mapped = field_id if field_id in SHEET_FIELD_KEYS else LABEL_TO_FIELD.get(field_id)
        if mapped == "legacy" or mapped not in SHEET_FIELD_KEYS:
            return
        text = str(value or "").strip()[:MAX_FIELD_CHARS]
        if text and mapped not in found:
            found[mapped] = text

    for key, value in data.items():
        if str(key) in {"fields", "world", "worldbuilding", "sections"} and isinstance(value, dict):
            found.update(_flatten_analysis_dict(value))
            continue
        if str(key) in FIELD_SECTIONS.values() and isinstance(value, dict):
            found.update(_flatten_analysis_dict(value))
            continue
        take(key, value)
    return found


def parse_analysis_json(raw: object) -> dict[str, str]:
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
            return {}
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    if not isinstance(data, dict):
        return {}
    return _flatten_analysis_dict(data)


def build_analysis_prompt(
    manuscript: str,
    *,
    plot_context: str = "",
    infer: bool = False,
) -> tuple[str, str]:
    body = str(manuscript or "").strip()
    if len(body) > MAX_MANUSCRIPT_CHARS:
        body = body[:MAX_MANUSCRIPT_CHARS] + "\n…(이하 생략)"
    plot = str(plot_context or "").strip()
    if len(plot) > 12_000:
        plot = plot[:12_000] + "\n…(이하 생략)"
    has_manuscript = len(body) >= 40
    field_lines = []
    for section in WORLD_BUILDING_SCHEMA:
        field_lines.append(f"- {section['id']} ({section['title']})")
        for field_id, label in section["fields"]:
            field_lines.append(f'  "{field_id}": "{label}"')
    json_shape = (
        "[출력 JSON — 필드 id]\n"
        "{\n"
        '  "reality": "현실 / 가상 구분",\n'
        '  "era": "시대 배경",\n'
        '  "locale": "주요 배경",\n'
        '  "special": "특수 요소",\n'
        '  "rules": "작동 규칙",\n'
        '  "limits": "한계와 대가",\n'
        '  "extreme_event": "극단적 사건 / 환경",\n'
        '  "extreme_impact": "세상에 미친 영향",\n'
        '  "power": "지배 구조",\n'
        '  "daily": "기본 생활",\n'
        '  "class": "계급 / 신분",\n'
        '  "factions": "주요 세력",\n'
        '  "conflict": "갈등의 원인"\n'
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
                "아래 원고를 적극 반영해 세계관 시트의 빈 칸을 모두 채우세요. "
                "줄거리·로그라인은 보조 자료입니다. 가능한 한 빈 문자열을 남기지 마세요.\n\n"
            )
        else:
            task = (
                "[작업]\n"
                "원고는 없고 줄거리만 있습니다. 줄거리·로그라인을 바탕으로 "
                "세계관 시트를 대략 작성하세요. 줄거리와 모순되지 않게 보완하고, "
                "가능한 한 모든 칸을 채우세요.\n\n"
            )
        extras = f"[줄거리·설정]\n{plot}\n\n" if plot else ""
        source = f"[원고]\n{body}" if has_manuscript else "[원고]\n(없음)"
        return system, (
            task
            + extras
            + json_shape
            + "[칸 안내]\n"
            + f"{chr(10).join(field_lines)}\n\n"
            + source
        )
    system = (
        "당신은 한국어 소설 설정집 도우미 토리입니다. "
        "원고에 실제로 나온 세계관만 적고, 없는 설정은 지어내지 마세요. "
        "출력은 JSON만 합니다."
    )
    user = (
        "[작업]\n"
        "아래 원고에서 세계관 시트 칸에 맞춰 정리하세요.\n"
        "장소·시대·규칙·세력 등이 분명히 드러난 것만 적으세요.\n"
        "근거가 없는 칸은 빈 문자열로 두세요.\n\n"
        f"{json_shape}"
        "[칸 안내]\n"
        f"{chr(10).join(field_lines)}\n\n"
        "[원고]\n"
        f"{body}"
    )
    return system, user


def _upsert_pending(
    connection: sqlite3.Connection,
    project_id: int,
    section_name: str,
    field_name: str,
    content: str,
) -> None:
    connection.execute(
        "INSERT INTO world_tori_analysis(project_id, section_name, field_name, analyzed_content) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(project_id, field_name) DO UPDATE SET "
        "analyzed_content = excluded.analyzed_content, "
        "section_name = excluded.section_name, "
        "created_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
        (int(project_id), section_name, field_name, content),
    )


def _clear_pending(
    connection: sqlite3.Connection,
    project_id: int,
    field_name: str,
) -> None:
    connection.execute(
        "DELETE FROM world_tori_analysis WHERE project_id = ? AND field_name = ?",
        (int(project_id), field_name),
    )


def apply_parsed_fields(
    connection: sqlite3.Connection,
    project_id: int,
    parsed: dict[str, str],
) -> dict[str, int]:
    """Fill empty world sheet fields; store occupied-field analysis as pending."""
    stats = {"filled": 0, "pending": 0}
    row = connection.execute(
        "SELECT worldbuilding_md FROM project WHERE id = ? AND deleted_at IS NULL",
        (int(project_id),),
    ).fetchone()
    if row is None:
        return stats
    values = parse_worldbuilding_md(row["worldbuilding_md"] or "")
    changed = False
    for section_id, field_id, _label in SHEET_FIELDS:
        content = str(parsed.get(field_id) or "").strip()[:MAX_FIELD_CHARS]
        if not content:
            continue
        if is_field_empty(values.get(field_id)):
            values[field_id] = mark_tori_text(content)
            _clear_pending(connection, project_id, field_id)
            stats["filled"] += 1
            changed = True
        else:
            _upsert_pending(connection, project_id, section_id, field_id, mark_tori_text(content))
            stats["pending"] += 1
    if changed:
        md = compose_worldbuilding_md(values)[:MAX_WORLD_MD_CHARS]
        connection.execute(
            "UPDATE project SET worldbuilding_md = ? WHERE id = ? AND deleted_at IS NULL",
            (md, int(project_id)),
        )
    return stats


def list_pending_for_project(connection: sqlite3.Connection, project_id: int) -> dict[str, dict]:
    try:
        rows = connection.execute(
            "SELECT section_name, field_name, analyzed_content, created_at "
            "FROM world_tori_analysis WHERE project_id = ? ORDER BY field_name",
            (int(project_id),),
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
            "section_name": str(row["section_name"] or field_section(key)),
            "label": field_label(key),
            "content": str(row["analyzed_content"] or ""),
            "created_at": row["created_at"],
        }
    return pending


def apply_pending_field(
    connection: sqlite3.Connection,
    project_id: int,
    field_name: str,
) -> str:
    if not is_sheet_field(field_name):
        raise ValueError("바꿀 수 있는 세계관 칸이 아닙니다.")
    row = connection.execute(
        "SELECT analyzed_content FROM world_tori_analysis "
        "WHERE project_id = ? AND field_name = ?",
        (int(project_id), field_name),
    ).fetchone()
    if row is None:
        raise ValueError("적용할 토리 분석이 없습니다.")
    content = str(row["analyzed_content"] or "")
    current = connection.execute(
        "SELECT worldbuilding_md FROM project WHERE id = ? AND deleted_at IS NULL",
        (int(project_id),),
    ).fetchone()
    if current is None:
        raise ValueError("작품을 찾을 수 없습니다.")
    values = parse_worldbuilding_md(current["worldbuilding_md"] or "")
    values[field_name] = content
    md = compose_worldbuilding_md(values)[:MAX_WORLD_MD_CHARS]
    connection.execute(
        "UPDATE project SET worldbuilding_md = ? WHERE id = ? AND deleted_at IS NULL",
        (md, int(project_id)),
    )
    _clear_pending(connection, project_id, field_name)
    return md
