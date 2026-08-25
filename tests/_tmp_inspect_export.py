# -*- coding: utf-8 -*-
"""Inspect export txt around 1부/1화 for project 22. Read-only."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app
import document_export
import import_hierarchy


def main() -> None:
    db = ROOT / "data" / "supertory.sqlite3"
    uri = db.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row

    pid = 22
    print("=== SCENES matching 겨울/프롤로그 in project 22 ===")
    rows = conn.execute(
        """
        SELECT s.id, s.title, s.parent_scene_id, s.chapter_id, s.folder_id, s.sort_order,
               c.title AS chapter_title, length(COALESCE(r.content_md,'')) AS body_len
        FROM scene s
        JOIN chapter c ON c.id = s.chapter_id
        LEFT JOIN scene_revision r ON r.scene_id = s.id AND r.is_current = 1
        WHERE s.project_id = ? AND s.deleted_at IS NULL
          AND (s.title LIKE '%겨울%' OR s.title LIKE '%프롤로그%' OR c.title LIKE '%1부%')
        ORDER BY s.chapter_id, s.sort_order, s.id
        """,
        (pid,),
    ).fetchall()
    for row in rows:
        print(
            f"id={row['id']} parent={row['parent_scene_id']} ch={row['chapter_id']}:{row['chapter_title']!r} "
            f"folder={row['folder_id']} sort={row['sort_order']} len={row['body_len']} title={row['title']!r}"
        )

    print("\n=== 1부 folder scenes (tree) ===")
    folders = conn.execute(
        "SELECT id, title, parent_id, is_box, sort_order FROM folder "
        "WHERE project_id=? AND deleted_at IS NULL AND title IN ('1권','1부','추가확인') "
        "ORDER BY sort_order, id",
        (pid,),
    ).fetchall()
    for f in folders:
        print(dict(f))
        scs = conn.execute(
            "SELECT id, title, parent_scene_id, sort_order FROM scene "
            "WHERE project_id=? AND deleted_at IS NULL AND folder_id=? "
            "ORDER BY sort_order, id",
            (pid, f["id"]),
        ).fetchall()
        for s in scs:
            print("  ", dict(s))

    conn.close()

    handler = object.__new__(app.SuperToryHandler)
    exported = handler.export_project(pid, "txt")
    text = exported.data.decode("utf-8-sig")
    out = ROOT / "tests" / "_tmp_export_lady.txt"
    out.write_text(text, encoding="utf-8")
    print("\n=== EXPORT META ===")
    print("filename", exported.filename)
    print("chars", len(text))
    print("saved", out)

    markers = ["한 겨울", "한겨울", "프롤로그", "1화", "1부", "【"]
    print("\n=== MARKER COUNTS ===")
    for m in markers:
        print(repr(m), text.count(m))

    # Print window around first 한 겨울 / 1화
    idx = text.find("한 겨울")
    if idx < 0:
        idx = text.find("한겨울")
    if idx < 0:
        idx = text.find("1화")
    start = max(0, idx - 400)
    end = min(len(text), idx + 1200)
    print("\n=== TXT WINDOW around 1화 ===")
    print(text[start:end])
    print("=== END WINDOW ===")

    # Chapter/scene block kinds
    print("\n=== FIRST 80 NONEMPTY LINES ===")
    n = 0
    for line in text.splitlines():
        if not line.strip() and n > 15:
            continue
        print(repr(line[:120]))
        n += 1
        if n >= 80:
            break

    # How import would classify this export
    print("\n=== HIERARCHY PLAN (no AI rewrite in this script if gemini fails) ===")
    try:
        plan = import_hierarchy.build_hierarchy_plan(text)
        print("toc_source", plan.toc_source)
        print("warnings", plan.warnings)
        print("volumes", len(plan.volumes))
        for vol in plan.volumes:
            print(f"  VOL {vol.title!r} folders={len(vol.folders)}")
            for folder in vol.folders:
                print(
                    f"    FOLDER {folder.title!r} transparent={folder.transparent} "
                    f"episodes={len(folder.episodes)}"
                )
                for ep in folder.episodes[:6]:
                    print(
                        f"      EP {ep.title!r} content_len={len(ep.content or '')} "
                        f"head={((ep.content or '')[:60]).replace(chr(10),' / ')!r}"
                    )
                if len(folder.episodes) > 6:
                    print(f"      ... +{len(folder.episodes)-6} more")
                    last = folder.episodes[-1]
                    print(
                        f"      LAST {last.title!r} content_len={len(last.content or '')}"
                    )
    except Exception as exc:
        print("plan failed", type(exc), exc)


if __name__ == "__main__":
    main()
