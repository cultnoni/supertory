"""Project-local coined-word glossary (토리 사전).

Name overlaps with characters, items, and worldbuilding are reported as
informational `conflicts` and never block a save.
"""

from __future__ import annotations

import re
import sqlite3

from world_import_analysis import parse_worldbuilding_md

_WS = re.compile(r"\s+")


def _fold(value: object) -> str:
    return _WS.sub(" ", str(value or "")).strip().casefold()


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _collect_names(connection: sqlite3.Connection, sql: str, project_id: int) -> set[str]:
    names: set[str] = set()
    try:
        rows = connection.execute(sql, (project_id,)).fetchall()
    except sqlite3.Error:
        return names
    for row in rows:
        folded = _fold(row[0])
        if folded:
            names.add(folded)
    return names


def conflict_name_sets(connection: sqlite3.Connection, project_id: int) -> dict[str, set[str]]:
    characters = _collect_names(
        connection,
        "SELECT name FROM character WHERE project_id = ? AND deleted_at IS NULL",
        project_id,
    )
    if _table_exists(connection, "character_alias"):
        characters |= _collect_names(
            connection,
            "SELECT alias FROM character_alias WHERE project_id = ?",
            project_id,
        )
    items: set[str] = set()
    if _table_exists(connection, "item"):
        items = _collect_names(
            connection,
            "SELECT name FROM item WHERE project_id = ? AND deleted_at IS NULL",
            project_id,
        )
        if _table_exists(connection, "item_alias"):
            items |= _collect_names(
                connection,
                "SELECT alias FROM item_alias WHERE project_id = ?",
                project_id,
            )
    world: set[str] = set()
    try:
        row = connection.execute(
            "SELECT worldbuilding_md FROM project WHERE id = ? AND deleted_at IS NULL",
            (project_id,),
        ).fetchone()
    except sqlite3.Error:
        row = None
    if row is not None:
        values = parse_worldbuilding_md(row["worldbuilding_md"] if isinstance(row, sqlite3.Row) else row[0])
        for text in values.values():
            for line in str(text or "").splitlines():
                folded = _fold(line)
                if folded:
                    world.add(folded)
    return {"character": characters, "item": items, "world": world}


def conflicts_for_term(term: str, name_sets: dict[str, set[str]]) -> list[str]:
    needle = _fold(term)
    if not needle:
        return []
    found: list[str] = []
    for kind in ("character", "item", "world"):
        if needle in name_sets.get(kind, set()):
            found.append(kind)
    return found


def serialize_term(row: sqlite3.Row, conflicts: list[str] | None = None) -> dict:
    memo = str(row["memo"] or "").strip()
    return {
        "id": int(row["id"]),
        "project_id": int(row["project_id"]),
        "term": str(row["term"] or ""),
        "definition": str(row["definition"] or ""),
        "memo": memo or None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "conflicts": list(conflicts or []),
    }


def _load_row(connection: sqlite3.Connection, term_id: int) -> sqlite3.Row:
    row = connection.execute(
        "SELECT id, project_id, term, definition, memo, created_at, updated_at "
        "FROM custom_dictionary_terms WHERE id = ?",
        (term_id,),
    ).fetchone()
    if row is None:
        raise ValueError("단어를 찾을 수 없습니다.")
    return row


def _parse_fields(body: dict | None, *, require_term: bool) -> dict:
    payload = body if isinstance(body, dict) else {}
    fields: dict = {}
    if "term" in payload or require_term:
        term = str(payload.get("term") or "").strip()
        if not term:
            raise ValueError("단어를 적어 주세요.")
        fields["term"] = term
    if "definition" in payload or require_term:
        fields["definition"] = str(payload.get("definition") or "")
    if "memo" in payload:
        memo = str(payload.get("memo") or "").strip()
        fields["memo"] = memo or None
    elif require_term:
        memo = str(payload.get("memo") or "").strip()
        fields["memo"] = memo or None
    return fields


def list_terms(connection: sqlite3.Connection, project_id: int) -> list[dict]:
    rows = connection.execute(
        "SELECT id, project_id, term, definition, memo, created_at, updated_at "
        "FROM custom_dictionary_terms WHERE project_id = ? "
        "ORDER BY datetime(updated_at) DESC, id DESC",
        (project_id,),
    ).fetchall()
    name_sets = conflict_name_sets(connection, project_id)
    return [
        serialize_term(row, conflicts_for_term(str(row["term"] or ""), name_sets))
        for row in rows
    ]


def create_term(connection: sqlite3.Connection, project_id: int, body: dict | None) -> dict:
    fields = _parse_fields(body, require_term=True)
    cursor = connection.execute(
        "INSERT INTO custom_dictionary_terms(project_id, term, definition, memo) "
        "VALUES (?, ?, ?, ?)",
        (project_id, fields["term"], fields["definition"], fields["memo"]),
    )
    row = _load_row(connection, int(cursor.lastrowid))
    return serialize_term(
        row,
        conflicts_for_term(fields["term"], conflict_name_sets(connection, project_id)),
    )


def update_term(connection: sqlite3.Connection, term_id: int, body: dict | None) -> dict:
    row = _load_row(connection, term_id)
    fields = _parse_fields(body, require_term=False)
    if not fields:
        name_sets = conflict_name_sets(connection, int(row["project_id"]))
        return serialize_term(row, conflicts_for_term(str(row["term"] or ""), name_sets))
    term = fields.get("term", str(row["term"] or ""))
    definition = fields.get("definition", str(row["definition"] or ""))
    memo = fields["memo"] if "memo" in fields else (str(row["memo"] or "").strip() or None)
    connection.execute(
        "UPDATE custom_dictionary_terms SET term = ?, definition = ?, memo = ?, "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
        (term, definition, memo, term_id),
    )
    updated = _load_row(connection, term_id)
    return serialize_term(
        updated,
        conflicts_for_term(term, conflict_name_sets(connection, int(updated["project_id"]))),
    )


def delete_term(connection: sqlite3.Connection, term_id: int) -> dict:
    row = _load_row(connection, term_id)
    connection.execute("DELETE FROM custom_dictionary_terms WHERE id = ?", (term_id,))
    return {"ok": True, "id": int(row["id"])}


def _definition_needles(term: object, aliases: object = None) -> list[str]:
    pieces: list[str] = []
    seen: set[str] = set()

    def push(value: object) -> None:
        raw = str(value or "").strip()
        if not raw:
            return
        parts = [raw, *re.split(r"\s*[\/|,、·]\s*", raw)]
        for part in parts:
            folded = _fold(part)
            if not folded or folded in seen:
                continue
            seen.add(folded)
            pieces.append(folded)

    push(term)
    if isinstance(aliases, (list, tuple)):
        for item in aliases:
            if isinstance(item, dict):
                push(item.get("alias") or item.get("name"))
            else:
                push(item)
    else:
        push(aliases)
    return pieces


def definition_map(connection: sqlite3.Connection, project_id: int) -> dict[str, str]:
    """Folded term/alias → definition for translation review cards."""
    out: dict[str, str] = {}
    if not _table_exists(connection, "custom_dictionary_terms"):
        return out
    try:
        cols = {
            str(col[1])
            for col in connection.execute("PRAGMA table_info(custom_dictionary_terms)").fetchall()
        }
    except sqlite3.Error:
        return out
    if "term" not in cols:
        return out
    select = "term, definition"
    if "aliases" in cols:
        select += ", aliases"
    try:
        rows = connection.execute(
            f"SELECT {select} FROM custom_dictionary_terms WHERE project_id = ?",
            (int(project_id),),
        ).fetchall()
    except sqlite3.Error:
        return out
    for row in rows:
        data = dict(row)
        meaning = str(data.get("definition") or "").strip()
        for key in _definition_needles(data.get("term"), data.get("aliases")):
            if key not in out:
                out[key] = meaning
    return out
