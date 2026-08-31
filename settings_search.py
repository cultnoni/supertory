"""Search characters, items, worldbuilding, and relation labels in one project."""

from __future__ import annotations

import re
import sqlite3

from world_import_analysis import FIELD_LABELS, parse_worldbuilding_md

_WS = re.compile(r"\s+")
_PROFILE_HEADING = re.compile(r"^\[([^\]]+)\]\s*$")
SNIPPET_RADIUS = 36
MAX_HITS = 40

CHARACTER_FIELD_LABELS = {
    "name": "이름",
    "alias": "별칭",
    "short_description": "한 줄 소개",
    "profile": "인물 설정",
    "appearance": "외모",
    "personality": "성격",
    "relations": "관계",
    "strengths": "무기·강점",
    "weaknesses": "약점",
}
ITEM_FIELD_LABELS = {
    "name": "이름",
    "alias": "별칭",
    "description": "설명",
}
PROFILE_HEADING_FIELDS = {
    "외모": "appearance",
    "성격": "personality",
    "관계": "relations",
}


def compact_text(value: object) -> str:
    return _WS.sub("", str(value or "")).casefold()


def text_matches(value: object, query: str) -> bool:
    raw = str(value or "")
    needle = str(query or "").strip()
    if not needle or not raw:
        return False
    if needle.casefold() in raw.casefold():
        return True
    compact_needle = compact_text(needle)
    return bool(compact_needle) and compact_needle in compact_text(raw)


def make_snippet(value: object, query: str, radius: int = SNIPPET_RADIUS) -> str:
    raw = _WS.sub(" ", str(value or "")).strip()
    if not raw:
        return ""
    needle = str(query or "").strip()
    lower = raw.casefold()
    idx = lower.find(needle.casefold()) if needle else -1
    if idx < 0:
        start = 0
        end = min(len(raw), radius * 2)
        snippet = raw[start:end]
        return snippet + ("…" if end < len(raw) else "")
    start = max(0, idx - radius)
    end = min(len(raw), idx + max(len(needle), 1) + radius)
    snippet = raw[start:end]
    if start:
        snippet = "…" + snippet
    if end < len(raw):
        snippet += "…"
    return snippet


def _hit(kind: str, item_id: object, title: str, field: str, field_label: str, snippet: str, **extra) -> dict:
    payload = {
        "type": kind,
        "id": item_id,
        "title": title,
        "field": field,
        "field_label": field_label,
        "snippet": snippet,
    }
    payload.update(extra)
    return payload


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _split_profile_sections(profile_md: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    heading = "인물 설정"
    buf: list[str] = []
    for line in str(profile_md or "").splitlines():
        matched = _PROFILE_HEADING.match(line.strip())
        if matched:
            if buf:
                sections.append((heading, "\n".join(buf).strip()))
            heading = matched.group(1).strip() or "인물 설정"
            buf = []
        else:
            buf.append(line)
    body = "\n".join(buf).strip()
    if body or not sections:
        sections.append((heading, body))
    return sections


def _profile_field_key(heading: str) -> str:
    text = str(heading or "").strip()
    for label, key in PROFILE_HEADING_FIELDS.items():
        if label in text:
            return key
    return "profile"


def _search_characters(connection: sqlite3.Connection, project_id: int, query: str) -> list[dict]:
    if not _table_exists(connection, "character"):
        return []
    rows = connection.execute(
        "SELECT id, name, short_description, profile_md, strengths_md, weaknesses_md "
        "FROM character WHERE project_id = ? AND deleted_at IS NULL ORDER BY sort_order, id",
        (project_id,),
    ).fetchall()
    alias_rows = []
    if _table_exists(connection, "character_alias"):
        alias_rows = connection.execute(
            "SELECT character_id, alias FROM character_alias WHERE project_id = ? ORDER BY id",
            (project_id,),
        ).fetchall()
    aliases: dict[int, list[str]] = {}
    for row in alias_rows:
        alias = str(row["alias"] or "").strip()
        if alias:
            aliases.setdefault(int(row["character_id"]), []).append(alias)
    hits: list[dict] = []
    for row in rows:
        if len(hits) >= MAX_HITS:
            break
        cid = int(row["id"])
        name = str(row["name"] or "")
        candidates: list[tuple[str, str, str]] = [
            ("name", CHARACTER_FIELD_LABELS["name"], name),
        ]
        for alias in aliases.get(cid, []):
            candidates.append(("alias", CHARACTER_FIELD_LABELS["alias"], alias))
        candidates.append(
            ("short_description", CHARACTER_FIELD_LABELS["short_description"], str(row["short_description"] or ""))
        )
        profile = str(row["profile_md"] or "")
        profile_sections = _split_profile_sections(profile)
        had_body = False
        for heading, body in profile_sections:
            if not body:
                continue
            had_body = True
            key = _profile_field_key(heading)
            label = CHARACTER_FIELD_LABELS.get(key, heading or CHARACTER_FIELD_LABELS["profile"])
            candidates.append((key, label, body))
        if profile.strip() and not had_body:
            candidates.append(("profile", CHARACTER_FIELD_LABELS["profile"], profile))
        candidates.append(("strengths", CHARACTER_FIELD_LABELS["strengths"], str(row["strengths_md"] or "")))
        candidates.append(("weaknesses", CHARACTER_FIELD_LABELS["weaknesses"], str(row["weaknesses_md"] or "")))
        for field, label, text in candidates:
            if text_matches(text, query):
                hits.append(_hit(
                    "character",
                    cid,
                    name or f"인물#{cid}",
                    field,
                    label,
                    make_snippet(text, query),
                ))
                break
    return hits


def _search_items(connection: sqlite3.Connection, project_id: int, query: str) -> list[dict]:
    if not _table_exists(connection, "item"):
        return []
    rows = connection.execute(
        "SELECT id, name, description FROM item "
        "WHERE project_id = ? AND deleted_at IS NULL ORDER BY sort_order, id",
        (project_id,),
    ).fetchall()
    alias_rows = []
    if _table_exists(connection, "item_alias"):
        alias_rows = connection.execute(
            "SELECT item_id, alias FROM item_alias WHERE project_id = ? ORDER BY id",
            (project_id,),
        ).fetchall()
    aliases: dict[int, list[str]] = {}
    for row in alias_rows:
        alias = str(row["alias"] or "").strip()
        if alias:
            aliases.setdefault(int(row["item_id"]), []).append(alias)
    hits: list[dict] = []
    for row in rows:
        if len(hits) >= MAX_HITS:
            break
        iid = int(row["id"])
        name = str(row["name"] or "")
        candidates: list[tuple[str, str, str]] = [
            ("name", ITEM_FIELD_LABELS["name"], name),
        ]
        for alias in aliases.get(iid, []):
            candidates.append(("alias", ITEM_FIELD_LABELS["alias"], alias))
        candidates.append(("description", ITEM_FIELD_LABELS["description"], str(row["description"] or "")))
        for field, label, text in candidates:
            if text_matches(text, query):
                hits.append(_hit("item", iid, name or f"아이템#{iid}", field, label, make_snippet(text, query)))
                break
    return hits


def _search_world(connection: sqlite3.Connection, project_id: int, query: str) -> list[dict]:
    row = connection.execute(
        "SELECT worldbuilding_md FROM project WHERE id = ? AND deleted_at IS NULL",
        (project_id,),
    ).fetchone()
    if row is None:
        return []
    values = parse_worldbuilding_md(row["worldbuilding_md"])
    hits: list[dict] = []
    for field_id, text in values.items():
        if len(hits) >= MAX_HITS:
            break
        if not text_matches(text, query):
            continue
        label = FIELD_LABELS.get(field_id, "기타 · 기존 메모" if field_id == "legacy" else field_id)
        hits.append(_hit(
            "world",
            field_id,
            label,
            field_id,
            label,
            make_snippet(text, query),
            section=field_id,
        ))
    return hits


def _search_relations(connection: sqlite3.Connection, project_id: int, query: str) -> list[dict]:
    if not _table_exists(connection, "character_relations"):
        return []
    rows = connection.execute(
        "SELECT r.id, r.character_a_id, r.character_b_id, r.label, r.status, "
        "a.name AS a_name, b.name AS b_name "
        "FROM character_relations r "
        "JOIN character a ON a.id = r.character_a_id AND a.deleted_at IS NULL "
        "JOIN character b ON b.id = r.character_b_id AND b.deleted_at IS NULL "
        "WHERE r.project_id = ? ORDER BY r.id",
        (project_id,),
    ).fetchall()
    hits: list[dict] = []
    for row in rows:
        if len(hits) >= MAX_HITS:
            break
        label = str(row["label"] or "")
        if not text_matches(label, query):
            continue
        a_name = str(row["a_name"] or "")
        b_name = str(row["b_name"] or "")
        title = f"{a_name} — {b_name}" if a_name or b_name else label
        hits.append(_hit(
            "relation",
            int(row["id"]),
            title,
            "label",
            "관계 라벨",
            make_snippet(label, query),
            character_a_id=int(row["character_a_id"]),
            character_b_id=int(row["character_b_id"]),
            label=label,
            status=str(row["status"] or ""),
        ))
    return hits


def search_project_settings(connection: sqlite3.Connection, project_id: int, query: str) -> dict:
    """Return grouped hits for one project. Empty query yields empty groups."""
    q = str(query or "").strip()[:200]
    empty = {
        "query": q,
        "characters": [],
        "items": [],
        "world": [],
        "relations": [],
    }
    if not q:
        return empty
    project_id = int(project_id)
    return {
        "query": q,
        "characters": _search_characters(connection, project_id, q),
        "items": _search_items(connection, project_id, q),
        "world": _search_world(connection, project_id, q),
        "relations": _search_relations(connection, project_id, q),
    }
