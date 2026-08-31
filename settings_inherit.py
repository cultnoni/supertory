"""One-shot copy of world / characters / items (and optional chronicle + relations)."""

from __future__ import annotations

import sqlite3

import character_import_analysis
import character_relations
from world_import_analysis import compose_worldbuilding_md, parse_worldbuilding_md


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on"}


def _mark(value: object) -> str:
    return character_import_analysis.mark_tori_text(value)


def _copy_worldbuilding(connection: sqlite3.Connection, source_id: int, dest_id: int) -> bool:
    row = connection.execute(
        "SELECT worldbuilding_md FROM project WHERE id = ? AND deleted_at IS NULL",
        (source_id,),
    ).fetchone()
    if row is None:
        return False
    raw = str(row["worldbuilding_md"] or "")
    values = parse_worldbuilding_md(raw)
    filled = False
    for key, text in list(values.items()):
        if str(text or "").strip():
            values[key] = _mark(text)
            filled = True
    md = compose_worldbuilding_md(values) if filled else ""
    connection.execute(
        "UPDATE project SET worldbuilding_md = ? WHERE id = ?",
        (md, dest_id),
    )
    return filled


def _copy_characters(connection: sqlite3.Connection, source_id: int, dest_id: int) -> dict[int, int]:
    mapping: dict[int, int] = {}
    if not _table_exists(connection, "character"):
        return mapping
    cols = _columns(connection, "character")
    rows = connection.execute(
        "SELECT * FROM character WHERE project_id = ? AND deleted_at IS NULL ORDER BY sort_order, id",
        (source_id,),
    ).fetchall()
    alias_rows = []
    if _table_exists(connection, "character_alias"):
        alias_rows = connection.execute(
            "SELECT character_id, alias, alias_type FROM character_alias WHERE project_id = ? ORDER BY id",
            (source_id,),
        ).fetchall()
    aliases: dict[int, list[tuple[str, str]]] = {}
    for row in alias_rows:
        alias = str(row["alias"] or "").strip()
        if alias:
            aliases.setdefault(int(row["character_id"]), []).append(
                (alias, str(row["alias_type"] or "other"))
            )
    for index, row in enumerate(rows):
        old_id = int(row["id"])
        fields = {
            "project_id": dest_id,
            "name": str(row["name"] or "").strip() or f"인물#{old_id}",
            "sort_order": index,
        }
        if "sort_name" in cols:
            fields["sort_name"] = str(row["sort_name"] or "")
        if "role" in cols:
            role = row["role"]
            fields["role"] = role if str(role or "").strip() else None
        if "short_description" in cols:
            fields["short_description"] = _mark(row["short_description"])
        if "profile_md" in cols:
            fields["profile_md"] = _mark(row["profile_md"])
        if "strengths_md" in cols:
            fields["strengths_md"] = _mark(row["strengths_md"])
        if "weaknesses_md" in cols:
            fields["weaknesses_md"] = _mark(row["weaknesses_md"])
        if "author_notes_md" in cols:
            fields["author_notes_md"] = ""
        names = [key for key in fields if key in cols or key in {"project_id", "name", "sort_order"}]
        placeholders = ", ".join("?" for _ in names)
        cursor = connection.execute(
            f"INSERT INTO character({', '.join(names)}) VALUES ({placeholders})",
            tuple(fields[key] for key in names),
        )
        new_id = int(cursor.lastrowid)
        mapping[old_id] = new_id
        for alias, alias_type in aliases.get(old_id, []):
            marked = _mark(alias)
            if not marked:
                continue
            try:
                connection.execute(
                    "INSERT INTO character_alias(character_id, project_id, alias, alias_type) "
                    "VALUES (?, ?, ?, ?)",
                    (new_id, dest_id, marked, alias_type or "other"),
                )
            except sqlite3.IntegrityError:
                continue
    return mapping


def _copy_items(
    connection: sqlite3.Connection,
    source_id: int,
    dest_id: int,
    character_map: dict[int, int],
) -> dict[int, int]:
    mapping: dict[int, int] = {}
    if not _table_exists(connection, "item"):
        return mapping
    rows = connection.execute(
        "SELECT * FROM item WHERE project_id = ? AND deleted_at IS NULL ORDER BY sort_order, id",
        (source_id,),
    ).fetchall()
    alias_rows = []
    if _table_exists(connection, "item_alias"):
        alias_rows = connection.execute(
            "SELECT item_id, alias, alias_type FROM item_alias WHERE project_id = ? ORDER BY id",
            (source_id,),
        ).fetchall()
    aliases: dict[int, list[tuple[str, str]]] = {}
    for row in alias_rows:
        alias = str(row["alias"] or "").strip()
        if alias:
            aliases.setdefault(int(row["item_id"]), []).append(
                (alias, str(row["alias_type"] or "other"))
            )
    for index, row in enumerate(rows):
        old_id = int(row["id"])
        owner = row["owner_character_id"] if "owner_character_id" in row.keys() else None
        new_owner = character_map.get(int(owner)) if owner not in (None, "") else None
        cursor = connection.execute(
            "INSERT INTO item(project_id, name, description, owner_character_id, sort_order) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                dest_id,
                str(row["name"] or "").strip() or f"아이템#{old_id}",
                _mark(row["description"]),
                new_owner,
                index,
            ),
        )
        new_id = int(cursor.lastrowid)
        mapping[old_id] = new_id
        for alias, alias_type in aliases.get(old_id, []):
            marked = _mark(alias)
            if not marked:
                continue
            try:
                connection.execute(
                    "INSERT INTO item_alias(item_id, project_id, alias, alias_type) VALUES (?, ?, ?, ?)",
                    (new_id, dest_id, marked, alias_type or "other"),
                )
            except sqlite3.IntegrityError:
                continue
    return mapping


def _copy_trait_history(
    connection: sqlite3.Connection,
    table: str,
    id_column: str,
    source_id: int,
    dest_id: int,
    id_map: dict[int, int],
) -> int:
    if not _table_exists(connection, table) or not id_map:
        return 0
    cols = _columns(connection, table)
    has_applied = "applied" in cols
    select = f"SELECT {id_column}, field_name, detected_content"
    if has_applied:
        select += ", applied"
    select += f" FROM {table} WHERE project_id = ? ORDER BY id"
    rows = connection.execute(select, (source_id,)).fetchall()
    copied = 0
    for row in rows:
        new_id = id_map.get(int(row[id_column]))
        if not new_id:
            continue
        field_name = str(row["field_name"] or "").strip()
        if not field_name:
            continue
        if has_applied:
            connection.execute(
                f"INSERT INTO {table}({id_column}, project_id, scene_id, field_name, detected_content, applied) "
                "VALUES (?, ?, NULL, ?, ?, ?)",
                (
                    new_id,
                    dest_id,
                    field_name,
                    str(row["detected_content"] or ""),
                    int(row["applied"] or 0),
                ),
            )
        else:
            connection.execute(
                f"INSERT INTO {table}({id_column}, project_id, scene_id, field_name, detected_content) "
                "VALUES (?, ?, NULL, ?, ?)",
                (new_id, dest_id, field_name, str(row["detected_content"] or "")),
            )
        copied += 1
    return copied


def _copy_relations(
    connection: sqlite3.Connection,
    source_id: int,
    dest_id: int,
    character_map: dict[int, int],
) -> int:
    if not _table_exists(connection, "character_relations") or not character_map:
        return 0
    rows = connection.execute(
        "SELECT character_a_id, character_b_id, label, source "
        "FROM character_relations WHERE project_id = ? ORDER BY id",
        (source_id,),
    ).fetchall()
    copied = 0
    seen: set[tuple[int, int, str]] = set()
    for row in rows:
        left = character_map.get(int(row["character_a_id"]))
        right = character_map.get(int(row["character_b_id"]))
        if not left or not right:
            continue
        try:
            pair = character_relations.ordered_pair(left, right)
        except ValueError:
            continue
        label = character_relations.clean_label(row["label"])
        if not label:
            continue
        key = (pair[0], pair[1], label)
        if key in seen:
            continue
        seen.add(key)
        source = str(row["source"] or "manual")
        if source not in {"ai", "manual"}:
            source = "manual"
        try:
            connection.execute(
                "INSERT INTO character_relations"
                "(project_id, character_a_id, character_b_id, label, status, source) "
                "VALUES (?, ?, ?, ?, 'confirmed', ?)",
                (dest_id, pair[0], pair[1], label, source),
            )
            copied += 1
        except sqlite3.IntegrityError:
            continue
    return copied


def inherit_project_settings(
    connection: sqlite3.Connection,
    source_id: int,
    dest_id: int,
    *,
    inherit_chronicle: bool = False,
) -> dict:
    """Copy settings from source into dest. Never writes to the source project."""
    source_id = int(source_id)
    dest_id = int(dest_id)
    if source_id == dest_id:
        raise ValueError("같은 작품으로는 설정을 이어갈 수 없어요.")
    source = connection.execute(
        "SELECT id, title FROM project WHERE id = ? AND deleted_at IS NULL",
        (source_id,),
    ).fetchone()
    dest = connection.execute(
        "SELECT id FROM project WHERE id = ? AND deleted_at IS NULL",
        (dest_id,),
    ).fetchone()
    if source is None:
        raise ValueError("이어받을 작품을 찾을 수 없습니다.")
    if dest is None:
        raise ValueError("새 작품을 찾을 수 없습니다.")
    title = str(source["title"] or "").strip()
    _copy_worldbuilding(connection, source_id, dest_id)
    character_map = _copy_characters(connection, source_id, dest_id)
    item_map = _copy_items(connection, source_id, dest_id, character_map)
    history_count = 0
    relation_count = 0
    if inherit_chronicle:
        history_count += _copy_trait_history(
            connection, "character_trait_history", "character_id", source_id, dest_id, character_map
        )
        history_count += _copy_trait_history(
            connection, "item_trait_history", "item_id", source_id, dest_id, item_map
        )
        relation_count = _copy_relations(connection, source_id, dest_id, character_map)
    return {
        "inherited_from_id": source_id,
        "inherited_from_title": title,
        "inherited_chronicle": bool(inherit_chronicle),
        "inherited_characters": len(character_map),
        "inherited_items": len(item_map),
        "inherited_history": history_count,
        "inherited_relations": relation_count,
    }
