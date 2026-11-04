"""웹소설 「장르 클리셰」 칸 HTTP API. app.py는 라우트만 연결한다.

칸은 두 개이고 작가만 쓴다(docs/webnovel-genre-pipeline.md).
- cliche_used:  의도적으로 쓴 클리셰
- cliche_avoid: 피하고 싶은 클리셰
"""

from __future__ import annotations

import re

import webnovel_genre_track


def _database():
    import app

    return app.database()


def _project_row(connection, project_id: int):
    row = connection.execute(
        "SELECT cluster_id, main_genre, sub_genre, cliche_used, cliche_avoid "
        "FROM project WHERE id = ? AND deleted_at IS NULL",
        (project_id,),
    ).fetchone()
    if row is None:
        raise ValueError("소설을 찾을 수 없습니다.")
    return row


def _payload(row) -> dict:
    project = dict(row)
    group = webnovel_genre_track.genre_group(project)
    return {
        "ok": True,
        "applies": group is not None,
        "group": group or "",
        **webnovel_genre_track.cliche_values(project),
    }


def get_webnovel_cliche(project_id: int) -> dict:
    with _database() as connection:
        return _payload(_project_row(connection, project_id))


def save_webnovel_cliche(project_id: int, body: dict) -> dict:
    present = [key for key in webnovel_genre_track.CLICHE_FIELDS if key in body]
    with _database() as connection:
        row = _project_row(connection, project_id)
        if present:
            if webnovel_genre_track.genre_group(dict(row)) is None:
                raise ValueError("장르 클리셰는 웹소설 작품에서만 쓸 수 있어요.")
            values = [
                str(body.get(key) or "")[: webnovel_genre_track.CLICHE_FIELD_MAX_CHARS]
                for key in present
            ]
            assignments = ", ".join(f"{key} = ?" for key in present)
            connection.execute(
                f"UPDATE project SET {assignments} WHERE id = ?",
                (*values, project_id),
            )
            row = _project_row(connection, project_id)
        return _payload(row)


_PATH = re.compile(r"/api/projects/(\d+)/webnovel-cliche")


def try_handle_get(handler, path: str) -> bool:
    match = _PATH.fullmatch(path)
    if not match:
        return False
    handler.send_json(get_webnovel_cliche(int(match.group(1))))
    return True


def try_handle_post(handler, path: str, body: dict | None) -> bool:
    match = _PATH.fullmatch(path)
    if not match:
        return False
    handler.send_json(save_webnovel_cliche(int(match.group(1)), body or {}))
    return True
