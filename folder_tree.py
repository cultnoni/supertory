"""Parallel folder-tree helpers (migration 028+).

Read path: build legacy-shaped outline payloads from `folder` + mapped ids.
Sync path: (re)build folder rows / scene.folder_id from part/chapter for one project.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

# Advance stored updated_at by 1ms so ``scene_touch`` does not treat a
# same-millisecond follow-up UPDATE as "updated_at unchanged" and bump row_version.
SCENE_TOUCH_SAFE_UPDATED_AT = (
    "strftime('%Y-%m-%dT%H:%M:%fZ', "
    "REPLACE(REPLACE(COALESCE(updated_at, 'now'), 'T', ' '), 'Z', ''), "
    "'+0.001 seconds')"
)


def folder_table_ready(connection: sqlite3.Connection) -> bool:
    try:
        connection.execute("SELECT 1 FROM folder LIMIT 1")
        connection.execute("SELECT folder_id FROM scene LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False


def project_folder_mapping_complete(
    connection: sqlite3.Connection, project_id: int
) -> bool:
    """True when every active part/chapter/scene has a folder map row (structure ignored)."""
    if not folder_table_ready(connection):
        return False
    parts = connection.execute(
        "SELECT id FROM part WHERE project_id = ? AND deleted_at IS NULL",
        (project_id,),
    ).fetchall()
    for row in parts:
        hit = connection.execute(
            "SELECT 1 FROM folder "
            "WHERE project_id = ? AND source_kind = 'part' AND source_id = ? "
            "AND deleted_at IS NULL",
            (project_id, int(row["id"])),
        ).fetchone()
        if hit is None:
            return False
    chapters = connection.execute(
        "SELECT id FROM chapter WHERE project_id = ? AND deleted_at IS NULL",
        (project_id,),
    ).fetchall()
    for row in chapters:
        hit = connection.execute(
            "SELECT 1 FROM folder "
            "WHERE project_id = ? AND source_kind = 'chapter' AND source_id = ? "
            "AND deleted_at IS NULL",
            (project_id, int(row["id"])),
        ).fetchone()
        if hit is None:
            return False
    missing = connection.execute(
        """
        SELECT COUNT(*) AS c FROM scene
        WHERE project_id = ? AND deleted_at IS NULL
          AND (
            folder_id IS NULL
            OR NOT EXISTS (
              SELECT 1 FROM folder f
              WHERE f.id = scene.folder_id AND f.deleted_at IS NULL
            )
          )
        """,
        (project_id,),
    ).fetchone()["c"]
    return int(missing) == 0


def project_folder_tree_is_legacy_compatible(
    connection: sqlite3.Connection, project_id: int
) -> bool:
    """True when folder parents still match legacy part/chapter rules.

    Allowed:
      - part folders at root
      - chapter folders under a part (or root / ungrouped)
      - chapter under chapter only when the child chapter has parent_scene_id
        (under-manuscript folders)

    After arbitrary reparent (part under part, free folder nesting) this becomes
    False — legacy outline must not be used as fallback.
    """
    if not folder_table_ready(connection):
        return True
    pid = int(project_id)
    # Part-mapped folders must stay at the root.
    if connection.execute(
        """
        SELECT 1 FROM folder
        WHERE project_id = ? AND deleted_at IS NULL
          AND source_kind = 'part' AND parent_id IS NOT NULL
        LIMIT 1
        """,
        (pid,),
    ).fetchone():
        return False
    # Unmapped folders (native tree) under anything break pure legacy shape.
    if connection.execute(
        """
        SELECT 1 FROM folder
        WHERE project_id = ? AND deleted_at IS NULL
          AND source_kind IS NULL
        LIMIT 1
        """,
        (pid,),
    ).fetchone():
        return False
    # Chapter-mapped folders: parent must be part, or host chapter (under-scene).
    ch_rows = connection.execute(
        """
        SELECT f.id AS folder_id, f.parent_id AS parent_id, f.source_id AS source_id
        FROM folder f
        WHERE f.project_id = ? AND f.deleted_at IS NULL
          AND f.source_kind = 'chapter' AND f.parent_id IS NOT NULL
        """,
        (pid,),
    ).fetchall()
    for row in ch_rows:
        parent_id = int(row["parent_id"] if hasattr(row, "keys") else row[1])
        source_id = int(row["source_id"] if hasattr(row, "keys") else row[2])
        parent = connection.execute(
            """
            SELECT source_kind, source_id FROM folder
            WHERE id = ? AND project_id = ? AND deleted_at IS NULL
            """,
            (parent_id, pid),
        ).fetchone()
        if parent is None:
            return False
        kind = parent["source_kind"] if hasattr(parent, "keys") else parent[0]
        if kind == "part":
            continue
        if kind == "chapter":
            # Only valid legacy case: chapter nested under a manuscript
            # (chapter.parent_scene_id set).
            ch = connection.execute(
                """
                SELECT parent_scene_id FROM chapter
                WHERE id = ? AND project_id = ? AND deleted_at IS NULL
                """,
                (source_id, pid),
            ).fetchone()
            if ch is None:
                return False
            ps = ch["parent_scene_id"] if hasattr(ch, "keys") else ch[0]
            if ps is None:
                return False
            continue
        return False
    return True


def max_folder_depth(connection: sqlite3.Connection, project_id: int) -> int:
    """Max depth of active folder tree (root = 1). Empty project → 0."""
    if not folder_table_ready(connection):
        return 0
    pid = int(project_id)
    rows = connection.execute(
        """
        SELECT id, parent_id FROM folder
        WHERE project_id = ? AND deleted_at IS NULL
        """,
        (pid,),
    ).fetchall()
    if not rows:
        return 0
    ids: set[int] = set()
    parent_of: dict[int, int | None] = {}
    children: dict[int | None, list[int]] = defaultdict(list)
    for row in rows:
        fid = int(row["id"] if hasattr(row, "keys") else row[0])
        raw_p = row["parent_id"] if hasattr(row, "keys") else row[1]
        parent_id = int(raw_p) if raw_p is not None else None
        ids.add(fid)
        parent_of[fid] = parent_id
    for fid, parent_id in parent_of.items():
        if parent_id is not None and parent_id not in ids:
            children[None].append(fid)
        else:
            children[parent_id].append(fid)
    max_d = 0
    stack = [(fid, 1) for fid in children.get(None, [])]
    while stack:
        fid, depth = stack.pop()
        if depth > max_d:
            max_d = depth
        for child in children.get(fid, []):
            stack.append((child, depth + 1))
    return max_d


def project_folder_sync_complete(connection: sqlite3.Connection, project_id: int) -> bool:
    """True when mapping is complete AND structure still matches part→chapter legacy."""
    if not project_folder_mapping_complete(connection, project_id):
        return False
    return project_folder_tree_is_legacy_compatible(connection, project_id)


def folder_sibling_sort_key(row: dict) -> tuple:
    """Display order among siblings: pinned first, then sort_order, id.

    Does not mutate sort_order — pin only affects query/assembly order.
    """
    pinned = 1 if row.get("is_pinned") else 0
    return (
        -pinned,  # is_pinned DESC
        int(row.get("sort_order") or 0),
        int(row.get("id") or 0),
    )


def load_project_folder_rows(
    connection: sqlite3.Connection, project_id: int
) -> list[dict]:
    """One query: all active folders for a project (sibling display order)."""
    if not folder_table_ready(connection):
        return []
    pid = int(project_id)
    # Prefer 034+ columns (adds color_bright); fall back for pre-migration DBs mid-upgrade.
    try:
        rows = connection.execute(
            """
            SELECT id, parent_id, title, synopsis_md, notes_md, is_box, sort_order,
                   source_kind, source_id, color, color_bright, is_pinned, is_bookmarked
            FROM folder
            WHERE project_id = ? AND deleted_at IS NULL
            ORDER BY is_pinned DESC, sort_order ASC, id ASC
            """,
            (pid,),
        ).fetchall()
        has_bright = True
        has_color_pin = True
        has_bookmark = True
    except sqlite3.OperationalError:
        has_bright = False
        try:
            rows = connection.execute(
                """
                SELECT id, parent_id, title, synopsis_md, notes_md, is_box, sort_order,
                       source_kind, source_id, color, is_pinned, is_bookmarked
                FROM folder
                WHERE project_id = ? AND deleted_at IS NULL
                ORDER BY is_pinned DESC, sort_order ASC, id ASC
                """,
                (pid,),
            ).fetchall()
            has_color_pin = True
            has_bookmark = True
        except sqlite3.OperationalError:
            try:
                rows = connection.execute(
                    """
                    SELECT id, parent_id, title, synopsis_md, notes_md, is_box, sort_order,
                           source_kind, source_id, color, is_pinned
                    FROM folder
                    WHERE project_id = ? AND deleted_at IS NULL
                    ORDER BY is_pinned DESC, sort_order ASC, id ASC
                    """,
                    (pid,),
                ).fetchall()
                has_color_pin = True
                has_bookmark = False
            except sqlite3.OperationalError:
                rows = connection.execute(
                    """
                    SELECT id, parent_id, title, synopsis_md, notes_md, is_box, sort_order,
                           source_kind, source_id
                    FROM folder
                    WHERE project_id = ? AND deleted_at IS NULL
                    ORDER BY sort_order ASC, id ASC
                    """,
                    (pid,),
                ).fetchall()
                has_color_pin = False
                has_bookmark = False
    out: list[dict] = []
    for row in rows:
        if hasattr(row, "keys"):
            raw_parent = row["parent_id"]
            raw_source_id = row["source_id"]
            item = {
                "id": int(row["id"]),
                "parent_id": int(raw_parent) if raw_parent is not None else None,
                "title": row["title"],
                "synopsis_md": row["synopsis_md"] or "",
                "notes_md": row["notes_md"] or "",
                "is_box": bool(int(row["is_box"] or 0)),
                "sort_order": int(row["sort_order"] or 0),
                "source_kind": row["source_kind"],
                "source_id": (
                    int(raw_source_id) if raw_source_id is not None else None
                ),
                "color": None,
                "color_bright": None,
                "is_pinned": 0,
                "is_bookmarked": 0,
            }
            if has_color_pin:
                c = row["color"]
                item["color"] = (str(c).strip().lower() if c else None) or None
                item["is_pinned"] = 1 if int(row["is_pinned"] or 0) else 0
            if has_bright:
                cb = row["color_bright"]
                item["color_bright"] = (str(cb).strip().lower() if cb else None) or None
            if has_bookmark:
                try:
                    item["is_bookmarked"] = 1 if int(row["is_bookmarked"] or 0) else 0
                except (KeyError, IndexError, TypeError):
                    item["is_bookmarked"] = 0
            out.append(item)
        else:
            raw_parent = row[1]
            raw_source_id = row[8]
            item = {
                "id": int(row[0]),
                "parent_id": int(raw_parent) if raw_parent is not None else None,
                "title": row[2],
                "synopsis_md": row[3] or "",
                "notes_md": row[4] or "",
                "is_box": bool(int(row[5] or 0)),
                "sort_order": int(row[6] or 0),
                "source_kind": row[7],
                "source_id": (
                    int(raw_source_id) if raw_source_id is not None else None
                ),
                "color": None,
                "color_bright": None,
                "is_pinned": 0,
                "is_bookmarked": 0,
            }
            if has_bright and len(row) > 12:
                c = row[9]
                item["color"] = (str(c).strip().lower() if c else None) or None
                cb = row[10]
                item["color_bright"] = (str(cb).strip().lower() if cb else None) or None
                item["is_pinned"] = 1 if int(row[11] or 0) else 0
                item["is_bookmarked"] = 1 if int(row[12] or 0) else 0
            elif has_color_pin and len(row) > 10:
                c = row[9]
                item["color"] = (str(c).strip().lower() if c else None) or None
                item["is_pinned"] = 1 if int(row[10] or 0) else 0
                if has_bookmark and len(row) > 11:
                    item["is_bookmarked"] = 1 if int(row[11] or 0) else 0
            out.append(item)
    # Defensive re-sort in memory (same parent_id groups handled in assemble)
    out.sort(
        key=lambda r: (
            r.get("parent_id") is not None,
            int(r["parent_id"]) if r.get("parent_id") is not None else -1,
            *folder_sibling_sort_key(r),
        )
    )
    return out


def assemble_folder_nodes_by_parent(
    folder_rows: list[dict],
) -> tuple[dict[int, dict], dict[int | None, list[int]]]:
    """Index folder nodes and sibling id lists by parent_id (None = roots)."""
    nodes: dict[int, dict] = {}
    by_parent: dict[int | None, list[int]] = defaultdict(list)
    for row in folder_rows:
        fid = int(row["id"])
        color = row.get("color")
        if color is not None:
            color = str(color).strip().lower() or None
        color_bright = row.get("color_bright")
        if color_bright is not None:
            color_bright = str(color_bright).strip().lower() or None
        nodes[fid] = {
            "id": fid,
            "title": row.get("title") or "",
            "is_box": bool(row.get("is_box")),
            "synopsis_md": row.get("synopsis_md") or "",
            "notes_md": row.get("notes_md") or "",
            "sort_order": int(row.get("sort_order") or 0),
            "source_kind": row.get("source_kind"),
            "source_id": row.get("source_id"),
            "color": color,
            "color_bright": color_bright,
            "is_pinned": 1 if row.get("is_pinned") else 0,
            "is_bookmarked": 1 if row.get("is_bookmarked") else 0,
            "children": [],
            "scenes": [],
        }
        parent_id = row.get("parent_id")
        parent_key = int(parent_id) if parent_id is not None else None
        by_parent[parent_key].append(fid)
    # Pinned first among siblings, then sort_order, id (display only)
    for parent_key, ids in by_parent.items():
        ids.sort(key=lambda i: folder_sibling_sort_key(nodes[i]))
    return nodes, by_parent


def build_folder_forest(
    folder_rows: list[dict],
    scenes_by_folder: dict[int, list[dict]] | None = None,
) -> list[dict]:
    """
    Build root list of recursive folder nodes from bulk-loaded rows.

    scenes_by_folder: optional map folder_id → nested scene trees (already built).
    Does not query the database (no N+1).
    """
    if not folder_rows:
        return []
    nodes, by_parent = assemble_folder_nodes_by_parent(folder_rows)
    scenes_by_folder = scenes_by_folder or {}
    for fid, node in nodes.items():
        node["scenes"] = list(scenes_by_folder.get(fid) or [])
        node["children"] = []

    for parent_key, ids in by_parent.items():
        if parent_key is None or parent_key not in nodes:
            continue
        parent = nodes[parent_key]
        for fid in ids:
            parent["children"].append(nodes[fid])

    roots = [nodes[fid] for fid in by_parent.get(None, [])]
    # Orphan folders (parent missing / deleted) → promote to root
    for parent_key, ids in by_parent.items():
        if parent_key is not None and parent_key not in nodes:
            for fid in ids:
                roots.append(nodes[fid])
    roots.sort(key=folder_sibling_sort_key)
    return roots


def walk_folder_forest_preorder(roots: list[dict]):
    """Yield folder nodes depth-first.

    Sibling order is the binder display order already applied by
    ``build_folder_forest`` / ``folder_sibling_sort_key``
    (pinned, then ``sort_order``, then id).
    """
    for node in roots or []:
        if not node:
            continue
        yield node
        yield from walk_folder_forest_preorder(node.get("children") or [])


def folder_id_for_source(
    connection: sqlite3.Connection,
    project_id: int,
    source_kind: str,
    source_id: int,
) -> int | None:
    row = connection.execute(
        """
        SELECT id FROM folder
        WHERE project_id = ?
          AND source_kind = ?
          AND source_id = ?
          AND deleted_at IS NULL
        """,
        (int(project_id), source_kind, int(source_id)),
    ).fetchone()
    return int(row["id"] if hasattr(row, "keys") else row[0]) if row else None


def next_folder_sibling_sort(
    connection: sqlite3.Connection,
    project_id: int,
    parent_id: int | None,
) -> int:
    if parent_id is None:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(sort_order) + 1, 0) AS n FROM folder
            WHERE project_id = ? AND parent_id IS NULL AND deleted_at IS NULL
            """,
            (int(project_id),),
        ).fetchone()
    else:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(sort_order) + 1, 0) AS n FROM folder
            WHERE project_id = ? AND parent_id = ? AND deleted_at IS NULL
            """,
            (int(project_id), int(parent_id)),
        ).fetchone()
    return int(row["n"] if hasattr(row, "keys") else row[0])


def bind_folder_source(
    connection: sqlite3.Connection,
    folder_id: int,
    source_kind: str,
    source_id: int,
) -> None:
    connection.execute(
        """
        UPDATE folder
        SET source_kind = ?,
            source_id = ?,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE id = ?
        """,
        (source_kind, int(source_id), int(folder_id)),
    )


def reapply_chapter_folder_order(
    connection: sqlite3.Connection,
    project_id: int,
    chapter_ids: list[int],
    parent_folder_id: int | None,
) -> None:
    """Align folder.sort_order (and parent) with a chapter id list (two-phase)."""
    for index, chapter_id in enumerate(chapter_ids):
        connection.execute(
            """
            UPDATE folder
            SET parent_id = ?,
                sort_order = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE project_id = ?
              AND source_kind = 'chapter'
              AND source_id = ?
              AND deleted_at IS NULL
            """,
            (parent_folder_id, 1_000_000 + index, int(project_id), int(chapter_id)),
        )
    for index, chapter_id in enumerate(chapter_ids):
        connection.execute(
            """
            UPDATE folder
            SET sort_order = ?
            WHERE project_id = ?
              AND source_kind = 'chapter'
              AND source_id = ?
              AND deleted_at IS NULL
            """,
            (index, int(project_id), int(chapter_id)),
        )


def _folder_row_id(row) -> int:
    return int(row["id"] if hasattr(row, "keys") else row[0])


def collect_folder_descendant_ids(
    connection: sqlite3.Connection,
    root_folder_id: int,
) -> list[int]:
    """Root + all nested child folders (active only walk)."""
    found: list[int] = [int(root_folder_id)]
    queue = [int(root_folder_id)]
    while queue:
        parent = queue.pop()
        rows = connection.execute(
            """
            SELECT id FROM folder
            WHERE parent_id = ? AND deleted_at IS NULL
            """,
            (parent,),
        ).fetchall()
        for row in rows:
            fid = _folder_row_id(row)
            if fid not in found:
                found.append(fid)
                queue.append(fid)
    return found


def soft_delete_folder_ids(
    connection: sqlite3.Connection,
    folder_ids: list[int],
) -> int:
    """Soft-delete the given folder ids. Returns number of rows updated."""
    if not folder_ids:
        return 0
    placeholders = ",".join("?" * len(folder_ids))
    cur = connection.execute(
        f"""
        UPDATE folder
        SET deleted_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
            row_version = row_version + 1
        WHERE id IN ({placeholders}) AND deleted_at IS NULL
        """,
        [int(x) for x in folder_ids],
    )
    return int(cur.rowcount or 0)


def soft_delete_folder_for_source(
    connection: sqlite3.Connection,
    project_id: int,
    source_kind: str,
    source_id: int,
    *,
    cascade_children: bool = True,
) -> list[int]:
    """
    Soft-delete the folder mapped to a legacy part/chapter (and optionally descendants).
    Returns list of folder ids soft-deleted.
    """
    root = folder_id_for_source(connection, project_id, source_kind, source_id)
    if root is None:
        return []
    ids = (
        collect_folder_descendant_ids(connection, root)
        if cascade_children
        else [root]
    )
    soft_delete_folder_ids(connection, ids)
    return ids


def reapply_part_folder_order(
    connection: sqlite3.Connection,
    project_id: int,
    part_ids: list[int],
) -> None:
    """Set root part-folder sort_order to match part_ids list (two-phase for unique index)."""
    for index, part_id in enumerate(part_ids):
        connection.execute(
            """
            UPDATE folder
            SET sort_order = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE project_id = ?
              AND source_kind = 'part'
              AND source_id = ?
              AND deleted_at IS NULL
            """,
            (1_000_000 + index, int(project_id), int(part_id)),
        )
    for index, part_id in enumerate(part_ids):
        connection.execute(
            """
            UPDATE folder
            SET sort_order = ?
            WHERE project_id = ?
              AND source_kind = 'part'
              AND source_id = ?
              AND deleted_at IS NULL
            """,
            (index, int(project_id), int(part_id)),
        )


def move_chapter_folder_to_part(
    connection: sqlite3.Connection,
    project_id: int,
    chapter_id: int,
    target_part_id: int | None,
    new_sort_order: int,
) -> bool:
    """
    Update chapter folder parent + sort (folder-first move).
    Returns False if chapter folder map is missing.
    """
    ch_folder = folder_id_for_source(connection, project_id, "chapter", chapter_id)
    if ch_folder is None:
        return False
    parent_folder_id = None
    if target_part_id is not None:
        parent_folder_id = folder_id_for_source(
            connection, project_id, "part", int(target_part_id)
        )
        if parent_folder_id is None:
            return False
    # Park then set to avoid unique sibling collisions
    connection.execute(
        """
        UPDATE folder
        SET sort_order = ?,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
            row_version = row_version + 1
        WHERE id = ?
        """,
        (9_000_000 + int(chapter_id), ch_folder),
    )
    connection.execute(
        """
        UPDATE folder
        SET parent_id = ?,
            sort_order = ?,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
            row_version = row_version + 1
        WHERE id = ?
        """,
        (parent_folder_id, int(new_sort_order), ch_folder),
    )
    return True


def recompact_chapter_folders_under_part(
    connection: sqlite3.Connection,
    project_id: int,
    part_id: int | None,
) -> None:
    """Re-number folder sort_order for chapters currently in a legacy part group."""
    if part_id is None:
        rows = connection.execute(
            """
            SELECT id FROM chapter
            WHERE project_id = ? AND part_id IS NULL AND parent_scene_id IS NULL
              AND deleted_at IS NULL
            ORDER BY sort_order, id
            """,
            (int(project_id),),
        ).fetchall()
        parent_folder_id = None
    else:
        rows = connection.execute(
            """
            SELECT id FROM chapter
            WHERE project_id = ? AND part_id = ? AND parent_scene_id IS NULL
              AND deleted_at IS NULL
            ORDER BY sort_order, id
            """,
            (int(project_id), int(part_id)),
        ).fetchall()
        parent_folder_id = folder_id_for_source(
            connection, project_id, "part", int(part_id)
        )
    chapter_ids = [int(r["id"] if hasattr(r, "keys") else r[0]) for r in rows]
    if not chapter_ids:
        return
    reapply_chapter_folder_order(
        connection, project_id, chapter_ids, parent_folder_id
    )


def _insert_folder(
    connection: sqlite3.Connection,
    **fields,
) -> int:
    cur = connection.execute(
        """
        INSERT INTO folder(
            project_id, parent_id, title, synopsis_md, notes_md, goal_word_count,
            is_box, sort_order, created_at, updated_at, deleted_at, row_version,
            source_kind, source_id
        ) VALUES (
            ?, ?, ?, ?, ?, ?,
            ?, ?,
            COALESCE(?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            COALESCE(?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            ?, ?, ?, ?
        )
        """,
        (
            fields["project_id"],
            fields.get("parent_id"),
            fields["title"],
            fields.get("synopsis_md") or "",
            fields.get("notes_md") or "",
            max(0, int(fields.get("goal_word_count") or 0)),
            1 if fields.get("is_box") else 0,
            int(fields["sort_order"]),
            fields.get("created_at"),
            fields.get("updated_at"),
            fields.get("deleted_at"),
            max(1, int(fields.get("row_version") or 1)),
            fields.get("source_kind"),
            fields.get("source_id"),
        ),
    )
    return int(cur.lastrowid)


def _renumber_active_siblings(connection: sqlite3.Connection, project_id: int) -> None:
    groups = connection.execute(
        """
        SELECT parent_id FROM folder
        WHERE project_id = ? AND deleted_at IS NULL
        GROUP BY parent_id
        """,
        (project_id,),
    ).fetchall()
    for g in groups:
        parent_id = g["parent_id"]
        if parent_id is None:
            rows = connection.execute(
                """
                SELECT id FROM folder
                WHERE project_id = ? AND parent_id IS NULL AND deleted_at IS NULL
                ORDER BY sort_order, id
                """,
                (project_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT id FROM folder
                WHERE project_id = ? AND parent_id = ? AND deleted_at IS NULL
                ORDER BY sort_order, id
                """,
                (project_id, int(parent_id)),
            ).fetchall()
        for i, row in enumerate(rows):
            connection.execute(
                "UPDATE folder SET sort_order = ? WHERE id = ?",
                (1_000_000 + i, int(row["id"])),
            )
        for i, row in enumerate(rows):
            connection.execute(
                "UPDATE folder SET sort_order = ? WHERE id = ?",
                (i, int(row["id"])),
            )


def _flatten_nested_scenes(connection: sqlite3.Connection, chapter_id: int) -> None:
    """One-shot backfill: lift nested manuscripts to chapter roots.

    Sets ``updated_at`` so ``scene_touch`` does not bump ``row_version``.
    """
    scenes = connection.execute(
        """
        SELECT id, parent_scene_id, sort_order
        FROM scene WHERE chapter_id = ?
        ORDER BY sort_order, id
        """,
        (chapter_id,),
    ).fetchall()
    if not scenes or not any(s["parent_scene_id"] is not None for s in scenes):
        return
    by_parent: dict[int | None, list] = defaultdict(list)
    for s in scenes:
        by_parent[s["parent_scene_id"]].append(s)
    ordered: list = []

    def walk(parent: int | None) -> None:
        kids = sorted(
            by_parent.get(parent, []),
            key=lambda r: (int(r["sort_order"] or 0), int(r["id"])),
        )
        for kid in kids:
            ordered.append(kid)
            walk(int(kid["id"]))

    walk(None)
    seen = {int(r["id"]) for r in ordered}
    for s in scenes:
        if int(s["id"]) not in seen:
            ordered.append(s)
    stamp = SCENE_TOUCH_SAFE_UPDATED_AT
    for i, s in enumerate(ordered):
        connection.execute(
            f"UPDATE scene SET parent_scene_id = NULL, sort_order = ?, "
            f"updated_at = {stamp} WHERE id = ?",
            (1_000_000 + i, int(s["id"])),
        )
    for i, s in enumerate(ordered):
        connection.execute(
            f"UPDATE scene SET sort_order = ?, updated_at = {stamp} WHERE id = ?",
            (i, int(s["id"])),
        )


def sync_project_folder_tree(
    connection: sqlite3.Connection,
    project_id: int,
    *,
    flatten_nested_scenes: bool = False,
) -> dict:
    """Rebuild folder rows + scene.folder_id for one project from part/chapter.

    flatten_nested_scenes: only for one-shot backfill; dual-write keeps parent_scene_id.
    """
    if not folder_table_ready(connection):
        return {"ok": False, "reason": "folder schema missing"}

    # Clear this project's folders and scene links (break self-FK before delete).
    # Touch-safe updated_at — do not bump scene.row_version (in-flight editor save).
    connection.execute(
        f"""
        UPDATE scene SET folder_id = NULL,
            updated_at = {SCENE_TOUCH_SAFE_UPDATED_AT}
        WHERE project_id = ? AND folder_id IS NOT NULL
        """,
        (project_id,),
    )
    # Detach parents with unique temp sort_orders (ux_folder_sibling_order is active-only)
    connection.execute(
        """
        UPDATE folder
        SET parent_id = NULL,
            sort_order = id + 1000000
        WHERE project_id = ?
        """,
        (project_id,),
    )
    connection.execute(
        "DELETE FROM folder WHERE project_id = ?",
        (project_id,),
    )

    part_map: dict[int, int] = {}
    chapter_map: dict[int, int] = {}
    root_seq = 0

    parts = connection.execute(
        """
        SELECT id, title, synopsis_md, sort_order, created_at, updated_at,
               deleted_at, row_version
        FROM part WHERE project_id = ?
        ORDER BY sort_order, id
        """,
        (project_id,),
    ).fetchall()
    for p in parts:
        fid = _insert_folder(
            connection,
            project_id=project_id,
            parent_id=None,
            title=str(p["title"] or "폴더"),
            synopsis_md=str(p["synopsis_md"] or ""),
            notes_md="",
            goal_word_count=0,
            is_box=1,
            sort_order=root_seq,
            created_at=p["created_at"],
            updated_at=p["updated_at"],
            deleted_at=p["deleted_at"],
            row_version=int(p["row_version"] or 1),
            source_kind="part",
            source_id=int(p["id"]),
        )
        part_map[int(p["id"])] = fid
        root_seq += 1

    chapters = connection.execute(
        """
        SELECT id, part_id, parent_scene_id, title, synopsis_md, notes_md,
               goal_word_count, sort_order, created_at, updated_at, deleted_at, row_version
        FROM chapter WHERE project_id = ?
        ORDER BY sort_order, id
        """,
        (project_id,),
    ).fetchall()

    child_seq: dict[int | None, int] = defaultdict(int)
    under_scene: list = []
    for ch in chapters:
        if ch["parent_scene_id"] is not None:
            under_scene.append(ch)
            continue
        part_id = ch["part_id"]
        parent_folder_id = part_map.get(int(part_id)) if part_id is not None else None
        if parent_folder_id is None:
            seq = root_seq
            root_seq += 1
        else:
            seq = child_seq[parent_folder_id]
            child_seq[parent_folder_id] = seq + 1
        fid = _insert_folder(
            connection,
            project_id=project_id,
            parent_id=parent_folder_id,
            title=str(ch["title"] or "폴더"),
            synopsis_md=str(ch["synopsis_md"] or ""),
            notes_md=str(ch["notes_md"] or ""),
            goal_word_count=int(ch["goal_word_count"] or 0),
            is_box=0,
            sort_order=seq,
            created_at=ch["created_at"],
            updated_at=ch["updated_at"],
            deleted_at=ch["deleted_at"],
            row_version=int(ch["row_version"] or 1),
            source_kind="chapter",
            source_id=int(ch["id"]),
        )
        chapter_map[int(ch["id"])] = fid

    for ch in under_scene:
        host = connection.execute(
            "SELECT chapter_id FROM scene WHERE id = ?",
            (int(ch["parent_scene_id"]),),
        ).fetchone()
        if host and host["chapter_id"] is not None:
            parent_folder_id = chapter_map.get(int(host["chapter_id"]))
        else:
            parent_folder_id = None
        if parent_folder_id is None:
            seq = root_seq
            root_seq += 1
        else:
            seq = child_seq[parent_folder_id]
            child_seq[parent_folder_id] = seq + 1
        fid = _insert_folder(
            connection,
            project_id=project_id,
            parent_id=parent_folder_id,
            title=str(ch["title"] or "폴더"),
            synopsis_md=str(ch["synopsis_md"] or ""),
            notes_md=str(ch["notes_md"] or ""),
            goal_word_count=int(ch["goal_word_count"] or 0),
            is_box=0,
            sort_order=seq,
            created_at=ch["created_at"],
            updated_at=ch["updated_at"],
            deleted_at=ch["deleted_at"],
            row_version=int(ch["row_version"] or 1),
            source_kind="chapter",
            source_id=int(ch["id"]),
        )
        chapter_map[int(ch["id"])] = fid

    _renumber_active_siblings(connection, project_id)

    for s in connection.execute(
        "SELECT id, chapter_id FROM scene WHERE project_id = ?",
        (project_id,),
    ).fetchall():
        fid = chapter_map.get(int(s["chapter_id"]))
        if fid is not None:
            connection.execute(
                f"UPDATE scene SET folder_id = ?, "
                f"updated_at = {SCENE_TOUCH_SAFE_UPDATED_AT} "
                "WHERE id = ?",
                (fid, int(s["id"])),
            )

    if flatten_nested_scenes:
        for row in connection.execute(
            """
            SELECT DISTINCT chapter_id FROM scene
            WHERE project_id = ? AND parent_scene_id IS NOT NULL
            """,
            (project_id,),
        ).fetchall():
            _flatten_nested_scenes(connection, int(row["chapter_id"]))

    return {
        "ok": True,
        "project_id": project_id,
        "parts": len(parts),
        "chapters": len(chapters),
        "folders": connection.execute(
            "SELECT COUNT(*) AS c FROM folder WHERE project_id = ?",
            (project_id,),
        ).fetchone()["c"],
    }


def recompact_folder_siblings(
    connection: sqlite3.Connection,
    project_id: int,
    parent_id: int | None,
) -> list[int]:
    """Re-number sort_order 0..n-1 under parent (two-phase for unique sibling index)."""
    pid = int(project_id)
    if parent_id is None:
        rows = connection.execute(
            """
            SELECT id FROM folder
            WHERE project_id = ? AND parent_id IS NULL AND deleted_at IS NULL
            ORDER BY sort_order, id
            """,
            (pid,),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT id FROM folder
            WHERE project_id = ? AND parent_id = ? AND deleted_at IS NULL
            ORDER BY sort_order, id
            """,
            (pid, int(parent_id)),
        ).fetchall()
    ids = [int(r["id"] if hasattr(r, "keys") else r[0]) for r in rows]
    if not ids:
        return []
    for index, fid in enumerate(ids):
        connection.execute(
            """
            UPDATE folder
            SET sort_order = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (1_000_000 + index, fid),
        )
    for index, fid in enumerate(ids):
        connection.execute(
            """
            UPDATE folder
            SET sort_order = ?
            WHERE id = ?
            """,
            (index, fid),
        )
    return ids


def _load_active_folder(
    connection: sqlite3.Connection,
    folder_id: int,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT id, project_id, parent_id, title, sort_order, is_box,
               source_kind, source_id
        FROM folder
        WHERE id = ? AND deleted_at IS NULL
        """,
        (int(folder_id),),
    ).fetchone()


def list_folder_sibling_ids(
    connection: sqlite3.Connection,
    project_id: int,
    parent_id: int | None,
) -> list[int]:
    """Active sibling folder ids under *parent_id* (None = project roots), ordered."""
    pid = int(project_id)
    if parent_id is None:
        rows = connection.execute(
            """
            SELECT id FROM folder
            WHERE project_id = ? AND parent_id IS NULL AND deleted_at IS NULL
            ORDER BY sort_order, id
            """,
            (pid,),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT id FROM folder
            WHERE project_id = ? AND parent_id = ? AND deleted_at IS NULL
            ORDER BY sort_order, id
            """,
            (pid, int(parent_id)),
        ).fetchall()
    return [int(r["id"] if hasattr(r, "keys") else r[0]) for r in rows]


def folder_sibling_index(
    connection: sqlite3.Connection,
    project_id: int,
    folder_id: int,
    parent_id: int | None,
) -> int | None:
    """0-based index of *folder_id* among siblings, or None if not found."""
    siblings = list_folder_sibling_ids(connection, project_id, parent_id)
    try:
        return siblings.index(int(folder_id))
    except ValueError:
        return None


def reparent_folder(
    connection: sqlite3.Connection,
    folder_id: int,
    *,
    new_parent_id: int | None = None,
    position: str = "inside",
    target_id: int | None = None,
    new_parent_id_provided: bool = False,
    index: int | None = None,
) -> dict:
    """
    Move a folder under a new parent and/or among siblings.

    position:
      - inside: become last child of parent (new_parent_id, else target_id)
      - before / after: insert as sibling of target_id (parent = target's parent)
      - index: insert at 0-based *index* among children of new_parent_id
        (used by undo to restore exact sibling slot)

    Rejects cycles (self or descendant as parent). Does not rewrite legacy
    part/chapter rows — deep / non-legacy shapes make
    project_folder_sync_complete False.

    When moved=True, result includes before-snapshot fields for action log:
      old_parent_id, old_sort_order, old_index, old_sibling_ids
    """
    if not folder_table_ready(connection):
        raise ValueError("폴더 트리가 아직 준비되지 않았습니다.")

    pos = str(position or "inside").strip().lower()
    if pos not in ("before", "after", "inside", "index"):
        raise ValueError(
            "position은 before, after, inside, index 중 하나여야 합니다."
        )

    moving = _load_active_folder(connection, folder_id)
    if moving is None:
        raise ValueError("폴더를 찾을 수 없습니다.")
    project_id = int(moving["project_id"])
    old_parent_raw = moving["parent_id"]
    old_parent_id = int(old_parent_raw) if old_parent_raw is not None else None
    old_sort = int(moving["sort_order"] or 0)
    old_sibling_ids = list_folder_sibling_ids(
        connection, project_id, old_parent_id
    )
    try:
        old_index = old_sibling_ids.index(int(folder_id))
    except ValueError:
        old_index = old_sort

    resolved_parent: int | None
    anchor_id: int | None = None
    insert_index: int | None = None

    if pos == "index":
        if not new_parent_id_provided and target_id is not None:
            resolved_parent = int(target_id)
        elif new_parent_id_provided:
            resolved_parent = (
                int(new_parent_id) if new_parent_id is not None else None
            )
        else:
            raise ValueError(
                "index 위치에는 new_parent_id(또는 null 루트)가 필요합니다."
            )
        if index is None:
            raise ValueError("index 위치에는 index 값이 필요합니다.")
        try:
            insert_index = int(index)
        except (TypeError, ValueError) as error:
            raise ValueError("index가 올바르지 않습니다.") from error
    elif pos == "inside":
        if new_parent_id_provided:
            resolved_parent = (
                int(new_parent_id) if new_parent_id is not None else None
            )
        elif target_id is not None:
            resolved_parent = int(target_id)
        else:
            resolved_parent = None
        # Optional: if both provided and disagree, prefer new_parent_id (already used).
        if (
            new_parent_id_provided
            and target_id is not None
            and new_parent_id is not None
            and int(target_id) != int(new_parent_id)
            and resolved_parent is not None
        ):
            # inside target is the drop folder; new_parent_id should be that folder
            # Allow target_id to be the drop target (= parent). If both differ, error.
            if int(new_parent_id) != int(target_id):
                # new_parent_id wins when provided; target_id ignored for parent
                pass
    else:
        if target_id is None:
            raise ValueError("before/after 위치에는 target_id가 필요합니다.")
        anchor = _load_active_folder(connection, int(target_id))
        if anchor is None:
            raise ValueError("기준 폴더를 찾을 수 없습니다.")
        if int(anchor["project_id"]) != project_id:
            raise ValueError("다른 작품의 폴더로는 옮길 수 없습니다.")
        if int(anchor["id"]) == int(folder_id):
            raise ValueError("같은 폴더를 기준으로 순서를 바꿀 수 없습니다.")
        anchor_id = int(anchor["id"])
        raw_ap = anchor["parent_id"]
        resolved_parent = int(raw_ap) if raw_ap is not None else None
        if new_parent_id_provided:
            want = int(new_parent_id) if new_parent_id is not None else None
            if want != resolved_parent:
                raise ValueError(
                    "before/after일 때 new_parent_id는 기준 폴더의 부모와 같아야 합니다."
                )

    # Validate parent exists in same project
    if resolved_parent is not None:
        parent_row = _load_active_folder(connection, resolved_parent)
        if parent_row is None:
            raise ValueError("상위 폴더를 찾을 수 없습니다.")
        if int(parent_row["project_id"]) != project_id:
            raise ValueError("다른 작품의 폴더 아래로는 옮길 수 없습니다.")
        if int(resolved_parent) == int(folder_id):
            raise ValueError("폴더를 자기 자신의 하위로 넣을 수 없습니다.")
        descendants = collect_folder_descendant_ids(connection, int(folder_id))
        if int(resolved_parent) in descendants:
            raise ValueError(
                "폴더를 자기 자손 아래로 넣을 수 없습니다. (순환 참조)"
            )

    # Build ordered sibling list under destination (excluding the moving node)
    siblings = [
        fid
        for fid in list_folder_sibling_ids(
            connection, project_id, resolved_parent
        )
        if fid != int(folder_id)
    ]

    if pos == "before" and anchor_id is not None:
        if anchor_id not in siblings:
            raise ValueError("기준 폴더가 같은 상위 아래에 있지 않습니다.")
        insert_at = siblings.index(anchor_id)
        siblings.insert(insert_at, int(folder_id))
    elif pos == "after" and anchor_id is not None:
        if anchor_id not in siblings:
            raise ValueError("기준 폴더가 같은 상위 아래에 있지 않습니다.")
        insert_at = siblings.index(anchor_id) + 1
        siblings.insert(insert_at, int(folder_id))
    elif pos == "index" and insert_index is not None:
        clamped = max(0, min(int(insert_index), len(siblings)))
        siblings.insert(clamped, int(folder_id))
    else:
        # inside (or append)
        siblings.append(int(folder_id))

    # No-op: same parent and same sibling order
    same_parent = resolved_parent == old_parent_id
    if same_parent:
        cur_ids = list_folder_sibling_ids(
            connection, project_id, resolved_parent
        )
        if cur_ids == siblings:
            return {
                "ok": True,
                "id": int(folder_id),
                "project_id": project_id,
                "parent_id": resolved_parent,
                "sort_order": old_sort,
                "moved": False,
                "old_parent_id": old_parent_id,
                "old_sort_order": old_sort,
                "old_index": old_index,
                "old_sibling_ids": old_sibling_ids,
                "legacy_compatible": project_folder_tree_is_legacy_compatible(
                    connection, project_id
                ),
                "max_depth": max_folder_depth(connection, project_id),
                "folder_sync_complete": project_folder_sync_complete(
                    connection, project_id
                ),
            }

    # Park mover to free unique (parent, sort_order) slot
    connection.execute(
        """
        UPDATE folder
        SET sort_order = ?,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
            row_version = row_version + 1
        WHERE id = ?
        """,
        (9_000_000 + int(folder_id), int(folder_id)),
    )
    # Attach to new parent with temp sort
    connection.execute(
        """
        UPDATE folder
        SET parent_id = ?,
            sort_order = ?,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
            row_version = row_version + 1
        WHERE id = ?
        """,
        (resolved_parent, 9_000_000 + int(folder_id), int(folder_id)),
    )

    # Two-phase renumber destination siblings
    for renum_index, fid in enumerate(siblings):
        connection.execute(
            """
            UPDATE folder
            SET sort_order = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (1_000_000 + renum_index, fid),
        )
    for renum_index, fid in enumerate(siblings):
        connection.execute(
            """
            UPDATE folder
            SET sort_order = ?
            WHERE id = ?
            """,
            (renum_index, fid),
        )

    # Recompact old sibling group if parent changed
    if not same_parent:
        recompact_folder_siblings(connection, project_id, old_parent_id)

    new_sort = siblings.index(int(folder_id))
    return {
        "ok": True,
        "id": int(folder_id),
        "project_id": project_id,
        "parent_id": resolved_parent,
        "sort_order": new_sort,
        "moved": True,
        "position": pos,
        "old_parent_id": old_parent_id,
        "old_sort_order": old_sort,
        "old_index": old_index,
        "old_sibling_ids": old_sibling_ids,
        "legacy_compatible": project_folder_tree_is_legacy_compatible(
            connection, project_id
        ),
        "max_depth": max_folder_depth(connection, project_id),
        "folder_sync_complete": project_folder_sync_complete(connection, project_id),
    }


# ---------------------------------------------------------------------------
# U1/U2: folder action log (undo stack)
# ---------------------------------------------------------------------------

FOLDER_ACTION_LOG_LIMIT = 20

U1_ACTION_TYPES = frozenset({
    "folder.rename",
    "folder.color",
    "folder.color_bright",
    "folder.display_color",
    "folder.box",
    "folder.pin",
    "folder.bookmark",
})

U2_ACTION_TYPES = frozenset({
    "folder.reparent",
})

U3_ACTION_TYPES = frozenset({
    "folder.create",
    "folder.trash",
})

U5_ACTION_TYPES = frozenset({
    "folder.renumber_titles",
})

UNDOABLE_ACTION_TYPES = (
    U1_ACTION_TYPES | U2_ACTION_TYPES | U3_ACTION_TYPES | U5_ACTION_TYPES
)


def action_log_table_ready(connection: sqlite3.Connection) -> bool:
    try:
        connection.execute("SELECT 1 FROM folder_action_log LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False


def invalidate_redo_stack(
    connection: sqlite3.Connection,
    project_id: int,
) -> int:
    """Delete all undone entries (standard: new action clears redo history)."""
    if not action_log_table_ready(connection):
        return 0
    cur = connection.execute(
        """
        DELETE FROM folder_action_log
        WHERE project_id = ? AND undone_at IS NOT NULL
        """,
        (int(project_id),),
    )
    return int(cur.rowcount or 0)


def append_folder_action_log(
    connection: sqlite3.Connection,
    project_id: int,
    type_: str,
    label_ko: str,
    payload: dict,
    *,
    keep: int = FOLDER_ACTION_LOG_LIMIT,
) -> int | None:
    """Insert one undo entry; clear redo history; purge active stack beyond *keep*."""
    if not action_log_table_ready(connection):
        return None
    # New user action invalidates any redo branch
    invalidate_redo_stack(connection, int(project_id))
    payload_json = json.dumps(payload, ensure_ascii=False)
    cur = connection.execute(
        """
        INSERT INTO folder_action_log(project_id, type, label_ko, payload_json)
        VALUES (?, ?, ?, ?)
        """,
        (int(project_id), str(type_), str(label_ko or ""), payload_json),
    )
    log_id = int(cur.lastrowid)
    purge_folder_action_log(connection, int(project_id), keep=keep)
    return log_id


def purge_folder_action_log(
    connection: sqlite3.Connection,
    project_id: int,
    *,
    keep: int = FOLDER_ACTION_LOG_LIMIT,
) -> int:
    """Delete oldest active (undone_at IS NULL) entries beyond *keep*."""
    if not action_log_table_ready(connection):
        return 0
    keep = max(1, int(keep))
    rows = connection.execute(
        """
        SELECT id FROM folder_action_log
        WHERE project_id = ? AND undone_at IS NULL
        ORDER BY id DESC
        """,
        (int(project_id),),
    ).fetchall()
    if len(rows) <= keep:
        return 0
    drop_ids = [
        int(r["id"] if hasattr(r, "keys") else r[0]) for r in rows[keep:]
    ]
    if not drop_ids:
        return 0
    placeholders = ",".join("?" * len(drop_ids))
    cur = connection.execute(
        f"DELETE FROM folder_action_log WHERE id IN ({placeholders})",
        drop_ids,
    )
    return int(cur.rowcount or 0)


def mark_action_log_undone(
    connection: sqlite3.Connection,
    log_id: int,
) -> None:
    connection.execute(
        """
        UPDATE folder_action_log
        SET undone_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE id = ?
        """,
        (int(log_id),),
    )


def clear_action_log_undone(
    connection: sqlite3.Connection,
    log_id: int,
) -> None:
    """Return a log entry to the active undo stack (after successful redo)."""
    connection.execute(
        """
        UPDATE folder_action_log
        SET undone_at = NULL
        WHERE id = ?
        """,
        (int(log_id),),
    )


def delete_action_log(
    connection: sqlite3.Connection,
    log_id: int,
) -> None:
    """Drop a log entry (conflict skip on redo, or hard discard)."""
    connection.execute(
        "DELETE FROM folder_action_log WHERE id = ?",
        (int(log_id),),
    )


def fetch_undo_stack_top(
    connection: sqlite3.Connection,
    project_id: int,
):
    """Latest active log row for project, or None."""
    if not action_log_table_ready(connection):
        return None
    return connection.execute(
        """
        SELECT id, project_id, created_at, type, label_ko, payload_json, undone_at
        FROM folder_action_log
        WHERE project_id = ? AND undone_at IS NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(project_id),),
    ).fetchone()


def fetch_redo_stack_top(
    connection: sqlite3.Connection,
    project_id: int,
):
    """Most recently undone log row (redo candidate), or None."""
    if not action_log_table_ready(connection):
        return None
    return connection.execute(
        """
        SELECT id, project_id, created_at, type, label_ko, payload_json, undone_at
        FROM folder_action_log
        WHERE project_id = ? AND undone_at IS NOT NULL
        ORDER BY undone_at DESC, id DESC
        LIMIT 1
        """,
        (int(project_id),),
    ).fetchone()


def count_undone_action_logs(
    connection: sqlite3.Connection,
    project_id: int,
) -> int:
    if not action_log_table_ready(connection):
        return 0
    row = connection.execute(
        """
        SELECT COUNT(*) AS c FROM folder_action_log
        WHERE project_id = ? AND undone_at IS NOT NULL
        """,
        (int(project_id),),
    ).fetchone()
    return int(row["c"] if hasattr(row, "keys") else row[0])


def count_active_action_logs(
    connection: sqlite3.Connection,
    project_id: int,
) -> int:
    if not action_log_table_ready(connection):
        return 0
    row = connection.execute(
        """
        SELECT COUNT(*) AS c FROM folder_action_log
        WHERE project_id = ? AND undone_at IS NULL
        """,
        (int(project_id),),
    ).fetchone()
    return int(row["c"] if hasattr(row, "keys") else row[0])


def build_patch_action_payload(
    folder_id: int,
    field: str,
    old_value,
    new_value,
) -> dict:
    """Standard U1 payload for rename/color/box/pin."""
    return {
        "folder_id": int(folder_id),
        "forward": {
            "field": field,
            "old": old_value,
            "new": new_value,
        },
        "reverse": {
            "op": "patch",
            "fields": {field: old_value},
        },
    }


def build_display_color_action_payload(
    folder_id: int,
    old_color,
    old_bright,
    new_color,
    new_bright,
) -> dict:
    """U1 payload for one swatch pick that touches both `color` and `color_bright`
    at once (색상 팔레트: 무채색/쨍한 색 중 하나를 고르면 다른 쪽은 함께 지워짐).
    Kept as a single log entry so one Ctrl+Z restores the whole pair atomically.
    """
    fid = int(folder_id)
    pair_old = {"color": old_color, "color_bright": old_bright}
    pair_new = {"color": new_color, "color_bright": new_bright}
    return {
        "folder_id": fid,
        "forward": {
            "field": "display_color",
            "old": pair_old,
            "new": pair_new,
        },
        "reverse": {
            "op": "patch",
            "fields": {"display_color": pair_old},
        },
    }


def build_reparent_action_payload(
    *,
    folder_id: int,
    old_parent_id: int | None,
    old_sort_order: int,
    old_index: int,
    old_sibling_ids: list[int],
    new_parent_id: int | None,
    new_sort_order: int,
    new_position: str = "inside",
) -> dict:
    """Standard U2 payload for folder reparent / reorder."""
    fid = int(folder_id)
    return {
        "folder_id": fid,
        "forward": {
            "folder_id": fid,
            "old_parent_id": old_parent_id,
            "old_sort_order": int(old_sort_order),
            "old_index": int(old_index),
            "old_sibling_ids": [int(x) for x in old_sibling_ids],
            "new_parent_id": new_parent_id,
            "new_sort_order": int(new_sort_order),
            "new_position": str(new_position or "inside"),
        },
        "reverse": {
            "op": "reparent",
            "folder_id": fid,
            "parent_id": old_parent_id,
            "index": int(old_index),
            "old_sort_order": int(old_sort_order),
            "old_sibling_ids": [int(x) for x in old_sibling_ids],
        },
    }


def _norm_parent_id(value) -> int | None:
    if value is None or value == "" or value is False:
        return None
    return int(value)


# ---------------------------------------------------------------------------
# U3: create / trash undo helpers
# ---------------------------------------------------------------------------

CREATE_UNDO_BLOCKED_MSG = (
    "하위 항목이 있어 되돌릴 수 없어요. "
    "먼저 하위 항목을 옮기거나 삭제해주세요"
)


def build_create_action_payload(
    *,
    folder_id: int,
    source_kind: str | None,
    source_id: int | None,
    parent_id: int | None,
    sort_order: int,
) -> dict:
    fid = int(folder_id)
    return {
        "folder_id": fid,
        "forward": {
            "folder_id": fid,
            "source_kind": source_kind,
            "source_id": int(source_id) if source_id is not None else None,
            "parent_id": parent_id,
            "sort_order": int(sort_order),
        },
        "reverse": {
            "op": "trash_one",
            "folder_id": fid,
            "source_kind": source_kind,
            "source_id": int(source_id) if source_id is not None else None,
        },
    }


def build_renumber_titles_action_payload(
    *,
    style: str,
    items: list[dict],
) -> dict:
    """Bulk chapter title renumber snapshot for undo/redo.

    *items*: list of {chapter_id, folder_id?, old, new}
    """
    norm_items = []
    for raw in items:
        cid = int(raw["chapter_id"])
        old = str(raw.get("old") or "")
        new = str(raw.get("new") or "")
        fid_raw = raw.get("folder_id")
        fid = int(fid_raw) if fid_raw is not None and fid_raw != "" else None
        norm_items.append(
            {
                "chapter_id": cid,
                "folder_id": fid,
                "old": old,
                "new": new,
            }
        )
    return {
        "folder_id": 0,
        "forward": {
            "style": str(style or "jang"),
            "items": norm_items,
        },
        "reverse": {
            "op": "restore_titles",
            "items": [
                {
                    "chapter_id": it["chapter_id"],
                    "folder_id": it["folder_id"],
                    "title": it["old"],
                }
                for it in norm_items
            ],
        },
    }


def build_trash_action_payload(
    *,
    root_folder_id: int,
    folder_ids: list[int],
    part_ids: list[int],
    chapter_ids: list[int],
    scene_ids: list[int],
    root_parent_id: int | None,
    root_sort_order: int,
) -> dict:
    rid = int(root_folder_id)
    root = {
        "parent_id": root_parent_id,
        "sort_order": int(root_sort_order),
    }
    body = {
        "root_folder_id": rid,
        "folder_ids": [int(x) for x in folder_ids],
        "part_ids": [int(x) for x in part_ids],
        "chapter_ids": [int(x) for x in chapter_ids],
        "scene_ids": [int(x) for x in scene_ids],
        "root": root,
    }
    return {
        "folder_id": rid,
        "forward": dict(body),
        "reverse": {"op": "restore_cascade", **body},
    }


def folder_has_undo_blocking_children(
    connection: sqlite3.Connection,
    folder_id: int,
) -> bool:
    """True if active child folders exist, or mapped chapter has active scenes."""
    fid = int(folder_id)
    child = connection.execute(
        """
        SELECT 1 FROM folder
        WHERE parent_id = ? AND deleted_at IS NULL
        LIMIT 1
        """,
        (fid,),
    ).fetchone()
    if child is not None:
        return True
    row = connection.execute(
        """
        SELECT source_kind, source_id, project_id
        FROM folder WHERE id = ?
        """,
        (fid,),
    ).fetchone()
    if row is None:
        return False
    sk = row["source_kind"] if hasattr(row, "keys") else row[0]
    sid = row["source_id"] if hasattr(row, "keys") else row[1]
    if sk == "chapter" and sid is not None:
        sc = connection.execute(
            """
            SELECT 1 FROM scene
            WHERE chapter_id = ? AND deleted_at IS NULL
            LIMIT 1
            """,
            (int(sid),),
        ).fetchone()
        if sc is not None:
            return True
    if sk == "part" and sid is not None:
        # Active chapter folders under this part folder already covered by
        # parent_id walk; also catch legacy chapters if parent drifted.
        pid = int(row["project_id"] if hasattr(row, "keys") else row[2])
        legacy = connection.execute(
            """
            SELECT 1 FROM chapter
            WHERE project_id = ? AND part_id = ? AND deleted_at IS NULL
            LIMIT 1
            """,
            (pid, int(sid)),
        ).fetchone()
        if legacy is not None:
            return True
    return False


def snapshot_folder_trash(
    connection: sqlite3.Connection,
    project_id: int,
    root_folder_id: int,
    *,
    part_ids: list[int] | None = None,
    chapter_ids: list[int] | None = None,
    scene_ids: list[int] | None = None,
) -> dict:
    """Build trash undo payload from live tree + optional legacy id lists."""
    root_id = int(root_folder_id)
    folder_ids = collect_folder_descendant_ids(connection, root_id)
    root_row = connection.execute(
        """
        SELECT parent_id, sort_order, source_kind, source_id
        FROM folder WHERE id = ?
        """,
        (root_id,),
    ).fetchone()
    if root_row is None:
        raise ValueError("폴더를 찾을 수 없습니다.")
    raw_p = root_row["parent_id"] if hasattr(root_row, "keys") else root_row[0]
    root_parent = int(raw_p) if raw_p is not None else None
    root_sort = int(
        (root_row["sort_order"] if hasattr(root_row, "keys") else root_row[1]) or 0
    )

    # Derive legacy maps from folder rows when not provided
    derived_parts: list[int] = []
    derived_chapters: list[int] = []
    for fid in folder_ids:
        fr = connection.execute(
            "SELECT source_kind, source_id FROM folder WHERE id = ?",
            (int(fid),),
        ).fetchone()
        if fr is None:
            continue
        sk = fr["source_kind"] if hasattr(fr, "keys") else fr[0]
        sid = fr["source_id"] if hasattr(fr, "keys") else fr[1]
        if sk == "part" and sid is not None:
            derived_parts.append(int(sid))
        elif sk == "chapter" and sid is not None:
            derived_chapters.append(int(sid))

    parts = list(dict.fromkeys(int(x) for x in (part_ids if part_ids is not None else derived_parts)))
    chapters = list(
        dict.fromkeys(int(x) for x in (chapter_ids if chapter_ids is not None else derived_chapters))
    )

    if scene_ids is not None:
        scenes = list(dict.fromkeys(int(x) for x in scene_ids))
    else:
        scenes = []
        if chapters:
            ph = ",".join("?" * len(chapters))
            rows = connection.execute(
                f"""
                SELECT id FROM scene
                WHERE chapter_id IN ({ph}) AND deleted_at IS NULL
                """,
                chapters,
            ).fetchall()
            scenes = [int(r["id"] if hasattr(r, "keys") else r[0]) for r in rows]

    _ = project_id  # reserved for future project-scoped validation
    return build_trash_action_payload(
        root_folder_id=root_id,
        folder_ids=folder_ids,
        part_ids=parts,
        chapter_ids=chapters,
        scene_ids=scenes,
        root_parent_id=root_parent,
        root_sort_order=root_sort,
    )


def trash_one_created_folder(
    connection: sqlite3.Connection,
    folder_id: int,
) -> dict:
    """Soft-delete a single created folder + its legacy part/chapter (no cascade).

    Raises ValueError with CREATE_UNDO_BLOCKED_MSG if children block undo.
    """
    fid = int(folder_id)
    row = connection.execute(
        """
        SELECT id, project_id, parent_id, sort_order, source_kind, source_id, deleted_at
        FROM folder WHERE id = ?
        """,
        (fid,),
    ).fetchone()
    if row is None:
        raise ValueError("그 사이 폴더가 바뀌어 되돌릴 수 없어요.")
    deleted = row["deleted_at"] if hasattr(row, "keys") else row[6]
    if deleted is not None:
        return {"ok": True, "noop": True, "folder_id": fid}

    if folder_has_undo_blocking_children(connection, fid):
        raise ValueError(CREATE_UNDO_BLOCKED_MSG)

    project_id = int(row["project_id"] if hasattr(row, "keys") else row[1])
    parent_id = row["parent_id"] if hasattr(row, "keys") else row[2]
    parent_id = int(parent_id) if parent_id is not None else None
    sk = row["source_kind"] if hasattr(row, "keys") else row[4]
    sid = row["source_id"] if hasattr(row, "keys") else row[5]
    now_sql = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"

    soft_delete_folder_ids(connection, [fid])

    if sk == "part" and sid is not None:
        connection.execute(
            f"""
            UPDATE part SET deleted_at = {now_sql},
                updated_at = {now_sql},
                row_version = row_version + 1
            WHERE id = ? AND deleted_at IS NULL
            """,
            (int(sid),),
        )
    elif sk == "chapter" and sid is not None:
        connection.execute(
            f"""
            UPDATE chapter SET deleted_at = {now_sql},
                updated_at = {now_sql},
                row_version = row_version + 1
            WHERE id = ? AND deleted_at IS NULL
            """,
            (int(sid),),
        )

    recompact_folder_siblings(connection, project_id, parent_id)
    return {"ok": True, "noop": False, "folder_id": fid}


def restore_folder_trash_snapshot(
    connection: sqlite3.Connection,
    project_id: int,
    payload: dict,
) -> dict:
    """Restore folders + legacy rows from a folder.trash reverse payload."""
    reverse = payload.get("reverse") or payload.get("forward") or payload
    root_id = int(
        reverse.get("root_folder_id")
        or payload.get("folder_id")
        or 0
    )
    folder_ids = [int(x) for x in (reverse.get("folder_ids") or [])]
    part_ids = [int(x) for x in (reverse.get("part_ids") or [])]
    chapter_ids = [int(x) for x in (reverse.get("chapter_ids") or [])]
    scene_ids = [int(x) for x in (reverse.get("scene_ids") or [])]
    root_meta = reverse.get("root") or {}
    root_parent = _norm_parent_id(root_meta.get("parent_id"))
    try:
        root_sort = int(root_meta.get("sort_order", 0))
    except (TypeError, ValueError):
        root_sort = 0

    if not root_id:
        raise ValueError("되돌리기 기록이 올바르지 않아 건너뛰었어요.")
    if root_id not in folder_ids:
        folder_ids = [root_id] + [x for x in folder_ids if x != root_id]

    # Load root (may be soft-deleted)
    root_row = connection.execute(
        """
        SELECT id, project_id, parent_id, sort_order, deleted_at
        FROM folder WHERE id = ?
        """,
        (root_id,),
    ).fetchone()
    if root_row is None:
        raise ValueError("그 사이 폴더가 바뀌어 되돌릴 수 없어요.")
    if int(root_row["project_id"] if hasattr(root_row, "keys") else root_row[1]) != int(
        project_id
    ):
        raise ValueError("그 사이 폴더가 바뀌어 되돌릴 수 없어요.")

    root_deleted = root_row["deleted_at"] if hasattr(root_row, "keys") else root_row[4]

    # Verify all folder ids still exist
    for fid in folder_ids:
        exists = connection.execute(
            "SELECT 1 FROM folder WHERE id = ?", (int(fid),)
        ).fetchone()
        if exists is None:
            raise ValueError("그 사이 폴더가 바뀌어 되돌릴 수 없어요.")

    # Parent of root must be active (or null)
    if root_parent is not None:
        parent_row = connection.execute(
            """
            SELECT id, deleted_at FROM folder WHERE id = ?
            """,
            (int(root_parent),),
        ).fetchone()
        if parent_row is None:
            raise ValueError("그 사이 폴더가 바뀌어 되돌릴 수 없어요.")
        pdel = parent_row["deleted_at"] if hasattr(parent_row, "keys") else parent_row[1]
        if pdel is not None and int(root_parent) not in folder_ids:
            raise ValueError("그 사이 폴더가 바뀌어 되돌릴 수 없어요.")

    # No-op: root already active and all listed folders active
    if root_deleted is None:
        any_deleted = False
        for fid in folder_ids:
            d = connection.execute(
                "SELECT deleted_at FROM folder WHERE id = ?", (int(fid),)
            ).fetchone()
            if d is not None and (
                d["deleted_at"] if hasattr(d, "keys") else d[0]
            ) is not None:
                any_deleted = True
                break
        if not any_deleted:
            # Check legacy/scenes still deleted? if root active, treat full noop
            return {
                "ok": True,
                "noop": True,
                "folder_id": root_id,
                "restored_folders": 0,
            }

    now_sql = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"

    # Capture parent/sort before park (soft-delete keeps these; park would overwrite sort)
    saved: dict[int, tuple[int | None, int]] = {}
    for fid in folder_ids:
        fr = connection.execute(
            "SELECT parent_id, sort_order FROM folder WHERE id = ?",
            (int(fid),),
        ).fetchone()
        if fr is None:
            continue
        raw = fr["parent_id"] if hasattr(fr, "keys") else fr[0]
        p = int(raw) if raw is not None else None
        s = int((fr["sort_order"] if hasattr(fr, "keys") else fr[1]) or 0)
        # Root may prefer snapshot meta (sibling slot among live peers)
        if int(fid) == root_id:
            p = root_parent
            s = int(root_sort)
        saved[int(fid)] = (p, s)

    # Park all restoring folders at unique temp sort, clear deleted_at
    for fid in folder_ids:
        connection.execute(
            f"""
            UPDATE folder
            SET sort_order = ?,
                deleted_at = NULL,
                updated_at = {now_sql},
                row_version = row_version + 1
            WHERE id = ?
            """,
            (9_000_000 + int(fid), int(fid)),
        )

    # Restore parent_id from saved snapshot
    for fid, (p, _s) in saved.items():
        connection.execute(
            f"""
            UPDATE folder
            SET parent_id = ?,
                updated_at = {now_sql}
            WHERE id = ?
            """,
            (p, int(fid)),
        )

    # Per-parent: merge restored folders (by saved sort) with siblings that stayed active
    restored_set = set(saved.keys())
    parents: set[int | None] = {p for (p, _s) in saved.values()}
    for parent in parents:
        # Active siblings currently under parent (includes restored at temp sort)
        current = list_folder_sibling_ids(connection, project_id, parent)
        stayed = [x for x in current if x not in restored_set]
        # Keep stayed order; insert each restored at preferred index (saved sort)
        restored_here = sorted(
            [(fid, saved[fid][1]) for fid in restored_set if saved[fid][0] == parent],
            key=lambda t: (t[1], t[0]),
        )
        ordered = stayed[:]
        for fid, pref in restored_here:
            at = max(0, min(int(pref), len(ordered)))
            ordered.insert(at, fid)
        # Two-phase renumber
        for index, fid in enumerate(ordered):
            connection.execute(
                "UPDATE folder SET sort_order = ? WHERE id = ?",
                (1_000_000 + index, fid),
            )
        for index, fid in enumerate(ordered):
            connection.execute(
                "UPDATE folder SET sort_order = ? WHERE id = ?",
                (index, fid),
            )

    # Legacy restore
    for pid in part_ids:
        connection.execute(
            f"""
            UPDATE part SET deleted_at = NULL,
                updated_at = {now_sql},
                row_version = row_version + 1
            WHERE id = ? AND deleted_at IS NOT NULL
            """,
            (int(pid),),
        )
    for cid in chapter_ids:
        connection.execute(
            f"""
            UPDATE chapter SET deleted_at = NULL,
                updated_at = {now_sql},
                row_version = row_version + 1
            WHERE id = ? AND deleted_at IS NOT NULL
            """,
            (int(cid),),
        )
    if scene_ids:
        # Park then restore original sort when possible
        for sid in scene_ids:
            connection.execute(
                f"""
                UPDATE scene SET deleted_at = NULL,
                    updated_at = {now_sql}
                WHERE id = ? AND deleted_at IS NOT NULL
                """,
                (int(sid),),
            )

    return {
        "ok": True,
        "noop": False,
        "folder_id": root_id,
        "restored_folders": len(folder_ids),
        "restored_scenes": len(scene_ids),
        "restored_chapters": len(chapter_ids),
        "restored_parts": len(part_ids),
    }

