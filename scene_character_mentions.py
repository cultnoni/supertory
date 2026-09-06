"""Index and list scenes where a registered character name/alias is mentioned.

Cross-reference search (settings_search) looks inside character cards, not
manuscript bodies. Manuscript name matching already lives in scene_cast_detect
(character_labels + find_name_spans / detect_known_cast). This module reuses
that matcher: any hit (appears or mentioned) counts as an appearance.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from typing import Any

import scene_cast_detect

PlainFn = Callable[[str], str]

SNIPPET_CHARS = 90
_QUOTE_RE = re.compile(r'[“"「『]([^“”"「」『』\n]{1,240})[”"」』]')
_SPEECH_VERB = re.compile(
    r"(말했|말했어|말한다|말하며|물었|물었어|되물|대답|중얼|외쳤|소리쳤|속삭|답했)"
)
_WS = re.compile(r"\s+")


def table_ready(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'scene_character_mention'"
    ).fetchone()
    return row is not None


def load_project_characters(connection: sqlite3.Connection, project_id: int) -> list[dict]:
    rows = connection.execute(
        "SELECT id, name FROM character "
        "WHERE project_id = ? AND deleted_at IS NULL ORDER BY sort_order, id",
        (int(project_id),),
    ).fetchall()
    alias_rows = []
    try:
        alias_rows = connection.execute(
            "SELECT character_id, alias FROM character_alias WHERE project_id = ? ORDER BY id",
            (int(project_id),),
        ).fetchall()
    except sqlite3.OperationalError:
        alias_rows = []
    aliases: dict[int, list[str]] = {}
    for row in alias_rows:
        alias = str(row["alias"] or "").strip()
        if alias:
            aliases.setdefault(int(row["character_id"]), []).append(alias)
    characters: list[dict] = []
    for row in rows:
        cid = int(row["id"])
        characters.append(
            {
                "id": cid,
                "name": str(row["name"] or ""),
                "aliases": aliases.get(cid, []),
            }
        )
    return characters


def _clip_snippet(value: str, limit: int = SNIPPET_CHARS) -> str:
    text = _WS.sub(" ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _quote_matches_labels(left: str, right: str, labels: list[str]) -> bool:
    left_tail = left[-48:]
    right_head = right[:42]
    for label in labels:
        if not label:
            continue
        if _NAME_COLON_OK(left_tail, label):
            return True
        if scene_cast_detect.find_name_spans(left_tail, label) and _SPEECH_VERB.search(left_tail):
            return True
        if scene_cast_detect.find_name_spans(right_head, label):
            return True
    return False


def _NAME_COLON_OK(left: str, label: str) -> bool:
    return bool(re.search(re.escape(label) + r"[은는이가도]?\s*[:：]\s*$", left))


def last_dialogue(plain_text: str, character: dict, roster: list[dict] | None = None) -> str:
    """Last quoted line attributed to this character in the scene."""
    text = str(plain_text or "")
    if not text:
        return ""
    try:
        ours = int(character.get("id"))
    except (TypeError, ValueError):
        return ""
    others = [item for item in (roster or [character]) if item is not None]
    last_speaker: int | None = None
    last_line = ""
    for match in _QUOTE_RE.finditer(text):
        body = str(match.group(1) or "").strip()
        if not body:
            continue
        left = text[max(0, match.start() - 48):match.start()]
        right = text[match.end():min(len(text), match.end() + 42)]
        speakers: list[int] = []
        for item in others:
            try:
                cid = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            item_labels = scene_cast_detect.character_labels(item)
            if _quote_matches_labels(left, right, item_labels):
                speakers.append(cid)
        if ours in speakers:
            last_speaker = ours
            last_line = body
        elif speakers:
            last_speaker = speakers[0]
        elif last_speaker == ours:
            last_line = body
    return last_line


def describe_mention(plain_text: str, character: dict, roster: list[dict] | None = None) -> dict[str, str]:
    """How this character shows up in a scene: kind, snippet, last spoken line."""
    text = str(plain_text or "")
    labels = scene_cast_detect.character_labels(character)
    matched = ""
    kind = ""
    snippet = ""
    first_span: tuple[int, int] | None = None
    appear_span: tuple[int, int] | None = None
    for label in labels:
        for start, end in scene_cast_detect.find_name_spans(text, label):
            hit_kind = scene_cast_detect.classify_hit(text, start, end, name=label)
            if first_span is None:
                matched = label
                first_span = (start, end)
            if hit_kind == scene_cast_detect.APPEARS and appear_span is None:
                matched = label
                appear_span = (start, end)
    if appear_span is not None:
        kind = scene_cast_detect.APPEARS
        snippet = _clip_snippet(scene_cast_detect._sentence_span(text, *appear_span))
    elif first_span is not None:
        kind = scene_cast_detect.MENTIONED
        snippet = _clip_snippet(scene_cast_detect._sentence_span(text, *first_span))
    return {
        "matched_label": matched,
        "kind": kind,
        "snippet": snippet,
        "last_line": last_dialogue(text, character, roster or [character]),
    }


def mention_hits(plain_text: str, characters: list[dict]) -> dict[int, str]:
    """character_id → longest matching name/alias label."""
    text = str(plain_text or "")
    if not text or not characters:
        return {}
    detected = scene_cast_detect.detect_known_cast(text, characters)
    hits: dict[int, str] = {}
    for character in characters:
        try:
            cid = int(character.get("id"))
        except (TypeError, ValueError):
            continue
        if cid not in detected:
            continue
        label = ""
        for name in scene_cast_detect.character_labels(character):
            if scene_cast_detect.find_name_spans(text, name):
                label = name
                break
        hits[cid] = label
    return hits


def _write_scene_hits(
    connection: sqlite3.Connection,
    scene_id: int,
    project_id: int,
    hits: dict[int, str],
) -> None:
    connection.execute(
        "DELETE FROM scene_character_mention WHERE scene_id = ?",
        (int(scene_id),),
    )
    for character_id, label in hits.items():
        connection.execute(
            "INSERT INTO scene_character_mention"
            "(scene_id, character_id, project_id, matched_label) "
            "VALUES (?, ?, ?, ?)",
            (int(scene_id), int(character_id), int(project_id), str(label or "")),
        )


def _load_scene_content(
    connection: sqlite3.Connection, scene_id: int
) -> tuple[int, str] | None:
    row = connection.execute(
        "SELECT s.project_id, COALESCE(r.content_md, '') AS content_md "
        "FROM scene s "
        "LEFT JOIN scene_revision r ON r.scene_id = s.id AND r.is_current = 1 "
        "WHERE s.id = ? AND s.deleted_at IS NULL",
        (int(scene_id),),
    ).fetchone()
    if row is None:
        return None
    return int(row["project_id"]), str(row["content_md"] or "")


def reindex_scene(
    connection: sqlite3.Connection,
    scene_id: int,
    content: str | None,
    plain_fn: PlainFn,
) -> None:
    if not table_ready(connection):
        return
    loaded = _load_scene_content(connection, scene_id)
    if loaded is None:
        connection.execute(
            "DELETE FROM scene_character_mention WHERE scene_id = ?",
            (int(scene_id),),
        )
        return
    project_id, stored = loaded
    raw = stored if content is None else str(content)
    characters = load_project_characters(connection, project_id)
    hits = mention_hits(plain_fn(raw), characters)
    _write_scene_hits(connection, scene_id, project_id, hits)


def reindex_project(
    connection: sqlite3.Connection,
    project_id: int,
    plain_fn: PlainFn,
) -> None:
    if not table_ready(connection):
        return
    pid = int(project_id)
    connection.execute(
        "DELETE FROM scene_character_mention WHERE project_id = ?",
        (pid,),
    )
    characters = load_project_characters(connection, pid)
    rows = connection.execute(
        "SELECT s.id, COALESCE(r.content_md, '') AS content_md "
        "FROM scene s "
        "LEFT JOIN scene_revision r ON r.scene_id = s.id AND r.is_current = 1 "
        "WHERE s.project_id = ? AND s.deleted_at IS NULL",
        (pid,),
    ).fetchall()
    for row in rows:
        hits = mention_hits(plain_fn(str(row["content_md"] or "")), characters)
        _write_scene_hits(connection, int(row["id"]), pid, hits)
    connection.execute(
        "INSERT INTO scene_character_mention_state(project_id, indexed_at) "
        "VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) "
        "ON CONFLICT(project_id) DO UPDATE SET indexed_at = excluded.indexed_at",
        (pid,),
    )


def project_is_indexed(connection: sqlite3.Connection, project_id: int) -> bool:
    if not table_ready(connection):
        return False
    row = connection.execute(
        "SELECT 1 FROM scene_character_mention_state WHERE project_id = ?",
        (int(project_id),),
    ).fetchone()
    return row is not None


def ensure_project_indexed(
    connection: sqlite3.Connection,
    project_id: int,
    plain_fn: PlainFn,
) -> None:
    if project_is_indexed(connection, project_id):
        return
    reindex_project(connection, project_id, plain_fn)


def delete_character_mentions(connection: sqlite3.Connection, character_id: int) -> None:
    if not table_ready(connection):
        return
    connection.execute(
        "DELETE FROM scene_character_mention WHERE character_id = ?",
        (int(character_id),),
    )


def list_mentions(
    connection: sqlite3.Connection,
    project_id: int,
    character_id: int,
    binder_scenes: list[dict],
    plain_fn: PlainFn | None = None,
) -> list[dict[str, Any]]:
    """Return mention rows in reverse binder DFS order (latest first)."""
    pid = int(project_id)
    cid = int(character_id) if character_id else 0
    if cid > 0:
        rows = connection.execute(
            "SELECT m.scene_id, m.character_id, m.matched_label, c.name AS character_name "
            "FROM scene_character_mention m "
            "JOIN character c ON c.id = m.character_id AND c.deleted_at IS NULL "
            "WHERE m.project_id = ? AND m.character_id = ?",
            (pid, cid),
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT m.scene_id, m.character_id, m.matched_label, c.name AS character_name "
            "FROM scene_character_mention m "
            "JOIN character c ON c.id = m.character_id AND c.deleted_at IS NULL "
            "WHERE m.project_id = ?",
            (pid,),
        ).fetchall()
    roster = load_project_characters(connection, pid)
    by_id = {int(item["id"]): item for item in roster}
    to_plain = plain_fn or (lambda value: str(value or ""))
    index_by_id = {}
    meta_by_id = {}
    for index, scene in enumerate(binder_scenes):
        try:
            sid = int(scene.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if sid <= 0:
            continue
        index_by_id[sid] = index
        meta_by_id[sid] = scene
    entries: list[dict[str, Any]] = []
    for row in rows:
        sid = int(row["scene_id"])
        meta = meta_by_id.get(sid) or {}
        character = by_id.get(int(row["character_id"])) or {
            "id": int(row["character_id"]),
            "name": str(row["character_name"] or ""),
            "aliases": [],
        }
        detail = describe_mention(to_plain(meta.get("content_md") or ""), character, roster)
        entries.append(
            {
                "scene_id": sid,
                "character_id": int(row["character_id"]),
                "character_name": str(row["character_name"] or ""),
                "matched_label": detail["matched_label"] or str(row["matched_label"] or ""),
                "kind": detail["kind"],
                "snippet": detail["snippet"],
                "last_line": detail["last_line"],
                "title": str(meta.get("title") or ""),
                "chapter_title": str(meta.get("chapter_title") or ""),
                "folder_path": str(meta.get("folder_path") or ""),
                "binder_index": index_by_id.get(sid),
            }
        )
    entries.sort(
        key=lambda item: (
            -(item["binder_index"] if item["binder_index"] is not None else -1),
            str(item["character_name"] or ""),
            int(item["character_id"] or 0),
        )
    )
    for offset, item in enumerate(entries):
        item["latest"] = offset == 0
        item.pop("binder_index", None)
    return entries
