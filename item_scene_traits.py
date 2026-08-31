"""Detect registered items and new item candidates when a scene is completed.

Registered names/aliases are matched with the same span rules as cast-sync.
Empty description is filled with a 〔토리〕 prefix; occupied fields become a
pending badge. Every detection is stored in item_trait_history.
Unregistered proper nouns that repeat in the scene become candidates — the
author must approve before an item row is created.
"""

from __future__ import annotations

import json
import re
import sqlite3

import character_import_analysis
import scene_cast_detect

MAX_SCENE_CHARS = 24_000
MAX_FIELD_CHARS = 4000
MAX_CANDIDATES = 8
MIN_CANDIDATE_HITS = 2

_ALIAS_SPLIT = re.compile(r"[,，;/\n]+")


def _usable_text(value: object) -> str:
    return str(value or "").strip()[:MAX_FIELD_CHARS]


def item_labels(item: dict) -> list[str]:
    return scene_cast_detect.character_labels(item)


def count_label_hits(text: str, labels: list[str]) -> int:
    total = 0
    for label in labels:
        total += len(scene_cast_detect.find_name_spans(text, label))
    return total


def list_mentioned_items(
    connection: sqlite3.Connection,
    project_id: int,
    plain_text: str,
) -> list[dict]:
    rows = connection.execute(
        "SELECT id, name, description, owner_character_id "
        "FROM item WHERE project_id = ? AND deleted_at IS NULL "
        "ORDER BY sort_order, id",
        (int(project_id),),
    ).fetchall()
    alias_rows = connection.execute(
        "SELECT item_id, alias FROM item_alias WHERE project_id = ? ORDER BY id",
        (int(project_id),),
    ).fetchall()
    alias_by_id: dict[int, list[str]] = {}
    for row in alias_rows:
        alias = str(row["alias"] or "").strip()
        if alias:
            alias_by_id.setdefault(int(row["item_id"]), []).append(alias)
    mentioned: list[dict] = []
    for row in rows:
        item = {
            "id": int(row["id"]),
            "name": str(row["name"] or ""),
            "description": str(row["description"] or ""),
            "owner_character_id": row["owner_character_id"],
            "aliases": alias_by_id.get(int(row["id"]), []),
        }
        if count_label_hits(plain_text, item_labels(item)) > 0:
            mentioned.append(item)
    return mentioned


def known_item_labels(connection: sqlite3.Connection, project_id: int) -> list[str]:
    names = [
        str(row[0] or "").strip()
        for row in connection.execute(
            "SELECT name FROM item WHERE project_id = ? AND deleted_at IS NULL",
            (int(project_id),),
        ).fetchall()
    ]
    names.extend(
        str(row[0] or "").strip()
        for row in connection.execute(
            "SELECT alias FROM item_alias WHERE project_id = ?",
            (int(project_id),),
        ).fetchall()
    )
    names.extend(
        str(row[0] or "").strip()
        for row in connection.execute(
            "SELECT name FROM character WHERE project_id = ? AND deleted_at IS NULL",
            (int(project_id),),
        ).fetchall()
    )
    names.extend(
        str(row[0] or "").strip()
        for row in connection.execute(
            "SELECT alias FROM character_alias WHERE project_id = ?",
            (int(project_id),),
        ).fetchall()
    )
    return [name for name in names if name]


def parse_item_analysis_json(raw: object, mentioned: list[dict]) -> tuple[list[dict], list[str]]:
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
            return [], []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return [], []
    if not isinstance(data, dict):
        return [], []
    by_id = {int(item["id"]): item for item in mentioned if item.get("id") is not None}
    by_name: dict[str, dict] = {}
    for item in mentioned:
        key = str(item.get("name") or "").strip().casefold()
        if key:
            by_name[key] = item
    parsed_items: list[dict] = []
    rows = data.get("items") or []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            matched = None
            try:
                matched = by_id.get(int(row.get("id")))
            except (TypeError, ValueError):
                matched = None
            if matched is None:
                key = str(row.get("name") or "").strip().casefold()
                matched = by_name.get(key)
            if matched is None:
                continue
            description = _usable_text(row.get("description"))
            if not description:
                continue
            parsed_items.append({"id": int(matched["id"]), "description": description})
    candidates: list[str] = []
    seen: set[str] = set()
    raw_names = data.get("candidates") or data.get("names") or []
    if isinstance(raw_names, list):
        for item in raw_names:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
            else:
                name = str(item or "").strip()
            if not name or len(name) > 40:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(name)
    return parsed_items, candidates[:MAX_CANDIDATES]


def build_item_prompt(
    manuscript: str,
    mentioned: list[dict],
    known_labels: list[str],
) -> tuple[str, str]:
    body = str(manuscript or "").strip()
    if len(body) > MAX_SCENE_CHARS:
        body = body[:MAX_SCENE_CHARS] + "\n…(이하 생략)"
    known = "、".join(str(item).strip() for item in known_labels if str(item or "").strip()) or "(없음)"
    registered_lines = []
    for item in mentioned:
        aliases = " / ".join(item.get("aliases") or []) or "(없음)"
        desc = str(item.get("description") or "").strip() or "(비어 있음)"
        registered_lines.append(
            f"- id={item['id']} name={item['name']} aliases={aliases} description={desc[:200]}"
        )
    registered = "\n".join(registered_lines) or "(이 회차에 언급된 등록 아이템 없음)"
    system = (
        "당신은 한국어 소설의 소품·아이템을 가려내는 도우미 토리입니다. "
        "출력은 JSON만 합니다."
    )
    user = (
        "[작업]\n"
        "1) 이미 등록된 아이템이 이 회차에서 속성·외형·능력으로 명시되면 description에 그 사실만 적는다. "
        "원래/평소부터 그런 설정으로 보이는 일회성 연출은 넣지 마라. "
        "추측·분위기만으로 채우지 마라. 새 사실이 없으면 그 아이템은 배열에서 빼라.\n"
        "2) 아직 등록되지 않은 물건 고유명사만 candidates에 넣는다. "
        "같은 이름이 본문에서 반복해서 구체적으로 등장할 때만. "
        "한 번 스쳐 지나가는 사물·일반명사(칼, 문, 편지, 잔)는 빼라. "
        "이미 있는 이름과 인물 이름도 빼라. "
        f"최대 {MAX_CANDIDATES}개.\n\n"
        "[이미 있는 이름]\n"
        f"{known}\n\n"
        "[이 회차에 언급된 등록 아이템]\n"
        f"{registered}\n\n"
        "[출력 JSON]\n"
        "{\n"
        '  "items": [{"id": 1, "description": "짧은 속성 설명"}],\n'
        '  "candidates": ["고유명사"]\n'
        "}\n"
        "해당이 없으면 {\"items\": [], \"candidates\": []} 만 출력한다. JSON 외 텍스트는 출력하지 마세요.\n\n"
        "[원고]\n"
        f"{body}"
    )
    return system, user


def filter_repeated_candidates(plain_text: str, candidates: list[str], known_labels: list[str]) -> list[str]:
    known = {str(name or "").strip().casefold() for name in known_labels if str(name or "").strip()}
    kept: list[str] = []
    for name in candidates:
        key = name.casefold()
        if key in known:
            continue
        if count_label_hits(plain_text, [name]) < MIN_CANDIDATE_HITS:
            continue
        kept.append(name)
        known.add(key)
    return kept[:MAX_CANDIDATES]


def _upsert_item_pending(
    connection: sqlite3.Connection,
    item_id: int,
    field_name: str,
    content: str,
) -> None:
    connection.execute(
        "INSERT INTO item_tori_analysis(item_id, field_name, analyzed_content) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT(item_id, field_name) DO UPDATE SET "
        "analyzed_content = excluded.analyzed_content, "
        "created_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
        (int(item_id), field_name, content),
    )


def _clear_item_pending(
    connection: sqlite3.Connection,
    item_id: int,
    field_name: str,
) -> None:
    connection.execute(
        "DELETE FROM item_tori_analysis WHERE item_id = ? AND field_name = ?",
        (int(item_id), field_name),
    )


def list_pending_for_item(connection: sqlite3.Connection, item_id: int) -> dict[str, dict]:
    pending: dict[str, dict] = {}
    rows = connection.execute(
        "SELECT field_name, analyzed_content, created_at FROM item_tori_analysis "
        "WHERE item_id = ?",
        (int(item_id),),
    ).fetchall()
    for row in rows:
        key = str(row["field_name"] or "")
        if key != "description":
            continue
        pending[key] = {
            "field_name": key,
            "label": "설명",
            "content": str(row["analyzed_content"] or ""),
            "created_at": row["created_at"],
        }
    return pending


def pending_item_ids(connection: sqlite3.Connection, project_id: int) -> set[int]:
    rows = connection.execute(
        "SELECT DISTINCT a.item_id FROM item_tori_analysis a "
        "JOIN item i ON a.item_id = i.id "
        "WHERE i.project_id = ? AND i.deleted_at IS NULL",
        (int(project_id),),
    ).fetchall()
    return {int(row[0]) for row in rows}


def apply_pending_description(connection: sqlite3.Connection, item_id: int) -> str:
    row = connection.execute(
        "SELECT analyzed_content FROM item_tori_analysis "
        "WHERE item_id = ? AND field_name = 'description'",
        (int(item_id),),
    ).fetchone()
    if row is None:
        raise ValueError("적용할 토리 분석이 없습니다.")
    content = str(row["analyzed_content"] or "").strip()
    if not content:
        raise ValueError("적용할 토리 분석이 없습니다.")
    connection.execute(
        "UPDATE item SET description = ?, "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
        "row_version = row_version + 1 "
        "WHERE id = ? AND deleted_at IS NULL",
        (content, int(item_id)),
    )
    _clear_item_pending(connection, int(item_id), "description")
    return content


def apply_item_detections(
    connection: sqlite3.Connection,
    *,
    project_id: int,
    scene_id: int,
    mentioned: list[dict],
    parsed: list[dict],
) -> list[dict]:
    by_id = {int(item["id"]): item for item in mentioned}
    summaries: list[dict] = []
    for item in parsed:
        item_id = int(item["id"])
        row = by_id.get(item_id)
        if row is None:
            continue
        description = _usable_text(item.get("description"))
        if not description:
            continue
        marked = character_import_analysis.mark_tori_text(description)
        empty = character_import_analysis.is_field_empty(row.get("description"))
        applied = False
        connection.execute(
            "INSERT INTO item_trait_history"
            "(item_id, project_id, scene_id, field_name, detected_content, applied) "
            "VALUES (?, ?, ?, 'description', ?, ?)",
            (item_id, int(project_id), int(scene_id), marked, 1 if empty else 0),
        )
        if empty:
            connection.execute(
                "UPDATE item SET description = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
                "row_version = row_version + 1 "
                "WHERE id = ? AND deleted_at IS NULL",
                (marked, item_id),
            )
            row["description"] = marked
            _clear_item_pending(connection, item_id, "description")
            applied = True
        else:
            _upsert_item_pending(connection, item_id, "description", marked)
        summaries.append({
            "id": item_id,
            "name": row.get("name") or "",
            "filled": 1 if applied else 0,
            "pending": 0 if applied else 1,
        })
    return summaries


def list_trait_history(connection: sqlite3.Connection, item_id: int) -> list[dict]:
    rows = connection.execute(
        "SELECT h.id, h.scene_id, h.field_name, h.detected_content, h.applied, "
        "h.created_at, s.title AS scene_title "
        "FROM item_trait_history h "
        "LEFT JOIN scene s ON s.id = h.scene_id "
        "WHERE h.item_id = ? "
        "ORDER BY h.id",
        (int(item_id),),
    ).fetchall()
    entries: list[dict] = []
    for row in rows:
        entries.append(
            {
                "id": int(row["id"]),
                "scene_id": int(row["scene_id"]),
                "field_name": str(row["field_name"] or ""),
                "detected_content": str(row["detected_content"] or ""),
                "applied": bool(row["applied"]),
                "created_at": row["created_at"],
                "scene_title": str(row["scene_title"] or ""),
            }
        )
    return entries
