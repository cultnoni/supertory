"""Detect lasting character traits when a scene is marked complete.

Only registered characters that appear in the scene (cast-sync matching) are
analysed. Empty sheet fields are filled; occupied fields become pending badges.
Every detection is stored in character_trait_history for a later timeline.
"""

from __future__ import annotations

import json
import re
import sqlite3

import character_import_analysis
import scene_cast_detect

TRAIT_FIELDS = ("profile_md", "aliases", "strengths_md", "weaknesses_md")
NEVER_FIELDS = frozenset({"short_description", "author_notes_md"})
CONDITIONAL_FIELDS = frozenset({"strengths_md", "weaknesses_md"})
MAX_SCENE_CHARS = 24_000
MAX_FIELD_CHARS = 4000

_ALIAS_SPLIT = re.compile(r"[,，;/\n]+")


def _usable_text(value: object) -> str:
    return str(value or "").strip()[:MAX_FIELD_CHARS]


def parse_alias_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        raw_items = value
    else:
        raw_items = _ALIAS_SPLIT.split(str(value or ""))
    names: list[str] = []
    seen: set[str] = set()
    prefix = character_import_analysis.TORI_TEXT_PREFIX
    for item in raw_items:
        if isinstance(item, dict):
            text = str(item.get("alias") or item.get("name") or "").strip()
        else:
            text = str(item or "").strip()
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
        if not text or len(text) > 80:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(text)
    return names


def parse_trait_json(raw: object, appearing: list[dict]) -> list[dict]:
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
        rows = data.get("characters") or data.get("items") or []
    if not isinstance(rows, list):
        return []
    by_id = {int(item["id"]): item for item in appearing if item.get("id") is not None}
    by_name: dict[str, dict] = {}
    for item in appearing:
        key = character_import_analysis.normalise_name(item.get("name")).casefold()
        if key:
            by_name.setdefault(key, item)
        for alias in item.get("aliases") or []:
            akey = character_import_analysis.normalise_name(alias).casefold()
            if akey:
                by_name.setdefault(akey, item)
    parsed: list[dict] = []
    seen_ids: set[int] = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        matched = None
        try:
            cid = int(item.get("id") or item.get("character_id"))
        except (TypeError, ValueError):
            cid = 0
        if cid in by_id:
            matched = by_id[cid]
        else:
            key = character_import_analysis.normalise_name(item.get("name")).casefold()
            matched = by_name.get(key)
        if matched is None:
            continue
        character_id = int(matched["id"])
        if character_id in seen_ids:
            continue
        seen_ids.add(character_id)
        profile = compose_profile(item)
        aliases = parse_alias_list(item.get("aliases") or item.get("alias"))
        fields = {
            "profile_md": profile,
            "aliases": aliases,
            "strengths_md": _usable_text(item.get("strengths_md") or item.get("strengths")),
            "weaknesses_md": _usable_text(item.get("weaknesses_md") or item.get("weaknesses")),
        }
        for forbidden in NEVER_FIELDS:
            fields.pop(forbidden, None)
        if not profile and not aliases and not fields["strengths_md"] and not fields["weaknesses_md"]:
            continue
        parsed.append({
            "id": character_id,
            "name": str(matched.get("name") or ""),
            "fields": fields,
        })
    return parsed


def compose_profile(item: dict) -> str:
    direct = _usable_text(item.get("profile_md") or item.get("profile"))
    if direct:
        return direct
    parts: list[str] = []
    for key, heading in (
        ("appearance", "외모"),
        ("personality", "성격"),
        ("relations", "관계"),
    ):
        text = _usable_text(item.get(key))
        if text:
            parts.append(f"[{heading}]\n{text}")
    return "\n\n".join(parts)[:MAX_FIELD_CHARS]


def build_trait_prompt(manuscript: str, appearing: list[dict]) -> tuple[str, str]:
    body = str(manuscript or "").strip()
    if len(body) > MAX_SCENE_CHARS:
        body = body[:MAX_SCENE_CHARS] + "\n…(이하 생략)"
    roster_lines: list[str] = []
    for item in appearing:
        aliases = ", ".join(str(a) for a in (item.get("aliases") or []) if str(a).strip())
        roster_lines.append(
            f"- id={item['id']} 이름={item.get('name') or ''}"
            + (f" 별칭={aliases}" if aliases else "")
            + f"\n  현재 인물 설정: {(item.get('profile_md') or '(비어 있음)')[:500]}"
            + f"\n  현재 강점: {(item.get('strengths_md') or '(비어 있음)')[:240]}"
            + f"\n  현재 약점: {(item.get('weaknesses_md') or '(비어 있음)')[:240]}"
        )
    roster = "\n".join(roster_lines) if roster_lines else "(없음)"
    system = (
        "당신은 한국어 소설 설정집 도우미 토리입니다. "
        "원고에 명시된 지속적 특성만 적고, 없는 설정은 지어내지 마세요. "
        "확신이 없으면 해당 칸을 비우세요. 출력은 JSON만 합니다."
    )
    user = (
        "[작업]\n"
        "아래 목록은 이 장면에 등장하는, 이미 등록된 인물입니다. "
        "이 인물들에 대해서만, 이번 화에서 새로 드러난 지속적 특성을 적으세요.\n"
        "목록에 없는 이름은 만들지 마세요.\n\n"
        "[판정 기준 — 반드시 지키세요]\n"
        "1. 명시적 서술 또는 대사로 직접 드러난 것만 인정합니다. "
        "추론·정황·분위기만으로 짐작하지 마세요.\n"
        "2. 일회성 행동과 지속적 특성을 구분하세요. "
        "「이번에 그랬다」가 아니라 「원래 그렇다」는 뉘앙스가 있을 때만 특성으로 적습니다.\n"
        "3. 관계 변화는 서술 자체가 변화를 명시한 경우만 인정합니다. "
        "대사 한 줄로 추측하지 마세요.\n"
        "4. 무기·강점·약점은 전투 등에서 실제로 쓰인 능력이 지속적 특성으로 "
        "드러날 때만 적습니다. 이번 한 번의 행동은 적지 마세요.\n"
        "5. 한 줄 소개와 작가 메모는 절대 적지 마세요.\n"
        "6. 확신이 서지 않으면 해당 칸을 빈 문자열로 두고 스킵하세요. "
        "억지로 채우지 마세요.\n"
        "7. 「원래」「평소」「늘」「항상」처럼 지속성이 분명히 적힌 서술은 "
        "해당 칸에 반드시 적으세요. 모든 칸이 빈 인물은 배열에 넣지 마세요.\n"
        "예: 「서윤은 원래 왼손잡이다」→ personality에 그 문장을 적습니다. "
        "「이번만 소리를 질렀다」「오늘은 그냥 한번 검을 들어 봤어」→ 적지 않습니다. "
        "「사이가 나빠진 것 같았다」처럼 추측만 있으면 relations를 비웁니다.\n\n"
        "[등록된 등장 인물]\n"
        f"{roster}\n\n"
        "[출력 JSON]\n"
        "{\n"
        '  "characters": [\n'
        "    {\n"
        '      "id": 숫자,\n'
        '      "name": "이름",\n'
        '      "appearance": "외모(명시된 것만)",\n'
        '      "personality": "성격(지속적 특성만)",\n'
        '      "relations": "관계(서술이 변화를 명시한 경우만)",\n'
        '      "aliases": ["이번 화에서 새로 불린 별칭"],\n'
        '      "strengths_md": "무기·강점(조건부)",\n'
        '      "weaknesses_md": "약점(조건부)"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "새 특성이 없는 인물은 배열에서 빼세요. JSON 외 텍스트는 출력하지 마세요.\n\n"
        "[원고]\n"
        f"{body}"
    )
    return system, user


def list_appearing_characters(
    connection: sqlite3.Connection,
    project_id: int,
    plain_text: str,
) -> list[dict]:
    char_rows = connection.execute(
        "SELECT id, name, role, short_description, profile_md, strengths_md, "
        "weaknesses_md, author_notes_md "
        "FROM character WHERE project_id = ? AND deleted_at IS NULL "
        "ORDER BY sort_order, id",
        (int(project_id),),
    ).fetchall()
    alias_rows = connection.execute(
        "SELECT character_id, alias FROM character_alias WHERE project_id = ? ORDER BY id",
        (int(project_id),),
    ).fetchall()
    alias_by_id: dict[int, list[str]] = {}
    for row in alias_rows:
        alias = str(row["alias"] or "").strip()
        if alias:
            alias_by_id.setdefault(int(row["character_id"]), []).append(alias)
    characters: list[dict] = []
    for row in char_rows:
        characters.append({
            "id": int(row["id"]),
            "name": str(row["name"] or ""),
            "role": str(row["role"] or "") or None,
            "aliases": alias_by_id.get(int(row["id"]), []),
            "short_description": str(row["short_description"] or ""),
            "profile_md": str(row["profile_md"] or ""),
            "strengths_md": str(row["strengths_md"] or ""),
            "weaknesses_md": str(row["weaknesses_md"] or ""),
            "author_notes_md": str(row["author_notes_md"] or ""),
        })
    detected = scene_cast_detect.detect_known_cast(plain_text, characters)
    appearing: list[dict] = []
    for item in characters:
        if detected.get(int(item["id"])) != scene_cast_detect.APPEARS:
            continue
        appearing.append(item)
    return appearing


def _field_is_empty(row: dict, field_name: str) -> bool:
    if field_name == "aliases":
        return not list(row.get("aliases") or [])
    return character_import_analysis.is_field_empty(row.get(field_name))


def _record_history(
    connection: sqlite3.Connection,
    *,
    character_id: int,
    project_id: int,
    scene_id: int,
    field_name: str,
    content: str,
) -> None:
    connection.execute(
        "INSERT INTO character_trait_history"
        "(character_id, project_id, scene_id, field_name, detected_content) "
        "VALUES (?, ?, ?, ?, ?)",
        (int(character_id), int(project_id), int(scene_id), field_name, content),
    )


def _add_aliases(
    connection: sqlite3.Connection,
    character_id: int,
    project_id: int,
    names: list[str],
) -> list[str]:
    existing = {
        str(row[0] or "").strip().casefold()
        for row in connection.execute(
            "SELECT alias FROM character_alias WHERE character_id = ?",
            (int(character_id),),
        ).fetchall()
    }
    added: list[str] = []
    for name in names:
        key = name.casefold()
        if key in existing:
            continue
        connection.execute(
            "INSERT INTO character_alias(character_id, project_id, alias, alias_type) "
            "VALUES (?, ?, ?, ?)",
            (int(character_id), int(project_id), name, "other"),
        )
        existing.add(key)
        added.append(name)
    return added


def apply_pending_aliases(connection: sqlite3.Connection, character_id: int) -> list[str]:
    row = connection.execute(
        "SELECT analyzed_content FROM character_tori_analysis "
        "WHERE character_id = ? AND field_name = 'aliases'",
        (int(character_id),),
    ).fetchone()
    if row is None:
        raise ValueError("적용할 토리 분석이 없습니다.")
    project = connection.execute(
        "SELECT project_id FROM character WHERE id = ? AND deleted_at IS NULL",
        (int(character_id),),
    ).fetchone()
    if project is None:
        raise ValueError("캐릭터를 찾을 수 없습니다.")
    names = parse_alias_list(row["analyzed_content"])
    added = _add_aliases(connection, int(character_id), int(project["project_id"]), names)
    character_import_analysis._clear_pending(connection, int(character_id), "aliases")
    return added


def apply_trait_detections(
    connection: sqlite3.Connection,
    *,
    project_id: int,
    scene_id: int,
    appearing: list[dict],
    parsed: list[dict],
) -> list[dict]:
    """Fill empty fields, pending occupied ones, always write history. Never touch NEVER_FIELDS."""
    by_id = {int(item["id"]): item for item in appearing}
    summaries: list[dict] = []
    for item in parsed:
        character_id = int(item["id"])
        row = by_id.get(character_id)
        if row is None:
            continue
        fields = dict(item.get("fields") or {})
        detections: list[dict] = []
        updates: dict[str, str] = {}
        filled = 0
        pending = 0
        before_profile = str(row.get("profile_md") or "")
        before_notes = str(row.get("author_notes_md") or "")
        before_summary = str(row.get("short_description") or "")

        profile = _usable_text(fields.get("profile_md"))
        if profile:
            marked = character_import_analysis.mark_tori_text(profile)
            _record_history(
                connection,
                character_id=character_id,
                project_id=project_id,
                scene_id=scene_id,
                field_name="profile_md",
                content=marked,
            )
            detections.append({"field": "profile_md", "applied": _field_is_empty(row, "profile_md")})
            if _field_is_empty(row, "profile_md"):
                updates["profile_md"] = marked
                row["profile_md"] = marked
                character_import_analysis._clear_pending(connection, character_id, "profile_md")
                filled += 1
            else:
                character_import_analysis._upsert_pending(connection, character_id, "profile_md", marked)
                pending += 1

        aliases = parse_alias_list(fields.get("aliases"))
        if aliases:
            existing = [str(a) for a in (row.get("aliases") or [])]
            existing_keys = {a.casefold() for a in existing}
            new_aliases = [name for name in aliases if name.casefold() not in existing_keys]
            if new_aliases:
                joined = "\n".join(new_aliases)
                marked_aliases = character_import_analysis.mark_tori_text(joined)
                _record_history(
                    connection,
                    character_id=character_id,
                    project_id=project_id,
                    scene_id=scene_id,
                    field_name="aliases",
                    content=marked_aliases,
                )
                detections.append({"field": "aliases", "applied": _field_is_empty(row, "aliases")})
                if _field_is_empty(row, "aliases"):
                    added = _add_aliases(connection, character_id, project_id, new_aliases)
                    row["aliases"] = list(existing) + added
                    character_import_analysis._clear_pending(connection, character_id, "aliases")
                    filled += 1
                else:
                    character_import_analysis._upsert_pending(
                        connection, character_id, "aliases", marked_aliases
                    )
                    pending += 1

        for field_name in ("strengths_md", "weaknesses_md"):
            content = _usable_text(fields.get(field_name))
            if not content:
                continue
            marked = character_import_analysis.mark_tori_text(content)
            _record_history(
                connection,
                character_id=character_id,
                project_id=project_id,
                scene_id=scene_id,
                field_name=field_name,
                content=marked,
            )
            detections.append({"field": field_name, "applied": _field_is_empty(row, field_name)})
            if _field_is_empty(row, field_name):
                updates[field_name] = marked
                row[field_name] = marked
                character_import_analysis._clear_pending(connection, character_id, field_name)
                filled += 1
            else:
                character_import_analysis._upsert_pending(connection, character_id, field_name, marked)
                pending += 1

        if updates:
            assignments = ", ".join(f"{key} = ?" for key in updates)
            connection.execute(
                f"UPDATE character SET {assignments} WHERE id = ? AND deleted_at IS NULL",
                (*updates.values(), character_id),
            )
        after = connection.execute(
            "SELECT short_description, author_notes_md FROM character WHERE id = ?",
            (character_id,),
        ).fetchone()
        if after is not None:
            if str(after["short_description"] or "") != before_summary:
                raise RuntimeError("한 줄 소개가 자동 변경되면 안 됩니다.")
            if str(after["author_notes_md"] or "") != before_notes:
                raise RuntimeError("작가 메모가 자동 변경되면 안 됩니다.")
        if not detections:
            continue
        summaries.append({
            "id": character_id,
            "name": str(row.get("name") or item.get("name") or ""),
            "count": len(detections),
            "filled": filled,
            "pending": pending,
            "fields": [entry["field"] for entry in detections],
        })
        del before_profile
    return summaries
