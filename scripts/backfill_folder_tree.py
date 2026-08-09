"""
Backfill parallel folder tree from part/chapter/scene (migration 028).

- Fills `folder` from all part + chapter rows (incl. soft-deleted).
- Sets `scene.folder_id` from chapter → folder map.
- Flattens scene nesting (parent_scene_id) to siblings under same chapter via DFS sort_order.
- Does not drop part/chapter or remove scene.chapter_id.

Idempotent: clears prior backfill folders (source_kind set) and scene.folder_id, then re-runs.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app  # noqa: E402


def _row_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def clear_prior_backfill(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE scene SET folder_id = NULL WHERE folder_id IS NOT NULL")
    # Remove folders created by backfill (all current folders should be backfill-only)
    conn.execute("DELETE FROM folder WHERE source_kind IS NOT NULL")
    # Orphans with no source (should not exist yet)
    leftover = conn.execute("SELECT COUNT(*) FROM folder").fetchone()[0]
    if leftover:
        conn.execute("DELETE FROM folder")


def insert_folder(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    parent_id: int | None,
    title: str,
    synopsis_md: str,
    notes_md: str,
    goal_word_count: int,
    is_box: int,
    sort_order: int,
    created_at: str | None,
    updated_at: str | None,
    deleted_at: str | None,
    row_version: int,
    source_kind: str,
    source_id: int,
) -> int:
    cur = conn.execute(
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
            project_id,
            parent_id,
            title,
            synopsis_md or "",
            notes_md or "",
            max(0, int(goal_word_count or 0)),
            1 if is_box else 0,
            int(sort_order),
            created_at,
            updated_at,
            deleted_at,
            max(1, int(row_version or 1)),
            source_kind,
            int(source_id),
        ),
    )
    return int(cur.lastrowid)


def renumber_active_folder_siblings(conn: sqlite3.Connection) -> None:
    """Ensure unique sort_order among active siblings (unique index)."""
    groups = conn.execute(
        """
        SELECT project_id, parent_id
        FROM folder
        WHERE deleted_at IS NULL
        GROUP BY project_id, parent_id
        """
    ).fetchall()
    for g in groups:
        project_id = int(g["project_id"])
        parent_id = g["parent_id"]
        if parent_id is None:
            rows = conn.execute(
                """
                SELECT id FROM folder
                WHERE project_id = ? AND parent_id IS NULL AND deleted_at IS NULL
                ORDER BY sort_order, id
                """,
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id FROM folder
                WHERE project_id = ? AND parent_id = ? AND deleted_at IS NULL
                ORDER BY sort_order, id
                """,
                (project_id, int(parent_id)),
            ).fetchall()
        # Phase 1: high temporary orders
        for i, row in enumerate(rows):
            conn.execute(
                "UPDATE folder SET sort_order = ? WHERE id = ?",
                (1_000_000 + i, int(row["id"])),
            )
        # Phase 2: compact
        for i, row in enumerate(rows):
            conn.execute(
                "UPDATE folder SET sort_order = ? WHERE id = ?",
                (i, int(row["id"])),
            )


def flatten_nested_scenes_in_chapter(conn: sqlite3.Connection, chapter_id: int) -> int:
    """DFS-order siblings; clear parent_scene_id. Returns number of scenes reordered."""
    scenes = conn.execute(
        """
        SELECT id, parent_scene_id, sort_order, deleted_at
        FROM scene
        WHERE chapter_id = ?
        ORDER BY sort_order, id
        """,
        (chapter_id,),
    ).fetchall()
    if not scenes:
        return 0
    if not any(s["parent_scene_id"] is not None for s in scenes):
        return 0

    by_parent: dict[int | None, list[sqlite3.Row]] = defaultdict(list)
    for s in scenes:
        pid = s["parent_scene_id"]
        by_parent[pid if pid is not None else None].append(s)

    ordered: list[sqlite3.Row] = []

    def walk(parent: int | None) -> None:
        kids = by_parent.get(parent, [])
        # stable by existing sort_order, id
        kids = sorted(kids, key=lambda r: (int(r["sort_order"] or 0), int(r["id"])))
        for kid in kids:
            ordered.append(kid)
            walk(int(kid["id"]))

    walk(None)
    # Orphans whose parent is missing from this chapter
    seen = {int(r["id"]) for r in ordered}
    for s in sorted(scenes, key=lambda r: (int(r["sort_order"] or 0), int(r["id"]))):
        if int(s["id"]) not in seen:
            ordered.append(s)
            seen.add(int(s["id"]))

    for i, s in enumerate(ordered):
        conn.execute(
            """
            UPDATE scene
            SET parent_scene_id = NULL,
                sort_order = ?
            WHERE id = ?
            """,
            (1_000_000 + i, int(s["id"])),
        )
    for i, s in enumerate(ordered):
        conn.execute(
            "UPDATE scene SET sort_order = ? WHERE id = ?",
            (i, int(s["id"])),
        )
    return len(ordered)


def backfill(conn: sqlite3.Connection) -> dict:
    clear_prior_backfill(conn)

    part_map: dict[int, int] = {}  # part_id -> folder_id
    chapter_map: dict[int, int] = {}  # chapter_id -> folder_id

    parts = conn.execute(
        """
        SELECT id, project_id, title, synopsis_md, sort_order,
               created_at, updated_at, deleted_at, row_version
        FROM part
        ORDER BY project_id, sort_order, id
        """
    ).fetchall()

    # Root part folders: temporary sort, renumber later
    root_seq: dict[int, int] = defaultdict(int)
    for p in parts:
        project_id = int(p["project_id"])
        seq = root_seq[project_id]
        root_seq[project_id] = seq + 1
        fid = insert_folder(
            conn,
            project_id=project_id,
            parent_id=None,
            title=str(p["title"] or "폴더"),
            synopsis_md=str(p["synopsis_md"] or ""),
            notes_md="",
            goal_word_count=0,
            is_box=1,
            sort_order=seq,
            created_at=p["created_at"],
            updated_at=p["updated_at"],
            deleted_at=p["deleted_at"],
            row_version=int(p["row_version"] or 1),
            source_kind="part",
            source_id=int(p["id"]),
        )
        part_map[int(p["id"])] = fid

    chapters = conn.execute(
        """
        SELECT id, project_id, part_id, parent_scene_id, title, synopsis_md, notes_md,
               goal_word_count, sort_order, created_at, updated_at, deleted_at, row_version
        FROM chapter
        ORDER BY project_id, sort_order, id
        """
    ).fetchall()

    # Pass 1: normal chapters (not under scene)
    child_seq: dict[tuple[int, int | None], int] = defaultdict(int)
    under_scene: list[sqlite3.Row] = []
    for ch in chapters:
        if ch["parent_scene_id"] is not None:
            under_scene.append(ch)
            continue
        project_id = int(ch["project_id"])
        part_id = ch["part_id"]
        parent_folder_id: int | None
        if part_id is not None:
            parent_folder_id = part_map.get(int(part_id))
            if parent_folder_id is None:
                raise RuntimeError(
                    f"chapter {ch['id']} part_id={part_id} has no part folder map"
                )
        else:
            parent_folder_id = None  # ungrouped root, is_box=false
        key = (project_id, parent_folder_id)
        seq = child_seq[key]
        child_seq[key] = seq + 1
        # Root ungrouped: continue after parts in same project root counter
        if parent_folder_id is None:
            seq = root_seq[project_id]
            root_seq[project_id] = seq + 1
        fid = insert_folder(
            conn,
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

    # Pass 2: chapter under scene → parent = host scene's chapter folder
    for ch in under_scene:
        project_id = int(ch["project_id"])
        host = conn.execute(
            """
            SELECT id, chapter_id, sort_order, title
            FROM scene
            WHERE id = ?
            """,
            (int(ch["parent_scene_id"]),),
        ).fetchone()
        if host is None or host["chapter_id"] is None:
            # Fallback: treat as ungrouped root
            parent_folder_id = None
            seq = root_seq[project_id]
            root_seq[project_id] = seq + 1
        else:
            host_chapter_id = int(host["chapter_id"])
            parent_folder_id = chapter_map.get(host_chapter_id)
            if parent_folder_id is None:
                raise RuntimeError(
                    f"chapter {ch['id']} under scene {ch['parent_scene_id']}: "
                    f"host chapter {host_chapter_id} has no folder"
                )
            key = (project_id, parent_folder_id)
            seq = child_seq[key]
            child_seq[key] = seq + 1
        fid = insert_folder(
            conn,
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

    renumber_active_folder_siblings(conn)

    # scene.folder_id
    scenes = conn.execute(
        "SELECT id, chapter_id, parent_scene_id FROM scene ORDER BY id"
    ).fetchall()
    missing_chapter = []
    for s in scenes:
        ch_id = int(s["chapter_id"])
        fid = chapter_map.get(ch_id)
        if fid is None:
            missing_chapter.append((int(s["id"]), ch_id))
            continue
        conn.execute(
            "UPDATE scene SET folder_id = ? WHERE id = ?",
            (fid, int(s["id"])),
        )
    if missing_chapter:
        raise RuntimeError(f"scenes without chapter folder map: {missing_chapter[:10]}")

    # Flatten nested scenes per chapter that has nesting
    chapters_with_nest = conn.execute(
        """
        SELECT DISTINCT chapter_id FROM scene
        WHERE parent_scene_id IS NOT NULL
        """
    ).fetchall()
    flattened_chapters = 0
    flattened_scenes = 0
    for row in chapters_with_nest:
        n = flatten_nested_scenes_in_chapter(conn, int(row["chapter_id"]))
        if n:
            flattened_chapters += 1
            flattened_scenes += n

    return {
        "parts": len(parts),
        "chapters": len(chapters),
        "under_scene_chapters": len(under_scene),
        "folders": conn.execute("SELECT COUNT(*) FROM folder").fetchone()[0],
        "part_folders": conn.execute(
            "SELECT COUNT(*) FROM folder WHERE source_kind = 'part'"
        ).fetchone()[0],
        "chapter_folders": conn.execute(
            "SELECT COUNT(*) FROM folder WHERE source_kind = 'chapter'"
        ).fetchone()[0],
        "scenes": len(scenes),
        "scenes_with_folder": conn.execute(
            "SELECT COUNT(*) FROM scene WHERE folder_id IS NOT NULL"
        ).fetchone()[0],
        "scenes_missing_folder": conn.execute(
            "SELECT COUNT(*) FROM scene WHERE folder_id IS NULL"
        ).fetchone()[0],
        "nested_remaining": conn.execute(
            "SELECT COUNT(*) FROM scene WHERE parent_scene_id IS NOT NULL"
        ).fetchone()[0],
        "flattened_chapters": flattened_chapters,
        "flattened_scenes": flattened_scenes,
        "part_map": part_map,
        "chapter_map": chapter_map,
    }


def verify(conn: sqlite3.Connection, stats: dict) -> list[str]:
    errors: list[str] = []
    parts = conn.execute("SELECT COUNT(*) FROM part").fetchone()[0]
    chapters = conn.execute("SELECT COUNT(*) FROM chapter").fetchone()[0]
    scenes = conn.execute("SELECT COUNT(*) FROM scene").fetchone()[0]
    folders = conn.execute("SELECT COUNT(*) FROM folder").fetchone()[0]
    if folders != parts + chapters:
        errors.append(f"folder count {folders} != part+chapter {parts}+{chapters}")
    if stats["scenes_missing_folder"]:
        errors.append(f"scenes missing folder_id: {stats['scenes_missing_folder']}")
    if stats["nested_remaining"]:
        errors.append(f"parent_scene_id still set: {stats['nested_remaining']}")

    # Every part/chapter mapped uniquely
    for kind, table in (("part", "part"), ("chapter", "chapter")):
        n_src = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        n_map = conn.execute(
            "SELECT COUNT(*) FROM folder WHERE source_kind = ?",
            (kind,),
        ).fetchone()[0]
        if n_src != n_map:
            errors.append(f"{kind} map {n_map} != source {n_src}")

    # Sample structure compare
    samples = conn.execute(
        """
        SELECT id, title FROM project
        WHERE deleted_at IS NULL
        ORDER BY id
        """
    ).fetchall()
    return errors, samples


def print_project_compare(conn: sqlite3.Connection, project_id: int, title: str) -> None:
    print(f"\n--- project {project_id}: {title!r} ---")
    parts = conn.execute(
        """
        SELECT id, title, sort_order, deleted_at
        FROM part WHERE project_id = ?
        ORDER BY sort_order, id
        """,
        (project_id,),
    ).fetchall()
    print("OLD parts:")
    for p in parts:
        flag = " [deleted]" if p["deleted_at"] else ""
        print(f"  part {p['id']} {p['title']!r} sort={p['sort_order']}{flag}")
        chs = conn.execute(
            """
            SELECT id, title, sort_order, parent_scene_id, deleted_at
            FROM chapter
            WHERE project_id = ? AND part_id = ? AND parent_scene_id IS NULL
            ORDER BY sort_order, id
            """,
            (project_id, int(p["id"])),
        ).fetchall()
        for ch in chs:
            cflag = " [deleted]" if ch["deleted_at"] else ""
            print(f"    chapter {ch['id']} {ch['title']!r}{cflag}")
            scs = conn.execute(
                """
                SELECT id, title, sort_order, folder_id, parent_scene_id
                FROM scene WHERE chapter_id = ?
                ORDER BY sort_order, id
                """,
                (int(ch["id"]),),
            ).fetchall()
            for s in scs:
                print(
                    f"      scene {s['id']} {s['title']!r} "
                    f"folder_id={s['folder_id']} sort={s['sort_order']}"
                )

    ungrouped = conn.execute(
        """
        SELECT id, title, sort_order, parent_scene_id, deleted_at
        FROM chapter
        WHERE project_id = ? AND part_id IS NULL AND parent_scene_id IS NULL
        ORDER BY sort_order, id
        """,
        (project_id,),
    ).fetchall()
    if ungrouped:
        print("OLD ungrouped chapters:")
        for ch in ungrouped:
            print(f"  chapter {ch['id']} {ch['title']!r}")

    under = conn.execute(
        """
        SELECT c.id, c.title, c.parent_scene_id, s.title AS scene_title, s.chapter_id
        FROM chapter c
        LEFT JOIN scene s ON s.id = c.parent_scene_id
        WHERE c.project_id = ? AND c.parent_scene_id IS NOT NULL
        """,
        (project_id,),
    ).fetchall()
    if under:
        print("OLD chapter-under-scene (pre-map; folders promoted):")
        for u in under:
            print(
                f"  chapter {u['id']} {u['title']!r} was under scene "
                f"{u['parent_scene_id']} {u['scene_title']!r}"
            )

    print("NEW folder tree:")

    def walk(parent_id: int | None, depth: int = 0) -> None:
        if parent_id is None:
            rows = conn.execute(
                """
                SELECT id, title, is_box, sort_order, source_kind, source_id, deleted_at
                FROM folder
                WHERE project_id = ? AND parent_id IS NULL
                ORDER BY sort_order, id
                """,
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, title, is_box, sort_order, source_kind, source_id, deleted_at
                FROM folder
                WHERE project_id = ? AND parent_id = ?
                ORDER BY sort_order, id
                """,
                (project_id, parent_id),
            ).fetchall()
        for r in rows:
            pad = "  " * depth
            box = "box" if r["is_box"] else "folder"
            dflag = " [deleted]" if r["deleted_at"] else ""
            print(
                f"{pad}- [{box}] folder {r['id']} {r['title']!r} "
                f"src={r['source_kind']}:{r['source_id']}{dflag}"
            )
            scs = conn.execute(
                """
                SELECT id, title, sort_order
                FROM scene WHERE folder_id = ?
                ORDER BY sort_order, id
                """,
                (int(r["id"]),),
            ).fetchall()
            for s in scs:
                print(f"{pad}  · scene {s['id']} {s['title']!r} sort={s['sort_order']}")
            walk(int(r["id"]), depth + 1)

    walk(None)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill folder tree from part/chapter")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run backfill then rollback transaction",
    )
    args = parser.parse_args()

    app.initialise_database()
    print("DATABASE_PATH", app.DATABASE_PATH)

    with app.database() as conn:
        conn.row_factory = sqlite3.Row
        # Ensure migration 28
        ver = conn.execute(
            "SELECT 1 FROM schema_migration WHERE version = 28"
        ).fetchone()
        if not ver:
            raise SystemExit("migration 28 not applied; run app.initialise_database first")

        if args.dry_run:
            conn.execute("BEGIN")
        try:
            stats = backfill(conn)
            errors, samples = verify(conn, stats)
            print("=== BACKFILL STATS ===")
            for k, v in stats.items():
                if k in {"part_map", "chapter_map"}:
                    continue
                print(f"  {k}: {v}")
            active_parts = conn.execute(
                "SELECT COUNT(*) FROM part WHERE deleted_at IS NULL"
            ).fetchone()[0]
            active_chapters = conn.execute(
                "SELECT COUNT(*) FROM chapter WHERE deleted_at IS NULL"
            ).fetchone()[0]
            active_scenes = conn.execute(
                "SELECT COUNT(*) FROM scene WHERE deleted_at IS NULL"
            ).fetchone()[0]
            active_folders = conn.execute(
                "SELECT COUNT(*) FROM folder WHERE deleted_at IS NULL"
            ).fetchone()[0]
            print("=== ACTIVE COUNTS ===")
            print(f"  parts={active_parts} chapters={active_chapters} scenes={active_scenes}")
            print(f"  folders_active={active_folders} (expect parts+chapters active if none deleted folders)")
            active_part_f = conn.execute(
                "SELECT COUNT(*) FROM folder WHERE source_kind='part' AND deleted_at IS NULL"
            ).fetchone()[0]
            active_ch_f = conn.execute(
                "SELECT COUNT(*) FROM folder WHERE source_kind='chapter' AND deleted_at IS NULL"
            ).fetchone()[0]
            print(f"  folder from active-ish sources: part_f={active_part_f} chapter_f={active_ch_f}")

            # Prefer real works for sample compare
            prefer = conn.execute(
                """
                SELECT id, title FROM project
                WHERE deleted_at IS NULL
                ORDER BY
                  (SELECT COUNT(*) FROM scene s WHERE s.project_id = project.id AND s.deleted_at IS NULL) DESC,
                  id
                LIMIT 3
                """
            ).fetchall()
            for p in prefer:
                print_project_compare(conn, int(p["id"]), str(p["title"]))

            # Also deleted project that had under-scene chapters if any
            special = conn.execute(
                """
                SELECT DISTINCT c.project_id, p.title
                FROM chapter c
                JOIN project p ON p.id = c.project_id
                WHERE c.parent_scene_id IS NOT NULL
                LIMIT 2
                """
            ).fetchall()
            # parent_scene cleared on scenes but chapter.parent_scene_id still set
            special = conn.execute(
                """
                SELECT DISTINCT c.project_id, p.title
                FROM chapter c
                JOIN project p ON p.id = c.project_id
                WHERE c.parent_scene_id IS NOT NULL
                LIMIT 2
                """
            ).fetchall()
            for p in special:
                print_project_compare(conn, int(p["project_id"]), str(p["title"]))

            if errors:
                print("=== VERIFY ERRORS ===")
                for e in errors:
                    print(" ", e)
                if args.dry_run:
                    conn.execute("ROLLBACK")
                return 1
            print("=== VERIFY_OK ===")
            if args.dry_run:
                conn.execute("ROLLBACK")
                print("(dry-run rolled back)")
            else:
                # database() context commits on success
                print("(committed)")
            return 0
        except Exception:
            if args.dry_run:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
            raise


if __name__ == "__main__":
    raise SystemExit(main())
